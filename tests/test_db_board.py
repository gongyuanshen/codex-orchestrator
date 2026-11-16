"""Comprehensive tests for config.py, db.py (SQLite WAL), and board.py."""

from __future__ import annotations

import socket
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from orchestrator.board import BoardService, extract_mentions
from orchestrator.config import Settings, get_settings
from orchestrator.db import Database, NotFoundError, SCHEMA_VERSION


@pytest.fixture
def db(tmp_path: Path) -> Database:
    settings = Settings(
        data_dir=tmp_path / "data",
        stall_sec=60,
        watchdog_interval_sec=5,
        port_range=(18800, 18820),
    )
    database = Database(settings.db_path, settings=settings)
    yield database
    database.close()


@pytest.fixture
def board(db: Database) -> BoardService:
    return BoardService(db)


def test_board_summary_omits_internal_manifest_without_mutating_storage(db, board, tmp_path):
    run = db.create_run(project_root=tmp_path, is_git=False, mode="swarm", title="Summary")
    manifest = {"files": {"large.txt": "a" * 64}}
    ws = db.create_workspace(run["id"], "coder", path=tmp_path / "workspace",
                             mode="snapshot", snapshot_manifest=manifest)
    summary = board.board_status(run["id"])["workspaces"][0]
    assert summary["id"] == ws["id"]
    assert "snapshot_manifest" not in summary
    assert "snapshot_manifest_json" not in summary
    assert db.get_workspace(ws["id"])["snapshot_manifest"] == manifest


# ----------------------------------------------------------------------
# config.py tests
# ----------------------------------------------------------------------


def test_settings_defaults_and_env_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for key in (
        "TRI_ORCH_HOST",
        "TRI_ORCH_PORT",
        "TRI_ORCH_DATA_DIR",
        "TRI_ORCH_STALL_SEC",
        "TRI_ORCH_WATCHDOG_INTERVAL_SEC",
        "TRI_ORCH_PORT_RANGE",
    ):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalApp"))
    defaults = get_settings()
    assert defaults.host == "127.0.0.1"
    assert defaults.port == 18790
    assert defaults.data_dir == (tmp_path / "LocalApp" / "tri-orchestrator")
    assert defaults.stall_sec == 300
    assert defaults.watchdog_interval_sec == 15
    assert defaults.port_range == (18800, 18999)
    assert defaults.db_path == defaults.data_dir / "state.db"

    custom_dir = tmp_path / "override-dir"
    monkeypatch.setenv("TRI_ORCH_HOST", "0.0.0.0")
    monkeypatch.setenv("TRI_ORCH_PORT", "19001")
    monkeypatch.setenv("TRI_ORCH_DATA_DIR", str(tmp_path / "env-dir"))
    monkeypatch.setenv("TRI_ORCH_STALL_SEC", "120")
    monkeypatch.setenv("TRI_ORCH_WATCHDOG_INTERVAL_SEC", "2.5")
    monkeypatch.setenv("TRI_ORCH_PORT_RANGE", "19100-19150")

    env_settings = get_settings()
    assert env_settings.host == "0.0.0.0"
    assert env_settings.port == 19001
    assert env_settings.data_dir == (tmp_path / "env-dir")
    assert env_settings.stall_sec == 120
    assert env_settings.watchdog_interval_sec == 2.5
    assert env_settings.port_range == (19100, 19150)

    # Explicit data_dir parameter wins over TRI_ORCH_DATA_DIR
    overridden = get_settings(data_dir=custom_dir)
    assert overridden.data_dir == custom_dir

    with pytest.raises(ValueError):
        Settings(port_range="19000..18900")
    with pytest.raises(ValueError):
        Settings(port=70000)


# ----------------------------------------------------------------------
# db.py schema, pragmas, CRUD & versioning tests
# ----------------------------------------------------------------------


