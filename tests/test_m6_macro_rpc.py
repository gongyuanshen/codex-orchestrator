"""M6 Macro RPC composite transactions, automatic SHA-256 receipts, env_profile, and M9 hook RPCs."""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

import httpx
import pytest

from orchestrator.board import BoardService
from orchestrator.config import Settings
from orchestrator.daemon import create_app
from orchestrator.db import Database
from orchestrator.inbox import InboxConflictError
from orchestrator.rpc_handlers import RpcDispatcher
from orchestrator.workflow import WorkflowConflictError
from orchestrator.workspace_mgr import WORKSPACES_DIRNAME, WorkspaceManager


@pytest.fixture
def macro_env(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (project / "other.py").write_text("OTHER = 2\n", encoding="utf-8")
    settings = Settings(data_dir=tmp_path / "data", port_range=(28800, 28850))
    db = Database(settings=settings)
    board = BoardService(db, settings)
    ws_mgr = WorkspaceManager(db, settings)
    dispatcher = RpcDispatcher(db, board, ws_mgr)
    yield project, settings, db, board, ws_mgr, dispatcher
    db.close()


def test_prepare_dispatch_atomic_rollback_cleans_workspace_dir_on_mid_tx_failure(macro_env):
    project, _, db, board, _, dispatcher = macro_env
    run = dispatcher.dispatch(
        "run_create",
        {
            "mode": "swarm",
            "title": "M6 Rollback Atomicity",
            "project_root": str(project),
            "session_id": "sess_init",
        },
    )
    run_id = run["id"]
    dispatcher.dispatch(
        "task_packet_save",
        {
            "run_id": run_id,
            "title": "Frozen Ownership",
            "goal": "Test atomic rollback",
            "file_ownership": {"coder_a": ["app.py"], "coder_b": ["app.py"]},
            "acceptance_criteria": ["AC-1"],
        },
    )

    # First author claims ownership on app.py cleanly.
    first = dispatcher.dispatch(
        "workflow_prepare_dispatch",
        {
            "run_id": run_id,
            "create_workspace": True,
            "owner": "coder_a",
            "task_name": "coder_a",
            "verifier": "verifier_1",
            "step_key": "slice-a",
            "title": "Slice A",
            "instruction": "Implement slice A",
        },
    )
    assert Path(first["workspace"]["path"]).is_dir()

    ws_root = project / WORKSPACES_DIRNAME
    existing_dirs_before = set(ws_root.iterdir())
    worker_count_before = len(db.list_workers(run_id=run_id))
    ws_count_before = len(db.list_workspaces(run_id=run_id))
    steps_before = len(dispatcher.workflow._steps(run_id))

    # Case 1: Ownership conflict at step 4 (workflow_step_advance(dispatch)) after workspace_create + worker_register + step_save.
    with pytest.raises(WorkflowConflictError, match="ownership"):
        dispatcher.dispatch(
            "workflow_prepare_dispatch",
            {
                "run_id": run_id,
                "create_workspace": True,
                "owner": "coder_b",
                "task_name": "coder_b",
                "verifier": "verifier_1",
                "step_key": "slice-b-conflict",
                "title": "Conflicting Slice B",
                "instruction": "Should roll back completely",
                "session_id": "sess_rolled_back",
            },
        )

    assert set(ws_root.iterdir()) == existing_dirs_before
    assert len(db.list_workers(run_id=run_id)) == worker_count_before
    assert db.get_worker_by_name(run_id, "coder_b") is None
    assert len(db.list_workspaces(run_id=run_id)) == ws_count_before
    assert len(dispatcher.workflow._steps(run_id)) == steps_before
    assert db.get_session_binding("sess_rolled_back") is None

    # Case 2: Pending control_request failure on a second non-overlapping slice -> rolls back DB + removes newly created empty workspace dir.
    run2_project = project.parent / "project_clean"
    run2_project.mkdir()
    (run2_project / "mod.py").write_text("X = 0\n", encoding="utf-8")
    run2 = dispatcher.dispatch(
        "run_create",
        {"mode": "swarm", "title": "Control Block Rollback", "project_root": str(run2_project)},
    )
    dispatcher.dispatch(
        "task_packet_save",
        {
            "run_id": run2["id"],
            "title": "Control Test",
            "goal": "Pending control blocks prepare_dispatch",
            "file_ownership": {"coder_ctl": ["mod.py"]},
            "acceptance_criteria": ["AC-CTL"],
        },
    )
    guard_worker = board.worker_register(
        run2["id"], "existing_peer", "coder", agent_id="/root/existing_peer", status="working"
    )
    db.request_worker_control(guard_worker["id"], "stopped")

    with pytest.raises(WorkflowConflictError, match="control"):
        dispatcher.dispatch(
            "workflow_prepare_dispatch",
            {
                "run_id": run2["id"],
                "create_workspace": True,
                "owner": "coder_ctl",
                "task_name": "coder_ctl",
                "verifier": "ver_ctl",
                "step_key": "ctl-blocked",
                "instruction": "Blocked by pending control",
            },
        )

    assert not (run2_project / WORKSPACES_DIRNAME).exists()
    assert db.get_worker_by_name(run2["id"], "coder_ctl") is None
    assert db.list_workspaces(run_id=run2["id"]) == []
    assert dispatcher.workflow._steps(run2["id"]) == []


def test_native_action_record_call_persists_exact_sha256_and_rejects_disk_failure(macro_env):
    project, settings, db, _, _, dispatcher = macro_env
    run = dispatcher.dispatch(
        "run_create",
        {"mode": "boost", "title": "Record Call Receipt", "project_root": str(project)},
    )
    run_id = run["id"]
    dispatcher.dispatch(
        "task_packet_save",
        {
            "run_id": run_id,
            "title": "Receipt Check",
            "goal": "Verify server-side SHA256 receipt persistence",
            "file_ownership": {"author_1": ["app.py"]},
            "acceptance_criteria": ["AC-1"],
        },
    )
    prep = dispatcher.dispatch(
        "workflow_prepare_dispatch",
        {
            "run_id": run_id,
            "owner": "author_1",
            "task_name": "author_1",
            "verifier": "verifier_1",
            "step_key": "step-1",
            "instruction": "Write feature",
        },
    )
    assert set(prep) >= {
        "step",
        "workspace",
        "worker",
        "message_id",
        "board_seq",
        "action_id",
        "action_type",
        "target_agent_path",
        "claim_token",
        "expected_revision",
        "lease_expires_at",
        "native_call_hint",
    }
    assert prep["action_type"] == "spawn_agent"
    assert prep["target_agent_path"] == "/root/author_1"

    # Simulate disk write failure by occupying <data_dir>/receipts with a regular file.
    receipts_blocker = settings.data_dir / "receipts"
    receipts_blocker.write_text("not a directory", encoding="utf-8")

    with pytest.raises(RuntimeError, match="receipt"):
        dispatcher.dispatch(
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

    # Action MUST remain invoking and never be marked confirmed on disk failure!
    stored = dispatcher.native_actions.get(prep["action_id"])
    assert stored["status"] == "invoking"
    assert db.get_worker_by_name(run_id, "author_1")["agent_id"] is None

    # Restore directory capability and confirm cleanly.
    receipts_blocker.unlink()
    recorded = dispatcher.dispatch(
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
    assert recorded["status"] == "confirmed"
    receipt_file = Path(recorded["evidence_ref"])
    assert receipt_file.is_absolute() and receipt_file.is_file()
    exact_sha256 = hashlib.sha256(receipt_file.read_bytes()).hexdigest()
    assert recorded["evidence_digest"] == exact_sha256
    assert len(recorded["evidence_digest"]) == 64

    # Also verify grouped record_call automatic receipt persistence & SHA-256 matching.
    run_grp = dispatcher.dispatch(
        "run_create",
        {"mode": "swarm", "title": "Group Record Call", "project_root": str(project)},
    )
    gid_run = run_grp["id"]
    dispatcher.dispatch(
        "task_packet_save",
        {
            "run_id": gid_run,
            "title": "Group Packet",
            "goal": "Grouped native_action_record_call",
            "file_ownership": {"grp_author": ["other.py"]},
            "acceptance_criteria": ["AC-GRP"],
        },
    )
    dispatcher.board.worker_register(gid_run, "grp_author", "swarm_worker", agent_id="/root/grp_author")
    dispatcher.board.worker_register(gid_run, "grp_ver", "verifier", agent_id="/root/grp_ver")
    grp_step = dispatcher.workflow.step_save(gid_run, "grp-step", "grp_author", "grp_ver")
    grp_step = dispatcher.workflow.step_advance(grp_step["id"], "dispatch", grp_step["revision"])
    dispatcher.inbox.send(gid_run, "coordinator", "grp_author", "g1", "task", "Task 1", "Body 1", step_id=grp_step["id"])
    dispatcher.inbox.send(gid_run, "coordinator", "grp_author", "g2", "task", "Task 2", "Body 2", step_id=grp_step["id"])

    proj_group = dispatcher.native_actions.project(gid_run)["next_action_groups"][0]
    claimed_grp = dispatcher.dispatch(
        "native_action_update",
        {
            "action": "claim_group",
            "run_id": gid_run,
            "recipient": "grp_author",
            "action_type": "followup_task",
            "members": [
                {k: m[k] for k in ("source_type", "source_id", "source_digest")}
                for m in proj_group["members"]
            ],
        },
    )
    begun_grp = dispatcher.dispatch(
        "native_action_update",
        {
            "action": "begin_group",
            "group_id": claimed_grp["group_id"],
            "members": [
                {
                    "action_id": m["action_id"],
                    "expected_revision": m["revision"],
                    "claim_token": m["claim_token"],
                }
                for m in claimed_grp["members"]
            ],
        },
    )
    rec_grp = dispatcher.dispatch(
        "native_action_record_call",
        {
            "run_id": gid_run,
            "group_id": begun_grp["group_id"],
            "members": [
                {
                    "action_id": m["action_id"],
                    "expected_revision": m["revision"],
                    "claim_token": m["claim_token"],
                }
                for m in begun_grp["members"]
            ],
            "outcome": "confirmed",
            "raw_response": {"batch_delivered": 2},
        },
    )
    grp_file = Path(rec_grp["evidence_ref"])
    assert grp_file.is_file()
    assert rec_grp["evidence_digest"] == hashlib.sha256(grp_file.read_bytes()).hexdigest()
    assert all(m["status"] == "confirmed" for m in rec_grp["members"])


def test_workflow_author_turn_accept_submit_and_env_profile_never_stores_env_values(macro_env, monkeypatch):
    import os
    project, settings, db, _, _, dispatcher = macro_env
    secret_sentinel = "SUPER_SECRET_TOKEN_VALUE_DO_NOT_SERIALIZE_99182"
    monkeypatch.setenv("PATH", os.environ.get("PATH", "") + os.pathsep + secret_sentinel)
    monkeypatch.setenv("CUSTOM_KEY", secret_sentinel)
    monkeypatch.setenv("SYSTEMROOT", os.environ.get("SYSTEMROOT") or r"C:\Windows")

    run = dispatcher.dispatch(
        "run_create",
        {"mode": "swarm", "title": "Author Macro Turn & Env Profile", "project_root": str(project)},
    )
    run_id = run["id"]
    dispatcher.dispatch("worker_register", {"run_id": run_id, "name": "coordinator", "role": "coder", "agent_id": "/root"})
    dispatcher.dispatch(
        "task_packet_save",
        {
            "run_id": run_id,
            "title": "Author Turn",
            "goal": "Test accept/submit macro and env_profile",
            "file_ownership": {"coder_1": ["app.py"]},
            "acceptance_criteria": ["AC-ENV"],
        },
    )
    prep = dispatcher.dispatch(
        "workflow_prepare_dispatch",
        {
            "run_id": run_id,
            "create_workspace": True,
            "owner": "coder_1",
            "task_name": "coder_1",
            "verifier": "verifier_1",
            "step_key": "env-step",
            "instruction": "Update app.py",
        },
    )
    dispatcher.dispatch(
        "native_action_record_call",
        {
            "run_id": run_id,
            "action_id": prep["action_id"],
            "claim_token": prep["claim_token"],
            "expected_revision": prep["expected_revision"],
            "outcome": "confirmed",
            "raw_response": {"agent_id": "/root/coder_1"},
        },
    )

    step_id = prep["step"]["id"]
    msg_id = prep["message_id"]

    # Wrong author cannot accept.
    with pytest.raises(WorkflowConflictError):
        dispatcher.dispatch(
            "workflow_author_turn",
            {
                "run_id": run_id,
                "step_id": step_id,
                "message_id": msg_id,
                "phase": "accept",
                "author": "verifier_1",
            },
        )

    accepted = dispatcher.dispatch(
        "workflow_author_turn",
        {
            "run_id": run_id,
            "step_id": step_id,
            "message_id": msg_id,
            "phase": "accept",
            "summary": "Accepted slice",
        },
    )
    assert accepted["step"]["state"] == "implementing"
    assert accepted["message"]["status"] == "accepted"

    ws_path = Path(prep["workspace"]["path"])
    (ws_path / "app.py").write_text("VALUE = 42\n", encoding="utf-8")

    submitted = dispatcher.dispatch(
        "workflow_author_turn",
        {
            "run_id": run_id,
            "step_id": step_id,
            "message_id": msg_id,
            "phase": "submit",
            "summary": "Updated VALUE to 42",
            "post_board_note": True,
        },
    )
    assert submitted["step"]["state"] == "candidate_review"
    assert submitted["message"]["status"] == "completed"
    assert submitted["board_pointer"] is not None
    assert "#verify" in submitted["board_pointer"]["summary"]
    # Only the coordinator is bound before the first lazy verifier spawn.
    assert submitted["board_pointer"]["wake_targets"] == [{"name": "coordinator", "agent_id": "/root"}]

    # Dispatch lazy verifier and capture candidate with windows_minimal_py & posix_minimal_py.
    ver_prep = dispatcher.dispatch(
        "workflow_prepare_dispatch",
        {
            "run_id": run_id,
            "dispatch_action": "request_verify",
            "step_id": step_id,
            "verifier": "verifier_1",
            "task_name": "verifier_1",
            "instruction": "Verify candidate",
        },
    )
    dispatcher.dispatch(
        "native_action_record_call",
        {
            "run_id": run_id,
            "action_id": ver_prep["action_id"],
            "claim_token": ver_prep["claim_token"],
            "expected_revision": ver_prep["expected_revision"],
            "outcome": "confirmed",
            "raw_response": {"agent_id": "/root/verifier_1"},
        },
    )

    art_win = dispatcher.dispatch(
        "artifact_capture",
        {
            "step_id": step_id,
            "scope": "candidate",
            "env_profile": "windows_minimal_py",
            "verification_commands": [
                {
                    "id": "py-check",
                    "argv": [sys.executable, "-c", "print('ok')"],
                    "cwd": str(settings.data_dir),
                    "timeout_sec": 10,
                    "env_keys": ["CUSTOM_KEY"],
                    "criteria": ["AC-ENV"],
                }
            ],
        },
    )
    manifest_raw = Path(art_win["manifest_path"]).read_text(encoding="utf-8")
    assert secret_sentinel not in manifest_raw
    assert r"C:\Windows" not in manifest_raw
    frozen_keys = art_win["descriptor"]["verification_manifest"]["commands"][0]["env_keys"]
    assert frozen_keys == [
        "CUSTOM_KEY",
        "SYSTEMROOT",
        "SystemRoot",
        "COMSPEC",
        "PATHEXT",
        "PATH",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "PYTHONUTF8",
        "PYTHONIOENCODING",
    ]

    art_posix = dispatcher.dispatch(
        "artifact_capture",
        {
            "step_id": step_id,
            "scope": "candidate",
            "env_profile": "posix_minimal_py",
            "verification_commands": [
                {
                    "id": "posix-check",
                    "argv": [sys.executable, "-c", "print('ok')"],
                    "cwd": str(settings.data_dir),
                    "timeout_sec": 10,
                    "criteria": ["AC-ENV"],
                }
            ],
        },
    )
    posix_keys = art_posix["descriptor"]["verification_manifest"]["commands"][0]["env_keys"]
    assert posix_keys == [
        "PATH",
        "HOME",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "PYTHONUTF8",
        "PYTHONIOENCODING",
    ]
    with db.read_connection() as conn:
        db_dump = json.dumps(
            [dict(r) for r in conn.execute("SELECT * FROM workflow_artifacts;").fetchall()]
        )
    assert secret_sentinel not in db_dump


def test_session_hook_rpc_enforces_all_three_safety_guardrails(macro_env):
    project, settings, db, board, _, _ = macro_env

    async def scenario():
        app = create_app(settings, db=db, start_watchdog=False, manage_pid_file=False)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:

                async def rpc(tool: str, **kwargs):
                    resp = await client.post(
                        "/api/rpc", json={"tool": tool, "arguments": kwargs}
                    )
                    assert resp.status_code == 200, resp.text
                    return resp.json()["result"]

                # Guardrail 1a: Unbound ordinary chat session passes through silently.
                unbound = await rpc(
                    "session_hook_check",
                    session_id="ordinary_chat_session",
                    event_name="Stop",
                    cwd=str(project),
                )
                assert unbound["bound"] is False
                assert unbound["should_block_stop"] is False

                # Create active run bound to root_sess_1.
                run = await rpc(
                    "run_create",
                    mode="swarm",
                    title="Hook Guardrails",
                    project_root=str(project),
                    session_id="root_sess_1",
                )
                run_id = run["id"]
                await rpc(
                    "task_packet_save",
                    run_id=run_id,
                    title="Hook Packet",
                    goal="Verify 3 guardrails",
                    file_ownership={"coder_1": ["app.py"]},
                    acceptance_criteria=["AC-HOOK"],
                )

                # Guardrail 3a: normal_wait (no ready next_actions yet) -> pass through!
                wait_check = await rpc(
                    "session_hook_check",
                    session_id="root_sess_1",
                    event_name="Stop",
                    cwd=str(project),
                    mark_stop_blocked=True,
                )
                assert wait_check["bound"] is True
                assert wait_check["should_block_stop"] is False
                assert len(wait_check["resume_context"]) <= 800

                # Register bound workers and create an unblocked ready next_action (human guidance to coder_1).
                await rpc(
                    "worker_register",
                    run_id=run_id,
                    name="coder_1",
                    role="swarm_worker",
                    agent_id="/root/coder_1",
                )
                await rpc(
                    "worker_register",
                    run_id=run_id,
                    name="verifier_1",
                    role="verifier",
                    agent_id="/root/verifier_1",
                )
                await rpc(
                    "board_post",
                    run_id=run_id,
                    sender="human",
                    sender_role="human",
                    channel="human",
                    msg_type="human",
                    mentions=["coder_1"],
                    summary=" Clarify detail",
                    body_md="@coder_1 please check edge case",
                )

                # Guardrail 1b: Different repo cwd or subagent workspace cwd -> never block!
                other_cwd = await rpc(
                    "session_hook_check",
                    session_id="root_sess_1",
                    event_name="Stop",
                    cwd=str(project.parent / "unrelated_repo"),
                    mark_stop_blocked=True,
                )
                assert other_cwd["bound"] is False
                assert other_cwd["should_block_stop"] is False

                sub_ws_cwd = await rpc(
                    "session_hook_check",
                    session_id="root_sess_1",
                    event_name="Stop",
                    cwd=str(project / WORKSPACES_DIRNAME / "run-coder_1"),
                    mark_stop_blocked=True,
                )
                assert sub_ws_cwd["should_block_stop"] is False

                # Guardrail 2a: stop_hook_active=True -> never block!
                reentrant = await rpc(
                    "session_hook_check",
                    session_id="root_sess_1",
                    event_name="Stop",
                    cwd=str(project),
                    stop_hook_active=True,
                    mark_stop_blocked=True,
                )
                assert reentrant["should_block_stop"] is False

                # Guardrail 3b: Pending control request overrides ready next_actions -> never block!
                w_coder = db.get_worker_by_name(run_id, "coder_1")
                ctl = db.request_worker_control(w_coder["id"], "stopped")
                ctl_check = await rpc(
                    "session_hook_check",
                    session_id="root_sess_1",
                    event_name="Stop",
                    cwd=str(project),
                    mark_stop_blocked=True,
                )
                assert ctl_check["should_block_stop"] is False
                db.worker_control_ack(ctl["id"], "failed", "user cancelled pause for test")

                # Sole trigger: active run + unblocked ready next_actions + first time on this state key -> BLOCK once!
                blocked_once = await rpc(
                    "session_hook_check",
                    session_id="root_sess_1",
                    event_name="Stop",
                    cwd=str(project),
                    mark_stop_blocked=True,
                )
                assert blocked_once["should_block_stop"] is True
                assert run_id in blocked_once["stop_reason"]
                assert blocked_once["blocked_key"]

                # Guardrail 2b: Second check on identical state key -> immediately pass through!
                second_pass = await rpc(
                    "session_hook_check",
                    session_id="root_sess_1",
                    event_name="Stop",
                    cwd=str(project),
                    mark_stop_blocked=True,
                )
                assert second_pass["should_block_stop"] is False

                # PostCompact check returns concise <=800 char resume_context without blocking stop.
                compact_ctx = await rpc(
                    "session_hook_check",
                    session_id="root_sess_1",
                    event_name="PostCompact",
                    cwd=str(project),
                )
                assert compact_ctx["bound"] is True
                assert compact_ctx["should_block_stop"] is False
                assert 0 < len(compact_ctx["resume_context"]) <= 800

    asyncio.run(scenario())
