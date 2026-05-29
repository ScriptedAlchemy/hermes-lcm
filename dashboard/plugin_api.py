"""Dashboard API for hermes-lcm plugin.

Mounted by Hermes at /api/plugins/hermes-lcm.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
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


def _build_fts_match(raw: str) -> str:
    """Turn raw user input into a safe FTS5 MATCH expression.

    Tokenize on whitespace, drop punctuation-only tokens, strip embedded
    quotes, wrap each surviving token as a quoted string (so FTS5 treats any
    internal punctuation -- ``-``, ``:`` columns, etc. -- as token separators
    rather than operators), then append a prefix ``*`` to the final token so a
    trailing partial word still matches. Returns ``""`` when nothing usable
    remains, in which case callers should fall back to ``LIKE``.

    Never pass raw user text to MATCH directly: a bare ``-``, an unbalanced
    quote, or a ``col:`` prefix is a syntax error that raises at query time.
    """
    tokens: list[str] = []
    for chunk in str(raw or "").split():
        cleaned = chunk.replace('"', "")
        if not re.search(r"\w", cleaned, flags=re.UNICODE):
            continue
        tokens.append(cleaned)
    if not tokens:
        return ""
    quoted = [f'"{tok}"' for tok in tokens]
    quoted[-1] = quoted[-1] + "*"
    return " ".join(quoted)


def _parse_epoch(val: Any) -> float | None:
    """Accept an epoch (int/float/numeric string) or ISO-8601 string -> epoch seconds."""
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        pass
    text = str(val).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


_SOURCE_CASE = (
    "CASE WHEN {a}.source IS NULL OR TRIM({a}.source) = '' THEN 'unknown' ELSE {a}.source END"
)


def _message_facet_clauses(
    role: str | None,
    source: str | None,
    session_id: str | None,
    since_epoch: float | None,
    until_epoch: float | None,
    alias: str = "m",
) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if role:
        clauses.append(f"{alias}.role = ?")
        params.append(role)
    if source:
        if source == "unknown":
            clauses.append(f"({alias}.source IS NULL OR TRIM({alias}.source) = '')")
        else:
            clauses.append(f"{alias}.source = ?")
            params.append(source)
    if session_id:
        clauses.append(f"{alias}.session_id = ?")
        params.append(session_id)
    if since_epoch is not None:
        clauses.append(f"{alias}.timestamp >= ?")
        params.append(since_epoch)
    if until_epoch is not None:
        clauses.append(f"{alias}.timestamp <= ?")
        params.append(until_epoch)
    return clauses, params


def _coerce_id_list(raw: Any) -> list[int]:
    """Parse a JSON list (or already-decoded list) of ids into a list of ints."""
    if raw is None:
        return []
    value = raw
    if isinstance(value, (str, bytes)):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return []
    if not isinstance(value, (list, tuple)):
        return []
    out: list[int] = []
    for item in value:
        try:
            out.append(int(item))
        except (ValueError, TypeError):
            continue
    return out


def _node_columns(conn: sqlite3.Connection) -> dict[str, str]:
    """Map optional summary_nodes columns to a SELECT expression (literal fallback)."""
    optional = {
        "category": "'general'",
        "expand_hint": "''",
        "earliest_at": "NULL",
        "tags": "NULL",
        "entities": "NULL",
        "taxonomy_metadata": "NULL",
    }
    return {
        name: (name if _column_exists(conn, "summary_nodes", name) else fallback)
        for name, fallback in optional.items()
    }


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
            "compression": {
                "source_token_count": 0,
                "token_count": 0,
                "ratio": 0.0,
                "node_count": 0,
            },
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
            comp = conn.execute(
                """
                SELECT
                  COALESCE(SUM(source_token_count), 0) AS source_token_count,
                  COALESCE(SUM(token_count), 0) AS token_count,
                  COUNT(*) AS node_count
                FROM summary_nodes
                """
            ).fetchone()
            src_tok = int(comp["source_token_count"] or 0)
            out_tok = int(comp["token_count"] or 0)
            payload["overview"]["compression"] = {
                "source_token_count": src_tok,
                "token_count": out_tok,
                "ratio": round(src_tok / out_tok, 2) if out_tok else 0.0,
                "node_count": int(comp["node_count"] or 0),
            }
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


def build_search(
    db_path: Path,
    q: str = "",
    limit: int = 25,
    role: str | None = None,
    source: str | None = None,
    session_id: str | None = None,
    since: Any = None,
    until: Any = None,
) -> dict[str, Any]:
    """Ranked full-text search over messages + summary nodes.

    Uses the FTS5 mirrors (``messages_fts`` / ``nodes_fts``) with ``ORDER BY
    rank`` and ``snippet()`` highlighting when present; otherwise falls back to
    ``LIKE '%q%'`` so older databases still work. Optional facets (role, source,
    session_id, since/until) are applied as extra WHERE filters on messages;
    session_id + date range also constrain node results.
    """
    since_epoch = _parse_epoch(since)
    until_epoch = _parse_epoch(until)
    payload: dict[str, Any] = {
        "path": str(db_path),
        "exists": db_path.exists(),
        "query": q,
        "limit": limit,
        "engine": "none",
        "filters": {
            "role": role or None,
            "source": source or None,
            "session_id": session_id or None,
            "since": since_epoch,
            "until": until_epoch,
        },
        "matches": {"messages": [], "summary_nodes": []},
    }
    query = (q or "").strip()
    if not db_path.exists() or not query:
        return payload

    try:
        conn = _connect_readonly(db_path)
    except sqlite3.Error as exc:
        payload["error"] = str(exc)
        return payload

    with conn:
        has_messages = _table_exists(conn, "messages")
        has_nodes = _table_exists(conn, "summary_nodes")
        has_msg_fts = _table_exists(conn, "messages_fts")
        has_node_fts = _table_exists(conn, "nodes_fts")
        match_expr = _build_fts_match(query)
        like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        use_fts = bool(match_expr) and (has_msg_fts or has_node_fts)
        payload["engine"] = "fts" if use_fts else "like"
        source_case = _SOURCE_CASE.format(a="m")

        if has_messages:
            facet_clauses, facet_params = _message_facet_clauses(
                role, source, session_id, since_epoch, until_epoch, alias="m"
            )
            if match_expr and has_msg_fts:
                where = ["messages_fts MATCH ?"] + facet_clauses
                params: list[Any] = [match_expr] + facet_params + [limit]
                payload["matches"]["messages"] = _fetch_rows(
                    conn,
                    f"""
                    SELECT
                      m.store_id,
                      m.session_id,
                      m.role,
                      {source_case} AS source,
                      m.timestamp,
                      snippet(messages_fts, 0, '[', ']', '…', 12) AS snippet,
                      m.content
                    FROM messages_fts
                    JOIN messages m ON m.store_id = messages_fts.rowid
                    WHERE {" AND ".join(where)}
                    ORDER BY rank
                    LIMIT ?
                    """,
                    tuple(params),
                )
            else:
                where = ["m.content LIKE ? ESCAPE '\\'"] + facet_clauses
                params = [like] + facet_params + [limit]
                payload["matches"]["messages"] = _fetch_rows(
                    conn,
                    f"""
                    SELECT
                      m.store_id,
                      m.session_id,
                      m.role,
                      {source_case} AS source,
                      m.timestamp,
                      substr(m.content, 1, 280) AS snippet,
                      m.content
                    FROM messages m
                    WHERE {" AND ".join(where)}
                    ORDER BY m.timestamp DESC, m.store_id DESC
                    LIMIT ?
                    """,
                    tuple(params),
                )

        if has_nodes:
            cols = _node_columns(conn)
            node_clauses: list[str] = []
            node_params: list[Any] = []
            if session_id:
                node_clauses.append("n.session_id = ?")
                node_params.append(session_id)
            if since_epoch is not None:
                node_clauses.append("COALESCE(n.latest_at, n.created_at) >= ?")
                node_params.append(since_epoch)
            if until_epoch is not None:
                node_clauses.append("COALESCE(n.latest_at, n.created_at) <= ?")
                node_params.append(until_epoch)
            if match_expr and has_node_fts:
                where = ["nodes_fts MATCH ?"] + node_clauses
                params = [match_expr] + node_params + [limit]
                payload["matches"]["summary_nodes"] = _fetch_rows(
                    conn,
                    f"""
                    SELECT
                      n.node_id,
                      n.session_id,
                      n.depth,
                      {cols['category']} AS category,
                      n.source_type,
                      n.token_count,
                      n.source_token_count,
                      COALESCE(n.latest_at, n.created_at) AS recency,
                      snippet(nodes_fts, 0, '[', ']', '…', 14) AS snippet,
                      {cols['expand_hint']} AS expand_hint,
                      n.summary
                    FROM nodes_fts
                    JOIN summary_nodes n ON n.node_id = nodes_fts.rowid
                    WHERE {" AND ".join(where)}
                    ORDER BY rank
                    LIMIT ?
                    """,
                    tuple(params),
                )
            else:
                where = ["(n.summary LIKE ? ESCAPE '\\' OR " + cols["expand_hint"] + " LIKE ? ESCAPE '\\')"]
                where += node_clauses
                params = [like, like] + node_params + [limit]
                payload["matches"]["summary_nodes"] = _fetch_rows(
                    conn,
                    f"""
                    SELECT
                      n.node_id,
                      n.session_id,
                      n.depth,
                      {cols['category']} AS category,
                      n.source_type,
                      n.token_count,
                      n.source_token_count,
                      COALESCE(n.latest_at, n.created_at) AS recency,
                      substr(n.summary, 1, 280) AS snippet,
                      {cols['expand_hint']} AS expand_hint,
                      n.summary
                    FROM summary_nodes n
                    WHERE {" AND ".join(where)}
                    ORDER BY recency DESC, n.node_id DESC
                    LIMIT ?
                    """,
                    tuple(params),
                )

    return payload


def build_session(
    db_path: Path,
    session_id: str,
    limit: int = 200,
    offset: int = 0,
    order: str = "asc",
) -> dict[str, Any]:
    """One session's messages (oldest-first by default) plus its summary nodes.

    ``order`` accepts ``"asc"`` (oldest first, default) or ``"desc"``.
    """
    order_sql = "DESC" if str(order).lower() == "desc" else "ASC"
    payload: dict[str, Any] = {
        "path": str(db_path),
        "exists": db_path.exists(),
        "session_id": session_id,
        "limit": limit,
        "offset": offset,
        "order": "desc" if order_sql == "DESC" else "asc",
        "counts": {
            "message_count": 0,
            "summary_node_count": 0,
            "token_estimate_total": 0,
            "summary_token_count": 0,
            "source_token_count": 0,
        },
        "messages": [],
        "summary_nodes": [],
        "has_more": False,
    }
    if not db_path.exists():
        return payload

    try:
        conn = _connect_readonly(db_path)
    except sqlite3.Error as exc:
        payload["error"] = str(exc)
        return payload

    with conn:
        source_case = _SOURCE_CASE.format(a="m")
        if _table_exists(conn, "messages"):
            agg = conn.execute(
                "SELECT COUNT(*) AS c, COALESCE(SUM(token_estimate), 0) AS t FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            payload["counts"]["message_count"] = int(agg["c"] or 0)
            payload["counts"]["token_estimate_total"] = int(agg["t"] or 0)
            payload["messages"] = _fetch_rows(
                conn,
                f"""
                SELECT
                  m.store_id,
                  m.session_id,
                  m.role,
                  {source_case} AS source,
                  m.tool_name,
                  m.timestamp,
                  m.token_estimate,
                  m.pinned,
                  m.content
                FROM messages m
                WHERE m.session_id = ?
                ORDER BY m.timestamp {order_sql}, m.store_id {order_sql}
                LIMIT ? OFFSET ?
                """,
                (session_id, limit + 1, offset),
            )
            if len(payload["messages"]) > limit:
                payload["has_more"] = True
                payload["messages"] = payload["messages"][:limit]

        if _table_exists(conn, "summary_nodes"):
            cols = _node_columns(conn)
            nagg = conn.execute(
                """
                SELECT
                  COUNT(*) AS c,
                  COALESCE(SUM(token_count), 0) AS tc,
                  COALESCE(SUM(source_token_count), 0) AS stc
                FROM summary_nodes WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            payload["counts"]["summary_node_count"] = int(nagg["c"] or 0)
            payload["counts"]["summary_token_count"] = int(nagg["tc"] or 0)
            payload["counts"]["source_token_count"] = int(nagg["stc"] or 0)
            payload["summary_nodes"] = _fetch_rows(
                conn,
                f"""
                SELECT
                  node_id,
                  session_id,
                  depth,
                  {cols['category']} AS category,
                  source_type,
                  token_count,
                  source_token_count,
                  COALESCE(latest_at, created_at) AS recency,
                  created_at,
                  {cols['expand_hint']} AS expand_hint,
                  summary
                FROM summary_nodes
                WHERE session_id = ?
                ORDER BY depth ASC, COALESCE(latest_at, created_at) ASC, node_id ASC
                """,
                (session_id,),
            )

    return payload