def test_wal_pragmas_and_schema_migration(db: Database, tmp_path: Path) -> None:
    with db.read_connection() as conn:
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        sync_mode = conn.execute("PRAGMA synchronous;").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
        fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table';"
            ).fetchall()
        }
        ws_cols = {
            r["name"]
            for r in conn.execute("PRAGMA table_info(workspaces);").fetchall()
        }

    assert str(journal_mode).lower() == "wal"
    # synchronous=NORMAL is 1 in SQLite
    assert int(sync_mode) == 1
    assert int(busy_timeout) == 5000
    assert int(fk) == 1
    assert {
        "projects",
        "runs",
        "task_packets",
        "workers",
        "board_messages",
        "read_cursors",
        "workspaces",
        "schema_migrations",
    }.issubset(tables)
    assert "snapshot_manifest_json" in ws_cols
    assert "merged_commit" in ws_cols
    assert "merged_target_commit" in ws_cols

    # Re-running migrate() is idempotent
    assert db.migrate() == SCHEMA_VERSION

    # Upgrade guard: an existing workspaces table missing snapshot_manifest_json gets migrated
    legacy_path = tmp_path / "legacy.db"
    raw_conn = sqlite3.connect(legacy_path)
    raw_conn.executescript(
        """
        CREATE TABLE projects (
            id TEXT PRIMARY KEY, root_path TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL, is_git INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL
        );
        CREATE TABLE runs (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, mode TEXT NOT NULL,
            title TEXT NOT NULL, status TEXT NOT NULL, metadata_json TEXT NOT NULL,
            created_at REAL NOT NULL, updated_at REAL NOT NULL
        );
        CREATE TABLE workspaces (
            id TEXT PRIMARY KEY, run_id TEXT NOT NULL, worker_name TEXT NOT NULL,
            path TEXT NOT NULL, branch_name TEXT, base_commit TEXT, mode TEXT NOT NULL,
            assigned_port INTEGER, status TEXT NOT NULL, created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        """
    )
    raw_conn.execute("INSERT INTO projects VALUES ('legacy-project', 'legacy-root', 'Legacy', 1, 0)")
    raw_conn.execute("INSERT INTO runs VALUES ('legacy-run', 'legacy-project', 'boost', 'Legacy', 'active', '{}', 0, 0)")
    raw_conn.execute("INSERT INTO workspaces VALUES ('legacy-ws', 'legacy-run', 'legacy', 'legacy-path', 'worker', 'old-base', 'git_clone', NULL, 'merged', 0, 0)")
    raw_conn.commit()
    raw_conn.close()
    migrated = Database(legacy_path)
    try:
        with migrated.read_connection() as conn:
            cols = {
                r["name"]
                for r in conn.execute("PRAGMA table_info(workspaces);").fetchall()
            }
        assert "snapshot_manifest_json" in cols
        assert "merged_commit" in cols
        assert "merged_target_commit" in cols
        legacy_ws = migrated.get_workspace("legacy-ws")
        assert legacy_ws["status"] == "merged"
        assert legacy_ws["base_commit"] == "old-base"
        assert legacy_ws["merged_commit"] is None
        assert legacy_ws["merged_target_commit"] is None
        assert migrated.migrate() == SCHEMA_VERSION
    finally:
        migrated.close()


def test_projects_runs_and_task_packet_versioning(db: Database, tmp_path: Path) -> None:
    repo_dir = tmp_path / "sample-repo"
    repo_dir.mkdir()

    run = db.create_run(
        project_root=repo_dir,
        is_git=True,
        mode="teamwork",
        title="Build feature X",
        metadata={"branch": "main"},
    )
    assert run["mode"] == "teamwork"
    assert run["status"] == "active"
    assert run["metadata"] == {"branch": "main"}

    # Calling ensure_project on same root returns the same project
    proj = db.ensure_project(repo_dir, is_git=True)
    assert proj["id"] == run["project_id"]
    assert proj["is_git"] is True
    assert len(db.list_projects()) == 1

    # Retain the migrated-v0 packet-after-completion compatibility contract.
    db.connect().execute("UPDATE runs SET workflow_version=0 WHERE id=?", (run["id"],))
    updated = db.update_run(run["id"], status="completed", metadata={"passed": True})
    assert updated["status"] == "completed"
    assert updated["metadata"] == {"passed": True}

    # Task packet monotonic versioning per run
    p1 = db.save_task_packet(
        run["id"],
        title="Plan v1",
        goal="Implement DB",
        file_ownership={"db-board": ["src/db.py"]},
        acceptance_criteria=["WAL enabled"],
    )
    p2 = db.save_task_packet(
        run["id"],
        title="Plan v2",
        goal="Implement DB + Board",
        file_ownership={"db-board": ["src/db.py", "src/board.py"]},
        acceptance_criteria=["WAL enabled", "Tests green"],
    )
    assert p1["version"] == 1
    assert p2["version"] == 2
    assert db.get_task_packet(run["id"])["version"] == 2
    assert db.get_task_packet(run["id"], version=1)["title"] == "Plan v1"
    assert [p["version"] for p in db.list_task_packets(run["id"])] == [1, 2]

    # Transaction rollback leaves DB untouched
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.save_task_packet(run["id"], title="Should rollback", goal="nope")
            raise RuntimeError("boom")
    assert [p["version"] for p in db.list_task_packets(run["id"])] == [1, 2]


