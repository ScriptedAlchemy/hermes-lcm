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


def _seed_fts_db(path: Path) -> None:
    """Full schema with FTS5 external-content mirrors + token counts."""
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
          earliest_at REAL,
          latest_at REAL,
          expand_hint TEXT DEFAULT '',
          category TEXT NOT NULL DEFAULT 'general',
          tags TEXT,
          entities TEXT,
          taxonomy_metadata TEXT
        );
        CREATE VIRTUAL TABLE messages_fts USING fts5(
          content, content='messages', content_rowid='store_id'
        );
        CREATE VIRTUAL TABLE nodes_fts USING fts5(
          summary, content='summary_nodes', content_rowid='node_id'
        );
        """
    )
    conn.executemany(
        "INSERT INTO messages (store_id, session_id, source, role, content, tool_name, timestamp, token_estimate) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, "sess-a", "cli", "user", "alpha compression beta", None, 1000.0, 30),
            (2, "sess-a", "cli", "assistant", "gamma delta epsilon", None, 1100.0, 40),
            (3, "sess-b", "telegram", "tool", "compression note for tool", "read_file", 2000.0, 20),
        ],
    )
    conn.executemany(
        "INSERT INTO summary_nodes (node_id, session_id, depth, summary, token_count, source_token_count, "
        "source_ids, source_type, created_at, latest_at, expand_hint, category) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, "sess-a", 0, "alpha summary about compression", 10, 100, "[1, 2]", "messages",
             1200.0, 1200.0, "expand node one", "general"),
            (2, "sess-b", 0, "beta summary tool", 5, 50, "[3]", "messages",
             2100.0, 2100.0, "", "general"),
            (3, "sess-a", 1, "top level rollup summary", 8, 18, "[1, 2]", "nodes",
             1300.0, 1300.0, "expand top", "general"),
        ],
    )
    conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
    conn.execute("INSERT INTO nodes_fts(nodes_fts) VALUES('rebuild')")
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


# --------------------------------------------------------------------------- #
# FTS-backed search
# --------------------------------------------------------------------------- #


def test_build_search_uses_fts_with_snippets(tmp_path):
    db_path = tmp_path / "fts.db"
    _seed_fts_db(db_path)
    result = plugin_api.build_search(db_path, q="compression", limit=25)
    assert result["engine"] == "fts"
    msgs = result["matches"]["messages"]
    nodes = result["matches"]["summary_nodes"]
    # store_id 1 and 3 contain "compression"
    assert {m["store_id"] for m in msgs} == {1, 3}
    # snippet() wraps matched terms in [ ... ]
    assert any("[" in (m.get("snippet") or "") for m in msgs)
    assert any(n["node_id"] == 1 for n in nodes)


def test_build_search_prefix_match(tmp_path):
    db_path = tmp_path / "fts_prefix.db"
    _seed_fts_db(db_path)
    # "compress" should prefix-match "compression" on the trailing token
    result = plugin_api.build_search(db_path, q="compress", limit=25)
    assert result["engine"] == "fts"
    assert {m["store_id"] for m in result["matches"]["messages"]} == {1, 3}


def test_build_search_sanitizes_fts_special_characters(tmp_path):
    db_path = tmp_path / "fts_special.db"
    _seed_fts_db(db_path)
    # raw FTS5-hostile input: trailing unbalanced quote, bare hyphen, colon column.
    # Only "compression" is a usable token; the junk must be stripped, not crash.
    result = plugin_api.build_search(db_path, q='compression" -: :', limit=25)
    # must not raise, must still use FTS (a real token survived)
    assert "error" not in result
    assert result["engine"] == "fts"
    assert {m["store_id"] for m in result["matches"]["messages"]} == {1, 3}


def test_build_search_punctuation_only_query_does_not_crash(tmp_path):
    db_path = tmp_path / "fts_punct.db"
    _seed_fts_db(db_path)
    # no usable tokens -> match expr empty -> graceful LIKE path, no exception
    result = plugin_api.build_search(db_path, q='-: " ', limit=25)
    assert "error" not in result
    assert result["engine"] == "like"


def test_build_search_facets_filter_messages(tmp_path):
    db_path = tmp_path / "fts_facet.db"
    _seed_fts_db(db_path)
    by_role = plugin_api.build_search(db_path, q="compression", role="tool")
    assert {m["store_id"] for m in by_role["matches"]["messages"]} == {3}
    by_source = plugin_api.build_search(db_path, q="compression", source="cli")
    assert {m["store_id"] for m in by_source["matches"]["messages"]} == {1}
    by_session = plugin_api.build_search(db_path, q="compression", session_id="sess-b")
    assert {m["store_id"] for m in by_session["matches"]["messages"]} == {3}


def test_build_search_falls_back_to_like_without_fts(tmp_path):
    db_path = tmp_path / "nofts.db"
    _seed_db(db_path)  # no FTS mirror tables
    result = plugin_api.build_search(db_path, q="summary", limit=25)
    assert result["engine"] == "like"
    assert len(result["matches"]["messages"]) == 1
    assert len(result["matches"]["summary_nodes"]) == 1


def test_build_search_missing_db(tmp_path):
    result = plugin_api.build_search(tmp_path / "nope.db", q="x")
    assert result["exists"] is False
    assert result["matches"]["messages"] == []


# --------------------------------------------------------------------------- #
# Drill-down: session + node
# --------------------------------------------------------------------------- #


def test_build_session_returns_messages_and_nodes(tmp_path):
    db_path = tmp_path / "sess.db"
    _seed_fts_db(db_path)
    result = plugin_api.build_session(db_path, "sess-a", limit=10)
    assert result["counts"]["message_count"] == 2
    assert result["counts"]["summary_node_count"] == 2  # node 1 (d0) + node 3 (d1)
    assert result["counts"]["token_estimate_total"] == 70
    assert result["order"] == "asc"
    # oldest first
    assert [m["store_id"] for m in result["messages"]] == [1, 2]
    assert result["has_more"] is False


def test_build_session_pagination_has_more(tmp_path):
    db_path = tmp_path / "sess_page.db"
    _seed_fts_db(db_path)
    result = plugin_api.build_session(db_path, "sess-a", limit=1)
    assert len(result["messages"]) == 1
    assert result["has_more"] is True


def test_build_node_expands_messages(tmp_path):
    db_path = tmp_path / "node_msg.db"
    _seed_fts_db(db_path)
    result = plugin_api.build_node(db_path, 1)
    assert result["node"]["node_id"] == 1
    assert result["sources"]["type"] == "messages"
    assert [m["store_id"] for m in result["sources"]["messages"]] == [1, 2]
    assert result["node"]["expand_hint"] == "expand node one"


def test_build_node_expands_child_nodes(tmp_path):
    db_path = tmp_path / "node_nodes.db"
    _seed_fts_db(db_path)
    result = plugin_api.build_node(db_path, 3)
    assert result["sources"]["type"] == "nodes"
    assert {n["node_id"] for n in result["sources"]["nodes"]} == {1, 2}
    assert result["sources"]["messages"] == []


def test_build_node_missing(tmp_path):
    db_path = tmp_path / "node_missing.db"
    _seed_fts_db(db_path)
    result = plugin_api.build_node(db_path, 9999)
    assert result["node"] is None


# --------------------------------------------------------------------------- #
# Aggregates: timeline + compression
# --------------------------------------------------------------------------- #


def test_build_timeline_buckets(tmp_path):
    db_path = tmp_path / "tl.db"
    _seed_fts_db(db_path)
    result = plugin_api.build_timeline(db_path, bucket="day")
    assert result["bucket"] == "day"
    buckets = {b["bucket"]: b["count"] for b in result["buckets"]}
    # all three messages fall on 1970-01-01 (epochs 1000/1100/2000)
    assert sum(buckets.values()) == 3
    assert result["node_buckets"]  # summary coverage overlay present


def test_build_compression_overview_and_groups(tmp_path):
    db_path = tmp_path / "comp.db"
    _seed_fts_db(db_path)
    result = plugin_api.build_compression(db_path, by="session")
    # overall: src 100+50+18=168, out 10+5+8=23
    assert result["overall"]["source_token_count"] == 168
    assert result["overall"]["token_count"] == 23
    assert result["overall"]["node_count"] == 3
    assert result["overall"]["ratio"] > 0
    keys = {g["key"] for g in result["groups"]}
    assert keys == {"sess-a", "sess-b"}


def test_overview_includes_compression_headline(tmp_path):
    db_path = tmp_path / "ov_comp.db"
    _seed_fts_db(db_path)
    result = plugin_api.build_overview(db_path, q="", limit=10)
    comp = result["overview"]["compression"]
    assert comp["source_token_count"] == 168
    assert comp["token_count"] == 23
    assert comp["node_count"] == 3


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


def test_new_routes_via_client(tmp_path, monkeypatch):
    db_path = tmp_path / "routes.db"
    _seed_fts_db(db_path)
    monkeypatch.setenv("LCM_DATABASE_PATH", str(db_path))

    app = FastAPI()
    app.include_router(plugin_api.router)
    client = TestClient(app)

    r = client.get("/search", params={"q": "compression", "role": "tool"})
    assert r.status_code == 200
    assert {m["store_id"] for m in r.json()["matches"]["messages"]} == {3}

    r = client.get("/session/sess-a")
    assert r.status_code == 200
    assert r.json()["counts"]["message_count"] == 2

    r = client.get("/node/3")
    assert r.status_code == 200
    assert r.json()["sources"]["type"] == "nodes"

    r = client.get("/timeline", params={"bucket": "day"})
    assert r.status_code == 200
    assert r.json()["bucket"] == "day"

    r = client.get("/compression", params={"by": "node", "limit": 5})
    assert r.status_code == 200
    assert r.json()["by"] == "node"