def build_node(db_path: Path, node_id: int) -> dict[str, Any]:
    """A summary node plus the exact source items it covers (lossless expand).

    ``source_type == 'messages'`` -> resolve ``source_ids`` against
    ``messages.store_id``; ``source_type == 'nodes'`` -> resolve against child
    ``summary_nodes.node_id``.
    """
    payload: dict[str, Any] = {
        "path": str(db_path),
        "exists": db_path.exists(),
        "node_id": node_id,
        "node": None,
        "sources": {"type": None, "ids": [], "messages": [], "nodes": []},
    }
    if not db_path.exists():
        return payload

    try:
        conn = _connect_readonly(db_path)
    except sqlite3.Error as exc:
        payload["error"] = str(exc)
        return payload

    with conn:
        if not _table_exists(conn, "summary_nodes"):
            return payload
        cols = _node_columns(conn)
        node = conn.execute(
            f"""
            SELECT
              node_id,
              session_id,
              depth,
              summary,
              token_count,
              source_token_count,
              source_ids,
              source_type,
              created_at,
              {cols['earliest_at']} AS earliest_at,
              latest_at,
              {cols['expand_hint']} AS expand_hint,
              {cols['category']} AS category,
              {cols['tags']} AS tags,
              {cols['entities']} AS entities
            FROM summary_nodes
            WHERE node_id = ?
            """,
            (node_id,),
        ).fetchone()
        if node is None:
            return payload
        node_dict = dict(node)
        payload["node"] = node_dict

        ids = _coerce_id_list(node_dict.get("source_ids"))
        source_type = node_dict.get("source_type") or "messages"
        payload["sources"]["type"] = source_type
        payload["sources"]["ids"] = ids
        if not ids:
            return payload
        placeholders = ",".join("?" for _ in ids)

        if source_type == "nodes" and _table_exists(conn, "summary_nodes"):
            payload["sources"]["nodes"] = _fetch_rows(
                conn,
                f"""
                SELECT
                  node_id,
                  session_id,
                  depth,
                  {cols['category']} AS category,
                  source_type,
                  token_count,
                  source_token_count,
                  COALESCE(latest_at, created_at) AS recency,
                  {cols['expand_hint']} AS expand_hint,
                  summary
                FROM summary_nodes
                WHERE node_id IN ({placeholders})
                ORDER BY COALESCE(latest_at, created_at) ASC, node_id ASC
                """,
                tuple(ids),
            )
        elif _table_exists(conn, "messages"):
            source_case = _SOURCE_CASE.format(a="m")
            rows = _fetch_rows(
                conn,
                f"""
                SELECT
                  m.store_id,
                  m.session_id,
                  m.role,
                  {source_case} AS source,
                  m.tool_name,
                  m.timestamp,
                  m.token_estimate,
                  m.content
                FROM messages m
                WHERE m.store_id IN ({placeholders})
                """,
                tuple(ids),
            )
            order = {sid: i for i, sid in enumerate(ids)}
            rows.sort(key=lambda r: order.get(r.get("store_id"), 1 << 30))
            payload["sources"]["messages"] = rows

    return payload