def test_workspace_crud_and_port_pool_allocation(db: Database, tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    run = db.create_run(project_root=repo_dir, is_git=False, mode="swarm", title="Swarm")

    # Occupy port 18800 on localhost to verify socket-bind exclusion works
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 18800))
    blocker.listen(1)
    try:
        ws1 = db.create_workspace(
            run["id"],
            "coder-1",
            path=repo_dir / ".swarm-workspaces" / "coder-1",
            mode="snapshot",
            snapshot_manifest={"a.py": "deadbeef"},
        )
        ws2 = db.create_workspace(
            run["id"],
            "coder-2",
            path=repo_dir / ".swarm-workspaces" / "coder-2",
            mode="git_clone",
            branch_name="swarm/r1/coder-2",
            base_commit="abc123",
        )
    finally:
        blocker.close()

    assert ws1["assigned_port"] != 18800
    assert 18801 <= ws1["assigned_port"] <= 18820
    assert ws2["assigned_port"] != ws1["assigned_port"]
    assert ws1["snapshot_manifest"] == {"a.py": "deadbeef"}

    # Cleanup frees port for reuse
    db.update_workspace(ws1["id"], status="merged")
    db.update_workspace(ws1["id"], status="cleaned")
    assert ws1["assigned_port"] not in db.list_used_ports()
    assert db.get_workspace_by_worker(run["id"], "coder-1") is None
    assert db.get_workspace_by_worker(run["id"], "coder-1", include_cleaned=True)["id"] == ws1["id"]


# ----------------------------------------------------------------------
# board.py tests: #seq, cursors, @mentions, worker state machine
# ----------------------------------------------------------------------


def test_mention_extraction_and_board_post_compact_pointer(
    db: Database, board: BoardService, tmp_path: Path
) -> None:
    mentions = extract_mentions(
        "Done #14 (@verifier, @coder-1.) — contact me@example.com for details",
        explicit_mentions=["@coordinator", "verifier"],
    )
    assert mentions == ["coordinator", "verifier", "coder-1"]

    repo = tmp_path / "proj"
    repo.mkdir()
    run = db.create_run(project_root=repo, is_git=True, mode="swarm", title="Test Board")

    p1 = board.board_post(
        run["id"],
        sender="coder-1",
        sender_role="coder",
        channel="#backend",
        msg_type="handoff",
        summary="Implemented auth @verifier",
        body_md="Long diff details...\nPing @db-board.",
        mentions=["@qa"],
    )
    assert set(p1.keys()) == {"seq", "run_id", "channel", "mentions", "summary"}
    assert "body_md" not in p1
    assert p1["seq"] == 1
    assert p1["channel"] == "#backend"
    assert p1["mentions"] == ["qa", "verifier", "db-board"]

    p2 = board.board_post(
        run["id"],
        sender="verifier",
        sender_role="verifier",
        body_md="# Review\nLooks good @coder-1",
    )
    assert p2["seq"] == p1["seq"] + 1
    assert p2["channel"] == "main"
    assert p2["summary"] == "Review"
    assert p2["mentions"] == ["coder-1"]


