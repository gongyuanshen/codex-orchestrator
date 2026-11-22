"""T1: exercise the metadata bridge against isolated Codex state fixtures."""
import importlib
import json
import os
import sqlite3
from contextlib import ExitStack, closing
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest


def _bridge():
    return importlib.import_module("orchestrator.codex_bridge")


@pytest.fixture
def codex_home(tmp_path, monkeypatch):
    home = tmp_path / "Codex #1 笔记"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    with closing(sqlite3.connect(home / "state_5.sqlite")) as conn, conn:
        conn.executescript("""
            CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE project_roots (
                project_id TEXT NOT NULL REFERENCES projects(id),
                position INTEGER NOT NULL, path TEXT NOT NULL,
                PRIMARY KEY (project_id, position)
            );
            CREATE TABLE threads (
                id TEXT PRIMARY KEY, cwd TEXT, project_id TEXT, name TEXT,
                title TEXT, first_user_message TEXT, thread_source TEXT, source TEXT
            );
        """)
        conn.executemany("INSERT INTO projects VALUES (?, ?)", [
            ("world", "星河笔记"), ("nested", "Nested"), ("foo", "Foo"),
            ("share", "Share"), ("rootless", "No registered root"),
        ])
        conn.executemany("INSERT INTO project_roots VALUES (?, ?, ?)", [
            ("world", 0, r"G:\星河笔记工程"), ("world", 1, r"H:\World"),
            ("nested", 0, r"\\?\G:\星河笔记工程\plugin"),
            ("foo", 0, "/foo"), ("share", 0, r"\\?\UNC\Server\Share\Repo"),
        ])
        conn.executemany("INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
            ("renamed", r"\\?\g:\星河笔记工程\.swarm-workspaces\worker", None,
             "database name", "DB title", "First", None, "vscode"),
            ("named", r"G:\星河笔记工程\PLUGIN\src", None,
             "Database name", "DB title", "First", "user", None),
            ("titled", "/foo/child", None, " ", "First title\r\nSecond title",
             "First message", "user", "cli"),
            ("message", "/foobar/child", None, None, "", "First message\nSecond message",
             None, "cli"),
            ("assigned", r"H:\World\child", "world", "Explicit", None, None, None, "cli"),
            ("agent", "/foo", "foo", "Agent", None, None, "subagent", "cli"),
            ("guardian", "/foo", "foo", "Review", None, None, "guardian_review", "cli"),
            ("agent-source", "/foo", "foo", "Agent source", None, None,
             "user", '{"subagent":{"thread_spawn":{}}}'),
        ])
    _append_names(home, ("renamed", "Old name"), ("agent", "Not a user thread"),
                  ("renamed", "最新重命名"))
    return home


def _append_names(home, *entries):
    with (home / "session_index.jsonl").open("a", encoding="utf-8") as index:
        for session_id, name in entries:
            index.write(json.dumps({"id": session_id, "thread_name": name},
                                   ensure_ascii=False) + "\n")


def _snapshot(home):
    return {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in home.iterdir()}


def test_extended_prefix_and_deepest_root_without_partial_match(codex_home):
    bridge = _bridge()
    assert bridge._strip_win_extended_prefix(r"\\?\G:\星河笔记工程") == r"G:\星河笔记工程"
    assert bridge._strip_win_extended_prefix(r"\\?\UNC\Server\Share") == r"\\Server\Share"
    assert bridge._strip_win_extended_prefix("/foo") == "/foo"

    resolve = bridge.resolve_codex_project_for_path
    assert resolve(r"\\?\g:\星河笔记工程") == {
        "project_id": "world", "name": "星河笔记", "root_path": r"G:\星河笔记工程",
    }
    assert resolve(r"\\?\g:\星河笔记工程\PLUGIN\slice")["project_id"] == "nested"
    assert resolve(r"G:/星河笔记工程/plugin2/slice")["project_id"] == "world"
    assert resolve(Path(r"\\SERVER\share\repo\slice"))["root_path"] == r"\\Server\Share\Repo"
    assert resolve(r"G:\星河笔记工程\.swarm-workspaces\run-a")["project_id"] == "world"
    assert resolve("/foo/one/two")["project_id"] == "foo"
    assert resolve("/foobar") is None
    assert resolve("") is None


