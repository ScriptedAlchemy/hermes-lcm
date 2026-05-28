"""Dashboard API for hermes-lcm plugin.

Mounted by Hermes at /api/plugins/hermes-lcm.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

router = APIRouter()


def _expand_hermes_path(raw: str | None, fallback: Path) -> Path:
    if not raw:
        return fallback
    try:
        from hermes_constants import get_hermes_home

        hermes_home = str(get_hermes_home())
    except Exception:
        hermes_home = str(Path.home() / ".hermes")
    expanded = raw.replace("${HERMES_HOME}", hermes_home).replace("$HERMES_HOME", hermes_home)
    return Path(expanded).expanduser()


def resolve_lcm_db_path() -> Path:
    explicit = os.environ.get("LCM_DATABASE_PATH") or os.environ.get("LCM_DB_PATH")
    try:
        from hermes_constants import get_hermes_home

        fallback = Path(get_hermes_home()) / "lcm.db"
    except Exception:
        fallback = Path.home() / ".hermes" / "lcm.db"
    return _expand_hermes_path(explicit, fallback)


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=2.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return bool(row)


def _column_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    if not _table_exists(conn, table_name):
        return False
    return any(row["name"] == column_name for row in conn.execute(f"PRAGMA table_info({table_name})"))


def _fetch_rows(conn: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def build_overview(db_path: Path, q: str = "", limit: int = 25) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "path": str(db_path),
        "exists": db_path.exists(),
        "overview": {
            "messages_total": 0,
            "sessions_total": 0,
            "summary_nodes_total": 0,
            "summary_node_sessions_total": 0,
            "max_summary_depth": 0,
            "role_counts": [],
            "source_counts": [],
            "depth_counts": [],
        },
        "latest_sessions": [],
        "latest_summary_nodes": [],
        "matches": {"messages": [], "summary_nodes": []},
        "query": q,
        "limit": limit,
    }
    if not db_path.exists():
        return payload

    try:
        conn = _connect_readonly(db_path)
    except sqlite3.Error as exc:
        payload["error"] = str(exc)
        return payload

    with conn:
        has_messages = _table_exists(conn, "messages")
        has_nodes = _table_exists(conn, "summary_nodes")

        if has_messages:
            counts = conn.execute(
                "SELECT COUNT(*) AS messages_total, COUNT(DISTINCT session_id) AS sessions_total FROM messages"
            ).fetchone()
            payload["overview"]["messages_total"] = int(counts["messages_total"] or 0)
            payload["overview"]["sessions_total"] = int(counts["sessions_total"] or 0)
            payload["overview"]["role_counts"] = _fetch_rows(
                conn,
                """
                SELECT role, COUNT(*) AS count
                FROM messages
                GROUP BY role
                ORDER BY count DESC, role ASC
                """,
            )
            payload["overview"]["source_counts"] = _fetch_rows(
                conn,
                """
                SELECT
                  CASE
                    WHEN source IS NULL OR TRIM(source) = '' THEN 'unknown'
                    ELSE source
                  END AS source,
                  COUNT(*) AS count
                FROM messages
                GROUP BY source
                ORDER BY count DESC, source ASC
                """,
            )
            payload["latest_sessions"] = _fetch_rows(
                conn,
                """
                SELECT
                  session_id,
                  COUNT(*) AS message_count,
                  MAX(store_id) AS last_store_id,
                  MAX(timestamp) AS last_timestamp
                FROM messages
                GROUP BY session_id
                ORDER BY last_timestamp DESC
                LIMIT ?
                """,
                (limit,),
            )

        if has_nodes:
            has_category = _column_exists(conn, "summary_nodes", "category")
            has_expand_hint = _column_exists(conn, "summary_nodes", "expand_hint")
            category_select = "category" if has_category else "'general'"
            expand_hint_select = "expand_hint" if has_expand_hint else "''"
            node_counts = conn.execute(
                """
                SELECT
                  COUNT(*) AS summary_nodes_total,
                  COUNT(DISTINCT session_id) AS summary_node_sessions_total,
                  COALESCE(MAX(depth), 0) AS max_summary_depth
                FROM summary_nodes
                """
            ).fetchone()
            payload["overview"]["summary_nodes_total"] = int(node_counts["summary_nodes_total"] or 0)
            payload["overview"]["summary_node_sessions_total"] = int(
                node_counts["summary_node_sessions_total"] or 0
            )
            payload["overview"]["max_summary_depth"] = int(node_counts["max_summary_depth"] or 0)
            payload["overview"]["depth_counts"] = _fetch_rows(
                conn,
                """
                SELECT depth, COUNT(*) AS count
                FROM summary_nodes
                GROUP BY depth
                ORDER BY depth ASC
                """,
            )
            payload["latest_summary_nodes"] = _fetch_rows(
                conn,
                f"""
                SELECT
                  node_id,
                  session_id,
                  depth,
                  {category_select} AS category,
                  source_type,
                  token_count,
                  source_token_count,
                  latest_at,
                  created_at,
                  {expand_hint_select} AS expand_hint,
                  summary
                FROM summary_nodes
                ORDER BY COALESCE(latest_at, created_at) DESC, node_id DESC
                LIMIT ?
                """,
                (limit,),
            )

        query = (q or "").strip()
        if query:
            like = f"%{query}%"
            if has_messages:
                payload["matches"]["messages"] = _fetch_rows(
                    conn,
                    """
                    SELECT
                      store_id,
                      session_id,
                      role,
                      CASE
                        WHEN source IS NULL OR TRIM(source) = '' THEN 'unknown'
                        ELSE source
                      END AS source,
                      timestamp,
                      content
                    FROM messages
                    WHERE content LIKE ? ESCAPE '\\'
                    ORDER BY timestamp DESC, store_id DESC
                    LIMIT ?
                    """,
                    (like, limit),
                )
            if has_nodes:
                if has_expand_hint:
                    node_where = "summary LIKE ? ESCAPE '\\' OR expand_hint LIKE ? ESCAPE '\\'"
                    node_params: tuple[Any, ...] = (like, like, limit)
                else:
                    node_where = "summary LIKE ? ESCAPE '\\'"
                    node_params = (like, limit)
                payload["matches"]["summary_nodes"] = _fetch_rows(
                    conn,
                    f"""
                    SELECT
                      node_id,
                      session_id,
                      depth,
                      {category_select} AS category,
                      source_type,
                      COALESCE(latest_at, created_at) AS recency,
                      summary,
                      {expand_hint_select} AS expand_hint
                    FROM summary_nodes
                    WHERE {node_where}
                    ORDER BY recency DESC, node_id DESC
                    LIMIT ?
                    """,
                    node_params,
                )

    return payload


@router.get("/overview")
def get_overview(
    q: str = Query(default=""),
    limit: int = Query(default=25, ge=1, le=200),
) -> dict[str, Any]:
    db_path = resolve_lcm_db_path()
    return build_overview(db_path, q=q, limit=limit)
