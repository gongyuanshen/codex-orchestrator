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
    home = tmp_path / "Codex #1 星河"
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
    }
    assert metas["assigned"]["codex_project_root"] == r"H:\World"
    assert metas["named"]["codex_project_id"] == "nested"
    assert metas["message"]["codex_project_id"] is None
    assert bridge.get_thread_meta("renamed") == metas["renamed"]
    for excluded in ("missing", "agent", "guardian", "agent-source"):
        assert bridge.get_thread_meta(excluded) is None
    assert bridge.get_threads_meta([]) == {}


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
        assert _snapshot(codex_home) == before
        assert not caplog.records