def test_list_projects_joins_each_registered_root(codex_home):
    projects = _bridge().list_codex_projects()
    assert sorted((p["project_id"], p["name"], p["root_path"]) for p in projects) == sorted([
        ("world", "星河笔记", r"G:\星河笔记工程"), ("world", "星河笔记", r"H:\World"),
        ("nested", "Nested", r"G:\星河笔记工程\plugin"), ("foo", "Foo", "/foo"),
        ("share", "Share", r"\\Server\Share\Repo"),
    ])
    assert all(set(p) == {"project_id", "name", "root_path"} for p in projects)


def test_single_and_batch_title_priority_project_mapping_and_agent_exclusion(codex_home):
    bridge = _bridge()
    with (codex_home / "session_index.jsonl").open("ab") as index:
        index.write(b'\xff\n{"partial":\n[]\n')
    ids = ["renamed", "named", "titled", "message", "assigned", "missing",
           "agent", "guardian", "agent-source", "renamed"]
    metas = bridge.get_threads_meta(iter(ids))
    assert {key: val["title"] for key, val in metas.items()} == {
        "renamed": "最新重命名", "named": "Database name", "titled": "First title",
        "message": "First message", "assigned": "Explicit",
    }
    assert metas["renamed"] == {
        "session_id": "renamed", "title": "最新重命名",
        "cwd": r"g:\星河笔记工程\.swarm-workspaces\worker",
        "codex_project_id": "world", "codex_project_name": "星河笔记",
        "codex_project_root": r"G:\星河笔记工程",
        "model": None,
        "reasoning_effort": None,
        "model_provider": None,
    }
    assert metas["assigned"]["codex_project_root"] == r"H:\World"
    assert metas["named"]["codex_project_id"] == "nested"
    assert metas["message"]["codex_project_id"] is None
    assert bridge.get_thread_meta("renamed") == metas["renamed"]
    for excluded in ("missing", "agent", "guardian", "agent-source"):
        assert bridge.get_thread_meta(excluded) is None
    assert bridge.get_threads_meta([]) == {}
    assert bridge.get_session_subagents("renamed") == []
    assert bridge.get_sessions_subagents(["renamed", "named"]) == {"renamed": [], "named": []}
    assert bridge.get_sessions_subagents([]) == {}


def test_readonly_uri_and_cache_tracks_each_mtime_and_ttl(codex_home, monkeypatch):
    bridge = _bridge()
    original_connect = sqlite3.connect
    attempts = []
    now = [100.0]
    monkeypatch.setattr(bridge, "monotonic", lambda: now[0])

    def connect(filename, **kwargs):
        assert filename.startswith((codex_home / "state_5.sqlite").as_uri() + "?mode=ro")
        assert parse_qs(urlsplit(filename).query)["mode"] == ["ro"]
        assert kwargs == {"uri": True, "timeout": 0.5}
        attempts.append(filename)
        return original_connect(filename, **kwargs)

    monkeypatch.setattr(bridge.sqlite3, "connect", connect)
    before = _snapshot(codex_home)
    assert bridge.get_thread_meta("named")["title"] == "Database name"
    assert len(attempts) == 1
    now[0] = 101.49
    mutated = bridge.get_thread_meta("named")
    mutated["title"] = "caller edit"
    assert bridge.get_thread_meta("named")["title"] == "Database name"
    assert bridge.list_codex_projects()
    assert len(attempts) == 1
    assert _snapshot(codex_home) == before

    now[0] = 101.5
    assert bridge.get_thread_meta("named")["title"] == "Database name"
    assert len(attempts) == 2
    db = codex_home / "state_5.sqlite"
    with closing(original_connect(db)) as conn, conn:
        conn.execute("UPDATE threads SET name=? WHERE id=?", ("Changed DB", "named"))
    db_ns = db.stat().st_mtime_ns + 1_000_000_000
    os.utime(db, ns=(db_ns, db_ns))
    assert bridge.get_thread_meta("named")["title"] == "Changed DB"
    assert len(attempts) == 3

    _append_names(codex_home, ("named", "Changed JSONL"))
    index = codex_home / "session_index.jsonl"
    index_ns = index.stat().st_mtime_ns + 1_000_000_000
    os.utime(index, ns=(index_ns, index_ns))
    assert bridge.get_thread_meta("named")["title"] == "Changed JSONL"
    assert len(attempts) == 4


