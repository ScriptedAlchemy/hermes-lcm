import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_lcm.dashboard import plugin_api


def _seed_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE messages (
          store_id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id TEXT NOT NULL,
          source TEXT DEFAULT '',
          role TEXT NOT NULL,
          content TEXT,
          tool_call_id TEXT,
          tool_calls TEXT,
          tool_name TEXT,
          timestamp REAL NOT NULL,
          token_estimate INTEGER DEFAULT 0,
          pinned INTEGER DEFAULT 0
        );
        CREATE TABLE summary_nodes (
          node_id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id TEXT NOT NULL,
          depth INTEGER NOT NULL DEFAULT 0,
          summary TEXT NOT NULL,
          token_count INTEGER DEFAULT 0,
          source_token_count INTEGER DEFAULT 0,
          source_ids TEXT NOT NULL DEFAULT '[]',
          source_type TEXT NOT NULL DEFAULT 'messages',
          created_at REAL NOT NULL,
          latest_at REAL,
          expand_hint TEXT DEFAULT '',
          category TEXT NOT NULL DEFAULT 'general'
        );
        """
    )
    conn.execute(
        "INSERT INTO messages (session_id, source, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
        ("sess-a", "cli", "user", "hello world", 1000.0),
    )
    conn.execute(
        "INSERT INTO messages (session_id, source, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
        ("sess-b", "", "assistant", "summary ping", 2000.0),
    )
    conn.execute(
        """
        INSERT INTO summary_nodes
          (session_id, depth, summary, source_type, created_at, latest_at, expand_hint, category)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("sess-b", 1, "project summary node", "messages", 1500.0, 2500.0, "expand me", "general"),
    )
    conn.commit()
    conn.close()


def _seed_legacy_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE messages (
          store_id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id TEXT NOT NULL,
          source TEXT DEFAULT '',
          role TEXT NOT NULL,
          content TEXT,
          timestamp REAL NOT NULL
        );
        CREATE TABLE summary_nodes (
          node_id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id TEXT NOT NULL,
          depth INTEGER NOT NULL DEFAULT 0,
          summary TEXT NOT NULL,
          token_count INTEGER DEFAULT 0,
          source_token_count INTEGER DEFAULT 0,
          source_type TEXT NOT NULL DEFAULT 'messages',
          created_at REAL NOT NULL,
          latest_at REAL
        );
        """
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
        ("sess-legacy", "user", "legacy message", 1000.0),
    )
    conn.execute(
        """
        INSERT INTO summary_nodes
          (session_id, depth, summary, source_type, created_at, latest_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("sess-legacy", 0, "legacy summary", "messages", 1000.0, 1000.0),
    )
    conn.commit()
    conn.close()


def test_build_overview_handles_missing_db(tmp_path):
    result = plugin_api.build_overview(tmp_path / "missing.db", q="hello", limit=10)
    assert result["exists"] is False
    assert result["overview"]["messages_total"] == 0
    assert result["matches"]["messages"] == []


def test_build_overview_reads_counts_and_search(tmp_path):
    db_path = tmp_path / "lcm.db"
    _seed_db(db_path)
    result = plugin_api.build_overview(db_path, q="summary", limit=25)
    assert result["exists"] is True
    assert result["overview"]["messages_total"] == 2
    assert result["overview"]["summary_nodes_total"] == 1
    assert result["overview"]["max_summary_depth"] == 1
    assert len(result["latest_sessions"]) == 2
    assert len(result["latest_summary_nodes"]) == 1
    assert len(result["matches"]["messages"]) == 1
    assert len(result["matches"]["summary_nodes"]) == 1


def test_build_overview_supports_legacy_summary_node_schema(tmp_path):
    db_path = tmp_path / "legacy_lcm.db"
    _seed_legacy_db(db_path)
    result = plugin_api.build_overview(db_path, q="legacy", limit=25)
    assert result["exists"] is True
    assert result["overview"]["messages_total"] == 1
    assert result["overview"]["summary_nodes_total"] == 1
    assert result["latest_summary_nodes"][0]["category"] == "general"
    assert result["latest_summary_nodes"][0]["expand_hint"] == ""
    assert len(result["matches"]["summary_nodes"]) == 1


def test_dashboard_bundle_registers_with_host_sdk():
    bundle = (Path(__file__).resolve().parent.parent / "dashboard" / "dist" / "index.js").read_text()
    assert "__HERMES_PLUGINS__.register(\"hermes-lcm\", App)" in bundle
    assert "SDK.render" not in bundle
    assert "SDK.fetchJSON" in bundle


def test_overview_route_uses_env_path(tmp_path, monkeypatch):
    db_path = tmp_path / "env_lcm.db"
    _seed_db(db_path)
    monkeypatch.setenv("LCM_DATABASE_PATH", str(db_path))

    app = FastAPI()
    app.include_router(plugin_api.router)
    client = TestClient(app)
    resp = client.get("/overview", params={"q": "world", "limit": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["path"] == str(db_path)
    assert body["exists"] is True
    assert body["limit"] == 5
    assert len(body["matches"]["messages"]) == 1