def build_timeline(
    db_path: Path,
    bucket: str = "day",
    session_id: str | None = None,
    limit: int = 400,
) -> dict[str, Any]:
    """Message volume bucketed over time (per ``day`` or ``hour``).

    Returns ``buckets`` for messages and ``node_buckets`` for summary coverage,
    each as ``[{bucket, count, ...}]`` ordered chronologically.
    """
    fmt = "%Y-%m-%dT%H:00" if str(bucket).lower() == "hour" else "%Y-%m-%d"
    payload: dict[str, Any] = {
        "path": str(db_path),
        "exists": db_path.exists(),
        "bucket": "hour" if fmt.endswith(":00") else "day",
        "session_id": session_id or None,
        "buckets": [],
        "node_buckets": [],
    }
    if not db_path.exists():
        return payload

    try:
        conn = _connect_readonly(db_path)
    except sqlite3.Error as exc:
        payload["error"] = str(exc)
        return payload

    with conn:
        if _table_exists(conn, "messages"):
            where = "WHERE session_id = ?" if session_id else ""
            params: tuple[Any, ...] = (session_id, limit) if session_id else (limit,)
            payload["buckets"] = _fetch_rows(
                conn,
                f"""
                SELECT
                  strftime('{fmt}', timestamp, 'unixepoch') AS bucket,
                  COUNT(*) AS count,
                  COALESCE(SUM(token_estimate), 0) AS token_estimate
                FROM messages
                {where}
                GROUP BY bucket
                ORDER BY bucket ASC
                LIMIT ?
                """,
                params,
            )
        if _table_exists(conn, "summary_nodes"):
            where = "WHERE session_id = ?" if session_id else ""
            params = (session_id, limit) if session_id else (limit,)
            payload["node_buckets"] = _fetch_rows(
                conn,
                f"""
                SELECT
                  strftime('{fmt}', COALESCE(latest_at, created_at), 'unixepoch') AS bucket,
                  COUNT(*) AS count
                FROM summary_nodes
                {where}
                GROUP BY bucket
                ORDER BY bucket ASC
                LIMIT ?
                """,
                params,
            )

    return payload