def test_checkpointed_wal_database_creates_no_sidecars_during_read(codex_home, monkeypatch):
    bridge = _bridge()
    db = codex_home / "state_5.sqlite"
    scratch = codex_home.parent / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("TMPDIR", str(codex_home))
    monkeypatch.setenv("TEMP", str(scratch))
    original_connect = sqlite3.connect
    with closing(original_connect(db)) as conn:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    expected = {"state_5.sqlite", "session_index.jsonl"}
    assert set(_snapshot(codex_home)) == expected

    class InspectedConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            cursor = super().execute(sql, parameters)
            assert {p.name for p in codex_home.iterdir()} == expected
            return cursor

    def connect(filename, **kwargs):
        assert filename.startswith(scratch.as_uri() + "/")
        assert parse_qs(urlsplit(filename).query)["mode"] == ["ro"]
        assert kwargs == {"uri": True, "timeout": 0.5}
        return original_connect(filename, factory=InspectedConnection, **kwargs)

    monkeypatch.setattr(bridge.sqlite3, "connect", connect)
    assert bridge.list_codex_projects()
    assert bridge.get_thread_meta("renamed")["title"] == "最新重命名"
    assert set(_snapshot(codex_home)) == expected
    assert list(scratch.iterdir()) == []


def test_wal_changes_are_visible_without_changing_any_state_file(codex_home):
    bridge = _bridge()
    db = codex_home / "state_5.sqlite"
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        conn.execute("UPDATE threads SET name='WAL first' WHERE id='named'")
        conn.commit()
        before = _snapshot(codex_home)
        assert bridge.get_thread_meta("named")["title"] == "WAL first"
        assert _snapshot(codex_home) == before
        db_ns = db.stat().st_mtime_ns
        wal = codex_home / "state_5.sqlite-wal"
        wal_ns = wal.stat().st_mtime_ns + 1_000_000_000
        conn.execute("UPDATE threads SET name='WAL second' WHERE id='named'")
        conn.commit()
        os.utime(wal, ns=(wal_ns, wal_ns))
        assert db.stat().st_mtime_ns == db_ns
        before = _snapshot(codex_home)
        assert bridge.get_thread_meta("named")["title"] == "WAL second"
        assert _snapshot(codex_home) == before


def test_wal_snapshot_rejects_source_change_during_copy(codex_home, monkeypatch):
    bridge = _bridge()
    db = codex_home / "state_5.sqlite"
    wal = db.with_name(db.name + "-wal")
    original_copy = bridge.shutil.copyfile
    copies = []

    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        conn.execute("UPDATE threads SET name='Before copy' WHERE id='named'")
        conn.commit()

        def copy_and_mutate(source, target):
            result = original_copy(source, target)
            copies.append(source)
            if source == wal:
                original_ns = wal.stat().st_mtime_ns
                conn.execute("UPDATE threads SET name='After copy' WHERE id='named'")
                conn.commit()
                os.utime(wal, ns=(original_ns + 1_000_000_000,) * 2)
            return result

        with monkeypatch.context() as patch:
            patch.setattr(bridge.shutil, "copyfile", copy_and_mutate)
            assert bridge.get_thread_meta("named") is None
        assert copies == [db, wal]
        assert bridge.get_thread_meta("named")["title"] == "After copy"


