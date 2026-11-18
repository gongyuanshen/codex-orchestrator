"""M8 workflow5 pre-spawn direct admission, disconnect reconciliation, lazy verifier freezing, and legacy preservation."""
from __future__ import annotations

import asyncio
from contextlib import closing
import json
import sqlite3
import sys
from pathlib import Path

import httpx
import pytest

from orchestrator.board import BoardService
from orchestrator.config import Settings
from orchestrator.daemon import create_app
from orchestrator.db import SCHEMA_VERSION, Database
from orchestrator.native_actions import NativeActionConflictError
from orchestrator.rpc_handlers import RpcDispatcher
from orchestrator.verify_runner import collect
from orchestrator.workflow import WorkflowConflictError
from orchestrator.workspace_mgr import WorkspaceManager


@pytest.fixture
def wf5_env(tmp_path: Path):
    project = tmp_path / "repo"
    project.mkdir()
    (project / "calc.py").write_text("def add(a, b):\n    return 0\n", encoding="utf-8")
    settings = Settings(data_dir=tmp_path / "data", port_range=(28920, 28970))
    db = Database(settings=settings)
    board = BoardService(db, settings)
    ws_mgr = WorkspaceManager(db, settings)
    dispatcher = RpcDispatcher(db, board, ws_mgr)
    yield tmp_path, project, settings, db, board, ws_mgr, dispatcher
    db.close()