def test_board_read_cursors_mentions_and_channels(
    db: Database, board: BoardService, tmp_path: Path
) -> None:
    repo = tmp_path / "proj2"
    repo.mkdir()
    run = db.create_run(project_root=repo, is_git=True, mode="swarm", title="Cursors")

    m1 = board.board_post(run["id"], "coord", "Task 1 @coder-1", channel="main")
    m2 = board.board_post(run["id"], "coord", "Task 2 @coder-2", channel="#ui")
    m3 = board.board_post(run["id"], "coder-1", "Done 1 @verifier", channel="#backend")
    m4 = board.board_post(run["id"], "coder-2", "Done 2 @verifier @coder-1", channel="#ui")

    # Channel list always includes 'main' plus active channels
    ch_names = [c["channel"] for c in board.board_channel_list(run["id"])["channels"]]
    assert {"main", "#ui", "#backend"}.issubset(set(ch_names))

    # Incremental read without advancing cursor
    r1 = board.board_read(run["id"], reader_name="verifier", advance_cursor=False)
    assert [m["seq"] for m in r1["messages"]] == [m1["seq"], m2["seq"], m3["seq"], m4["seq"]]
    assert r1["cursor_seq"] == 0

    # Mention filtering with limit
    r_mention = board.board_read(
        run["id"], mention="@verifier", reader_name="verifier", limit=1
    )
    assert [m["seq"] for m in r_mention["messages"]] == [m3["seq"]]

    # Advance cursor on #ui only
    r_ui = board.board_read(
        run["id"], channel="#ui", reader_name="verifier", advance_cursor=True
    )
    assert [m["seq"] for m in r_ui["messages"]] == [m2["seq"], m4["seq"]]
    assert r_ui["cursor_seq"] == m4["seq"]

    # Reading #ui again returns 0 messages
    r_ui_again = board.board_read(run["id"], channel="#ui", reader_name="verifier")
    assert r_ui_again["count"] == 0

    # Reading main still starts at 0 because only #ui cursor advanced
    r_main = board.board_read(run["id"], channel="main", reader_name="verifier")
    assert [m["seq"] for m in r_main["messages"]] == [m1["seq"]]

    # Advancing all-channel cursor covers main & #backend too
    r_all = board.board_read(run["id"], reader_name="verifier", advance_cursor=True)
    assert [m["seq"] for m in r_all["messages"]] == [m1["seq"], m2["seq"], m3["seq"], m4["seq"]]
    assert board.board_read(run["id"], reader_name="verifier")["count"] == 0

    # New message after cursor arrives only once
    m5 = board.board_post(run["id"], "coord", "Followup @verifier", channel="#backend")
    r_next = board.board_read(
        run["id"], channel="#backend", reader_name="verifier", advance_cursor=True
    )
    assert [m["seq"] for m in r_next["messages"]] == [m5["seq"]]


def test_worker_state_machine_and_board_status(
    db: Database, board: BoardService, tmp_path: Path
) -> None:
    repo = tmp_path / "proj3"
    repo.mkdir()
    run = db.create_run(project_root=repo, is_git=True, mode="swarm", title="Workers")

    t0 = 1_000_000.0
    w1 = board.worker_register(
        run["id"],
        "coder-1",
        role="coder",
        status="waiting",
        current_task="idle",
        stall_threshold_sec=30,
        heartbeat_at=t0,
    )
    assert w1["status"] == "waiting"

    # waiting -> working -> stopped -> working -> killed
    w1 = board.worker_status_update(run["id"], "coder-1", "working", current_task="auth")
    assert w1["status"] == "working"

    w1 = board.worker_status_update(run["id"], "coder-1", "stopped")
    assert w1["status"] == "stopped"

    w1 = board.worker_status_update(worker_id=w1["id"], status="working")
    assert w1["status"] == "working"

    # Simulate old heartbeat for stall detection
    board.worker_heartbeat(run["id"], "coder-1", heartbeat_at=t0)
    stalled = db.find_stalled_workers(now=t0 + 45, run_id=run["id"])
    assert [w["name"] for w in stalled] == ["coder-1"]

    board.board_post(run["id"], "coord", "Check @coder-1", channel="main")
    status_stalled = board.board_status(run["id"], now=t0 + 45)
    assert status_stalled["stalled_workers"] == ["coder-1"]
    assert status_stalled["workers"][0]["is_stalled"] is True
    assert status_stalled["workers"][0]["unread_count"] == 1
    assert status_stalled["workers"][0]["unread_mentions"] == 1

    # Watchdog marks alerted, then heartbeat clears it
    db.mark_worker_stalled_alerted(w1["id"], alerted=True)
    assert db.find_stalled_workers(now=t0 + 45, run_id=run["id"]) == []
    board.worker_heartbeat(run["id"], "coder-1", heartbeat_at=t0 + 50)
    status_ok = board.board_status(run["id"], now=t0 + 55)
    assert status_ok["stalled_workers"] == []
    assert status_ok["workers"][0]["is_stalled"] is False

    # Transition to terminal killed state
    w1 = board.worker_status_update(run["id"], "coder-1", "killed")
    assert w1["status"] == "killed"
    with pytest.raises(ValueError):
        board.worker_status_update(run["id"], "coder-1", "working")
    with pytest.raises(ValueError):
        board.worker_heartbeat(run["id"], "coder-1")
    with pytest.raises(NotFoundError):
        board.board_status("run_nonexistent")