@pytest.mark.parametrize("writer_stays_open", [False, True])
def test_concurrent_wal_transition_never_opens_writable_sidecars(codex_home, monkeypatch, writer_stays_open):
    bridge = _bridge()
    db = codex_home / "state_5.sqlite"
    original_connect = sqlite3.connect
    baseline = None

    with ExitStack() as stack:
        def connect(filename, **kwargs):
            nonlocal baseline
            if baseline is None:
                writer = original_connect(db)
                try:
                    assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
                    writer.execute("UPDATE threads SET name='WAL transition' WHERE id='named'")
                    writer.commit()
                finally:
                    if writer_stays_open:
                        stack.callback(writer.close)
                    else:
                        writer.close()
                baseline = _snapshot(codex_home)
                assert ("state_5.sqlite-wal" in baseline) == writer_stays_open
                assert ("state_5.sqlite-shm" in baseline) == writer_stays_open
            return original_connect(filename, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(bridge.sqlite3, "connect", connect)
            bridge.get_thread_meta("named")
        assert baseline is not None
        assert _snapshot(codex_home) == baseline
        assert bridge.get_thread_meta("named")["title"] == "WAL transition"
        assert _snapshot(codex_home) == baseline


@pytest.mark.parametrize("broken", ["missing", "corrupt", "columns", "locked"])
def test_unavailable_state_returns_only_empty_values_and_never_writes(codex_home, broken, caplog):
    bridge = _bridge()
    db = codex_home / "state_5.sqlite"
    with ExitStack() as stack:
        if broken in {"columns", "locked"}:
            writer = stack.enter_context(closing(sqlite3.connect(db)))
            if broken == "columns":
                writer.execute("ALTER TABLE threads DROP COLUMN thread_source")
                writer.execute("ALTER TABLE project_roots DROP COLUMN path")
            else:
                writer.execute("BEGIN EXCLUSIVE")
        else:
            db.unlink()
            if broken == "corrupt":
                db.write_bytes(b"invalid database")
        before = _snapshot(codex_home)
        assert bridge.list_codex_projects() == []
        assert bridge.resolve_codex_project_for_path("/foo") is None
        assert bridge.get_thread_meta("renamed") is None
        assert bridge.get_threads_meta(["renamed", "titled"]) == {}
        assert bridge.get_session_subagents("renamed") == []
        assert bridge.get_sessions_subagents(["renamed", "titled"]) == {}
        assert _snapshot(codex_home) == before
        assert not caplog.records


def _create_full_codex_db(home: Path) -> Path:
    db = home / "state_5.sqlite"
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.executescript("""
            CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE project_roots (
                project_id TEXT NOT NULL REFERENCES projects(id),
                position INTEGER NOT NULL, path TEXT NOT NULL,
                PRIMARY KEY (project_id, position)
            );
            CREATE TABLE threads (
                id TEXT PRIMARY KEY, cwd TEXT, project_id TEXT, name TEXT,
                title TEXT, first_user_message TEXT, thread_source TEXT, source TEXT,
                model TEXT, reasoning_effort TEXT, model_provider TEXT,
                agent_path TEXT, agent_role TEXT, agent_nickname TEXT,
                created_at REAL, updated_at REAL
            );
            CREATE TABLE thread_spawn_edges (
                parent_thread_id TEXT NOT NULL,
                child_thread_id TEXT NOT NULL,
                status TEXT
            );
        """)
        conn.execute("INSERT INTO projects VALUES ('p1', 'Main')")
        conn.execute("INSERT INTO project_roots VALUES ('p1', 0, '/repo')")
    return db


def test_catalog_sources_and_bound_exec_are_request_scoped(tmp_path, monkeypatch):
    bridge = _bridge()
    home = tmp_path / "codex"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    db = _create_full_codex_db(home)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.executemany(
            """INSERT INTO threads
               (id, cwd, project_id, name, thread_source, source)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                ("desktop", "/repo", "p1", "Desktop", "user", "VSCODE"),
                ("legacy", "/repo", "p1", "Legacy", "user", None),
                ("cli", "/repo", "stale", "CLI", "user", "CLI"),
                ("standalone", "/repo-other", None, "Standalone CLI", "user", "cli"),
                ("bound", "/repo-other", None, "Bound Exec", "user", "EXEC"),
                ("bound-local", "/repo", "p1", "Bound Local Exec", "user", "exec"),
                ("bare", "/repo", "p1", "Read-only challenge", "user", "exec"),
                ("guard", "/repo-other", None, "Guardian", "guardian_review", "exec"),
            ],
        )

    before = _snapshot(home)
    default = bridge.get_codex_catalog()
    allowed_ids = ("bound", "bound-local", "guard", "missing")
    allowed = bridge.get_codex_catalog(iter(allowed_ids))
    assert default is not None and allowed is not None
    [project] = default["projects"]
    assert project["project_origin"] == "desktop"
    assert project["client_kinds"] == ["cli", "desktop"]
    assert {
        t["session_id"]: (t["client_kind"], t["codex_source"]) for t in project["threads"]
    } == {"desktop": ("desktop", "vscode"), "legacy": ("desktop", None), "cli": ("cli", "cli")}
    assert [t["session_id"] for t in default["unmatched_cli_threads"]] == ["standalone"]
    assert {t["session_id"] for t in allowed["projects"][0]["threads"]} == {
        "desktop", "legacy", "cli", "bound-local",
    }
    assert {
        t["session_id"]: (t["client_kind"], t["codex_source"]) for t in allowed["unmatched_cli_threads"]
    } == {"standalone": ("cli", "cli"), "bound": ("cli", "exec")}
    assert allowed["missing_session_ids"] == ["missing"]
    allowed["unmatched_cli_threads"][0]["title"] = "Mutated by caller"
    allowed["projects"][0]["client_kinds"].clear()
    reread = bridge.get_codex_catalog(include_exec_session_ids=set(allowed_ids))
    assert reread["unmatched_cli_threads"][0]["title"] != "Mutated by caller"
    assert reread["projects"][0]["client_kinds"] == ["cli", "desktop"]
    assert bridge.get_codex_catalog() == default
    assert _snapshot(home) == before


def test_thread_model_fields_and_subagent_tree_discovery(tmp_path, monkeypatch):
    bridge = _bridge()
    home = tmp_path / "full-codex"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    db = _create_full_codex_db(home)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.executemany(
            """INSERT INTO threads VALUES
               (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                ("sess-1", "/repo", "p1", "Main Session", "Title", "First", "user", "cli",
                 "  gpt-6.2-sol  ", "ultra", "openai", None, None, None, 100.0, 105.0),
                ("sess-blank", "/repo", "p1", "Blank Model", "Title", "First", "user", "cli",
                 "   ", "", None, None, None, None, 101.0, 101.0),
                ("w1", "/repo", "p1", "Coder", None, None, "subagent", '{"subagent":{}}',
                 "gpt-6.1-sol", "high", "openai", "/root/coder", "coder", "Kier", 110.0, 120.0),
                ("exp1", "/repo", "p1", "Explorer", None, None, "subagent", '{"subagent":{}}',
                 "gpt-6.2-mini", "low", "openai", "/root/explorer", "explorer", "Scout", 108.0, 112.0),
                ("gr1", "/repo", "p1", "Guardian", None, None, "guardian_review", '{"subagent":{}}',
                 "gpt-6.2-sol", "high", "openai", "/root/guardian", "guardian", "Guard", 109.0, 111.0),
                ("h2a", "/repo", "p1", "Helper A", None, None, "subagent", '{"subagent":{}}',
                 "gpt-6.2-mini", "medium", "openai", "/root/coder/sub-a", "explorer", "Lumen", 130.0, 135.0),
                ("h2b", "/repo", "p1", "Helper B", None, None, "subagent", '{"subagent":{}}',
                 "gpt-6.2-nano", None, "openai", "/root/coder/sub-b", "worker", "Spark", 125.0, 128.0),
                ("h3", "/repo", "p1", "Deep Helper", None, None, "subagent", '{"subagent":{}}',
                 "gpt-6.2-nano", "low", "anthropic", "/root/coder/sub-a/deep", "explorer", "Echo", 140.0, 145.0),
            ],
        )
        conn.executemany(
            "INSERT INTO thread_spawn_edges VALUES (?, ?, ?)",
            [
                ("sess-1", "w1", "completed"),
                ("sess-1", "exp1", "running"),
                ("sess-1", "gr1", "completed"),
                ("w1", "h2a", "completed"),
                ("w1", "h2b", "running"),
                ("h2a", "h3", "completed"),
            ],
        )

    now = [200.0]
    monkeypatch.setattr(bridge, "monotonic", lambda: now[0])

    meta = bridge.get_thread_meta("sess-1")
    assert meta == {
        "session_id": "sess-1",
        "title": "Main Session",
        "cwd": "/repo",
        "codex_project_id": "p1",
        "codex_project_name": "Main",
        "codex_project_root": "/repo",
        "model": "gpt-6.2-sol",
        "reasoning_effort": "ultra",
        "model_provider": "openai",
    }
    blank_meta = bridge.get_thread_meta("sess-blank")
    assert blank_meta["model"] is None
    assert blank_meta["reasoning_effort"] is None
    assert blank_meta["model_provider"] is None

    tree = bridge.get_session_subagents("sess-1")
    assert [(item["thread_id"], item["depth"], item["parent_thread_id"]) for item in tree] == [
        ("exp1", 1, "sess-1"),
        ("w1", 1, "sess-1"),
        ("h2b", 2, "w1"),
        ("h2a", 2, "w1"),
        ("h3", 3, "h2a"),
    ]
    assert tree[1] == {
        "thread_id": "w1",
        "parent_thread_id": "sess-1",
        "depth": 1,
        "edge_status": "completed",
        "agent_path": "/root/coder",
        "agent_role": "coder",
        "agent_nickname": "Kier",
        "model": "gpt-6.1-sol",
        "reasoning_effort": "high",
        "model_provider": "openai",
        "created_at": 110.0,
        "updated_at": 120.0,
    }

    batch = bridge.get_sessions_subagents(["sess-1", "sess-blank"])
    assert [item["thread_id"] for item in batch["sess-1"]] == ["exp1", "w1", "h2b", "h2a", "h3"]
    assert batch["sess-blank"] == []

    # Caller mutation must not corrupt _cache.
    tree[0]["model"] = "corrupted"
    tree.clear()
    fresh = bridge.get_session_subagents("sess-1")
    assert len(fresh) == 5
    assert fresh[0]["model"] == "gpt-6.2-mini"


def test_subagent_tree_cycle_protection_depth_limit_and_optional_columns(tmp_path, monkeypatch):
    bridge = _bridge()
    home = tmp_path / "cyclic-codex"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    with closing(sqlite3.connect(home / "state_5.sqlite")) as conn, conn:
        conn.executescript("""
            CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE project_roots (
                project_id TEXT NOT NULL REFERENCES projects(id),
                position INTEGER NOT NULL, path TEXT NOT NULL,
                PRIMARY KEY (project_id, position)
            );
            CREATE TABLE threads (
                id TEXT PRIMARY KEY, cwd TEXT, project_id TEXT, name TEXT,
                title TEXT, first_user_message TEXT, thread_source TEXT, source TEXT
            );
            CREATE TABLE thread_spawn_edges (
                parent_thread_id TEXT NOT NULL,
                child_thread_id TEXT NOT NULL
            );
        """)
        conn.execute("INSERT INTO projects VALUES ('p1', 'Main')")
        conn.execute("INSERT INTO project_roots VALUES ('p1', 0, '/repo')")
        nodes = [("root", "/repo", "p1", "Root", None, None, "user", "cli")]
        for depth in range(1, 11):
            nodes.append((f"d{depth}", "/repo", "p1", f"D{depth}", None, None, "subagent", "cli"))
        conn.executemany("INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)", nodes)
        edges = [
            ("root", "root"),
            ("root", "d1"),
            ("d1", "root"),
            ("d1", "d1"),
            ("d2", "d1"),
        ]
        for depth in range(1, 10):
            edges.append((f"d{depth}", f"d{depth + 1}"))
        conn.executemany("INSERT INTO thread_spawn_edges VALUES (?, ?)", edges)

    tree = bridge.get_session_subagents("root")
    assert [(item["thread_id"], item["depth"]) for item in tree] == [
        (f"d{depth}", depth) for depth in range(1, 9)
    ]
    assert tree[0] == {
        "thread_id": "d1",
        "parent_thread_id": "root",
        "depth": 1,
        "edge_status": None,
        "agent_path": "",
        "agent_role": None,
        "agent_nickname": None,
        "model": None,
        "reasoning_effort": None,
        "model_provider": None,
        "created_at": None,
        "updated_at": None,
    }


def test_subagents_preserve_wal_readonly_and_reflect_wal_updates(tmp_path, monkeypatch):
    bridge = _bridge()
    home = tmp_path / "wal-codex"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    db = _create_full_codex_db(home)
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        conn.execute(
            "INSERT INTO threads VALUES ('s1', '/repo', 'p1', 'S1', NULL, NULL, 'user', 'cli', 'gpt-6.2-sol', 'high', 'openai', NULL, NULL, NULL, 1.0, 1.0)"
        )
        conn.execute(
            "INSERT INTO threads VALUES ('c1', '/repo', 'p1', 'C1', NULL, NULL, 'subagent', 'cli', 'gpt-6.2-mini', 'low', 'openai', '/root/c1', 'coder', 'A', 2.0, 2.0)"
        )
        conn.execute("INSERT INTO thread_spawn_edges VALUES ('s1', 'c1', 'running')")
        conn.commit()

        before = _snapshot(home)
        assert [item["thread_id"] for item in bridge.get_session_subagents("s1")] == ["c1"]
        assert _snapshot(home) == before

        wal = home / "state_5.sqlite-wal"
        wal_ns = wal.stat().st_mtime_ns + 1_000_000_000
        conn.execute(
            "INSERT INTO threads VALUES ('c2', '/repo', 'p1', 'C2', NULL, NULL, 'subagent', 'cli', 'gpt-6.2-nano', 'medium', 'openai', '/root/c1/c2', 'explorer', 'B', 3.0, 3.0)"
        )
        conn.execute("INSERT INTO thread_spawn_edges VALUES ('c1', 'c2', 'completed')")
        conn.commit()
        os.utime(wal, ns=(wal_ns, wal_ns))

        before = _snapshot(home)
        subagents = bridge.get_session_subagents("s1")
        assert [(item["thread_id"], item["depth"]) for item in subagents] == [("c1", 1), ("c2", 2)]
        assert _snapshot(home) == before