def test_workflow5_direct_spawn_author_and_lazy_verifier_full_rework_and_freeze_loop(wf5_env):
    tmp_path, project, settings, db, board, ws_mgr, dispatcher = wf5_env
    judgment_file = tmp_path / "verifier_judgment.md"
    judgment_file.write_text("Independent verifier criterion review notes.\n", encoding="utf-8")

    run = dispatcher.dispatch(
        "run_create",
        {"mode": "swarm", "title": "WF5 Zero-Bootstrap Loop", "project_root": str(project)},
    )
    run_id = run["id"]
    assert run["workflow_version"] == 5

    # Reject invalid task_name syntax.
    with pytest.raises(ValueError, match="task_name"):
        dispatcher.dispatch(
            "worker_register",
            {"run_id": run_id, "name": "bad", "role": "swarm_worker", "task_name": "Bad Uppercase!"},
        )

    dispatcher.dispatch(
        "task_packet_save",
        {
            "run_id": run_id,
            "title": "Implement add()",
            "goal": "Zero empty waiting-only turns for author and lazy verifier",
            "file_ownership": {"author_1": ["calc.py"], "author_2": ["other.py"]},
            "acceptance_criteria": ["AC-ADD"],
        },
    )

    # 1. Pre-spawn direct dispatch for author_1; verifier_1 is registered lazily WITHOUT spawning!
    prep = dispatcher.dispatch(
        "workflow_prepare_dispatch",
        {
            "run_id": run_id,
            "create_workspace": True,
            "owner": "author_1",
            "task_name": "author_1",
            "verifier": "verifier_1",
            "step_key": "add-impl",
            "instruction": "Implement add(a, b) in calc.py",
        },
    )
    step_id = prep["step"]["id"]
    assert prep["action_type"] == "spawn_agent"
    assert prep["target_agent_path"] == "/root/author_1"
    assert prep["step"]["owner_agent_id"] == ""
    assert prep["step"]["verifier_agent_id"] == ""
    assert prep["step"]["checkpoint"]["pending_owner_path"] == "/root/author_1"
    assert prep["worker"]["agent_id"] is None
    assert prep["worker"]["pending_agent_path"] == "/root/author_1"

    # Duplicate task_name in same run MUST be rejected.
    with pytest.raises(ValueError, match="Duplicate task_name"):
        dispatcher.dispatch(
            "worker_register",
            {"run_id": run_id, "name": "author_2", "role": "swarm_worker", "task_name": "author_1"},
        )

    # Wrong spawned path MUST be rejected without mutating bindings.
    with pytest.raises(NativeActionConflictError, match="path"):
        dispatcher.dispatch(
            "native_action_record_call",
            {
                "run_id": run_id,
                "action_id": prep["action_id"],
                "claim_token": prep["claim_token"],
                "expected_revision": prep["expected_revision"],
                "outcome": "confirmed",
                "raw_response": {"agent_id": "/root/wrong_agent", "agent_path": "/root/wrong_agent"},
            },
        )
    assert db.get_worker_by_name(run_id, "author_1")["agent_id"] is None

    # Confirm valid spawn_agent -> atomically backfills worker, step, inbox, original_bindings, and sets working.
    rec_author = dispatcher.dispatch(
        "native_action_record_call",
        {
            "run_id": run_id,
            "action_id": prep["action_id"],
            "claim_token": prep["claim_token"],
            "expected_revision": prep["expected_revision"],
            "outcome": "confirmed",
            "raw_response": {"agent_id": "/root/author_1", "agent_path": "/root/author_1"},
        },
    )
    assert rec_author["status"] == "confirmed"
    w_author = db.get_worker_by_name(run_id, "author_1")
    assert w_author["agent_id"] == "/root/author_1"
    assert w_author["pending_agent_path"] is None
    assert w_author["status"] == "working"
    step_after_spawn = dispatcher.workflow.get_step(step_id)
    assert step_after_spawn["owner_agent_id"] == "/root/author_1"
    assert step_after_spawn["verifier_agent_id"] == ""
    assert step_after_spawn["checkpoint"]["original_bindings"]["owner_agent_id"] == "/root/author_1"
    assert dispatcher.inbox.get(prep["message_id"])["recipient_agent_id"] == "/root/author_1"

    # Author turn 1: accept + edit + submit (verifier STILL unspawned!).
    dispatcher.dispatch(
        "workflow_author_turn",
        {"run_id": run_id, "step_id": step_id, "message_id": prep["message_id"], "phase": "accept"},
    )
    ws_dir = Path(prep["workspace"]["path"])
    (ws_dir / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    dispatcher.dispatch(
        "workflow_author_turn",
        {
            "run_id": run_id,
            "step_id": step_id,
            "message_id": prep["message_id"],
            "phase": "submit",
            "summary": "First candidate (has intentional bug for rework check)",
        },
    )

    # 2. Lazy verifier instantiated on candidate submission via 1-turn direct spawn_agent!
    ver_prep = dispatcher.dispatch(
        "workflow_prepare_dispatch",
        {
            "run_id": run_id,
            "dispatch_action": "request_verify",
            "step_id": step_id,
            "verifier": "verifier_1",
            "task_name": "verifier_1",
            "instruction": "Verify candidate delivery in workspace",
        },
    )
    assert ver_prep["action_type"] == "spawn_agent"
    assert ver_prep["target_agent_path"] == "/root/verifier_1"

    dispatcher.dispatch(
        "native_action_record_call",
        {
            "run_id": run_id,
            "action_id": ver_prep["action_id"],
            "claim_token": ver_prep["claim_token"],
            "expected_revision": ver_prep["expected_revision"],
            "outcome": "confirmed",
            "raw_response": {"agent_id": "/root/verifier_1", "agent_path": "/root/verifier_1"},
        },
    )
    step_ver_bound = dispatcher.workflow.get_step(step_id)
    assert step_ver_bound["verifier_agent_id"] == "/root/verifier_1"
    assert step_ver_bound["checkpoint"]["original_bindings"]["verifier_agent_id"] == "/root/verifier_1"
    assert step_ver_bound["checkpoint"]["frozen_verifier_binding"] == {
        "verifier": "verifier_1",
        "verifier_agent_id": "/root/verifier_1",
    }

    # Verifier records candidate FAIL -> triggers rework.
    dispatcher.inbox.ack(ver_prep["message_id"], "verifier_1", "accepted", "reviewing candidate 0")
    cand0 = dispatcher.dispatch(
        "artifact_capture",
        {
            "step_id": step_id,
            "scope": "candidate",
            "env_profile": "windows_minimal_py",
            "verification_commands": [
                {
                    "id": "unit",
                    "argv": [
                        sys.executable,
                        "-B",
                        "-c",
                        f"import sys; sys.path.insert(0, {str(ws_dir)!r}); import calc; assert calc.add(2, 3) == 5",
                    ],
                    "cwd": str(settings.data_dir),
                    "timeout_sec": 10,
                    "criteria": ["AC-ADD"],
                }
            ],
        },
    )
    out0 = tmp_path / "collect-cand0"
    collect(Path(cand0["manifest_path"]), out0)
    dispatcher.dispatch(
        "verification_record",
        {
            "step_id": step_id,
            "artifact_id": cand0["id"],
            "verifier": "verifier_1",
            "verdict": "FAIL",
            "criteria_results": [{"criterion": "AC-ADD", "status": "FAIL"}],
            "evidence_files": [str(judgment_file)],
            "receipt_files": [str(out0 / "receipt.json")],
            "key": "cand-attempt-0",
        },
    )
    dispatcher.inbox.ack(ver_prep["message_id"], "verifier_1", "completed", "recorded FAIL")

    # Record author idle observation before rework admission.
    cur_step = dispatcher.workflow.get_step(step_id)
    assert cur_step["state"] == "rework"
    cur_step = dispatcher.dispatch(
        "workflow_step_advance",
        {
            "step_id": step_id,
            "action": "observe",
            "expected_revision": cur_step["revision"],
            "observation": {
                "agent_id": "/root/author_1",
                "state": "idle",
                "observed_at": cur_step["checkpoint"]["last_failure_at"] + 0.01,
                "evidence": "wait_agent returned author idle",
            },
        },
    )

    # Attempt to replace frozen lazy verifier with another verifier MUST be rejected!
    board.worker_register(run_id, "verifier_2", "verifier", agent_id="/root/verifier_2")
    with pytest.raises(WorkflowConflictError, match="original"):
        dispatcher.dispatch(
            "workflow_step_save",
            {
                "run_id": run_id,
                "step_key": "add-impl",
                "owner": "author_1",
                "verifier": "verifier_2",
                "workspace_id": prep["workspace"]["id"],
                "step_id": step_id,
                "expected_revision": cur_step["revision"],
            },
        )
    with pytest.raises(ValueError, match="immutable"):
        dispatcher.dispatch(
            "worker_register",
            {"run_id": run_id, "name": "verifier_1", "role": "verifier", "agent_id": "/root/other_verifier"},
        )

    # Rework dispatch to original author reuses followup_task (NOT spawn_agent).
    rework_prep = dispatcher.dispatch(
        "workflow_prepare_dispatch",
        {
            "run_id": run_id,
            "dispatch_action": "start_rework",
            "step_id": step_id,
            "instruction": "Fix add(a, b) to return a + b",
        },
    )
    assert rework_prep["action_type"] == "followup_task"
    dispatcher.dispatch(
        "native_action_record_call",
        {
            "run_id": run_id,
            "action_id": rework_prep["action_id"],
            "claim_token": rework_prep["claim_token"],
            "expected_revision": rework_prep["expected_revision"],
            "outcome": "confirmed",
            "raw_response": {"status": "sent"},
        },
    )

    (ws_dir / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    dispatcher.dispatch(
        "workflow_author_turn",
        {
            "run_id": run_id,
            "step_id": step_id,
            "message_id": rework_prep["message_id"],
            "phase": "submit",
            "summary": "Fixed add(a, b)",
        },
    )

    # Re-verification MUST reuse the frozen verifier_1 via followup_task!
    rever_prep = dispatcher.dispatch(
        "workflow_prepare_dispatch",
        {
            "run_id": run_id,
            "dispatch_action": "request_verify",
            "step_id": step_id,
            "instruction": "Re-verify candidate attempt 1",
        },
    )
    assert rever_prep["action_type"] == "followup_task"
    assert rever_prep["target_agent_path"] == "/root/verifier_1"
    dispatcher.dispatch(
        "native_action_record_call",
        {
            "run_id": run_id,
            "action_id": rever_prep["action_id"],
            "claim_token": rever_prep["claim_token"],
            "expected_revision": rever_prep["expected_revision"],
            "outcome": "confirmed",
            "raw_response": {"status": "sent"},
        },
    )
    dispatcher.inbox.ack(rever_prep["message_id"], "verifier_1", "accepted", "re-reviewing")

    cand1 = dispatcher.dispatch(
        "artifact_capture",
        {
            "step_id": step_id,
            "scope": "candidate",
            "env_profile": "windows_minimal_py",
            "verification_commands": [
                {
                    "id": "unit",
                    "argv": [
                        sys.executable,
                        "-B",
                        "-c",
                        f"import sys; sys.path.insert(0, {str(ws_dir)!r}); import calc; assert calc.add(2, 3) == 5",
                    ],
                    "cwd": str(settings.data_dir),
                    "timeout_sec": 10,
                    "criteria": ["AC-ADD"],
                }
            ],
        },
    )
    out1 = tmp_path / "collect-cand1"
    collect(Path(cand1["manifest_path"]), out1)
    dispatcher.dispatch(
        "verification_record",
        {
            "step_id": step_id,
            "artifact_id": cand1["id"],
            "verifier": "verifier_1",
            "verdict": "PASS",
            "criteria_results": [{"criterion": "AC-ADD", "status": "PASS"}],
            "evidence_files": [str(judgment_file)],
            "receipt_files": [str(out1 / "receipt.json")],
            "key": "cand-attempt-1",
        },
    )
    dispatcher.inbox.ack(rever_prep["message_id"], "verifier_1", "completed", "candidate PASS")

    # Merge into main repo and run integrated verification reusing exact same verifier_1 via followup_task.
    dispatcher.dispatch("workspace_merge", {"workspace_id": prep["workspace"]["id"]})
    int_prep = dispatcher.dispatch(
        "workflow_prepare_dispatch",
        {
            "run_id": run_id,
            "dispatch_action": "request_verify",
            "step_id": step_id,
            "instruction": "Verify integrated main directory",
        },
    )
    assert int_prep["action_type"] == "followup_task"
    assert int_prep["target_agent_path"] == "/root/verifier_1"
    dispatcher.dispatch(
        "native_action_record_call",
        {
            "run_id": run_id,
            "action_id": int_prep["action_id"],
            "claim_token": int_prep["claim_token"],
            "expected_revision": int_prep["expected_revision"],
            "outcome": "confirmed",
            "raw_response": {"status": "sent"},
        },
    )
    dispatcher.inbox.ack(int_prep["message_id"], "verifier_1", "accepted", "integrated check")

    int_art = dispatcher.dispatch(
        "artifact_capture",
        {
            "step_id": step_id,
            "scope": "integrated",
            "env_profile": "windows_minimal_py",
            "verification_commands": [
                {
                    "id": "int-unit",
                    "argv": [
                        sys.executable,
                        "-B",
                        "-c",
                        f"import sys; sys.path.insert(0, {str(project)!r}); import calc; assert calc.add(10, 15) == 25",
                    ],
                    "cwd": str(settings.data_dir),
                    "timeout_sec": 10,
                    "criteria": ["AC-ADD"],
                }
            ],
        },
    )
    out_int = tmp_path / "collect-int"
    collect(Path(int_art["manifest_path"]), out_int)
    dispatcher.dispatch(
        "verification_record",
        {
            "step_id": step_id,
            "artifact_id": int_art["id"],
            "verifier": "verifier_1",
            "verdict": "PASS",
            "criteria_results": [{"criterion": "AC-ADD", "status": "PASS"}],
            "evidence_files": [str(judgment_file)],
            "receipt_files": [str(out_int / "receipt.json")],
            "key": "integrated-pass",
        },
    )
    dispatcher.inbox.ack(int_prep["message_id"], "verifier_1", "completed", "integrated PASS")

    done_step = dispatcher.workflow.get_step(step_id)
    done_step = dispatcher.dispatch(
        "workflow_step_advance",
        {"step_id": step_id, "action": "complete", "expected_revision": done_step["revision"]},
    )
    assert done_step["state"] == "done"
    closed = dispatcher.dispatch("run_update", {"run_id": run_id, "status": "completed"})
    assert closed["status"] == "completed"


def test_lazy_verifier_rejects_self_review_and_freezes_across_associated_planned_steps(wf5_env):
    _, project, _, db, board, _, dispatcher = wf5_env
    (project / "part2.py").write_text("P2 = 1\n", encoding="utf-8")
    run = dispatcher.dispatch(
        "run_create",
        {"mode": "swarm", "title": "Multi-Step Lazy Verifier Freeze", "project_root": str(project)},
    )
    run_id = run["id"]
    dispatcher.dispatch(
        "task_packet_save",
        {
            "run_id": run_id,
            "title": "Two Slices Shared Verifier",
            "goal": "Freeze lazy verifier across all associated steps",
            "file_ownership": {"author_a": ["calc.py"], "author_b": ["part2.py"]},
            "acceptance_criteria": ["AC-A", "AC-B"],
        },
    )
    board.worker_register(run_id, "author_b", "swarm_worker", task_name="author_b")
    board.worker_register(run_id, "shared_ver", "verifier")

    # Dispatch step A while step B remains planned sharing `shared_ver`.
    prep_a = dispatcher.dispatch(
        "workflow_prepare_dispatch",
        {
            "run_id": run_id,
            "owner": "author_a",
            "task_name": "author_a",
            "verifier": "shared_ver",
            "step_key": "step-a",
            "criteria": ["AC-A"],
            "instruction": "Work A",
        },
    )
    step_b = dispatcher.dispatch(
        "workflow_step_save",
        {
            "run_id": run_id,
            "step_key": "step-b",
            "owner": "author_b",
            "verifier": "shared_ver",
            "criteria": ["AC-B"],
        },
    )
    assert step_b["verifier_agent_id"] == ""

    dispatcher.dispatch(
        "native_action_record_call",
        {
            "run_id": run_id,
            "action_id": prep_a["action_id"],
            "claim_token": prep_a["claim_token"],
            "expected_revision": prep_a["expected_revision"],
            "outcome": "confirmed",
            "raw_response": {"agent_id": "/root/author_a"},
        },
    )
    dispatcher.dispatch(
        "workflow_author_turn",
        {"run_id": run_id, "step_id": prep_a["step"]["id"], "message_id": prep_a["message_id"], "phase": "accept"},
    )
    dispatcher.dispatch(
        "workflow_author_turn",
        {
            "run_id": run_id,
            "step_id": prep_a["step"]["id"],
            "message_id": prep_a["message_id"],
            "phase": "submit",
            "summary": "Done A",
        },
    )

    ver_prep = dispatcher.dispatch(
        "workflow_prepare_dispatch",
        {
            "run_id": run_id,
            "dispatch_action": "request_verify",
            "step_id": prep_a["step"]["id"],
            "verifier": "shared_ver",
            "task_name": "shared_ver",
            "instruction": "Review A",
        },
    )

    # Attempting to bind shared_ver to author_a's agent_id MUST be rejected!
    with pytest.raises(NativeActionConflictError):
        dispatcher.dispatch(
            "native_action_record_call",
            {
                "run_id": run_id,
                "action_id": ver_prep["action_id"],
                "claim_token": ver_prep["claim_token"],
                "expected_revision": ver_prep["expected_revision"],
                "outcome": "confirmed",
                "agent_id": "/root/author_a",
                "raw_response": {"agent_id": "/root/author_a", "agent_path": "/root/shared_ver"},
            },
        )
    assert db.get_worker_by_name(run_id, "shared_ver")["agent_id"] is None

    # Valid binding freezes shared_ver across BOTH step A and planned step B!
    dispatcher.dispatch(
        "native_action_record_call",
        {
            "run_id": run_id,
            "action_id": ver_prep["action_id"],
            "claim_token": ver_prep["claim_token"],
            "expected_revision": ver_prep["expected_revision"],
            "outcome": "confirmed",
            "raw_response": {"agent_id": "/root/shared_ver"},
        },
    )
    step_b_after = dispatcher.workflow.get_step(step_b["id"])
    assert step_b_after["verifier_agent_id"] == "/root/shared_ver"

    board.worker_register(run_id, "rogue_ver", "verifier", agent_id="/root/rogue_ver")
    with pytest.raises(WorkflowConflictError, match="original"):
        dispatcher.dispatch(
            "workflow_step_save",
            {
                "run_id": run_id,
                "step_key": "step-b",
                "owner": "author_b",
                "verifier": "rogue_ver",
                "step_id": step_b["id"],
                "expected_revision": step_b_after["revision"],
                "criteria": ["AC-B"],
            },
        )


def test_disconnect_fencing_blocks_409_and_reconciles_both_found_and_not_found_paths(wf5_env):
    _, project, settings, db, _, _, _ = wf5_env

    async def scenario():
        app = create_app(settings, db=db, start_watchdog=False, manage_pid_file=False)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:

                async def post_rpc(tool: str, **arguments):
                    return await client.post(
                        "/api/rpc", json={"tool": tool, "arguments": arguments}
                    )

                run = (
                    await post_rpc(
                        "run_create",
                        mode="swarm",
                        title="Disconnect Fencing",
                        project_root=str(project),
                    )
                ).json()["result"]
                run_id = run["id"]
                (project / "slice_b.py").write_text("B = 0\n", encoding="utf-8")

                await post_rpc(
                    "task_packet_save",
                    run_id=run_id,
                    title="Fencing Packet",
                    goal="Prevent double spawn after disconnect",
                    file_ownership={"coder_found": ["calc.py"], "coder_missing": ["slice_b.py"]},
                    acceptance_criteria=["AC-1", "AC-2"],
                )

                # --- Branch A: Disconnect after begin(spawn_agent), list_agents FINDS /root/coder_found ---
                prep_a = (
                    await post_rpc(
                        "workflow_prepare_dispatch",
                        run_id=run_id,
                        create_workspace=True,
                        owner="coder_found",
                        task_name="coder_found",
                        verifier="ver_a",
                        step_key="slice-a",
                        criteria=["AC-1"],
                        instruction="Implement slice A",
                    )
                ).json()["result"]

                # Simulate daemon restart / disconnect recovery fencing invoking -> needs_reconciliation.
                app.state.dispatcher.native_actions.recover()
                fenced_a = app.state.dispatcher.native_actions.get(prep_a["action_id"])
                assert fenced_a["status"] == "needs_reconciliation"

                # Attempting to re-dispatch or merge workspace BEFORE reconciliation MUST be rejected with HTTP 409!
                retry_resp = await post_rpc(
                    "workflow_prepare_dispatch",
                    run_id=run_id,
                    step_id=prep_a["step"]["id"],
                    owner="coder_found",
                    task_name="coder_found",
                    verifier="ver_a",
                    instruction="Blind retry forbidden",
                )
                assert retry_resp.status_code == 409

                merge_resp = await post_rpc(
                    "workspace_merge", workspace_id=prep_a["workspace"]["id"]
                )
                assert merge_resp.status_code == 409

                # Reconcile with list_agents result showing /root/coder_found exists -> atomically binds!
                rec_found = await post_rpc(
                    "native_action_record_call",
                    run_id=run_id,
                    action_id=prep_a["action_id"],
                    expected_revision=fenced_a["revision"],
                    reconcile=True,
                    outcome="confirmed",
                    raw_response={
                        "agents": [
                            {"agent_id": "/root/coder_found", "agent_path": "/root/coder_found"}
                        ]
                    },
                )
                assert rec_found.status_code == 200, rec_found.text
                assert db.get_worker_by_name(run_id, "coder_found")["agent_id"] == "/root/coder_found"
                assert app.state.workflow.get_step(prep_a["step"]["id"])["owner_agent_id"] == "/root/coder_found"

                # --- Branch B: Lease timeout after begin(spawn_agent), list_agents proves NOT FOUND -> cancel & re-dispatch ---
                prep_b = (
                    await post_rpc(
                        "workflow_prepare_dispatch",
                        run_id=run_id,
                        create_workspace=True,
                        owner="coder_missing",
                        task_name="coder_missing",
                        verifier="ver_a",
                        step_key="slice-b",
                        criteria=["AC-2"],
                        instruction="Implement slice B",
                    )
                ).json()["result"]

                with db.transaction() as conn:
                    conn.execute(
                        "UPDATE native_actions SET lease_expires_at = 0 WHERE action_id = ?;",
                        (prep_b["action_id"],),
                    )
                app.state.dispatcher.native_actions.expire()
                fenced_b = app.state.dispatcher.native_actions.get(prep_b["action_id"])
                assert fenced_b["status"] == "needs_reconciliation"

                # Re-dispatch blocked with 409 before reconciliation.
                blocked_b = await post_rpc(
                    "workflow_prepare_dispatch",
                    run_id=run_id,
                    step_id=prep_b["step"]["id"],
                    owner="coder_missing",
                    task_name="coder_missing",
                    verifier="ver_a",
                    instruction="Still blocked",
                )
                assert blocked_b.status_code == 409

                # Reconcile with outcome="not_called", cancel=True after list_agents proved empty -> releases step to planned!
                rec_missing = await post_rpc(
                    "native_action_record_call",
                    run_id=run_id,
                    action_id=prep_b["action_id"],
                    expected_revision=fenced_b["revision"],
                    reconcile=True,
                    cancel=True,
                    outcome="not_called",
                    raw_response={"agents": [], "note": "list_agents path_prefix=/root/coder_missing returned empty"},
                )
                assert rec_missing.status_code == 200, rec_missing.text
                assert rec_missing.json()["result"]["status"] == "cancelled"
                assert app.state.workflow.get_step(prep_b["step"]["id"])["state"] == "planned"

                # Now clean re-dispatch succeeds!
                redisp_b = await post_rpc(
                    "workflow_prepare_dispatch",
                    run_id=run_id,
                    step_id=prep_b["step"]["id"],
                    workspace_id=prep_b["workspace"]["id"],
                    owner="coder_missing",
                    task_name="coder_missing",
                    verifier="ver_a",
                    instruction="Clean re-dispatch after verified not_found",
                )
                assert redisp_b.status_code == 200, redisp_b.text
                assert redisp_b.json()["result"]["action_type"] == "spawn_agent"

    asyncio.run(scenario())


def test_schema7_to_schema8_migration_preserves_workflow0_to_4_semantics(tmp_path: Path):
    db_file = tmp_path / "legacy_schema7.db"
    proj_dir = tmp_path / "legacy_proj"
    proj_dir.mkdir()
    (proj_dir / "f.txt").write_text("legacy", encoding="utf-8")

    # Build a realistic schema 7 database containing workflow 0..4 runs.
    with closing(sqlite3.connect(db_file)) as conn, conn:
        conn.executescript(
            """
            CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at REAL NOT NULL);
            INSERT INTO schema_migrations VALUES (7, 1700000000.0);
            CREATE TABLE projects (
                id TEXT PRIMARY KEY, root_path TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
                is_git INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL
            );
            CREATE TABLE runs (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                mode TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
                metadata_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL,
                updated_at REAL NOT NULL, workflow_version INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE workers (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                name TEXT NOT NULL, role TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'waiting',
                workspace_id TEXT, current_task TEXT NOT NULL DEFAULT '',
                last_heartbeat_at REAL NOT NULL, stall_threshold_sec INTEGER NOT NULL DEFAULT 300,
                stalled_alerted INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
                updated_at REAL NOT NULL, agent_id TEXT, UNIQUE (run_id, name)
            );
            """
        )
        conn.execute(
            "INSERT INTO projects VALUES ('proj_1', ?, 'legacy', 0, 1.0);",
            (str(proj_dir.resolve()),),
        )
        for wf_v in range(5):
            conn.execute(
                "INSERT INTO runs VALUES (?, 'proj_1', 'swarm', ?, 'active', '{}', 10.0, 20.0, ?);",
                (f"run_wf{wf_v}", f"Legacy WF {wf_v}", wf_v),
            )
            conn.execute(
                "INSERT INTO workers VALUES (?, ?, 'old_worker', 'coder', 'waiting', NULL, '', 10.0, 300, 0, 10.0, 10.0, ?);",
                (f"wrk_{wf_v}", f"run_wf{wf_v}", f"/root/old_{wf_v}" if wf_v > 0 else None),
            )

    settings = Settings(data_dir=tmp_path / "migrated_state")
    db = Database(db_file, settings=settings)
    try:
        assert db.migrate() == SCHEMA_VERSION == 8
        for wf_v in range(5):
            r = db.get_run(f"run_wf{wf_v}")
            assert r["workflow_version"] == wf_v
            assert r["title"] == f"Legacy WF {wf_v}"
            w = db.get_worker_by_name(f"run_wf{wf_v}", "old_worker")
            assert w["pending_agent_path"] is None

        # workflow4 run MUST still reject workflow5 pre-spawn task_name and unbound step_save!
        board = BoardService(db, settings)
        dispatcher = RpcDispatcher(db, board, WorkspaceManager(db, settings))
        with pytest.raises(ValueError, match="workflow5"):
            dispatcher.dispatch(
                "worker_register",
                {"run_id": "run_wf4", "name": "new_coder", "role": "coder", "task_name": "new_coder"},
            )
        board.worker_register("run_wf4", "unbound_ver", "verifier", agent_id=None)
        db.save_task_packet(
            "run_wf4",
            title="WF4 Packet",
            goal="Keep strict pre-binding in WF4",
            file_ownership={"old_worker": ["f.txt"]},
            acceptance_criteria=["AC-WF4"],
        )
        with pytest.raises(WorkflowConflictError, match="binding"):
            dispatcher.dispatch(
                "workflow_step_save",
                {
                    "run_id": "run_wf4",
                    "step_key": "wf4-step",
                    "owner": "old_worker",
                    "verifier": "unbound_ver",
                },
            )
    finally:
        db.close()
