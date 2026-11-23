"""v0.6.11 step 3: discarding clean never-merged workspaces, residual reports, MCP-call liveness,
role stall defaults and structured reasoning-effort records.  Real files, real Git, real SQLite."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from orchestrator.board import BoardService
from orchestrator.config import Settings
from orchestrator.db import Database, default_stall_threshold
from orchestrator.rpc_handlers import RpcDispatcher
from orchestrator.workspace_mgr import WorkspaceCleanupGuardError, WorkspaceManager
from test_workspace_mgr import _git, _init_git_repo


@pytest.fixture
def env(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    settings = Settings(data_dir=tmp_path / "data", port_range=(29160, 29190))
    db = Database(settings=settings)
    mgr = WorkspaceManager(db, settings)
    dispatcher = RpcDispatcher(db, BoardService(db, settings), mgr)
    yield project, db, mgr, dispatcher
    db.close()


def _finish(db, run_id, status="failed"):
    with db.transaction() as conn:
        conn.execute("UPDATE runs SET status=? WHERE id=?", (status, run_id))


def _run(dispatcher, project, mode="swarm"):
    return dispatcher.dispatch("run_create", {"mode": mode, "title": "Residuals", "project_root": str(project)})["id"]


# ---------------------------------------------------------------------------
# workspace_discard
# ---------------------------------------------------------------------------

def test_clean_snapshot_of_failed_run_is_discarded_with_board_record(env):
    project, db, mgr, dispatcher = env
    run_id = _run(dispatcher, project)
    ws = mgr.workspace_create(run_id, "author", copy_env=False)
    assert Path(ws["path"]).exists()
    with pytest.raises(WorkspaceCleanupGuardError, match="Run status is 'active'"):
        mgr.workspace_discard(ws["id"], "too early")
    _finish(db, run_id)
    check = dispatcher.dispatch("workspace_discard", {"workspace_id": ws["id"], "reason": "probe", "dry_run": True})
    assert check["dry_run"] is True and check["discardable"] is True
    assert Path(ws["path"]).exists()
    result = dispatcher.dispatch("workspace_discard", {"workspace_id": ws["id"], "reason": "author never started"})
    assert result["discarded"] is True and result["status"] == "cleaned"
    assert not Path(ws["path"]).exists()
    assert db.get_workspace(ws["id"])["status"] == "cleaned"
    with db.read_connection() as conn:
        notes = conn.execute("SELECT body_md FROM board_messages WHERE run_id=? AND summary=?",
                             (run_id, f"workspace_discard {ws['id']}")).fetchall()
    assert len(notes) == 1 and "author never started" in notes[0]["body_md"]
    again = mgr.workspace_discard(ws["id"], "repeat")
    assert again["already_cleaned"] is True


@pytest.mark.parametrize("change", ["add", "modify", "delete"])
def test_any_snapshot_change_blocks_discard(env, change):
    project, db, mgr, dispatcher = env
    run_id = _run(dispatcher, project)
    ws = mgr.workspace_create(run_id, "author", copy_env=False)
    ws_path = Path(ws["path"])
    if change == "add":
        (ws_path / "new.py").write_text("x = 1\n", encoding="utf-8")
    elif change == "modify":
        (ws_path / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    else:
        (ws_path / "main.py").unlink()
    _finish(db, run_id, "archived")
    check = mgr.workspace_discard_check(ws["id"])
    assert check["discardable"] is False and "never discard unmerged work" in check["reason"]
    with pytest.raises(WorkspaceCleanupGuardError, match="never discard unmerged work"):
        mgr.workspace_discard(ws["id"], "should refuse")
    assert ws_path.exists() and db.get_workspace(ws["id"])["status"] == "active"


def test_merged_workspace_is_left_to_cleanup(env):
    project, db, mgr, dispatcher = env
    run_id = _run(dispatcher, project)
    db.connect().execute("UPDATE runs SET workflow_version=0 WHERE id=?", (run_id,))
    ws = mgr.workspace_create(run_id, "author", copy_env=False)
    assert mgr.workspace_merge(ws["id"])["merged"] is True
    _finish(db, run_id, "completed")
    check = mgr.workspace_discard_check(ws["id"])
    assert check["discardable"] is False and "workspace_cleanup" in check["reason"]


def test_unresolved_native_action_and_working_author_block_discard(env):
    project, db, mgr, dispatcher = env
    run_id = _run(dispatcher, project)
    dispatcher.dispatch("task_packet_save", {
        "run_id": run_id, "title": "T", "goal": "G",
        "file_ownership": {"coder_1": ["main.py"]}, "acceptance_criteria": ["AC-1"],
    })
    prep = dispatcher.dispatch("workflow_prepare_dispatch", {
        "run_id": run_id, "create_workspace": True, "owner": "coder_1", "task_name": "coder_1",
        "verifier": "verifier_1", "step_key": "s1", "criteria": ["AC-1"], "instruction": "do it",
    })
    ws_id = prep["workspace"]["id"]
    _finish(db, run_id)
    blocked = mgr.workspace_discard_check(ws_id)
    assert blocked["discardable"] is False and blocked["unresolved_actions"]
    assert blocked["unresolved_actions"][0]["status"] == "invoking"

    # The spawn is confirmed (author started) and the worker is recorded as working.
    with db.transaction() as conn:
        conn.execute("UPDATE native_actions SET status='confirmed' WHERE action_id=?", (prep["action_id"],))
        conn.execute("UPDATE workers SET agent_id='/root/coder_1', pending_agent_path=NULL, status='working' "
                     "WHERE run_id=? AND name='coder_1'", (run_id,))
    working = mgr.workspace_discard_check(ws_id)
    assert working["discardable"] is False and "still recorded as working" in working["reason"]
    db.update_worker_status(run_id=run_id, worker_name="coder_1", status="stopped")
    assert mgr.workspace_discard_check(ws_id)["discardable"] is True


def test_git_clone_discard_requires_head_at_base_and_clean_tree(tmp_path):
    repo = _init_git_repo(tmp_path / "repo")
    settings = Settings(data_dir=tmp_path / "data", port_range=(29200, 29220))
    db = Database(settings=settings)
    try:
        mgr = WorkspaceManager(db, settings)
        run = db.create_run(mode="swarm", title="git", project_root=repo)
        committed = mgr.workspace_create(run["id"], "committer", copy_env=False)
        untouched = mgr.workspace_create(run["id"], "idle", copy_env=False)
        assert committed["mode"] == "git_clone"
        path = Path(committed["path"])
        (path / "app.py").write_text("changed = True\n", encoding="utf-8")
        _git(path, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-am", "work")
        _finish(db, run["id"], "archived")
        refused = mgr.workspace_discard_check(committed["id"])
        assert refused["discardable"] is False and "commits beyond base" in refused["reason"]
        (Path(untouched["path"]) / "scratch.txt").write_text("x", encoding="utf-8")
        assert mgr.workspace_discard_check(untouched["id"])["discardable"] is False
        (Path(untouched["path"]) / "scratch.txt").unlink()
        assert mgr.workspace_discard(untouched["id"], "never used")["discarded"] is True
        assert not Path(untouched["path"]).exists() and path.exists()
    finally:
        db.close()


def test_run_update_archive_reports_residuals(env):
    project, db, mgr, dispatcher = env
    run_id = _run(dispatcher, project)
    clean = mgr.workspace_create(run_id, "clean", copy_env=False)
    dirty = mgr.workspace_create(run_id, "dirty", copy_env=False)
    (Path(dirty["path"]) / "main.py").write_text("VALUE = 9\n", encoding="utf-8")
    result = dispatcher.dispatch("run_update", {"run_id": run_id, "status": "archived"})
    residuals = {item["workspace_id"]: item for item in result["residuals"]["workspaces"]}
    assert residuals[clean["id"]]["discardable"] is True
    assert residuals[clean["id"]]["next_step"] == "workspace_discard"
    assert residuals[dirty["id"]]["discardable"] is False
    assert "never deleted automatically" in residuals[dirty["id"]]["next_step"]
    assert result["residuals"]["native_actions"] == [] and result["residuals"]["clean"] is False
    plain = dispatcher.dispatch("run_update", {"run_id": run_id, "title": "renamed"})
    assert "residuals" not in plain


def test_storage_report_and_dashboard_endpoint_require_confirmation(env, tmp_path):
    from starlette.testclient import TestClient
    from orchestrator.daemon import create_app
    from orchestrator.gc import collect_storage_report

    project, db, mgr, dispatcher = env
    run_id = _run(dispatcher, project)
    ws = mgr.workspace_create(run_id, "idle", copy_env=False)
    _finish(db, run_id)
    report = collect_storage_report(db, db.settings, workspace_mgr=mgr)
    item = next(i for i in report["workspaces"]["items"] if i["workspace_id"] == ws["id"])
    assert item["category"] == "discardable" and report["workspaces"]["discardable_count"] == 1

    app = create_app(db.settings, start_watchdog=False)
    with TestClient(app) as client:
        refused = client.post(f"/api/workspaces/{ws['id']}/discard", json={})
        assert refused.status_code == 400 and Path(ws["path"]).exists()
        done = client.post(f"/api/workspaces/{ws['id']}/discard", json={"confirm": True})
        assert done.status_code == 200, done.text
        assert done.json()["discarded"] is True and "snapshot_manifest" not in (done.json()["workspace"] or {})
    assert not Path(ws["path"]).exists()


# ---------------------------------------------------------------------------
# Liveness: MCP calls refresh heartbeats; role-based stall defaults
# ---------------------------------------------------------------------------

def _age(db, run_id, name, seconds):
    with db.transaction() as conn:
        conn.execute("UPDATE workers SET last_heartbeat_at=? WHERE run_id=? AND name=?",
                     (time.time() - seconds, run_id, name))


def test_worker_mcp_calls_refresh_heartbeat_but_never_status(env):
    project, db, mgr, dispatcher = env
    run_id = _run(dispatcher, project)
    dispatcher.dispatch("worker_register", {"run_id": run_id, "name": "w1", "role": "swarm_worker",
                                            "agent_id": "/root/w1", "status": "working"})
    dispatcher.dispatch("worker_register", {"run_id": run_id, "name": "idle", "role": "coder",
                                            "agent_id": "/root/idle", "status": "waiting"})
    _age(db, run_id, "w1", 5000)
    _age(db, run_id, "idle", 5000)
    assert {w["name"] for w in db.find_stalled_workers(run_id=run_id)} == {"w1"}
    dispatcher.dispatch("board_post", {"run_id": run_id, "sender": "w1", "summary": "still building"})
    assert db.find_stalled_workers(run_id=run_id) == []
    assert db.get_worker_by_name(run_id, "w1")["status"] == "working"
    dispatcher.dispatch("board_read", {"run_id": run_id, "reader_name": "idle"})
    idle = db.get_worker_by_name(run_id, "idle")
    assert idle["status"] == "waiting" and time.time() - idle["last_heartbeat_at"] > 4000

    # A coordinator naming a worker as the *target* of an action is not that worker's liveness.
    _age(db, run_id, "w1", 5000)
    dispatcher.dispatch("board_post", {"run_id": run_id, "sender": "coordinator", "mentions": ["w1"], "summary": "ping"})
    assert [w["name"] for w in db.find_stalled_workers(run_id=run_id)] == ["w1"]


def test_role_based_default_stall_threshold(env):
    project, db, mgr, dispatcher = env
    run_id = _run(dispatcher, project)
    base = db.settings.stall_sec
    for name, role, factor in (("v", "verifier", 3), ("c", "coder", 2), ("s", "swarm_worker", 2), ("p", "planner", 1)):
        worker = dispatcher.dispatch("worker_register", {"run_id": run_id, "name": name, "role": role})
        assert worker["stall_threshold_sec"] == int(base) * factor == default_stall_threshold(base, role)
    explicit = dispatcher.dispatch("worker_register", {"run_id": run_id, "name": "x", "role": "verifier",
                                                       "stall_threshold_sec": 42})
    assert explicit["stall_threshold_sec"] == 42


# ---------------------------------------------------------------------------
# Structured reasoning-effort plan and observation
# ---------------------------------------------------------------------------

PLAN = {"target_effort": "medium", "selection_reason": "bounded read-only contract check", "planned_source": "explicit"}


def test_effort_plan_via_register_and_dispatch_options(env):
    project, db, mgr, dispatcher = env
    run_id = _run(dispatcher, project)
    worker = dispatcher.dispatch("worker_register", {"run_id": run_id, "name": "helper", "role": "investigator",
                                                     "effort_plan": PLAN})
    assert worker["effort_plan"]["target_effort"] == "medium"
    assert worker["effort_plan"]["planned_source"] == "explicit" and worker["effort_plan"]["recorded_at"] > 0
    with pytest.raises(ValueError, match="planned_source"):
        dispatcher.dispatch("worker_register", {"run_id": run_id, "name": "bad", "role": "coder",
                                                "effort_plan": {**PLAN, "planned_source": "guess"}})
    with pytest.raises(ValueError, match="Unknown effort_plan keys"):
        dispatcher.dispatch("worker_register", {"run_id": run_id, "name": "bad", "role": "coder",
                                                "effort_plan": {**PLAN, "model": "x"}})

    dispatcher.dispatch("task_packet_save", {"run_id": run_id, "title": "T", "goal": "G",
        "file_ownership": {"coder_1": ["main.py"]}, "acceptance_criteria": ["AC-1"]})
    high = {"target_effort": "high", "selection_reason": "cross-module change", "planned_source": "explicit"}
    prep = dispatcher.dispatch("workflow_prepare_dispatch", {
        "run_id": run_id, "create_workspace": True, "owner": "coder_1", "task_name": "coder_1",
        "verifier": "verifier_1", "step_key": "s1", "criteria": ["AC-1"], "instruction": "do it",
        "options": {"effort_plan": high},
    })
    assert db.get_worker_by_name(run_id, "coder_1")["effort_plan"]["target_effort"] == "high"

    observation = {"observed_effort": "high", "observed_source": "explicit",
                   "evidence_ref": "rollout first turn #1", "evidence_kind": "first_turn"}
    recorded = dispatcher.dispatch("native_action_record_call", {
        "run_id": run_id, "action_id": prep["action_id"], "claim_token": prep["claim_token"],
        "expected_revision": prep["expected_revision"], "outcome": "confirmed",
        "raw_response": {"task_name": "/root/coder_1"}, "observed_effort": observation,
    })
    assert recorded["status"] == "confirmed"
    stored = db.get_worker_by_name(run_id, "coder_1")["effort_observation"]
    assert stored["observed_effort"] == "high" and stored["evidence_kind"] == "first_turn"


def test_effort_observation_validation(env):
    from orchestrator.db import normalize_effort_observation
    assert normalize_effort_observation({"observed_effort": "unverified"})["observed_source"] == "unknown"
    with pytest.raises(ValueError, match="evidence_ref is required"):
        normalize_effort_observation({"observed_effort": "high", "observed_source": "explicit"})
    with pytest.raises(ValueError, match="evidence_kind"):
        normalize_effort_observation({"observed_effort": "high", "evidence_ref": "r", "evidence_kind": "vibes"})
    with pytest.raises(ValueError, match="at most 32"):
        normalize_effort_observation({"observed_effort": "x" * 40, "evidence_ref": "r"})


def test_effort_columns_are_added_to_existing_databases(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    db = Database(settings=settings)
    run = db.create_run(mode="boost", title="t", project_root=tmp_path)
    db.upsert_worker(run["id"], "old", role="coder")
    conn = db.connect()
    conn.execute("ALTER TABLE workers DROP COLUMN effort_plan_json")
    conn.execute("ALTER TABLE workers DROP COLUMN effort_observation_json")
    assert db.get_worker_by_name(run["id"], "old")["effort_plan"] is None
    db.close()
    reopened = Database(settings=settings)
    try:
        columns = {r["name"] for r in reopened.connect().execute("PRAGMA table_info(workers)")}
        assert {"effort_plan_json", "effort_observation_json"} <= columns
        assert reopened.migrate() == 10
    finally:
        reopened.close()
