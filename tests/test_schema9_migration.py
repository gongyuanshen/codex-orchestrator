"""Schema 9 migration and persistence contracts, including the offline upgrade row gate."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sqlite3
import tempfile

import pytest

from orchestrator import db as db_module
from orchestrator.config import Settings


def _upgrade_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "upgrade.py"
    spec = spec_from_file_location("s1_upgrade", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _schema8_fixture(path, workspace):
    # Literal old DDL: using Database to make this fixture would exercise the new schema.
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript("""
            CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at REAL NOT NULL);
            INSERT INTO schema_migrations VALUES(8, 123.0);
            CREATE TABLE projects(
                id TEXT PRIMARY KEY, root_path TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL, is_git INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL
            );
            CREATE TABLE runs(
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                mode TEXT NOT NULL CHECK(mode IN ('boost','teamwork','swarm')),
                title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active'
                    CHECK(status IN ('active','completed','failed','archived')),
                metadata_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL, updated_at REAL NOT NULL,
                workflow_version INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE session_run_bindings(
                session_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, project_root TEXT NOT NULL,
                last_stop_blocked_key TEXT, updated_at REAL NOT NULL
            );
            CREATE INDEX idx_session_run_bindings_run ON session_run_bindings(run_id, updated_at DESC);
        """)
        conn.executemany("INSERT INTO projects VALUES (?, ?, ?, ?, ?)", [
            ("main", str(workspace), "  主项目  ", 1, 1),
            ("slice", str(workspace / "code" / ".." / "切片"), " 切片旧名 ", 0, 2),
            ("fixture", str(workspace / "docs" / "verification" / "s1" / "fixture"), " 验证夹具 ", 0, 3),
            ("git-slice", str(workspace / "nested-git"), " 独立仓库 ", 1, 4),
            ("temp", "C:\\Users\\Example\\AppData\\Local\\Temp\\s1", "临时目录", 0, 5),
        ])
        conn.executemany("INSERT INTO runs VALUES (?, ?, 'swarm', ?, ?, ?, ?, ?, 5)", [
            ("old-run", "main", " 旧标题\n原样 ", "completed", '{"note": "旧数据"}', 3, 4),
            ("slice-run", "slice", " 切片标题 ", "active", "{}", 1, 2),
            ("unbound", "fixture", " 未绑定 ", "completed", "{}", 5, 6),
        ])
        rows = [
            ("z-session", "old-run", r"\\?\G:\旧项目\.\切片", "  blocked  ", 70.0),
            ("a-session", "slice-run", "  未规范化/项目  ", None, 5.0),
            (None, "old-run", " legacy NULL key ", "", 80.0),
        ]
        conn.executemany("INSERT INTO session_run_bindings VALUES (?, ?, ?, ?, ?)", rows)
    return rows


def _assert_schema9(conn):
    def columns(table):
        return {r["name"]: (r["type"], r["notnull"], r["dflt_value"], r["pk"])
                for r in conn.execute(f"PRAGMA table_info({table})")}

    projects = columns("projects")
    for name in ("codex_project_id", "codex_project_name", "canonical_project_id"):
        assert projects[name] == ("TEXT", 0, None, 0)
    assert projects["project_kind"] == ("TEXT", 1, "'workspace'", 0)

    runs = columns("runs")
    for name in ("session_id", "turn_id"):
        assert runs[name] == ("TEXT", 0, None, 0)
    assert runs["session_seq"] == ("INTEGER", 1, "0", 0)
    for name in ("trigger_prompt", "execution_root"):
        assert runs[name] == ("TEXT", 1, "''", 0)

    bindings = columns("session_run_bindings")
    assert bindings["session_id"][-1] == 1
    assert bindings["run_id"][-1] == 2
    assert bindings["is_primary"] == ("INTEGER", 1, "1", 0)
    assert conn.execute("PRAGMA index_list(session_run_bindings)").fetchall()
    assert [r["name"] for r in conn.execute("PRAGMA index_info(idx_session_run_bindings_run)")] == [
        "run_id", "updated_at",
    ]

    turns = columns("session_turns")
    assert set(turns) == {"session_id", "turn_id", "cwd", "prompt_preview",
                          "active_run_id", "created_at", "updated_at"}
    assert (turns["session_id"][-1], turns["turn_id"][-1]) == (1, 2)
    assert [(r["name"], r["desc"]) for r in conn.execute(
        "PRAGMA index_xinfo(idx_session_turns_session_updated)") if r["key"]] == [
        ("session_id", 0), ("updated_at", 1),
    ]


def test_schema8_upgrade_keeps_old_column_hashes_and_order(tmp_path):
    path = tmp_path / "state.db"
    workspace = Path(tmp_path.anchor) / "S1 Workspaces" / "主项目"
    old_rows = _schema8_fixture(path, workspace)
    upgrade = _upgrade_script()
    before = upgrade.database_snapshot(path)
    assert before["schema_version"] == 8

    with db_module.Database(path, settings=Settings(data_dir=tmp_path)) as db:
        assert db.migrate() == db_module.SCHEMA_VERSION == 9
        upgrade.require_old_rows(path, before)
        with db.read_connection() as conn:
            _assert_schema9(conn)
            assert [tuple(row) for row in conn.execute("""
                SELECT session_id, run_id, project_root, last_stop_blocked_key, updated_at
                FROM session_run_bindings ORDER BY rowid ASC
            """)] == old_rows
        assert db.get_session_binding("z-session", "old-run")["is_primary"] == 1
        assert [(db.get_run(r)["session_id"], db.get_run(r)["session_seq"])
                for r in ("old-run", "slice-run", "unbound")] == [
            ("z-session", 1), ("a-session", 1), (None, 0),
        ]
        assert db.get_project("main")["project_kind"] == "workspace"
        assert db.get_project("slice")["canonical_project_id"] == "main"
        assert db.get_project("fixture")["project_kind"] == "fixture"
        assert db.get_project("git-slice")["canonical_project_id"] is None
        assert db.get_project("temp")["project_kind"] == "temp"

        new = db.create_run(mode="swarm", title="next", project_id="main", session_id="z-session")
        assert new["session_seq"] == 2
        assert [item["run_id"] for item in db.list_session_bindings("z-session")] == [new["id"], "old-run"]
        assert db.get_session_binding("z-session")["run_id"] == new["id"]
        assert db.get_session_binding("z-session", "old-run")["last_stop_blocked_key"] == "  blocked  "
        after = upgrade.database_snapshot(path)
        assert db.migrate() == 9
        assert upgrade.database_snapshot(path) == after
        upgrade.require_old_rows(path, before)


def test_schema9_project_and_run_serializers_and_turn_upserts(tmp_path, monkeypatch):
    with db_module.Database(tmp_path / "state.db", settings=Settings(data_dir=tmp_path)) as db:
        with db.read_connection() as conn:
            _assert_schema9(conn)
        root = tmp_path / "repo"
        root.mkdir()
        (root / ".git").mkdir()
        project = db.ensure_project(root, codex_project_id="codex-root", codex_project_name="真实项目")
        assert (project["codex_project_id"], project["codex_project_name"]) == ("codex-root", "真实项目")
        assert db.ensure_project(root)["codex_project_id"] == "codex-root"
        assert db.ensure_project(root, codex_project_name="已更新")["codex_project_name"] == "已更新"

        slice_dir = root / ".swarm-workspaces" / "slice"
        slice_project = db.ensure_project(slice_dir)
        assert slice_project["canonical_project_id"] == project["id"]
        assert db.ensure_project(slice_dir / "nested")["canonical_project_id"] == project["id"]
        independent = root / "independent"
        independent.mkdir()
        (independent / ".git").write_text("gitdir: ./foo\n", encoding="utf-8")
        assert db.ensure_project(independent)["canonical_project_id"] is None
        assert db.ensure_project(root / "recorded_git", is_git=True)["canonical_project_id"] is None
        branch_root = root / "future-parent"
        branch_child = db.ensure_project(branch_root / "first-registered")
        assert branch_child["canonical_project_id"] == project["id"]
        branch_parent = db.ensure_project(branch_root, is_git=True)
        assert db.get_project(branch_child["id"])["canonical_project_id"] == branch_parent["id"]

        t = iter(range(10, 200))
        monkeypatch.setattr(db_module.time, "time", lambda: next(t))
        common = {"mode": "swarm", "project_id": project["id"], "session_id": "my-session"}
        first = db.create_run(title="first", turn_id="turn-1", trigger_prompt="原始触发\n保留",
                              execution_root=str(slice_dir), **common)
        second = db.create_run(title="second", turn_id="turn-2", **common)
        assert (first["session_id"], first["session_seq"], second["session_seq"]) == ("my-session", 1, 2)
        assert (first["turn_id"], first["trigger_prompt"], first["execution_root"]) == (
            "turn-1", "原始触发\n保留", str(slice_dir),
        )
        assert second["execution_root"] == second["trigger_prompt"] == ""
        assert db.get_session_binding("my-session")["run_id"] == second["id"]
        db.update_run(second["id"], status="archived")
        db.update_session_stop_blocked_key("my-session", "explicit", run_id=second["id"])
        assert db.get_session_binding("my-session")["run_id"] == first["id"]
        db.update_session_stop_blocked_key("my-session", "primary")
        db.upsert_session_binding("my-session", second["id"])
        assert db.get_session_binding("my-session", first["id"])["last_stop_blocked_key"] == "primary"
        assert db.get_session_binding("my-session", second["id"])["last_stop_blocked_key"] == "explicit"
        late = db.create_run(mode="swarm", title="late binding", project_id=project["id"])
        db.upsert_session_binding("my-session", late["id"], is_primary=False)
        assert db.get_run(late["id"])["session_seq"] == 3
        assert db.get_session_binding("my-session", late["id"])["is_primary"] is False
        assert db.get_session_binding("my-session")["run_id"] == first["id"]

        first_turn = db.upsert_session_turn("my-session", "turn-1", cwd=str(root), prompt_preview="first")
        db.upsert_session_turn("my-session", "turn-2", cwd=str(root), prompt_preview="second")
        updated = db.upsert_session_turn("my-session", "turn-1", cwd=str(slice_dir),
                                         prompt_preview="edited", active_run_id=first["id"])
        db.upsert_session_turn("different-session", "turn-1", cwd=str(root), prompt_preview="different")
        assert updated["created_at"] == first_turn["created_at"]
        assert updated["updated_at"] > first_turn["updated_at"]
        assert (updated["cwd"], updated["prompt_preview"], updated["active_run_id"]) == (
            str(slice_dir), "edited", first["id"],
        )
        assert [r["turn_id"] for r in db.list_session_turns("my-session")] == ["turn-1", "turn-2"]


@pytest.mark.parametrize(("path", "kind"), [
    ("G:/repos/main", "workspace"),
    (r"C:\USERS\Example\APPDATA\LOCAL\TEMP\run", "temp"),
    (r"G:\tools\.codex\backups\trial", "temp"),
    ("G:/docs/verification/native-gap/s1/fixture/repo", "fixture"),
    ("G:/docs/verification/native-gap/fixture-not-a-marker", "workspace"),
    (Path(tempfile.gettempdir()) / "swarm-s1", "temp"),
])
def test_project_kind_segment_match(path, kind):
    assert db_module.classify_project_kind(path) == kind


def test_session_sequences_are_allocated_in_write_transaction(tmp_path):
    settings = Settings(data_dir=tmp_path)
    with db_module.Database(settings=settings) as one, db_module.Database(settings=settings) as two:
        project = one.ensure_project(tmp_path / "root")
        common = dict(mode="swarm", title="parallel", project_id=project["id"], session_id="parallel")
        with ThreadPoolExecutor(2) as pool:
            a = pool.submit(one.create_run, **common)
            b = pool.submit(two.create_run, **common)
            assert sorted((a.result()["session_seq"], b.result()["session_seq"])) == [1, 2]
        assert len(one.list_session_bindings("parallel")) == 2


def test_schema8_reader_rejects_schema9_without_changes(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    settings = Settings(data_dir=tmp_path)
    with db_module.Database(path, settings=settings) as db:
        assert db.migrate() == 9
    upgrade = _upgrade_script()
    before = upgrade.database_snapshot(path)
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 8)
    with pytest.raises(ValueError, match="Database schema 9 is newer than supported 8"):
        db_module.Database(path, settings=settings)
    assert upgrade.database_snapshot(path) == before
