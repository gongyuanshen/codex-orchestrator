"""Schema 10 migration and model metadata persistence contracts."""
from contextlib import closing
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sqlite3

import pytest

from orchestrator import db as db_module
from orchestrator.config import Settings


def _upgrade_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "upgrade.py"
    spec = spec_from_file_location("s10_upgrade", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _schema9_fixture(path: Path, workspace: Path) -> None:
    """Literal Schema 9 DDL and preexisting rows prior to Schema 10 upgrade."""
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript("""
            CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at REAL NOT NULL);
            INSERT INTO schema_migrations VALUES(9, 200.0);
            CREATE TABLE projects(
                id TEXT PRIMARY KEY,
                root_path TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                is_git INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                codex_project_id TEXT,
                codex_project_name TEXT,
                project_kind TEXT NOT NULL DEFAULT 'workspace'
                    CHECK (project_kind IN ('workspace', 'fixture', 'temp')),
                canonical_project_id TEXT
            );
            CREATE TABLE runs(
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                mode TEXT NOT NULL CHECK(mode IN ('boost', 'teamwork', 'swarm')),
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK(status IN ('active', 'completed', 'failed', 'archived')),
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                session_id TEXT,
                turn_id TEXT,
                session_seq INTEGER NOT NULL DEFAULT 0,
                trigger_prompt TEXT NOT NULL DEFAULT '',
                execution_root TEXT NOT NULL DEFAULT '',
                workflow_version INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE workers(
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN (
                    'interviewer', 'planner', 'investigator', 'coder', 'verifier', 'swarm_worker'
                )),
                agent_id TEXT,
                pending_agent_path TEXT,
                status TEXT NOT NULL DEFAULT 'waiting'
                    CHECK (status IN ('waiting', 'working', 'stopped', 'killed')),
                workspace_id TEXT,
                current_task TEXT NOT NULL DEFAULT '',
                last_heartbeat_at REAL NOT NULL,
                stall_threshold_sec INTEGER NOT NULL DEFAULT 300,
                stalled_alerted INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE (run_id, name)
            );
            CREATE TABLE session_run_bindings(
                session_id TEXT,
                run_id TEXT NOT NULL,
                project_root TEXT NOT NULL,
                last_stop_blocked_key TEXT,
                updated_at REAL NOT NULL,
                is_primary INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (session_id, run_id)
            );
            CREATE INDEX idx_session_run_bindings_run
                ON session_run_bindings(run_id, updated_at DESC);
            CREATE TABLE session_turns(
                session_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                cwd TEXT NOT NULL,
                prompt_preview TEXT NOT NULL,
                active_run_id TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (session_id, turn_id)
            );
            CREATE INDEX idx_session_turns_session_updated
                ON session_turns(session_id, updated_at DESC);
        """)
        conn.execute(
            "INSERT INTO projects VALUES (?, ?, ?, 1, 10.0, 'codex-p1', '主项目', 'workspace', NULL)",
            ("proj-1", str(workspace), "主项目"),
        )
        conn.execute(
            """INSERT INTO runs VALUES
               ('run-1', 'proj-1', 'swarm', '旧任务\n保留', 'completed', '{"k":"v"}',
                100.0, 105.0, 'sess-legacy', 'turn-legacy', 1, '触发词', '', 5)"""
        )
        conn.execute(
            """INSERT INTO workers VALUES
               ('wrk-1', 'run-1', 'coder', 'coder', '/root/coder', NULL,
                'stopped', NULL, '实现模块', 102.0, 300, 0, 100.0, 103.0)"""
        )
        conn.execute(
            "INSERT INTO session_run_bindings VALUES ('sess-legacy', 'run-1', ?, NULL, 105.0, 1)",
            (str(workspace),),
        )
        conn.execute(
            "INSERT INTO session_turns VALUES ('sess-legacy', 'turn-legacy', ?, '触发词', 'run-1', 99.0, 100.0)",
            (str(workspace),),
        )


def _assert_schema10(conn: sqlite3.Connection) -> None:
    def columns(table: str) -> dict[str, tuple]:
        return {
            r["name"]: (r["type"], r["notnull"], r["dflt_value"], r["pk"])
            for r in conn.execute(f"PRAGMA table_info({table})")
        }

    runs = columns("runs")
    assert runs["trigger_model"] == ("TEXT", 0, None, 0)
    assert runs["trigger_reasoning_effort"] == ("TEXT", 0, None, 0)

    turns = columns("session_turns")
    assert turns["model"] == ("TEXT", 0, None, 0)
    assert turns["reasoning_effort"] == ("TEXT", 0, None, 0)

    workers = columns("workers")
    for col in ("codex_thread_id", "model", "reasoning_effort", "model_provider"):
        assert workers[col] == ("TEXT", 0, None, 0)

    subagents = columns("worker_subagents")
    assert subagents["run_id"] == ("TEXT", 1, None, 1)
    assert subagents["thread_id"] == ("TEXT", 1, None, 2)
    assert subagents["parent_worker_id"] == ("TEXT", 0, None, 0)
    assert subagents["parent_thread_id"] == ("TEXT", 1, None, 0)
    assert subagents["depth"] == ("INTEGER", 1, "2", 0)
    assert subagents["agent_path"] == ("TEXT", 1, "''", 0)
    for col in ("agent_role", "agent_nickname", "model", "reasoning_effort", "model_provider", "edge_status"):
        assert subagents[col] == ("TEXT", 0, None, 0)
    assert subagents["created_at"] == ("REAL", 0, None, 0)
    assert subagents["updated_at"] == ("REAL", 1, None, 0)
    assert [r["name"] for r in conn.execute("PRAGMA index_info(idx_worker_subagents_run_parent)")] == [
        "run_id", "parent_worker_id", "depth", "created_at",
    ]


def test_schema9_upgrade_preserves_old_rows_and_is_idempotent(tmp_path):
    path = tmp_path / "state.db"
    workspace = tmp_path / "workspace"
    _schema9_fixture(path, workspace)
    upgrade = _upgrade_script()
    before = upgrade.database_snapshot(path)
    assert before["schema_version"] == 9

    with db_module.Database(path, settings=Settings(data_dir=tmp_path)) as db:
        assert db.migrate() == db_module.SCHEMA_VERSION == 10
        upgrade.require_old_rows(path, before)
        with db.read_connection() as conn:
            _assert_schema10(conn)

        old_run = db.get_run("run-1")
        assert old_run is not None
        assert old_run["trigger_model"] is None
        assert old_run["trigger_reasoning_effort"] is None
        assert old_run["trigger_prompt"] == "触发词"

        old_worker = db.get_worker("wrk-1")
        assert old_worker is not None
        assert old_worker["codex_thread_id"] is None
        assert old_worker["model"] is None
        assert old_worker["reasoning_effort"] is None
        assert old_worker["model_provider"] is None

        old_turn = db.get_session_turn("sess-legacy", "turn-legacy")
        assert old_turn is not None
        assert old_turn["model"] is None
        assert old_turn["reasoning_effort"] is None

        after = upgrade.database_snapshot(path)
        assert after["schema_version"] == 10
        assert db.migrate() == 10
        assert upgrade.database_snapshot(path) == after
        upgrade.require_old_rows(path, before)


def test_schema9_reader_rejects_schema10_without_changes(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    settings = Settings(data_dir=tmp_path)
    with db_module.Database(path, settings=settings) as db:
        assert db.migrate() == 10
    upgrade = _upgrade_script()
    before = upgrade.database_snapshot(path)
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 9)
    with pytest.raises(RuntimeError, match="Database schema 10 is newer than supported 9"):
        db_module.require_runtime_schema(path)
    with pytest.raises(ValueError, match="Database schema 10 is newer than supported 9"):
        db_module.Database(path, settings=settings)
    assert upgrade.database_snapshot(path) == before


def test_run_trigger_model_and_session_turn_model_contracts(tmp_path):
    with db_module.Database(tmp_path / "state.db", settings=Settings(data_dir=tmp_path)) as db:
        proj = db.ensure_project(tmp_path / "repo")
        run1 = db.create_run(
            mode="swarm",
            title="With Model",
            project_id=proj["id"],
            session_id="sess-1",
            turn_id="turn-1",
            trigger_model="  gpt-6.1-sol  ",
            trigger_reasoning_effort="high",
        )
        assert run1["trigger_model"] == "gpt-6.1-sol"
        assert run1["trigger_reasoning_effort"] == "high"

        run2 = db.create_run(
            mode="swarm",
            title="Without Model",
            project_id=proj["id"],
            session_id="sess-1",
            turn_id="turn-2",
            trigger_model="  ",
            trigger_reasoning_effort=None,
        )
        assert run2["trigger_model"] is None
        assert run2["trigger_reasoning_effort"] is None

        assert db.update_run_trigger_model("missing-run", trigger_model="gpt-6.2-sol") is None

        # Backfill trigger_model first (effort still missing), then guard against model mismatch.
        partial = db.update_run_trigger_model(run2["id"], trigger_model="gpt-6.1-sol")
        assert partial["trigger_model"] == "gpt-6.1-sol"
        assert partial["trigger_reasoning_effort"] is None

        mismatched = db.update_run_trigger_model(
            run2["id"], trigger_model="gpt-6.2-sol", trigger_reasoning_effort="ultra"
        )
        assert mismatched["trigger_model"] == "gpt-6.1-sol"
        assert mismatched["trigger_reasoning_effort"] is None

        matched = db.update_run_trigger_model(
            run2["id"], trigger_model="gpt-6.1-sol", trigger_reasoning_effort="medium"
        )
        assert matched["trigger_model"] == "gpt-6.1-sol"
        assert matched["trigger_reasoning_effort"] == "medium"

        # Existing non-null trigger_reasoning_effort is never overwritten.
        unchanged = db.update_run_trigger_model(
            run2["id"], trigger_model="gpt-6.1-sol", trigger_reasoning_effort="low"
        )
        assert unchanged["trigger_reasoning_effort"] == "medium"

        # Session turns upsert & model backfill.
        t1 = db.upsert_session_turn(
            "sess-1", "turn-1", cwd=str(tmp_path), prompt_preview="p1",
            model="gpt-6.1-sol", reasoning_effort=None,
        )
        assert (t1["model"], t1["reasoning_effort"]) == ("gpt-6.1-sol", None)

        # Upsert preserves non-null model when excluded is NULL, and updates when excluded is non-null.
        t1_again = db.upsert_session_turn(
            "sess-1", "turn-1", cwd=str(tmp_path), prompt_preview="p1 edit",
            model=None, reasoning_effort="high",
        )
        assert (t1_again["model"], t1_again["reasoning_effort"]) == ("gpt-6.1-sol", "high")

        t1_switched = db.upsert_session_turn(
            "sess-1", "turn-1", cwd=str(tmp_path), prompt_preview="p1 v2",
            model="gpt-6.2-sol", reasoning_effort=None,
        )
        assert (t1_switched["model"], t1_switched["reasoning_effort"]) == ("gpt-6.2-sol", "high")

        assert db.update_session_turn_model("sess-1", "missing", model="gpt-6.2-sol") is None
        t2 = db.upsert_session_turn("sess-1", "turn-2", cwd=str(tmp_path), prompt_preview="p2")
        assert (t2["model"], t2["reasoning_effort"]) == (None, None)

        t2_model = db.update_session_turn_model("sess-1", "turn-2", model="gpt-6.2-sol")
        assert (t2_model["model"], t2_model["reasoning_effort"]) == ("gpt-6.2-sol", None)
        # Mismatching model does not attach foreign effort.
        t2_bad = db.update_session_turn_model(
            "sess-1", "turn-2", model="gpt-6.1-sol", reasoning_effort="low"
        )
        assert (t2_bad["model"], t2_bad["reasoning_effort"]) == ("gpt-6.2-sol", None)
        t2_filled = db.update_session_turn_model("sess-1", "turn-2", reasoning_effort="ultra")
        assert (t2_filled["model"], t2_filled["reasoning_effort"]) == ("gpt-6.2-sol", "ultra")
        # Non-null fields are never cleared by NULL.
        t2_keep = db.update_session_turn_model("sess-1", "turn-2", model=None, reasoning_effort=None)
        assert (t2_keep["model"], t2_keep["reasoning_effort"]) == ("gpt-6.2-sol", "ultra")


def test_worker_model_meta_subagents_and_session_queries(tmp_path, monkeypatch):
    ticks = iter(range(100, 200))
    monkeypatch.setattr(db_module.time, "time", lambda: next(ticks))

    with db_module.Database(tmp_path / "state.db", settings=Settings(data_dir=tmp_path)) as db:
        proj = db.ensure_project(tmp_path / "repo")
        r1 = db.create_run(mode="swarm", title="R1", project_id=proj["id"], session_id="sess-a")
        r2 = db.create_run(mode="swarm", title="R2", project_id=proj["id"], session_id="sess-a")
        r_other = db.create_run(mode="swarm", title="Other", project_id=proj["id"], session_id="sess-b")

        ordered = db.list_session_runs_ordered("sess-a")
        assert [r["id"] for r in ordered] == [r1["id"], r2["id"]]
        assert db.list_session_runs_ordered("") == []

        w1 = db.upsert_worker(r1["id"], "coder", role="coder", agent_id="/root/coder")
        w2 = db.upsert_worker(r2["id"], "verifier", role="verifier", pending_agent_path="/root/verifier")
        db.upsert_worker(r_other["id"], "other", role="coder", agent_id="/root/other")

        assert db.list_session_registered_agent_paths("sess-a") == {"/root/coder", "/root/verifier"}
        assert db.list_session_registered_agent_paths("") == set()

        assert db.update_worker_model_meta("missing", model="gpt-6.2-sol") is None
        updated = db.update_worker_model_meta(
            w1["id"],
            codex_thread_id="thread-w1",
            model="gpt-6.1-sol",
            reasoning_effort="high",
            model_provider="openai",
        )
        assert updated["codex_thread_id"] == "thread-w1"
        assert updated["model"] == "gpt-6.1-sol"
        assert updated["reasoning_effort"] == "high"
        assert updated["model_provider"] == "openai"

        # Passing None/whitespace never overwrites non-null values.
        kept = db.update_worker_model_meta(
            w1["id"], codex_thread_id=" ", model=None, reasoning_effort="ultra", model_provider=None
        )
        assert kept["codex_thread_id"] == "thread-w1"
        assert kept["model"] == "gpt-6.1-sol"
        assert kept["reasoning_effort"] == "ultra"
        assert kept["model_provider"] == "openai"

        # Worker subagents CRUD + ordering + COALESCE preservation + cascade delete.
        with pytest.raises(db_module.NotFoundError):
            db.upsert_worker_subagents("missing-run", [])
        with pytest.raises(ValueError):
            db.upsert_worker_subagents(r1["id"], [{"thread_id": "", "parent_thread_id": "p"}])

        rows = db.upsert_worker_subagents(r1["id"], [
            {
                "thread_id": "sub-d3",
                "parent_worker_id": w1["id"],
                "parent_thread_id": "sub-d2a",
                "depth": 3,
                "agent_path": "/root/coder/a/deep",
                "agent_role": "explorer",
                "agent_nickname": "Echo",
                "model": "gpt-6.2-nano",
                "reasoning_effort": "low",
                "model_provider": "openai",
                "edge_status": "completed",
                "created_at": 15.0,
                "updated_at": 16.0,
            },
            {
                "thread_id": "sub-d2b",
                "parent_worker_id": w1["id"],
                "parent_thread_id": "thread-w1",
                "depth": 2,
                "agent_path": "/root/coder/b",
                "agent_role": "worker",
                "agent_nickname": "Spark",
                "model": "gpt-6.2-mini",
                "reasoning_effort": "medium",
                "model_provider": "openai",
                "edge_status": "running",
                "created_at": 12.0,
                "updated_at": 13.0,
            },
            {
                "thread_id": "sub-d2a",
                "parent_worker_id": w1["id"],
                "parent_thread_id": "thread-w1",
                "depth": 2,
                "agent_path": "/root/coder/a",
                "agent_role": "explorer",
                "agent_nickname": "Lumen",
                "model": "gpt-6.2-mini",
                "reasoning_effort": None,
                "model_provider": "openai",
                "edge_status": "running",
                "created_at": 10.0,
                "updated_at": 11.0,
            },
        ])
        assert [(row["thread_id"], row["depth"]) for row in rows] == [
            ("sub-d2a", 2), ("sub-d2b", 2), ("sub-d3", 3),
        ]

        # Re-upsert preserves existing non-null columns when excluded has NULL / ''.
        db.upsert_worker_subagents(r1["id"], [
            {
                "thread_id": "sub-d2a",
                "parent_worker_id": None,
                "parent_thread_id": "thread-w1",
                "depth": 2,
                "agent_path": "",
                "agent_role": None,
                "agent_nickname": None,
                "model": None,
                "reasoning_effort": "high",
                "model_provider": None,
                "edge_status": "completed",
                "created_at": None,
                "updated_at": 20.0,
            },
        ])
        # Empty upsert preserves existing snapshot rows.
        assert len(db.upsert_worker_subagents(r1["id"], [])) == 3

        listed = db.list_worker_subagents(r1["id"])
        assert listed[0] == {
            "run_id": r1["id"],
            "thread_id": "sub-d2a",
            "parent_worker_id": w1["id"],
            "parent_thread_id": "thread-w1",
            "depth": 2,
            "agent_path": "/root/coder/a",
            "agent_role": "explorer",
            "agent_nickname": "Lumen",
            "model": "gpt-6.2-mini",
            "reasoning_effort": "high",
            "model_provider": "openai",
            "edge_status": "completed",
            "created_at": 10.0,
            "updated_at": 20.0,
        }
        assert db.list_worker_subagents(r2["id"]) == []

        with db.transaction() as conn:
            conn.execute("DELETE FROM runs WHERE id = ?;", (r1["id"],))
        assert db.list_worker_subagents(r1["id"]) == []