def build_compression(db_path: Path, by: str = "session", limit: int = 50) -> dict[str, Any]:
    """Token-compression stats: overall ratio + per-group breakdown.

    ``by`` is ``"session"`` (default) or ``"node"``. Each group reports
    ``source_token_count`` (kept input) vs ``token_count`` (summary output)
    so the front-end can draw kept-vs-saved bars.
    """
    group_by_node = str(by).lower() == "node"
    payload: dict[str, Any] = {
        "path": str(db_path),
        "exists": db_path.exists(),
        "by": "node" if group_by_node else "session",
        "limit": limit,
        "overall": {
            "source_token_count": 0,
            "token_count": 0,
            "ratio": 0.0,
            "node_count": 0,
        },
        "groups": [],
    }
    if not db_path.exists():
        return payload

    try:
        conn = _connect_readonly(db_path)
    except sqlite3.Error as exc:
        payload["error"] = str(exc)
        return payload

    with conn:
        if not _table_exists(conn, "summary_nodes"):
            return payload
        overall = conn.execute(
            """
            SELECT
              COALESCE(SUM(source_token_count), 0) AS src,
              COALESCE(SUM(token_count), 0) AS out,
              COUNT(*) AS n
            FROM summary_nodes
            """
        ).fetchone()
        src = int(overall["src"] or 0)
        out = int(overall["out"] or 0)
        payload["overall"] = {
            "source_token_count": src,
            "token_count": out,
            "ratio": round(src / out, 2) if out else 0.0,
            "node_count": int(overall["n"] or 0),
        }
        if group_by_node:
            groups = _fetch_rows(
                conn,
                """
                SELECT
                  node_id AS key,
                  session_id,
                  depth,
                  source_token_count,
                  token_count
                FROM summary_nodes
                ORDER BY source_token_count DESC, node_id ASC
                LIMIT ?
                """,
                (limit,),
            )
        else:
            groups = _fetch_rows(
                conn,
                """
                SELECT
                  session_id AS key,
                  COUNT(*) AS node_count,
                  COALESCE(SUM(source_token_count), 0) AS source_token_count,
                  COALESCE(SUM(token_count), 0) AS token_count
                FROM summary_nodes
                GROUP BY session_id
                ORDER BY source_token_count DESC, session_id ASC
                LIMIT ?
                """,
                (limit,),
            )
        for g in groups:
            out_tok = int(g.get("token_count") or 0)
            src_tok = int(g.get("source_token_count") or 0)
            g["ratio"] = round(src_tok / out_tok, 2) if out_tok else 0.0
        payload["groups"] = groups

    return payload


