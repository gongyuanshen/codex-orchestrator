"""M11 core-ext tests: app_server_pipeline honest identity, gates, macro RPCs, and 16 capabilities."""
from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from orchestrator import __version__
from orchestrator.board import BoardService
from orchestrator.config import Settings
from orchestrator.daemon import create_app
from orchestrator.db import APPSERVER_AGENT_ID_RE, SCHEMA_VERSION, Database
from orchestrator.inbox import InboxConflictError
from orchestrator.mcp_shim import REQUIRED_CAPABILITIES
from orchestrator.native_actions import NativeActionConflictError
from orchestrator.rpc_handlers import RpcDispatcher, get_tool_definitions
from orchestrator.verify_runner import collect
from orchestrator.workflow import WorkflowConflictError
from orchestrator.workspace_mgr import WorkspaceManager


def _init_git_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=repo, check=True)
    (repo / "calc.py").write_text("def add(a: int, b: int) -> int:\n    return 0\n", encoding="utf-8")
    (repo / "mul.py").write_text("def mul(a: int, b: int) -> int:\n    return 0\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)


@pytest.fixture
def m11_env(tmp_path: Path):
    project = tmp_path / "repo"
    _init_git_repo(project)
    settings = Settings(data_dir=tmp_path / "data", port_range=(29100, 29180))
    db = Database(settings=settings)
    board = BoardService(db, settings)
    ws_mgr = WorkspaceManager(db, settings)
    dispatcher = RpcDispatcher(db, board, ws_mgr)
    yield tmp_path, project, settings, db, board, ws_mgr, dispatcher
    db.close()


def test_m11_capabilities_version_and_zero_schema_ddl_bloat(m11_env):
    _, project, settings, db, _, _, dispatcher = m11_env
    assert __version__ == "0.6.0"
    assert SCHEMA_VERSION == 8
    assert "pipeline_runner_v1" in REQUIRED_CAPABILITIES
    assert len(REQUIRED_CAPABILITIES) == 16

    # Verify tool schemas expose app_server_pipeline in both workflow_step_save and workflow_prepare_dispatch.
    schemas = {t["name"]: t["inputSchema"] for t in get_tool_definitions()}
    expected_enum = ["native", "coordinator_local", "app_server_pipeline"]
    assert schemas["workflow_step_save"]["properties"]["execution_kind"]["enum"] == expected_enum
    assert schemas["workflow_prepare_dispatch"]["properties"]["execution_kind"]["enum"] == expected_enum
    assert "observation" in schemas["workflow_prepare_dispatch"]["properties"]

    # Verify zero extra tables in SQLite schema8.
    with db.read_connection() as conn:
        tables = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    assert tables == {
        "schema_migrations",
        "projects",
        "runs",
        "task_packets",
        "workspaces",
        "workers",
        "session_run_bindings",
        "board_messages",
        "read_cursors",
        "worker_controls",
        "workflow_steps",
        "workflow_artifacts",
        "verification_records",
        "inbox_messages",
        "native_actions",
    }

    # Verify /api/health and /api/runs/{id}/snapshot over ASGI transport.
    run = dispatcher.dispatch(
        "run_create",
        {"mode": "swarm", "title": "Health & Snapshot Check", "project_root": str(project)},
    )
    run_id = run["id"]
    assert run["workflow_version"] == 5
    dispatcher.dispatch(
        "task_packet_save",
        {
            "run_id": run_id,
            "title": "Pkt",
            "goal": "Projection test",
            "file_ownership": {"author-a": ["calc.py"]},
            "acceptance_criteria": ["AC-1"],
        },
    )
    dispatcher.dispatch("worker_register", {"run_id": run_id, "name": "author-a", "role": "swarm_worker"})
    dispatcher.dispatch("worker_register", {"run_id": run_id, "name": "verifier-a", "role": "verifier"})
    saved = dispatcher.dispatch(
        "workflow_step_save",
        {
            "run_id": run_id,
            "step_key": "calc-step",
            "owner": "author-a",
            "verifier": "verifier-a",
            "execution_kind": "app_server_pipeline",
        },
    )
    assert saved["execution_kind"] == "app_server_pipeline"

    full_status = dispatcher.dispatch("board_status", {"run_id": run_id, "compact": False})
    compact_status = dispatcher.dispatch("board_status", {"run_id": run_id, "compact": True})
    assert full_status["workflow"]["steps"][0]["execution_kind"] == "app_server_pipeline"
    assert compact_status["workflow"]["steps"][0]["execution_kind"] == "app_server_pipeline"

    async def _probe_http() -> None:
        app = create_app(settings, db=db, start_watchdog=False)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                resp = await client.get("/api/health")
                assert resp.status_code == 200
                body = resp.json()
                assert body["software_version"] == "0.6.0"
                assert body["schema_version"] == 8
                assert set(body["capabilities"]) == set(REQUIRED_CAPABILITIES)
                assert len(body["capabilities"]) == 16

                snap = await client.get(f"/api/runs/{run_id}/snapshot")
                assert snap.status_code == 200
                snap_body = snap.json()
                assert snap_body["workflow"]["steps"][0]["execution_kind"] == "app_server_pipeline"

    asyncio.run(_probe_http())


def test_m11_honest_identity_rejects_root_spoofing_and_cross_kind_mixing(m11_env):
    _, project, _, db, _, _, dispatcher = m11_env

    # Regex unit checks
    assert APPSERVER_AGENT_ID_RE.fullmatch("appserver://thr_019abc-DEF_1.2:3")
    assert not APPSERVER_AGENT_ID_RE.fullmatch("appserver://")
    assert not APPSERVER_AGENT_ID_RE.fullmatch("appserver://nested/slash")
    assert not APPSERVER_AGENT_ID_RE.fullmatch("appserver://bad space")
    assert not APPSERVER_AGENT_ID_RE.fullmatch("appserver://" + "a" * 121)
    assert not APPSERVER_AGENT_ID_RE.fullmatch("/root/author_1")

    run = dispatcher.dispatch(
        "run_create",
        {"mode": "teamwork", "title": "Honest Identity Enforcement", "project_root": str(project)},
    )
    run_id = run["id"]
    dispatcher.dispatch(
        "task_packet_save",
        {
            "run_id": run_id,
            "title": "Identity Rules",
            "goal": "Enforce strict attribution",
            "file_ownership": {
                "native_owner": ["calc.py"],
                "pipe_owner": ["mul.py"],
                "spoof_owner": ["calc.py"],
            },
            "acceptance_criteria": ["AC-1", "AC-2"],
        },
    )

    # 1) Malformed appserver:// URI rejected on worker_register.
    for bad_id in ("appserver://", "appserver://bad/path", "appserver://has space", "appserver://" + "x" * 121):
        with pytest.raises(ValueError, match="appserver://"):
            dispatcher.dispatch(
                "worker_register",
                {"run_id": run_id, "name": "bad_w", "role": "coder", "agent_id": bad_id},
            )

    # 2) Simultaneous appserver:// agent_id + task_name / pending_agent_path rejected.
    with pytest.raises(ValueError):
        dispatcher.dispatch(
            "worker_register",
            {
                "run_id": run_id,
                "name": "bad_combo",
                "role": "coder",
                "agent_id": "appserver://thr_combo",
                "task_name": "bad_combo",
            },
        )

    # 3) /root/... worker or pre-spawn task_name rejected on app_server_pipeline step!
    dispatcher.dispatch(
        "worker_register",
        {"run_id": run_id, "name": "spoof_owner", "role": "coder", "agent_id": "/root/spoof_owner"},
    )
    dispatcher.dispatch(
        "worker_register",
        {"run_id": run_id, "name": "pipe_ver", "role": "verifier", "agent_id": "appserver://thr_ver_1"},
    )
    with pytest.raises((ValueError, WorkflowConflictError), match="appserver://"):
        dispatcher.dispatch(
            "workflow_step_save",
            {
                "run_id": run_id,
                "step_key": "spoof-root-on-pipeline",
                "owner": "spoof_owner",
                "verifier": "pipe_ver",
                "criteria": ["AC-1"],
                "execution_kind": "app_server_pipeline",
            },
        )

    # Pre-spawn task_name via workflow_prepare_dispatch on app_server_pipeline rejected!
    with pytest.raises((ValueError, WorkflowConflictError)):
        dispatcher.dispatch(
            "workflow_prepare_dispatch",
            {
                "run_id": run_id,
                "owner": "pipe_owner",
                "task_name": "pipe_owner",
                "verifier": "pipe_ver",
                "step_key": "spoof-prespawn",
                "criteria": ["AC-2"],
                "execution_kind": "app_server_pipeline",
                "instruction": "Should fail",
            },
        )

    # 4) Planned app_server_pipeline step created with unbound workers -> later spoofing /root/... or task_name rejected!
    dispatcher.dispatch("worker_register", {"run_id": run_id, "name": "pipe_owner", "role": "coder"})
    dispatcher.dispatch("worker_register", {"run_id": run_id, "name": "pipe_lazy_ver", "role": "verifier"})
    pipe_step = dispatcher.dispatch(
        "workflow_step_save",
        {
            "run_id": run_id,
            "step_key": "valid-pipe-step",
            "owner": "pipe_owner",
            "verifier": "pipe_lazy_ver",
            "criteria": ["AC-2"],
            "execution_kind": "app_server_pipeline",
        },
    )
    assert pipe_step["state"] == "planned"
    assert pipe_step["owner_agent_id"] == ""
    assert pipe_step["verifier_agent_id"] == ""

    with pytest.raises((ValueError, WorkflowConflictError)):
        dispatcher.dispatch(
            "worker_register",
            {"run_id": run_id, "name": "pipe_owner", "role": "coder", "task_name": "pipe_owner"},
        )
    with pytest.raises((ValueError, WorkflowConflictError)):
        dispatcher.dispatch(
            "worker_register",
            {"run_id": run_id, "name": "pipe_owner", "role": "coder", "agent_id": "/root/pipe_owner"},
        )

    # 5) appserver:// agent_id rejected on execution_kind="native" and "coordinator_local"!
    dispatcher.dispatch(
        "worker_register",
        {"run_id": run_id, "name": "native_owner", "role": "coder", "agent_id": "/root/native_owner"},
    )
    with pytest.raises((ValueError, WorkflowConflictError), match="appserver://"):
        dispatcher.dispatch(
            "workflow_step_save",
            {
                "run_id": run_id,
                "step_key": "cross-spoof-ver",
                "owner": "native_owner",
                "verifier": "pipe_ver",
                "criteria": ["AC-1"],
                "execution_kind": "native",
            },
        )

    # 6) Mixing the SAME worker (even while unbound!) across native and app_server_pipeline steps in the same run rejected!
    with pytest.raises(WorkflowConflictError, match="mix"):
        dispatcher.dispatch(
            "workflow_step_save",
            {
                "run_id": run_id,
                "step_key": "mix-shared-verifier",
                "owner": "native_owner",
                "verifier": "pipe_lazy_ver",
                "criteria": ["AC-1"],
                "execution_kind": "native",
            },
        )

    # Also test native step with unbound lazy verifier -> later attempting to bind appserver:// to that native verifier rejected!
    dispatcher.dispatch("worker_register", {"run_id": run_id, "name": "native_lazy_ver", "role": "verifier"})
    dispatcher.dispatch(
        "workflow_step_save",
        {
            "run_id": run_id,
            "step_key": "clean-native-step",
            "owner": "native_owner",
            "verifier": "native_lazy_ver",
            "criteria": ["AC-1"],
            "execution_kind": "native",
        },
    )
    with pytest.raises((ValueError, WorkflowConflictError), match="appserver://"):
        dispatcher.dispatch(
            "worker_register",
            {
                "run_id": run_id,
                "name": "native_lazy_ver",
                "role": "verifier",
                "agent_id": "appserver://thr_illegal_on_native",
            },
        )

    # 7) Coordinator-local rejecting appserver:// in Boost mode, and workflow_version < 5 rejecting app_server_pipeline.
    boost_run = dispatcher.dispatch(
        "run_create",
        {"mode": "boost", "title": "Boost Local & Legacy Check", "project_root": str(project)},
    )
    b_id = boost_run["id"]
    dispatcher.dispatch(
        "task_packet_save",
        {
            "run_id": b_id,
            "title": "Boost Pkt",
            "goal": "Check coordinator_local and workflow4 guard",
            "file_ownership": {"b_owner": ["calc.py"]},
            "acceptance_criteria": ["AC-B"],
        },
    )
    dispatcher.dispatch(
        "worker_register",
        {"run_id": b_id, "name": "b_owner", "role": "coder", "agent_id": "appserver://thr_boost_1"},
    )
    dispatcher.dispatch(
        "worker_register",
        {"run_id": b_id, "name": "b_ver", "role": "verifier", "agent_id": "/root/b_ver"},
    )
    with pytest.raises((ValueError, WorkflowConflictError)):
        dispatcher.dispatch(
            "workflow_step_save",
            {
                "run_id": b_id,
                "step_key": "bad-local",
                "owner": "b_owner",
                "verifier": "b_ver",
                "execution_kind": "coordinator_local",
            },
        )

    # Simulate legacy workflow_version=4 run: app_server_pipeline must be rejected!
    legacy_run = dispatcher.dispatch(
        "run_create",
        {"mode": "swarm", "title": "Legacy WF4 Run", "project_root": str(project)},
    )
    leg_id = legacy_run["id"]
    with db.transaction() as conn:
        conn.execute("UPDATE runs SET workflow_version = 4 WHERE id = ?;", (leg_id,))
    dispatcher.dispatch(
        "task_packet_save",
        {
            "run_id": leg_id,
            "title": "WF4",
            "goal": "Reject app_server_pipeline on wf4",
            "file_ownership": {"w4_owner": ["calc.py"]},
            "acceptance_criteria": ["AC-4"],
        },
    )
    with pytest.raises((ValueError, WorkflowConflictError), match="workflow5"):
        dispatcher.dispatch(
            "worker_register",
            {"run_id": leg_id, "name": "w4_owner", "role": "swarm_worker", "agent_id": "appserver://thr_wf4"},
        )


def test_m11_self_verification_and_skipping_verify_receipt_rejected_plus_boost_high_complexity_gates(m11_env):
    tmp_path, project, settings, db, _, _, dispatcher = m11_env
    notes_file = tmp_path / "blind_review_notes.md"
    notes_file.write_text("Structured blind verifier notes.\n", encoding="utf-8")

    run = dispatcher.dispatch(
        "run_create",
        {"mode": "boost", "title": "Boost High-Complexity Pipeline Gates", "project_root": str(project)},
    )
    run_id = run["id"]
    dispatcher.dispatch(
        "task_packet_save",
        {
            "run_id": run_id,
            "title": "Calc Contract",
            "goal": "Implement add(a, b)",
            "file_ownership": {"author-boost": ["calc.py"], "dup-author": ["calc.py"]},
            "acceptance_criteria": ["AC-CALC"],
        },
    )

    # 1) Pre-bound identical appserver:// thread IDs on author and verifier MUST be rejected!
    dispatcher.dispatch(
        "worker_register",
        {"run_id": run_id, "name": "dup-author", "role": "coder", "agent_id": "appserver://thr_same"},
    )
    dispatcher.dispatch(
        "worker_register",
        {"run_id": run_id, "name": "dup-verifier", "role": "verifier", "agent_id": "appserver://thr_same"},
    )
    with pytest.raises((ValueError, WorkflowConflictError), match="different"):
        dispatcher.dispatch(
            "workflow_step_save",
            {
                "run_id": run_id,
                "step_key": "self-verify-prebound",
                "owner": "dup-author",
                "verifier": "dup-verifier",
                "execution_kind": "app_server_pipeline",
            },
        )

    # 2) DAG planned step -> author binds thr_author -> lazy verifier attempting to bind SAME thr_author MUST be rejected!
    dispatcher.dispatch("worker_register", {"run_id": run_id, "name": "author-boost", "role": "coder"})
    dispatcher.dispatch("worker_register", {"run_id": run_id, "name": "verifier-boost", "role": "verifier"})
    step = dispatcher.dispatch(
        "workflow_step_save",
        {
            "run_id": run_id,
            "step_key": "calc-high",
            "owner": "author-boost",
            "verifier": "verifier-boost",
            "complexity": "high",
            "execution_kind": "app_server_pipeline",
        },
    )
    step_id = step["id"]

    # Dispatch before binding author appserver://<thread_id> MUST fail closed!
    with pytest.raises(WorkflowConflictError):
        dispatcher.dispatch(
            "workflow_step_advance",
            {"step_id": step_id, "action": "dispatch", "expected_revision": step["revision"]},
        )

    prep_author = dispatcher.dispatch(
        "workflow_prepare_dispatch",
        {
            "run_id": run_id,
            "step_id": step_id,
            "dispatch_action": "dispatch",
            "create_workspace": True,
            "agent_id": "appserver://thr_author_boost",
            "instruction": "Implement add(a, b) in calc.py",
        },
    )
    assert prep_author["action_type"] == "followup_task"
    assert prep_author["target_agent_path"] == "appserver://thr_author_boost"
    assert prep_author["step"]["workspace_id"] == prep_author["workspace"]["id"]
    assert prep_author["step"]["owner_agent_id"] == "appserver://thr_author_boost"

    rec = dispatcher.dispatch(
        "native_action_record_call",
        {
            "run_id": run_id,
            "action_id": prep_author["action_id"],
            "claim_token": prep_author["claim_token"],
            "expected_revision": prep_author["expected_revision"],
            "outcome": "confirmed",
            "raw_response": {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"thread": {"id": "thr_author_boost"}, "turn": {"id": "turn_1"}},
            },
        },
    )
    assert rec["status"] == "confirmed"
    receipt_on_disk = Path(rec["evidence_ref"])
    assert receipt_on_disk.is_file()
    assert hashlib.sha256(receipt_on_disk.read_bytes()).hexdigest() == rec["evidence_digest"]

    # Author implements working code and commits in isolated workspace.
    dispatcher.dispatch(
        "workflow_author_turn",
        {"run_id": run_id, "step_id": step_id, "message_id": prep_author["message_id"], "phase": "accept"},
    )
    ws_path = Path(prep_author["workspace"]["path"])
    (ws_path / "calc.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
    subprocess.run(["git", "add", "calc.py"], cwd=ws_path, check=True)
    subprocess.run(["git", "commit", "-qm", "implement add"], cwd=ws_path, check=True)
    dispatcher.dispatch(
        "workflow_author_turn",
        {
            "run_id": run_id,
            "step_id": step_id,
            "message_id": prep_author["message_id"],
            "phase": "submit",
            "summary": "Implemented add()",
        },
    )

    # Lazy self-verification attempt via request_verify or worker_register MUST fail and roll back!
    with pytest.raises((ValueError, WorkflowConflictError), match="different"):
        dispatcher.dispatch(
            "workflow_prepare_dispatch",
            {
                "run_id": run_id,
                "dispatch_action": "request_verify",
                "step_id": step_id,
                "verifier_agent_id": "appserver://thr_author_boost",
                "instruction": "Illegal self review",
            },
        )
    assert db.get_worker_by_name(run_id, "verifier-boost")["agent_id"] is None

    # Bind distinct independent verifier lazily via workflow_prepare_dispatch(request_verify, verifier_agent_id=...).
    prep_ver = dispatcher.dispatch(
        "workflow_prepare_dispatch",
        {
            "run_id": run_id,
            "dispatch_action": "request_verify",
            "step_id": step_id,
            "verifier_agent_id": "appserver://thr_ver_boost",
            "instruction": "Verify candidate calc.py",
        },
    )
    assert prep_ver["action_type"] == "followup_task"
    assert prep_ver["target_agent_path"] == "appserver://thr_ver_boost"
    assert prep_ver["step"]["verifier_agent_id"] == "appserver://thr_ver_boost"
    assert prep_ver["step"]["checkpoint"]["frozen_verifier_binding"] == {
        "verifier": "verifier-boost",
        "verifier_agent_id": "appserver://thr_ver_boost",
    }
    assert prep_ver["step"]["checkpoint"]["original_bindings"]["verifier_agent_id"] == "appserver://thr_ver_boost"

    dispatcher.dispatch(
        "native_action_record_call",
        {
            "run_id": run_id,
            "action_id": prep_ver["action_id"],
            "claim_token": prep_ver["claim_token"],
            "expected_revision": prep_ver["expected_revision"],
            "outcome": "confirmed",
            "raw_response": {"jsonrpc": "2.0", "id": 2, "result": {"thread": {"id": "thr_ver_boost"}}},
        },
    )
    dispatcher.inbox.ack(prep_ver["message_id"], "verifier-boost", "accepted", "reviewing")

    cand = dispatcher.dispatch(
        "artifact_capture",
        {
            "step_id": step_id,
            "scope": "candidate",
            "env_profile": "windows_minimal_py" if sys.platform == "win32" else "posix_minimal_py",
            "verification_commands": [
                {
                    "id": "pytest-calc",
                    "argv": [
                        sys.executable,
                        "-B",
                        "-c",
                        f"import sys; sys.path.insert(0, {str(ws_path)!r}); import calc; assert calc.add(4, 5) == 9",
                    ],
                    "cwd": str(settings.data_dir),
                    "timeout_sec": 10,
                    "criteria": ["AC-CALC"],
                }
            ],
        },
    )
    out_dir = tmp_path / "cand-receipt"
    collect(Path(cand["manifest_path"]), out_dir)
    receipt_path = str(out_dir / "receipt.json")

    # 3) Boost requires independent verifier challenge BEFORE candidate verification_record!
    with pytest.raises(WorkflowConflictError, match="challenge"):
        dispatcher.dispatch(
            "verification_record",
            {
                "step_id": step_id,
                "artifact_id": cand["id"],
                "verifier": "verifier-boost",
                "verdict": "PASS",
                "criteria_results": [{"criterion": "AC-CALC", "status": "PASS"}],
                "evidence_files": [str(notes_file)],
                "receipt_files": [receipt_path],
                "review_dimensions": {
                    "specification": "PASS",
                    "code": "PASS",
                    "stress": "PASS",
                    "evidence": "PASS",
                },
                "key": "no-challenge-yet",
            },
        )

    cur_step = dispatcher.workflow.get_step(step_id)
    # Author cannot record challenge!
    with pytest.raises(WorkflowConflictError):
        dispatcher.dispatch(
            "workflow_step_advance",
            {
                "step_id": step_id,
                "action": "challenge",
                "expected_revision": cur_step["revision"],
                "agent_id": "appserver://thr_author_boost",
                "artifact_id": cand["id"],
                "evidence": "Author pretending to challenge",
            },
        )

    dispatcher.dispatch(
        "workflow_step_advance",
        {
            "step_id": step_id,
            "action": "challenge",
            "expected_revision": cur_step["revision"],
            "agent_id": "appserver://thr_ver_boost",
            "artifact_id": cand["id"],
            "evidence": "Verified edge cases add(0,0), negatives, and overflow behavior",
        },
    )

    # 4) Skipping tri-orchestrator-verify receipt_files MUST be rejected!
    with pytest.raises(ValueError, match="receipt_files"):
        dispatcher.dispatch(
            "verification_record",
            {
                "step_id": step_id,
                "artifact_id": cand["id"],
                "verifier": "verifier-boost",
                "verdict": "PASS",
                "criteria_results": [{"criterion": "AC-CALC", "status": "PASS"}],
                "evidence_files": [str(notes_file)],
                "review_dimensions": {
                    "specification": "PASS",
                    "code": "PASS",
                    "stress": "PASS",
                    "evidence": "PASS",
                },
                "key": "missing-receipt",
            },
        )

    # 5) High-complexity requires all 4 review_dimensions == PASS!
    with pytest.raises(WorkflowConflictError, match="review dimensions"):
        dispatcher.dispatch(
            "verification_record",
            {
                "step_id": step_id,
                "artifact_id": cand["id"],
                "verifier": "verifier-boost",
                "verdict": "PASS",
                "criteria_results": [{"criterion": "AC-CALC", "status": "PASS"}],
                "evidence_files": [str(notes_file)],
                "receipt_files": [receipt_path],
                "key": "missing-4d",
            },
        )

    # Valid PASS with real receipt + 4D review_dimensions transitions to ready_to_merge.
    ver_rec = dispatcher.dispatch(
        "verification_record",
        {
            "step_id": step_id,
            "artifact_id": cand["id"],
            "verifier": "verifier-boost",
            "verdict": "PASS",
            "criteria_results": [{"criterion": "AC-CALC", "status": "PASS"}],
            "evidence_files": [str(notes_file)],
            "receipt_files": [receipt_path],
            "review_dimensions": {
                "specification": "PASS",
                "code": "PASS",
                "stress": "PASS",
                "evidence": "PASS",
            },
            "key": "valid-cand-pass",
        },
    )
    assert ver_rec["verdict"] == "PASS"
    assert dispatcher.workflow.get_step(step_id)["state"] == "ready_to_merge"


def test_m11_dag_planned_to_rework_cap_needs_decision_and_parallel_ownership_gates(m11_env):
    tmp_path, project, settings, db, _, _, dispatcher = m11_env
    notes_file = tmp_path / "dag_verifier_notes.md"
    notes_file.write_text("Verifier audit log.\n", encoding="utf-8")
    env_prof = "windows_minimal_py" if sys.platform == "win32" else "posix_minimal_py"

    # Part A: 4 consecutive failures (initial + 3 reworks) -> halts at needs_decision!
    run_cap = dispatcher.dispatch(
        "run_create",
        {"mode": "swarm", "title": "M11 Rework Cap 3 -> needs_decision", "project_root": str(project)},
    )
    cap_run_id = run_cap["id"]
    dispatcher.dispatch(
        "task_packet_save",
        {
            "run_id": cap_run_id,
            "title": "Cap Packet",
            "goal": "Test 3-rework ceiling",
            "file_ownership": {"author-cap": ["calc.py"], "author-overlap": ["calc.py"]},
            "acceptance_criteria": ["AC-CAP"],
        },
    )
    dispatcher.dispatch("worker_register", {"run_id": cap_run_id, "name": "author-cap", "role": "swarm_worker"})
    dispatcher.dispatch("worker_register", {"run_id": cap_run_id, "name": "ver-cap", "role": "verifier"})
    dispatcher.dispatch("worker_register", {"run_id": cap_run_id, "name": "author-overlap", "role": "swarm_worker"})
    dispatcher.dispatch("worker_register", {"run_id": cap_run_id, "name": "ver-overlap", "role": "verifier"})

    step_cap = dispatcher.dispatch(
        "workflow_step_save",
        {
            "run_id": cap_run_id,
            "step_key": "cap-step",
            "owner": "author-cap",
            "verifier": "ver-cap",
            "execution_kind": "app_server_pipeline",
        },
    )
    cap_step_id = step_cap["id"]

    # Planned app_server_pipeline step has clean resume_context with zero binding_issues.
    rc0 = dispatcher.workflow.resume_context(cap_run_id)
    assert rc0["binding_issues"] == []

    step_overlap = dispatcher.dispatch(
        "workflow_step_save",
        {
            "run_id": cap_run_id,
            "step_key": "overlap-step",
            "owner": "author-overlap",
            "verifier": "ver-overlap",
            "execution_kind": "app_server_pipeline",
        },
    )
    # Plan readiness detects parallel file_ownership conflict on calc.py!
    readiness = dispatcher.workflow.plan_readiness(cap_run_id)
    assert readiness["ready"] is False
    assert len(readiness["ownership_conflicts"]) == 1

    # Dispatch cap-step first -> succeeds; concurrent dispatch of overlapping overlap-step MUST be rejected!
    prep0 = dispatcher.dispatch(
        "workflow_prepare_dispatch",
        {
            "run_id": cap_run_id,
            "step_id": cap_step_id,
            "dispatch_action": "dispatch",
            "create_workspace": True,
            "agent_id": "appserver://thr_cap_author",
            "instruction": "Attempt 0",
        },
    )
    dispatcher.dispatch(
        "native_action_record_call",
        {
            "run_id": cap_run_id,
            "action_id": prep0["action_id"],
            "claim_token": prep0["claim_token"],
            "expected_revision": prep0["expected_revision"],
            "outcome": "confirmed",
            "raw_response": {"threadId": "thr_cap_author", "turnId": "turn_0"},
        },
    )

    with pytest.raises(WorkflowConflictError, match="ownership"):
        dispatcher.dispatch(
            "workflow_prepare_dispatch",
            {
                "run_id": cap_run_id,
                "step_id": step_overlap["id"],
                "dispatch_action": "dispatch",
                "create_workspace": True,
                "agent_id": "appserver://thr_overlap_author",
                "instruction": "Should fail due to calc.py overlap",
            },
        )

    ws_cap = Path(prep0["workspace"]["path"])
    current_msg_id = prep0["message_id"]

    for attempt_idx in range(4):
        dispatcher.dispatch(
            "workflow_author_turn",
            {"run_id": cap_run_id, "step_id": cap_step_id, "message_id": current_msg_id, "phase": "accept"},
        )
        # Keep calc.py buggy (returns a - b instead of a + b)
        (ws_cap / "calc.py").write_text(
            f"def add(a: int, b: int) -> int:\n    return a - b - {attempt_idx}\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "calc.py"], cwd=ws_cap, check=True)
        subprocess.run(["git", "commit", "-qm", f"buggy attempt {attempt_idx}"], cwd=ws_cap, check=True)
        dispatcher.dispatch(
            "workflow_author_turn",
            {
                "run_id": cap_run_id,
                "step_id": cap_step_id,
                "message_id": current_msg_id,
                "phase": "submit",
                "summary": f"Candidate {attempt_idx}",
            },
        )

        ver_prep = dispatcher.dispatch(
            "workflow_prepare_dispatch",
            {
                "run_id": cap_run_id,
                "dispatch_action": "request_verify",
                "step_id": cap_step_id,
                **(
                    {"verifier_agent_id": "appserver://thr_cap_ver"}
                    if attempt_idx == 0
                    else {}
                ),
                "instruction": f"Verify candidate {attempt_idx}",
            },
        )
        dispatcher.dispatch(
            "native_action_record_call",
            {
                "run_id": cap_run_id,
                "action_id": ver_prep["action_id"],
                "claim_token": ver_prep["claim_token"],
                "expected_revision": ver_prep["expected_revision"],
                "outcome": "confirmed",
                "raw_response": {"threadId": "thr_cap_ver", "turnId": f"vturn_{attempt_idx}"},
            },
        )
        dispatcher.inbox.ack(ver_prep["message_id"], "ver-cap", "accepted", f"verifying {attempt_idx}")

        cand = dispatcher.dispatch(
            "artifact_capture",
            {
                "step_id": cap_step_id,
                "scope": "candidate",
                "env_profile": env_prof,
                "verification_commands": [
                    {
                        "id": "check-add",
                        "argv": [
                            sys.executable,
                            "-B",
                            "-c",
                            f"import sys; sys.path.insert(0, {str(ws_cap)!r}); import calc; assert calc.add(2, 3) == 5",
                        ],
                        "cwd": str(settings.data_dir),
                        "timeout_sec": 10,
                        "criteria": ["AC-CAP"],
                    }
                ],
            },
        )
        out_attempt = tmp_path / f"cap-collect-{attempt_idx}"
        receipt = collect(Path(cand["manifest_path"]), out_attempt)
        assert receipt["commands"][0]["exit_code"] != 0

        # Trying to record PASS against a failing command receipt MUST fail!
        with pytest.raises(ValueError):
            dispatcher.dispatch(
                "verification_record",
                {
                    "step_id": cap_step_id,
                    "artifact_id": cand["id"],
                    "verifier": "ver-cap",
                    "verdict": "PASS",
                    "criteria_results": [{"criterion": "AC-CAP", "status": "PASS"}],
                    "evidence_files": [str(notes_file)],
                    "receipt_files": [str(out_attempt / "receipt.json")],
                    "key": f"illegal-pass-{attempt_idx}",
                },
            )

        dispatcher.dispatch(
            "verification_record",
            {
                "step_id": cap_step_id,
                "artifact_id": cand["id"],
                "verifier": "ver-cap",
                "verdict": "FAIL",
                "criteria_results": [{"criterion": "AC-CAP", "status": "FAIL"}],
                "evidence_files": [str(notes_file)],
                "receipt_files": [str(out_attempt / "receipt.json")],
                "key": f"fail-{attempt_idx}",
            },
        )
        dispatcher.inbox.ack(ver_prep["message_id"], "ver-cap", "completed", f"failed {attempt_idx}")

        after_fail = dispatcher.workflow.get_step(cap_step_id)
        if attempt_idx < 3:
            assert after_fail["state"] == "rework"
            assert after_fail["attempt"] == attempt_idx

            if attempt_idx == 0:
                # Without quiescent observation, start_rework MUST be rejected!
                with pytest.raises(WorkflowConflictError, match="idle/interrupted"):
                    dispatcher.dispatch(
                        "workflow_prepare_dispatch",
                        {
                            "run_id": cap_run_id,
                            "step_id": cap_step_id,
                            "dispatch_action": "start_rework",
                            "instruction": "Missing observation",
                        },
                    )
                # Attempt to swap frozen verifier on rework MUST be rejected!
                dispatcher.dispatch(
                    "worker_register",
                    {"run_id": cap_run_id, "name": "ver-rogue", "role": "verifier", "agent_id": "appserver://thr_rogue"},
                )
                with pytest.raises(WorkflowConflictError, match="original"):
                    dispatcher.dispatch(
                        "workflow_step_save",
                        {
                            "run_id": cap_run_id,
                            "step_id": cap_step_id,
                            "step_key": "cap-step",
                            "owner": "author-cap",
                            "verifier": "ver-rogue",
                            "expected_revision": after_fail["revision"],
                            "execution_kind": "app_server_pipeline",
                        },
                    )

            # Test inline `observation` on workflow_prepare_dispatch(start_rework) for attempts 0 & 1,
            # and prior `observe` step_advance for attempt 2!
            obs_ts = after_fail["checkpoint"]["last_failure_at"] + 0.01 + attempt_idx * 0.01
            if attempt_idx in (0, 1):
                rework_prep = dispatcher.dispatch(
                    "workflow_prepare_dispatch",
                    {
                        "run_id": cap_run_id,
                        "step_id": cap_step_id,
                        "dispatch_action": "start_rework",
                        "observation": {
                            "agent_id": "appserver://thr_cap_author",
                            "state": "idle",
                            "observed_at": obs_ts,
                            "evidence": f"thread/read status=idle after turn {attempt_idx}",
                        },
                        "instruction": f"Rework {attempt_idx + 1} on same thread",
                    },
                )
            else:
                dispatcher.dispatch(
                    "workflow_step_advance",
                    {
                        "step_id": cap_step_id,
                        "action": "observe",
                        "expected_revision": after_fail["revision"],
                        "observation": {
                            "agent_id": "appserver://thr_cap_author",
                            "state": "idle",
                            "observed_at": obs_ts,
                            "evidence": f"prior observe idle {attempt_idx}",
                        },
                    },
                )
                rework_prep = dispatcher.dispatch(
                    "workflow_prepare_dispatch",
                    {
                        "run_id": cap_run_id,
                        "step_id": cap_step_id,
                        "dispatch_action": "start_rework",
                        "instruction": f"Rework {attempt_idx + 1} on same thread",
                    },
                )

            assert rework_prep["step"]["state"] == "implementing"
            assert rework_prep["step"]["attempt"] == attempt_idx + 1
            assert rework_prep["step"]["owner_agent_id"] == "appserver://thr_cap_author"
            dispatcher.dispatch(
                "native_action_record_call",
                {
                    "run_id": cap_run_id,
                    "action_id": rework_prep["action_id"],
                    "claim_token": rework_prep["claim_token"],
                    "expected_revision": rework_prep["expected_revision"],
                    "outcome": "confirmed",
                    "raw_response": {"threadId": "thr_cap_author", "turnId": f"turn_{attempt_idx + 1}"},
                },
            )
            current_msg_id = rework_prep["message_id"]
        else:
            # 4th failure (attempt == 3 == MAX_REWORKS) transitions to needs_decision!
            assert after_fail["state"] == "needs_decision"
            assert after_fail["attempt"] == 3
            with pytest.raises(WorkflowConflictError):
                dispatcher.dispatch(
                    "workflow_prepare_dispatch",
                    {
                        "run_id": cap_run_id,
                        "step_id": cap_step_id,
                        "dispatch_action": "start_rework",
                        "observation": {
                            "agent_id": "appserver://thr_cap_author",
                            "state": "idle",
                            "observed_at": time.time() + 0.05,
                            "evidence": "idle after 4th failure",
                        },
                        "instruction": "4th rework must be blocked",
                    },
                )

    # Verify resume_context flags binding_issues if worker is stopped!
    dispatcher.dispatch(
        "worker_status_update",
        {"run_id": cap_run_id, "worker_name": "author-cap", "status": "stopped"},
    )
    rc_stopped = dispatcher.workflow.resume_context(cap_run_id)
    assert any(
        issue["step_id"] == cap_step_id and issue["role"] == "owner" and issue["decision_required"]
        for issue in rc_stopped["binding_issues"]
    )

    # Part B: Full 2-step DAG happy path -> candidate PASS -> merge -> main-tree integrated PASS -> completed!
    run_ok = dispatcher.dispatch(
        "run_create",
        {"mode": "swarm", "title": "M11 2-Step DAG Integrated Completion", "project_root": str(project)},
    )
    ok_id = run_ok["id"]
    dispatcher.dispatch(
        "task_packet_save",
        {
            "run_id": ok_id,
            "title": "DAG Calc + Mul",
            "goal": "Complete 2 dependent pipeline steps with integrated verification",
            "file_ownership": {"author-s1": ["calc.py"], "author-s2": ["mul.py"]},
            "acceptance_criteria": ["AC-S1", "AC-S2"],
        },
    )
    for w_name, role in (
        ("author-s1", "swarm_worker"),
        ("ver-s1", "verifier"),
        ("author-s2", "swarm_worker"),
        ("ver-s2", "verifier"),
    ):
        dispatcher.dispatch("worker_register", {"run_id": ok_id, "name": w_name, "role": role})

    s1 = dispatcher.dispatch(
        "workflow_step_save",
        {
            "run_id": ok_id,
            "step_key": "step-calc",
            "owner": "author-s1",
            "verifier": "ver-s1",
            "criteria": ["AC-S1"],
            "execution_kind": "app_server_pipeline",
        },
    )
    s2 = dispatcher.dispatch(
        "workflow_step_save",
        {
            "run_id": ok_id,
            "step_key": "step-mul",
            "owner": "author-s2",
            "verifier": "ver-s2",
            "dependencies": [s1["id"]],
            "criteria": ["AC-S2"],
            "execution_kind": "app_server_pipeline",
        },
    )
    assert dispatcher.workflow.plan_readiness(ok_id)["ready"] is True

    # Dependent step s2 CANNOT be dispatched before s1 reaches 'done'!
    with pytest.raises(WorkflowConflictError, match="dependency"):
        dispatcher.dispatch(
            "workflow_prepare_dispatch",
            {
                "run_id": ok_id,
                "step_id": s2["id"],
                "dispatch_action": "dispatch",
                "create_workspace": True,
                "agent_id": "appserver://thr_s2_author",
                "instruction": "Premature dispatch of dependent step",
            },
        )

    def _complete_pipeline_step(
        step_obj: dict,
        author_thr: str,
        ver_thr: str,
        rel_file: str,
        code: str,
        check_expr: str,
        criterion: str,
    ) -> str:
        sid = step_obj["id"]
        ver_name = step_obj["verifier"]
        author_name = step_obj["owner"]

        prep = dispatcher.dispatch(
            "workflow_prepare_dispatch",
            {
                "run_id": ok_id,
                "step_id": sid,
                "dispatch_action": "dispatch",
                "create_workspace": True,
                "agent_id": author_thr,
                "instruction": f"Implement {rel_file}",
            },
        )
        dispatcher.dispatch(
            "native_action_record_call",
            {
                "run_id": ok_id,
                "action_id": prep["action_id"],
                "claim_token": prep["claim_token"],
                "expected_revision": prep["expected_revision"],
                "outcome": "confirmed",
                "raw_response": {"threadId": author_thr},
            },
        )
        dispatcher.dispatch(
            "workflow_author_turn",
            {"run_id": ok_id, "step_id": sid, "message_id": prep["message_id"], "phase": "accept"},
        )
        ws_dir = Path(prep["workspace"]["path"])
        (ws_dir / rel_file).write_text(code, encoding="utf-8")
        subprocess.run(["git", "add", rel_file], cwd=ws_dir, check=True)
        subprocess.run(["git", "commit", "-qm", f"impl {rel_file}"], cwd=ws_dir, check=True)

        st_pre_obs = dispatcher.workflow.get_step(sid)
        dispatcher.dispatch(
            "workflow_step_advance",
            {
                "step_id": sid,
                "action": "observe",
                "expected_revision": st_pre_obs["revision"],
                "observation": {
                    "agent_id": author_thr,
                    "state": "idle",
                    "observed_at": time.time() + 0.01,
                    "evidence": "turn completed -> idle",
                },
            },
        )
        dispatcher.dispatch(
            "workflow_author_turn",
            {"run_id": ok_id, "step_id": sid, "message_id": prep["message_id"], "phase": "submit"},
        )

        vprep = dispatcher.dispatch(
            "workflow_prepare_dispatch",
            {
                "run_id": ok_id,
                "step_id": sid,
                "dispatch_action": "request_verify",
                "verifier_agent_id": ver_thr,
                "instruction": f"Verify candidate {rel_file}",
            },
        )
        dispatcher.dispatch(
            "native_action_record_call",
            {
                "run_id": ok_id,
                "action_id": vprep["action_id"],
                "claim_token": vprep["claim_token"],
                "expected_revision": vprep["expected_revision"],
                "outcome": "confirmed",
                "raw_response": {"threadId": ver_thr},
            },
        )
        dispatcher.inbox.ack(vprep["message_id"], ver_name, "accepted", "candidate review")

        cand_art = dispatcher.dispatch(
            "artifact_capture",
            {
                "step_id": sid,
                "scope": "candidate",
                "env_profile": env_prof,
                "verification_commands": [
                    {
                        "id": "cand-cmd",
                        "argv": [
                            sys.executable,
                            "-B",
                            "-c",
                            f"import sys; sys.path.insert(0, {str(ws_dir)!r}); {check_expr}",
                        ],
                        "cwd": str(settings.data_dir),
                        "timeout_sec": 10,
                        "criteria": [criterion],
                    }
                ],
            },
        )
        cand_out = tmp_path / f"ok-cand-{step_obj['step_key']}"
        collect(Path(cand_art["manifest_path"]), cand_out)
        dispatcher.dispatch(
            "verification_record",
            {
                "step_id": sid,
                "artifact_id": cand_art["id"],
                "verifier": ver_name,
                "verdict": "PASS",
                "criteria_results": [{"criterion": criterion, "status": "PASS"}],
                "evidence_files": [str(notes_file)],
                "receipt_files": [str(cand_out / "receipt.json")],
                "key": f"cand-pass-{step_obj['step_key']}",
            },
        )
        dispatcher.inbox.ack(vprep["message_id"], ver_name, "completed", "candidate PASS")

        # Merge into project_root -> transitions step to integrated_review!
        preview = dispatcher.dispatch("workspace_merge_preview", {"workspace_id": prep["workspace"]["id"]})
        assert preview["has_conflicts"] is False
        merged = dispatcher.dispatch("workspace_merge", {"workspace_id": prep["workspace"]["id"]})
        assert merged["status"] == "merged"
        assert dispatcher.workflow.get_step(sid)["state"] == "integrated_review"

        # Cannot complete without main-tree integrated verification!
        st_int = dispatcher.workflow.get_step(sid)
        with pytest.raises(WorkflowConflictError, match="integrated"):
            dispatcher.dispatch(
                "workflow_step_advance",
                {"step_id": sid, "action": "complete", "expected_revision": st_int["revision"]},
            )

        int_art = dispatcher.dispatch(
            "artifact_capture",
            {
                "step_id": sid,
                "scope": "integrated",
                "env_profile": env_prof,
                "verification_commands": [
                    {
                        "id": "int-cmd",
                        "argv": [
                            sys.executable,
                            "-B",
                            "-c",
                            f"import sys; sys.path.insert(0, {str(project)!r}); {check_expr}",
                        ],
                        "cwd": str(settings.data_dir),
                        "timeout_sec": 10,
                        "criteria": [criterion],
                    }
                ],
            },
        )
        int_out = tmp_path / f"ok-int-{step_obj['step_key']}"
        collect(Path(int_art["manifest_path"]), int_out)
        dispatcher.dispatch(
            "verification_record",
            {
                "step_id": sid,
                "artifact_id": int_art["id"],
                "verifier": ver_name,
                "verdict": "PASS",
                "criteria_results": [{"criterion": criterion, "status": "PASS"}],
                "evidence_files": [str(notes_file)],
                "receipt_files": [str(int_out / "receipt.json")],
                "key": f"int-pass-{step_obj['step_key']}",
            },
        )
        st_ready_done = dispatcher.workflow.get_step(sid)
        done_step = dispatcher.dispatch(
            "workflow_step_advance",
            {"step_id": sid, "action": "complete", "expected_revision": st_ready_done["revision"]},
        )
        assert done_step["state"] == "done"
        # Ensure quiescent observation after completion_at for any overlapping successor checks.
        dispatcher.dispatch(
            "workflow_step_advance",
            {
                "step_id": sid,
                "action": "observe",
                "expected_revision": done_step["revision"],
                "observation": {
                    "agent_id": author_thr,
                    "state": "idle",
                    "observed_at": time.time() + 0.02,
                    "evidence": f"{author_name} idle after completion",
                },
            },
        )
        return prep["workspace"]["id"]

    ws1_id = _complete_pipeline_step(
        s1,
        "appserver://thr_s1_author",
        "appserver://thr_s1_ver",
        "calc.py",
        "def add(a: int, b: int) -> int:\n    return a + b\n",
        "import calc; assert calc.add(10, 20) == 30",
        "AC-S1",
    )
    ws2_id = _complete_pipeline_step(
        s2,
        "appserver://thr_s2_author",
        "appserver://thr_s2_ver",
        "mul.py",
        "def mul(a: int, b: int) -> int:\n    return a * b\n",
        "import calc, mul; assert calc.add(2, 3) == 5 and mul.mul(6, 7) == 42",
        "AC-S2",
    )

    # Merging s2 into project_root staled s1's earlier full-tree integrated snapshot -> cleanup/completion blocked until reverify!
    with pytest.raises(WorkflowConflictError, match="stale"):
        dispatcher.dispatch("workspace_cleanup", {"workspace_id": ws1_id})

    s1_now = dispatcher.workflow.get_step(s1["id"])
    s1_rev = dispatcher.dispatch(
        "workflow_step_advance",
        {"step_id": s1["id"], "action": "reverify", "expected_revision": s1_now["revision"]},
    )
    assert s1_rev["state"] == "integrated_review"
    re_art = dispatcher.dispatch(
        "artifact_capture",
        {
            "step_id": s1["id"],
            "scope": "integrated",
            "env_profile": env_prof,
            "verification_commands": [
                {
                    "id": "reverify-s1",
                    "argv": [
                        sys.executable,
                        "-B",
                        "-c",
                        f"import sys; sys.path.insert(0, {str(project)!r}); import calc; assert calc.add(10, 20) == 30",
                    ],
                    "cwd": str(settings.data_dir),
                    "timeout_sec": 10,
                    "criteria": ["AC-S1"],
                }
            ],
        },
    )
    re_out = tmp_path / "ok-reverify-s1"
    collect(Path(re_art["manifest_path"]), re_out)
    dispatcher.dispatch(
        "verification_record",
        {
            "step_id": s1["id"],
            "artifact_id": re_art["id"],
            "verifier": "ver-s1",
            "verdict": "PASS",
            "criteria_results": [{"criterion": "AC-S1", "status": "PASS"}],
            "evidence_files": [str(notes_file)],
            "receipt_files": [str(re_out / "receipt.json")],
            "key": "reverify-pass-s1",
        },
    )
    s1_after_rev = dispatcher.workflow.get_step(s1["id"])
    dispatcher.dispatch(
        "workflow_step_advance",
        {"step_id": s1["id"], "action": "complete", "expected_revision": s1_after_rev["revision"]},
    )

    dispatcher.dispatch("workspace_cleanup", {"workspace_id": ws1_id})
    dispatcher.dispatch("workspace_cleanup", {"workspace_id": ws2_id})
    completed_run = dispatcher.dispatch("run_update", {"run_id": ok_id, "status": "completed"})
    assert completed_run["status"] == "completed"


def test_m11_lazy_verifier_inline_binding_and_high_complexity_challenger_finding_loop(m11_env):
    tmp_path, project, settings, db, _, _, dispatcher = m11_env
    notes_file = tmp_path / "challenger_loop_notes.md"
    notes_file.write_text("Verifier & challenger audit notes.\n", encoding="utf-8")
    env_prof = "windows_minimal_py" if sys.platform == "win32" else "posix_minimal_py"

    run = dispatcher.dispatch(
        "run_create",
        {"mode": "teamwork", "title": "Inline Lazy Verifier & Challenger Gate", "project_root": str(project)},
    )
    run_id = run["id"]
    dispatcher.dispatch(
        "task_packet_save",
        {
            "run_id": run_id,
            "title": "High-Complexity Calc",
            "goal": "Ensure challenger finding blocks PASS until same-author rework",
            "file_ownership": {"author-hc": ["calc.py"]},
            "acceptance_criteria": ["AC-HC"],
        },
    )
    dispatcher.dispatch("worker_register", {"run_id": run_id, "name": "author-hc", "role": "coder"})
    dispatcher.dispatch("worker_register", {"run_id": run_id, "name": "ver-hc", "role": "verifier"})
    dispatcher.dispatch(
        "worker_register",
        {"run_id": run_id, "name": "chal-hc", "role": "investigator", "agent_id": "appserver://thr_chal_hc"},
    )
    dispatcher.dispatch(
        "worker_register",
        {"run_id": run_id, "name": "spoof-chal", "role": "investigator", "agent_id": "/root/spoof_chal"},
    )

    step = dispatcher.dispatch(
        "workflow_step_save",
        {
            "run_id": run_id,
            "step_key": "hc-milestone",
            "kind": "task",
            "owner": "author-hc",
            "verifier": "ver-hc",
            "complexity": "high",
            "execution_kind": "app_server_pipeline",
        },
    )
    sid = step["id"]

    # Bind author inline via workflow_step_advance(action="dispatch", agent_id=...) after workspace_create + step_save.
    ws = dispatcher.dispatch("workspace_create", {"run_id": run_id, "worker_name": "author-hc"})
    step = dispatcher.dispatch(
        "workflow_step_save",
        {
            "run_id": run_id,
            "step_id": sid,
            "step_key": "hc-milestone",
            "owner": "author-hc",
            "verifier": "ver-hc",
            "workspace_id": ws["id"],
            "expected_revision": step["revision"],
            "complexity": "high",
            "execution_kind": "app_server_pipeline",
        },
    )
    step = dispatcher.dispatch(
        "workflow_step_advance",
        {
            "step_id": sid,
            "action": "dispatch",
            "expected_revision": step["revision"],
            "agent_id": "appserver://thr_author_hc",
        },
    )
    assert step["owner_agent_id"] == "appserver://thr_author_hc"
    assert step["checkpoint"]["original_bindings"]["owner_agent_id"] == "appserver://thr_author_hc"

    step = dispatcher.dispatch(
        "workflow_step_advance",
        {"step_id": sid, "action": "accept", "expected_revision": step["revision"]},
    )
    ws_dir = Path(ws["path"])
    (ws_dir / "calc.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
    subprocess.run(["git", "add", "calc.py"], cwd=ws_dir, check=True)
    subprocess.run(["git", "commit", "-qm", "initial impl"], cwd=ws_dir, check=True)
    step = dispatcher.dispatch(
        "workflow_step_advance",
        {"step_id": sid, "action": "submit", "expected_revision": step["revision"]},
    )

    # Bind lazy verifier directly during artifact_capture(verifier_agent_id="appserver://thr_ver_hc")!
    cand0 = dispatcher.dispatch(
        "artifact_capture",
        {
            "step_id": sid,
            "scope": "candidate",
            "verifier_agent_id": "appserver://thr_ver_hc",
            "env_profile": env_prof,
            "verification_commands": [
                {
                    "id": "hc-cmd-0",
                    "argv": [
                        sys.executable,
                        "-B",
                        "-c",
                        f"import sys; sys.path.insert(0, {str(ws_dir)!r}); import calc; assert calc.add(1, 2) == 3",
                    ],
                    "cwd": str(settings.data_dir),
                    "timeout_sec": 10,
                    "criteria": ["AC-HC"],
                }
            ],
        },
    )
    step_bound = dispatcher.workflow.get_step(sid)
    assert step_bound["verifier_agent_id"] == "appserver://thr_ver_hc"
    assert step_bound["checkpoint"]["frozen_verifier_binding"] == {
        "verifier": "ver-hc",
        "verifier_agent_id": "appserver://thr_ver_hc",
    }
    assert step_bound["checkpoint"]["original_bindings"]["verifier_agent_id"] == "appserver://thr_ver_hc"

    # Spoofed /root/... challenger MUST be rejected on app_server_pipeline step!
    with pytest.raises(WorkflowConflictError, match="appserver://"):
        dispatcher.dispatch(
            "workflow_review",
            {
                "step_id": sid,
                "expected_revision": step_bound["revision"],
                "agent_id": "/root/spoof_chal",
                "criterion": "AC-HC",
                "evidence": "Illegal native challenger on pipeline step",
            },
        )

    # Independent appserver:// challenger records finding -> blocks PASS until substantiated FAIL + rework!
    finding = dispatcher.dispatch(
        "workflow_review",
        {
            "step_id": sid,
            "expected_revision": step_bound["revision"],
            "agent_id": "appserver://thr_chal_hc",
            "criterion": "AC-HC",
            "evidence": "Missing type coercion validation on non-int inputs",
        },
    )
    out0 = tmp_path / "hc-out0"
    collect(Path(cand0["manifest_path"]), out0)
    dims_pass = {"specification": "PASS", "code": "PASS", "stress": "PASS", "evidence": "PASS"}

    with pytest.raises(WorkflowConflictError, match="finding"):
        dispatcher.dispatch(
            "verification_record",
            {
                "step_id": sid,
                "artifact_id": cand0["id"],
                "verifier": "ver-hc",
                "verdict": "PASS",
                "criteria_results": [{"criterion": "AC-HC", "status": "PASS"}],
                "evidence_files": [str(notes_file)],
                "receipt_files": [str(out0 / "receipt.json")],
                "review_dimensions": dims_pass,
                "key": "hc-blocked-pass",
            },
        )

    # Original verifier substantiates finding via FAIL -> same author reworks -> original verifier resolves finding on PASS.
    dispatcher.dispatch(
        "verification_record",
        {
            "step_id": sid,
            "artifact_id": cand0["id"],
            "verifier": "ver-hc",
            "verdict": "FAIL",
            "criteria_results": [{"criterion": "AC-HC", "status": "FAIL"}],
            "evidence_files": [str(notes_file)],
            "receipt_files": [str(out0 / "receipt.json")],
            "review_dimensions": {"specification": "PASS", "code": "PASS", "stress": "FAIL", "evidence": "PASS"},
            "key": "hc-substantiated-fail",
        },
    )
    st_rw = dispatcher.workflow.get_step(sid)
    assert st_rw["state"] == "rework"

    st_rw = dispatcher.dispatch(
        "workflow_step_advance",
        {
            "step_id": sid,
            "action": "observe",
            "expected_revision": st_rw["revision"],
            "observation": {
                "agent_id": "appserver://thr_author_hc",
                "state": "idle",
                "observed_at": st_rw["checkpoint"]["last_failure_at"] + 0.01,
                "evidence": "idle",
            },
        },
    )
    st_rw = dispatcher.dispatch(
        "workflow_step_advance",
        {"step_id": sid, "action": "start_rework", "expected_revision": st_rw["revision"]},
    )
    (ws_dir / "calc.py").write_text(
        "def add(a: int, b: int) -> int:\n    assert isinstance(a, int) and isinstance(b, int)\n    return a + b\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "calc.py"], cwd=ws_dir, check=True)
    subprocess.run(["git", "commit", "-qm", "enforce int check"], cwd=ws_dir, check=True)
    dispatcher.dispatch(
        "workflow_step_advance",
        {"step_id": sid, "action": "submit", "expected_revision": st_rw["revision"]},
    )

    cand1 = dispatcher.dispatch(
        "artifact_capture",
        {
            "step_id": sid,
            "scope": "candidate",
            "env_profile": env_prof,
            "verification_commands": [
                {
                    "id": "hc-cmd-1",
                    "argv": [
                        sys.executable,
                        "-B",
                        "-c",
                        f"import sys; sys.path.insert(0, {str(ws_dir)!r}); import calc; assert calc.add(3, 4) == 7",
                    ],
                    "cwd": str(settings.data_dir),
                    "timeout_sec": 10,
                    "criteria": ["AC-HC"],
                }
            ],
        },
    )
    out1 = tmp_path / "hc-out1"
    collect(Path(cand1["manifest_path"]), out1)
    rec_pass = dispatcher.dispatch(
        "verification_record",
        {
            "step_id": sid,
            "artifact_id": cand1["id"],
            "verifier": "ver-hc",
            "verdict": "PASS",
            "criteria_results": [{"criterion": "AC-HC", "status": "PASS"}],
            "evidence_files": [str(notes_file)],
            "receipt_files": [str(out1 / "receipt.json")],
            "review_dimensions": dims_pass,
            "resolved_finding_ids": [finding["id"]],
            "key": "hc-resolved-pass",
        },
    )
    assert rec_pass["verdict"] == "PASS"
    assert dispatcher.workflow.get_step(sid)["state"] == "ready_to_merge"