# ----------------------------------------------------------------------
# Multi-threaded concurrent WAL stress test
# ----------------------------------------------------------------------


def test_multithreaded_concurrent_wal_writes_no_lock_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "concurrent.db"
    settings = Settings(data_dir=tmp_path, port_range=(18850, 18999))
    main_db = Database(db_path, settings=settings)
    try:
        repo = tmp_path / "repo"
        repo.mkdir()
        run = main_db.create_run(
            project_root=repo, is_git=True, mode="swarm", title="Stress"
        )
        main_board = BoardService(main_db)

        n_threads = 10
        msgs_per_thread = 15
        barrier = threading.Barrier(n_threads)

        def worker_routine(idx: int) -> tuple[list[int], int]:
            # Half the threads reuse main_db, half open independent Database instances
            # to exercise both in-process RLock and cross-connection SQLite WAL busy_timeout.
            local_db: Database | None = None
            target_db = main_db
            target_board = main_board
            if idx % 2 == 1:
                local_db = Database(db_path, settings=settings, init_schema=False)
                target_db = local_db
                target_board = BoardService(local_db)

            try:
                wname = f"worker-{idx}"
                target_board.worker_register(
                    run["id"], wname, role="swarm_worker", status="working"
                )
                barrier.wait(timeout=10)

                seqs: list[int] = []
                for step in range(msgs_per_thread):
                    ptr = target_board.board_post(
                        run["id"],
                        sender=wname,
                        channel=f"#ch-{idx % 3}",
                        summary=f"msg {step} from @{wname}",
                        body_md=f"detail {idx}:{step}",
                    )
                    seqs.append(ptr["seq"])
                    target_board.worker_heartbeat(
                        run["id"], wname, current_task=f"step-{step}"
                    )

                ws = target_db.create_workspace(
                    run["id"],
                    wname,
                    path=repo / ".swarm-workspaces" / wname,
                    mode="git_clone",
                    branch_name=f"swarm/stress/{wname}",
                )
                pkt = target_db.save_task_packet(
                    run["id"],
                    title=f"pkt-{idx}",
                    goal=f"goal-{idx}",
                )
                assert pkt["version"] >= 1
                return seqs, int(ws["assigned_port"])
            finally:
                if local_db is not None:
                    local_db.close()

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            results = list(pool.map(worker_routine, range(n_threads)))

        all_seqs = [seq for seqs, _ in results for seq in seqs]
        all_ports = [port for _, port in results]

        assert len(all_seqs) == n_threads * msgs_per_thread
        # Every #seq is strictly unique and monotonic from 1..N
        assert sorted(all_seqs) == list(range(1, n_threads * msgs_per_thread + 1))
        # Every concurrently allocated workspace port is unique
        assert len(set(all_ports)) == n_threads
        # Task packets incremented cleanly without duplicate version errors
        packets = main_db.list_task_packets(run["id"])
        assert [p["version"] for p in packets] == list(range(1, n_threads + 1))

        # limit=None fetches all messages without truncation
        full = main_board.board_read(run["id"], limit=None)
        assert full["count"] == n_threads * msgs_per_thread
    finally:
        main_db.close()


def test_simultaneous_cold_start_database_instances(tmp_path: Path) -> None:
    db_path = tmp_path / "cold_start.db"
    settings = Settings(data_dir=tmp_path)
    n_threads = 8
    barrier = threading.Barrier(n_threads)

    def boot_and_write(idx: int) -> str:
        barrier.wait(timeout=10)
        inst = Database(db_path, settings=settings, init_schema=True)
        try:
            proj = inst.ensure_project(tmp_path / "shared-repo", is_git=True)
            return proj["id"]
        finally:
            inst.close()

    with ThreadPoolExecutor(max_workers=n_threads) as pool:
        project_ids = list(pool.map(boot_and_write, range(n_threads)))

    assert len(set(project_ids)) == 1