@router.get("/overview")
def get_overview(
    q: str = Query(default=""),
    limit: int = Query(default=25, ge=1, le=200),
) -> dict[str, Any]:
    db_path = resolve_lcm_db_path()
    return build_overview(db_path, q=q, limit=limit)


@router.get("/search")
def get_search(
    q: str = Query(default=""),
    limit: int = Query(default=25, ge=1, le=200),
    role: str = Query(default=""),
    source: str = Query(default=""),
    session_id: str = Query(default=""),
    since: str = Query(default=""),
    until: str = Query(default=""),
) -> dict[str, Any]:
    db_path = resolve_lcm_db_path()
    return build_search(
        db_path,
        q=q,
        limit=limit,
        role=role or None,
        source=source or None,
        session_id=session_id or None,
        since=since or None,
        until=until or None,
    )


@router.get("/session/{session_id}")
def get_session(
    session_id: str,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    order: str = Query(default="asc"),
) -> dict[str, Any]:
    db_path = resolve_lcm_db_path()
    return build_session(db_path, session_id, limit=limit, offset=offset, order=order)


@router.get("/node/{node_id}")
def get_node(node_id: int) -> dict[str, Any]:
    db_path = resolve_lcm_db_path()
    return build_node(db_path, node_id)


@router.get("/timeline")
def get_timeline(
    bucket: str = Query(default="day"),
    session_id: str = Query(default=""),
    limit: int = Query(default=400, ge=1, le=2000),
) -> dict[str, Any]:
    db_path = resolve_lcm_db_path()
    return build_timeline(db_path, bucket=bucket, session_id=session_id or None, limit=limit)


@router.get("/compression")
def get_compression(
    by: str = Query(default="session"),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    db_path = resolve_lcm_db_path()
    return build_compression(db_path, by=by, limit=limit)
