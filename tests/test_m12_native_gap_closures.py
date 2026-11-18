"""M12 comprehensive fail-first & regression suite for native gap closures (`v0.6.0`)."""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from orchestrator.board import (
    BoardService,
    _strip_artifact_manifests,
    _strip_step_manifests,
    _strip_workflow_manifests,
    extract_mentions,
)
from orchestrator.config import Settings
from orchestrator.db import Database
from orchestrator.mcp_shim import _deliver_tool_result, fetch_health
from orchestrator.native_actions import NativeActionConflictError, NativeActionStore
from orchestrator.rpc_handlers import (
    INTERNAL_RPC_DEFINITIONS,
    RpcDispatcher,
    _discover_cli_executables,
    get_tool_definitions,
)
from orchestrator.verify_runner import collect as collect_verify_receipt
from orchestrator.watchdog import classify_run
from orchestrator.workflow import WorkflowConflictError, WorkflowService
from orchestrator.workspace_mgr import WORKSPACES_DIRNAME, WorkspaceManager


@pytest.fixture
def env(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    (project / "lib.py").write_text("LIB = 2\n", encoding="utf-8")
    settings = Settings(data_dir=tmp_path / "data", port_range=(29100, 29150))
    db = Database(settings=settings)
    board = BoardService(db, settings)
    ws_mgr = WorkspaceManager(db, settings)
    dispatcher = RpcDispatcher(db, board, ws_mgr)
    yield project, settings, db, board, ws_mgr, dispatcher
    db.close()


def _verify_cmd(target_path: Path, criterion: str, cmd_id: str = "check") -> list[dict[str, Any]]:
    env_profile = "windows_minimal_py" if os.name == "nt" else "posix_minimal_py"
    return [
        {
            "id": cmd_id,
            "argv": [sys.executable, "-c", "import sys; print('ok'); sys.exit(0)"],
            "cwd": str(target_path),
            "timeout_sec": 15,
            "criteria": [criterion],
            "_profile": env_profile,
        }
    ]


# ===========================================================================
# 1. Real codex.exe V2 `spawn_agent` & `list_agents` Wire Payloads
# ===========================================================================


class TestCodexV2WirePayloads:
    def test_v2_spawn_agent_task_name_binds_end_to_end_without_explicit_agent_id(self, env):
        project, _, db, _, _, dispatcher = env
        run = dispatcher.dispatch(
            "run_create",
            {"mode": "swarm", "title": "V2 Wire Spawn", "project_root": str(project)},
        )
        run_id = run["id"]
        dispatcher.dispatch(
            "task_packet_save",
            {
                "run_id": run_id,
                "title": "V2 Packet",
                "goal": "Bind V2 task_name",
                "file_ownership": {"coder_1": ["main.py"], "coder_2": ["lib.py"]},
                "acceptance_criteria": ["AC-1", "AC-2"],
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
                "step_key": "slice-1",
                "criteria": ["AC-1"],
                "instruction": "Implement slice 1",
            },
        )
        # Real codex.exe V2 collaboration.spawn_agent returns {"task_name": "/root/coder_1"}
        recorded = dispatcher.dispatch(
            "native_action_record_call",
            {
                "run_id": run_id,
                "action_id": prep["action_id"],
                "claim_token": prep["claim_token"],
                "expected_revision": prep["expected_revision"],
                "outcome": "confirmed",
                "raw_response": {"task_name": "/root/coder_1"},
            },
        )
        assert recorded["status"] == "confirmed"
        worker = db.get_worker_by_name(run_id, "coder_1")
        assert worker is not None
        assert worker["agent_id"] == "/root/coder_1"
        step = WorkflowService(db).get_step(prep["step"]["id"])
        assert step["owner_agent_id"] == "/root/coder_1"

        # Test V2 collaboration.list_agents reconciliation payload (`agent_name` + dict `agent_status`)
        prep2 = dispatcher.dispatch(
            "workflow_prepare_dispatch",
            {
                "run_id": run_id,
                "create_workspace": True,
                "owner": "coder_2",
                "task_name": "coder_2",
                "verifier": "verifier_1",
                "step_key": "slice-2",
                "criteria": ["AC-2"],
                "instruction": "Implement slice 2",
            },
        )
        with db.transaction() as conn:
            conn.execute(
                "UPDATE native_actions SET lease_expires_at = ? WHERE action_id = ?",
                (time.time() - 10.0, prep2["action_id"]),
            )
        NativeActionStore(db).expire()
        fenced = NativeActionStore(db).get(prep2["action_id"])
        assert fenced["status"] == "needs_reconciliation"

        reconciled = dispatcher.dispatch(
            "native_action_record_call",
            {
                "run_id": run_id,
                "action_id": fenced["action_id"],
                "expected_revision": fenced["revision"],
                "reconcile": True,
                "outcome": "confirmed",
                "raw_response": {
                    "agents": [
                        {"agent_name": "/root", "agent_status": "running"},
                        {"agent_name": "/root/coder_2", "agent_status": {"completed": "done"}},
                    ]
                },
            },
        )
        assert reconciled["status"] == "confirmed"
        worker2 = db.get_worker_by_name(run_id, "coder_2")
        assert worker2 is not None
        assert worker2["agent_id"] == "/root/coder_2"

    def test_v2_task_name_and_agent_name_mismatch_rejected(self):
        with pytest.raises(NativeActionConflictError):
            NativeActionStore._resolve_spawn_binding(
                "/root/coder_1", None, json.dumps({"task_name": "/root/coder_wrong"})
            )
        with pytest.raises(NativeActionConflictError):
            NativeActionStore._resolve_spawn_binding(
                "/root/coder_1",
                None,
                json.dumps({"agents": [{"agent_name": "/root/other", "agent_status": "running"}]}),
            )


# ===========================================================================
# 2. Manifest Stripping, MCP Compact Serialization, Prune & Hidden Hook Tool
# ===========================================================================


class TestTokenBloatAndMcpDelivery:
    def test_manifest_stripping_across_prepare_compact_board_and_artifact_capture(self, env):
        project, _, db, board, _, dispatcher = env
        for idx in range(40):
            (project / f"mod_{idx:02d}.py").write_text(f"X = {idx}\n", encoding="utf-8")

        run = dispatcher.dispatch(
            "run_create",
            {"mode": "swarm", "title": "Manifest Bloat Check", "project_root": str(project)},
        )
        run_id = run["id"]
        dispatcher.dispatch(
            "task_packet_save",
            {
                "run_id": run_id,
                "title": "60-file Packet",
                "goal": "Strip giant manifests",
                "file_ownership": {"coder_1": ["main.py"]},
                "acceptance_criteria": ["AC-STRIP"],
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
                "step_key": "strip-slice",
                "instruction": "Touch main.py",
            },
        )
        assert "snapshot_manifest" not in prep["workspace"]
        assert "snapshot_manifest_json" not in prep["workspace"]
        adm = prep["step"]["checkpoint"]["admission"]
        assert "base_manifest" not in adm
        assert adm["base_manifest_file_count"] >= 42

        compact = board.board_status(run_id, compact=True)
        step_cp = compact["workflow"]["steps"][0]["checkpoint"]
        assert "base_manifest" not in step_cp["admission"]
        assert step_cp["admission"]["base_manifest_file_count"] >= 42
        assert step_cp["execution_kind"] == "native"
        assert "cli_executables" in compact

        # Direct unit check on merge/merge_history/handoff.pack/descriptor stripping
        synthetic_step = {
            "id": "s_syn",
            "state": "handoff",
            "checkpoint": {
                "execution_kind": "native",
                "complexity": "high",
                "review_dimensions": {"v1": {"specification": "PASS"}},
                "findings": [{"id": "f1", "status": "open"}],
                "challenge": {"artifact_id": "art_1"},
                "admission": {"base_manifest": {"a.py": "11", "b.py": "22"}},
                "merge": {
                    "merge_id": "m1",
                    "desired_manifest": {"a.py": "33"},
                    "observed_before_manifest": {"a.py": "11"},
                    "proof": {"merge_id": "m1", "mode": "snapshot", "target_manifest": {"a.py": "33"}},
                },
                "merge_history": [{"merge_id": "m1", "before_manifest": {"a.py": "11"}, "after_manifest": {"a.py": "33"}}],
                "handoff": {
                    "digest": "hd_123",
                    "pack": {
                        "sender_agent_id": "/root/c1",
                        "recipient_agent_id": "/root/c2",
                        "facts_digest": "fd_456",
                        "next_responsibility": "Continue phase 2",
                        "facts": {"huge": "payload"},
                    },
                },
                "descriptor": {"manifest": {"a.py": "33"}},
            },
        }
        stripped = _strip_step_manifests(synthetic_step)["checkpoint"]
        assert "base_manifest" not in stripped["admission"]
        assert stripped["admission"]["base_manifest_file_count"] == 2
        assert "desired_manifest" not in stripped["merge"]
        assert "target_manifest" not in stripped["merge"]["proof"]
        assert stripped["merge"]["proof"]["merge_id"] == "m1"
        assert stripped["merge"]["proof"]["target_manifest_file_count"] == 1
        assert "merge_history" not in stripped
        assert stripped["merge_history_count"] == 1
        assert "pack" not in stripped["handoff"]
        assert stripped["handoff"]["digest"] == "hd_123"
        assert stripped["handoff"]["recipient_agent_id"] == "/root/c2"
        assert "manifest" not in stripped["descriptor"]
        assert stripped["execution_kind"] == "native"
        assert stripped["complexity"] == "high"
        assert stripped["review_dimensions"] == {"v1": {"specification": "PASS"}}
        assert stripped["findings"] == [{"id": "f1", "status": "open"}]
        assert stripped["challenge"] == {"artifact_id": "art_1"}

        wf_stripped = _strip_workflow_manifests({"steps": [synthetic_step]})
        assert "base_manifest" not in wf_stripped["steps"][0]["checkpoint"]["admission"]

        art_stripped = _strip_artifact_manifests(
            {
                "id": "art_1",
                "descriptor": {
                    "manifest": {"a.py": "33", "b.py": "44"},
                    "verification_manifest": {
                        "commands": [{"id": "pytest", "env_keys": ["PATH"]}],
                        "artifact": {"manifest": {"a.py": "33", "b.py": "44"}},
                    },
                },
            }
        )
        assert "manifest" not in art_stripped["descriptor"]
        assert art_stripped["descriptor"]["manifest_file_count"] == 2
        assert art_stripped["descriptor"]["verification_manifest"]["commands"][0]["id"] == "pytest"
        assert "manifest" not in art_stripped["descriptor"]["verification_manifest"]["artifact"]

    def test_mcp_inline_compact_overflow_pretty_and_stale_pruning(self, tmp_path: Path):
        settings = Settings(data_dir=tmp_path / "shim_data")
        inline_res = _deliver_tool_result(settings, "board_status", {"ok": True, "items": [1, 2, 3]}, is_error=False)
        inline_text = inline_res.content[0].text
        assert inline_text == '{"ok":true,"items":[1,2,3]}'

        results_dir = settings.data_dir / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        stale_mtime = time.time() - 90000
        for idx in range(55):
            stale_file = results_dir / f"stale_{idx:03d}.json"
            stale_file.write_text("{}", encoding="utf-8")
            os.utime(stale_file, (stale_mtime, stale_mtime))

        huge_payload = {f"k_{i}": "v" * 2000 for i in range(600)}
        overflow_res = _deliver_tool_result(settings, "board_status", huge_payload, is_error=False)
        meta = json.loads(overflow_res.content[0].text)
        assert meta["delivery"] == "file"
        saved_text = Path(meta["result_file"]).read_text(encoding="utf-8")
        assert "\n  " in saved_text
        assert json.loads(saved_text) == huge_payload
        # Stale >24h files were pruned
        remaining = list(results_dir.glob("*.json"))
        assert len(remaining) == 1

    def test_session_hook_check_hidden_from_mcp_catalog_but_active_on_rpc(self, env):
        project, _, _, _, _, dispatcher = env
        public_names = {t["name"] for t in get_tool_definitions()}
        internal_names = {t["name"] for t in INTERNAL_RPC_DEFINITIONS}
        assert "session_hook_check" not in public_names
        assert "session_hook_check" in internal_names
        assert {"workflow_verifier_turn", "workflow_closeout_run", "workflow_author_turn"} <= public_names

        res = dispatcher.dispatch(
            "session_hook_check",
            {"session_id": "unbound_sess", "event_name": "Stop", "cwd": str(project)},
        )
        assert res["bound"] is False


# ===========================================================================
# 3. End-to-End Macro Turns, CLI Discovery & Teamwork Milestone Usability
# ===========================================================================


class TestMacrosAndCliDiscoveryEndToEnd:
    def test_cli_discovery_and_full_boost_macro_lifecycle_with_rework_and_closeout(self, env, tmp_path: Path):
        project, _, db, board, _, dispatcher = env
        cli_map = _discover_cli_executables()
        assert set(cli_map.keys()) == {"verify", "challenge"}

        run = dispatcher.dispatch(
            "run_create",
            {
                "mode": "boost",
                "title": "Full Macro Lifecycle",
                "project_root": str(project),
                "session_id": "sess_macro_e2e",
            },
        )
        run_id = run["id"]
        assert "cli_executables" in run

        dispatcher.dispatch(
            "task_packet_save",
            {
                "run_id": run_id,
                "title": "Two-Step Boost DAG",
                "goal": "End-to-end macro verification and closeout",
                "file_ownership": {"coder_1": ["main.py"], "coder_2": ["lib.py"]},
                "acceptance_criteria": ["AC-MAIN", "AC-LIB"],
            },
        )

        # Step 1: prepare + V2 spawn bind
        prep1 = dispatcher.dispatch(
            "workflow_prepare_dispatch",
            {
                "run_id": run_id,
                "create_workspace": True,
                "owner": "coder_1",
                "task_name": "coder_1",
                "verifier": "verifier_1",
                "step_key": "step-main",
                "criteria": ["AC-MAIN"],
                "instruction": "Update main.py",
            },
        )
        step1_id = prep1["step"]["id"]
        dispatcher.dispatch(
            "native_action_record_call",
            {
                "run_id": run_id,
                "action_id": prep1["action_id"],
                "claim_token": prep1["claim_token"],
                "expected_revision": prep1["expected_revision"],
                "outcome": "confirmed",
                "raw_response": {"task_name": "/root/coder_1"},
            },
        )

        # Reset coder_1 to waiting first to prove workflow_author_turn("accept") sets working + heartbeat
        db.update_worker_status(status="waiting", run_id=run_id, worker_name="coder_1")
        hb_before = db.get_worker_by_name(run_id, "coder_1")["last_heartbeat_at"]
        time.sleep(0.01)

        acc1 = dispatcher.dispatch(
            "workflow_author_turn",
            {
                "run_id": run_id,
                "step_id": step1_id,
                "message_id": prep1["message_id"],
                "phase": "accept",
            },
        )
        assert acc1["step"]["state"] == "implementing"
        w_coder1 = db.get_worker_by_name(run_id, "coder_1")
        assert w_coder1["status"] == "working"
        assert w_coder1["last_heartbeat_at"] >= hb_before

        # Stop hook MUST NOT block while author is actively implementing with zero coordinator transitions
        stop_during_impl = dispatcher.dispatch(
            "session_hook_check",
            {"session_id": "sess_macro_e2e", "event_name": "Stop", "cwd": str(project)},
        )
        assert stop_during_impl["should_block_stop"] is False

        ws1_path = Path(prep1["workspace"]["path"])
        (ws1_path / "main.py").write_text("VALUE = 10\n", encoding="utf-8")

        sub1 = dispatcher.dispatch(
            "workflow_author_turn",
            {
                "run_id": run_id,
                "step_id": step1_id,
                "message_id": prep1["message_id"],
                "phase": "submit",
                "summary": "Initial candidate",
                "body_md": "已完成初版修改，移交@verifier_1请复核",
            },
        )
        assert sub1["step"]["state"] == "candidate_review"
        assert sub1["board_pointer"] is not None
        assert "verifier_1" in sub1["board_pointer"]["mentions"]
        assert db.get_worker_by_name(run_id, "coder_1")["status"] == "waiting"

        # Stop hook MUST block when step sits in candidate_review with no verifier review dispatched yet!
        stop_cand = dispatcher.dispatch(
            "session_hook_check",
            {"session_id": "sess_macro_e2e", "event_name": "Stop", "cwd": str(project)},
        )
        assert stop_cand["should_block_stop"] is True
        assert "candidate_review" in stop_cand["stop_reason"]

        # Coordinator dispatches lazy verifier
        ver_prep1 = dispatcher.dispatch(
            "workflow_prepare_dispatch",
            {
                "run_id": run_id,
                "dispatch_action": "request_verify",
                "step_id": step1_id,
                "verifier": "verifier_1",
                "task_name": "verifier_1",
                "instruction": "Verify step-main",
            },
        )
        # While review action is invoking/pending, Stop hook MUST NOT block coordinator
        stop_ver_inflight = dispatcher.dispatch(
            "session_hook_check",
            {"session_id": "sess_macro_e2e", "event_name": "Stop", "cwd": str(project)},
        )
        assert stop_ver_inflight["should_block_stop"] is False

        dispatcher.dispatch(
            "native_action_record_call",
            {
                "run_id": run_id,
                "action_id": ver_prep1["action_id"],
                "claim_token": ver_prep1["claim_token"],
                "expected_revision": ver_prep1["expected_revision"],
                "outcome": "confirmed",
                "raw_response": {"task_name": "/root/verifier_1"},
            },
        )
        db.update_worker_status(status="waiting", run_id=run_id, worker_name="verifier_1")

        # Atomic rollback check on workflow_verifier_turn(phase="prepare") when commands are missing
        with pytest.raises(ValueError):
            dispatcher.dispatch(
                "workflow_verifier_turn",
                {"run_id": run_id, "step_id": step1_id, "phase": "prepare", "scope": "candidate"},
            )
        assert dispatcher.inbox.get(ver_prep1["message_id"])["status"] == "pending"
        assert db.get_worker_by_name(run_id, "verifier_1")["status"] == "waiting"

        # Real workflow_verifier_turn(phase="prepare") auto-locates review inbox message!
        env_profile = "windows_minimal_py" if os.name == "nt" else "posix_minimal_py"
        v_prep_res = dispatcher.dispatch(
            "workflow_verifier_turn",
            {
                "run_id": run_id,
                "step_id": step1_id,
                "phase": "prepare",
                "scope": "candidate",
                "env_profile": env_profile,
                "verification_commands": [
                    {
                        "id": "cand-check-0",
                        "argv": [sys.executable, "-c", "print('check0')"],
                        "cwd": str(ws1_path),
                        "timeout_sec": 15,
                        "criteria": ["AC-MAIN"],
                    }
                ],
            },
        )
        assert v_prep_res["message_id"] == ver_prep1["message_id"]
        assert dispatcher.inbox.get(ver_prep1["message_id"])["status"] == "accepted"
        assert db.get_worker_by_name(run_id, "verifier_1")["status"] == "working"
        assert v_prep_res["verify_command_hint"] is not None
        assert v_prep_res["verify_argv_template"] is not None

        out_dir_0 = tmp_path / "receipts_a0"
        collect_verify_receipt(Path(v_prep_res["artifact"]["manifest_path"]), out_dir_0)

        # Record FAIL on attempt 0 to exercise rework + attempt 1 Boost challenge re-binding!
        v_fail = dispatcher.dispatch(
            "workflow_verifier_turn",
            {
                "run_id": run_id,
                "step_id": step1_id,
                "phase": "record",
                "artifact_id": v_prep_res["artifact"]["id"],
                "verdict": "FAIL",
                "criteria_results": [{"criterion": "AC-MAIN", "status": "FAIL"}],
                "receipt_files": [str(out_dir_0 / "receipt.json")],
                "summary": "Value must be 42",
                "post_board_note": True,
            },
        )
        assert v_fail["step"]["state"] == "rework"
        assert v_fail["message"]["status"] == "completed"
        assert db.get_worker_by_name(run_id, "verifier_1")["status"] == "waiting"
        assert "coder_1" in v_fail["board_pointer"]["mentions"]

        # Stop hook MUST block when step sits in rework awaiting coordinator start_rework!
        stop_rework = dispatcher.dispatch(
            "session_hook_check",
            {"session_id": "sess_macro_e2e", "event_name": "Stop", "cwd": str(project)},
        )
        assert stop_rework["should_block_stop"] is True
        assert "rework" in stop_rework["stop_reason"]

        # Coordinator dispatches rework to coder_1 (with native idle observation)
        rw_prep = dispatcher.dispatch(
            "workflow_prepare_dispatch",
            {
                "run_id": run_id,
                "dispatch_action": "start_rework",
                "step_id": step1_id,
                "instruction": "Set VALUE = 42",
                "observation": {
                    "agent_id": "/root/coder_1",
                    "state": "idle",
                    "observed_at": v_fail["step"]["checkpoint"]["last_failure_at"] + 0.01,
                    "evidence": "list_agents idle",
                },
            },
        )
        dispatcher.dispatch(
            "native_action_record_call",
            {
                "run_id": run_id,
                "action_id": rw_prep["action_id"],
                "claim_token": rw_prep["claim_token"],
                "expected_revision": rw_prep["expected_revision"],
                "outcome": "confirmed",
                "raw_response": {"status": "queued"},
            },
        )
        (ws1_path / "main.py").write_text("VALUE = 42\n", encoding="utf-8")
        dispatcher.dispatch(
            "workflow_author_turn",
            {
                "run_id": run_id,
                "step_id": step1_id,
                "message_id": rw_prep["message_id"],
                "phase": "submit",
                "summary": "Fixed VALUE = 42",
            },
        )

        # Re-verify attempt 1 candidate via workflow_verifier_turn (tests stale challenge overwrite!)
        ver_prep_a1 = dispatcher.dispatch(
            "workflow_prepare_dispatch",
            {
                "run_id": run_id,
                "dispatch_action": "request_verify",
                "step_id": step1_id,
                "instruction": "Re-verify attempt 1",
            },
        )
        dispatcher.dispatch(
            "native_action_record_call",
            {
                "run_id": run_id,
                "action_id": ver_prep_a1["action_id"],
                "claim_token": ver_prep_a1["claim_token"],
                "expected_revision": ver_prep_a1["expected_revision"],
                "outcome": "confirmed",
                "raw_response": {"status": "queued"},
            },
        )
        v_prep_a1_res = dispatcher.dispatch(
            "workflow_verifier_turn",
            {
                "run_id": run_id,
                "step_id": step1_id,
                "phase": "prepare",
                "scope": "candidate",
                "env_profile": env_profile,
                "verification_commands": [
                    {
                        "id": "cand-check-1",
                        "argv": [sys.executable, "-c", "print('check1')"],
                        "cwd": str(ws1_path),
                        "timeout_sec": 15,
                        "criteria": ["AC-MAIN"],
                    }
                ],
            },
        )
        out_dir_1 = tmp_path / "receipts_a1"
        collect_verify_receipt(Path(v_prep_a1_res["artifact"]["manifest_path"]), out_dir_1)

        # Omit criteria_results & key on PASS to test auto-fill + deterministic idempotency
        v_pass_a1 = dispatcher.dispatch(
            "workflow_verifier_turn",
            {
                "run_id": run_id,
                "step_id": step1_id,
                "phase": "record",
                "artifact_id": v_prep_a1_res["artifact"]["id"],
                "verdict": "PASS",
                "receipt_files": [str(out_dir_1 / "receipt.json")],
                "summary": "Candidate PASS",
                "post_board_note": True,
            },
        )
        assert v_pass_a1["step"]["state"] == "ready_to_merge"
        assert v_pass_a1["message"]["status"] == "completed"

        # Stop hook MUST block on ready_to_merge!
        stop_rtm = dispatcher.dispatch(
            "session_hook_check",
            {"session_id": "sess_macro_e2e", "event_name": "Stop", "cwd": str(project)},
        )
        assert stop_rtm["should_block_stop"] is True
        assert "ready_to_merge" in stop_rtm["stop_reason"]

        # Merge workspace 1
        dispatcher.dispatch("workspace_merge", {"workspace_id": prep1["workspace"]["id"]})

        # Stop hook MUST block on integrated_review before integrated request_verify is dispatched!
        stop_ir_pre = dispatcher.dispatch(
            "session_hook_check",
            {"session_id": "sess_macro_e2e", "event_name": "Stop", "cwd": str(project)},
        )
        assert stop_ir_pre["should_block_stop"] is True
        assert "integrated_review" in stop_ir_pre["stop_reason"]

        # Closeout MUST refuse before integrated verification passes and before all criteria are covered!
        with pytest.raises(WorkflowConflictError):
            dispatcher.dispatch("workflow_closeout_run", {"run_id": run_id})

        # Integrated verify for step 1
        ver_int1 = dispatcher.dispatch(
            "workflow_prepare_dispatch",
            {
                "run_id": run_id,
                "dispatch_action": "request_verify",
                "step_id": step1_id,
                "instruction": "Verify integrated step 1",
            },
        )
        dispatcher.dispatch(
            "native_action_record_call",
            {
                "run_id": run_id,
                "action_id": ver_int1["action_id"],
                "claim_token": ver_int1["claim_token"],
                "expected_revision": ver_int1["expected_revision"],
                "outcome": "confirmed",
                "raw_response": {"status": "queued"},
            },
        )
        v_int1_prep = dispatcher.dispatch(
            "workflow_verifier_turn",
            {
                "run_id": run_id,
                "step_id": step1_id,
                "phase": "prepare",
                "scope": "integrated",
                "env_profile": env_profile,
                "verification_commands": [
                    {
                        "id": "int-check-1",
                        "argv": [sys.executable, "-c", "print('int1')"],
                        "cwd": str(project),
                        "timeout_sec": 15,
                        "criteria": ["AC-MAIN"],
                    }
                ],
            },
        )
        out_dir_int1 = tmp_path / "receipts_int1"
        collect_verify_receipt(Path(v_int1_prep["artifact"]["manifest_path"]), out_dir_int1)
        dispatcher.dispatch(
            "workflow_verifier_turn",
            {
                "run_id": run_id,
                "step_id": step1_id,
                "phase": "record",
                "artifact_id": v_int1_prep["artifact"]["id"],
                "verdict": "PASS",
                "receipt_files": [str(out_dir_int1 / "receipt.json")],
            },
        )

        # Stop hook MUST block when integrated PASS is recorded and step awaits complete/closeout!
        stop_ir_post = dispatcher.dispatch(
            "session_hook_check",
            {"session_id": "sess_macro_e2e", "event_name": "Stop", "cwd": str(project)},
        )
        assert stop_ir_post["should_block_stop"] is True
        assert stop_ir_post["blocked_key"] != stop_ir_pre["blocked_key"]

        # Complete step 1 first so dependent step 2 can be admitted, and verify all-done Stop hook!
        dispatcher.dispatch(
            "workflow_step_advance",
            {"step_id": step1_id, "action": "complete", "expected_revision": WorkflowService(db).get_step(step1_id)["revision"]},
        )
        stop_all_done = dispatcher.dispatch(
            "session_hook_check",
            {"session_id": "sess_macro_e2e", "event_name": "Stop", "cwd": str(project)},
        )
        assert stop_all_done["should_block_stop"] is True
        assert "workflow_closeout_run" in stop_all_done["stop_reason"]

        # Add dependent step 2
        prep2 = dispatcher.dispatch(
            "workflow_prepare_dispatch",
            {
                "run_id": run_id,
                "create_workspace": True,
                "owner": "coder_2",
                "task_name": "coder_2",
                "verifier": "verifier_1",
                "step_key": "step-lib",
                "dependencies": [step1_id],
                "criteria": ["AC-LIB"],
                "instruction": "Update lib.py",
            },
        )
        step2_id = prep2["step"]["id"]
        dispatcher.dispatch(
            "native_action_record_call",
            {
                "run_id": run_id,
                "action_id": prep2["action_id"],
                "claim_token": prep2["claim_token"],
                "expected_revision": prep2["expected_revision"],
                "outcome": "confirmed",
                "raw_response": {"task_name": "/root/coder_2"},
            },
        )
        dispatcher.dispatch(
            "workflow_author_turn",
            {"run_id": run_id, "step_id": step2_id, "message_id": prep2["message_id"], "phase": "accept"},
        )
        ws2_path = Path(prep2["workspace"]["path"])
        (ws2_path / "lib.py").write_text("LIB = 99\n", encoding="utf-8")
        dispatcher.dispatch(
            "workflow_author_turn",
            {"run_id": run_id, "step_id": step2_id, "message_id": prep2["message_id"], "phase": "submit"},
        )
        ver_cand2 = dispatcher.dispatch(
            "workflow_prepare_dispatch",
            {"run_id": run_id, "dispatch_action": "request_verify", "step_id": step2_id, "instruction": "Verify step 2"},
        )
        dispatcher.dispatch(
            "native_action_record_call",
            {
                "run_id": run_id,
                "action_id": ver_cand2["action_id"],
                "claim_token": ver_cand2["claim_token"],
                "expected_revision": ver_cand2["expected_revision"],
                "outcome": "confirmed",
                "raw_response": {"status": "queued"},
            },
        )
        vc2 = dispatcher.dispatch(
            "workflow_verifier_turn",
            {
                "run_id": run_id,
                "step_id": step2_id,
                "phase": "prepare",
                "scope": "candidate",
                "env_profile": env_profile,
                "verification_commands": [
                    {"id": "c2", "argv": [sys.executable, "-c", "print('c2')"], "cwd": str(ws2_path), "timeout_sec": 15, "criteria": ["AC-LIB"]}
                ],
            },
        )
        out_c2 = tmp_path / "receipts_c2"
        collect_verify_receipt(Path(vc2["artifact"]["manifest_path"]), out_c2)
        dispatcher.dispatch(
            "workflow_verifier_turn",
            {
                "run_id": run_id,
                "step_id": step2_id,
                "phase": "record",
                "artifact_id": vc2["artifact"]["id"],
                "verdict": "PASS",
                "receipt_files": [str(out_c2 / "receipt.json")],
            },
        )
        dispatcher.dispatch("workspace_merge", {"workspace_id": prep2["workspace"]["id"]})

        # Reopen step 1 via reverify after step 2 merge touched full-tree snapshot, then verify both!
        dispatcher.dispatch(
            "workflow_step_advance",
            {"step_id": step1_id, "action": "reverify", "expected_revision": WorkflowService(db).get_step(step1_id)["revision"]},
        )

        # Re-verify integrated step 1 after step 2 merge (full-tree integrated checkpoint) + verify integrated step 2
        ver_int1_re = dispatcher.dispatch(
            "workflow_prepare_dispatch",
            {"run_id": run_id, "dispatch_action": "request_verify", "step_id": step1_id, "instruction": "Re-verify int 1 post-merge"},
        )
        dispatcher.dispatch(
            "native_action_record_call",
            {
                "run_id": run_id,
                "action_id": ver_int1_re["action_id"],
                "claim_token": ver_int1_re["claim_token"],
                "expected_revision": ver_int1_re["expected_revision"],
                "outcome": "confirmed",
                "raw_response": {"status": "queued"},
            },
        )
        vi1_re = dispatcher.dispatch(
            "workflow_verifier_turn",
            {
                "run_id": run_id,
                "step_id": step1_id,
                "phase": "prepare",
                "scope": "integrated",
                "env_profile": env_profile,
                "verification_commands": [
                    {"id": "i1re", "argv": [sys.executable, "-c", "print('i1re')"], "cwd": str(project), "timeout_sec": 15, "criteria": ["AC-MAIN"]}
                ],
            },
        )
        out_i1_re = tmp_path / "receipts_i1_re"
        collect_verify_receipt(Path(vi1_re["artifact"]["manifest_path"]), out_i1_re)
        dispatcher.dispatch(
            "workflow_verifier_turn",
            {
                "run_id": run_id,
                "step_id": step1_id,
                "phase": "record",
                "artifact_id": vi1_re["artifact"]["id"],
                "verdict": "PASS",
                "receipt_files": [str(out_i1_re / "receipt.json")],
            },
        )

        ver_int2 = dispatcher.dispatch(
            "workflow_prepare_dispatch",
            {"run_id": run_id, "dispatch_action": "request_verify", "step_id": step2_id, "instruction": "Verify int 2"},
        )
        dispatcher.dispatch(
            "native_action_record_call",
            {
                "run_id": run_id,
                "action_id": ver_int2["action_id"],
                "claim_token": ver_int2["claim_token"],
                "expected_revision": ver_int2["expected_revision"],
                "outcome": "confirmed",
                "raw_response": {"status": "queued"},
            },
        )
        vi2 = dispatcher.dispatch(
            "workflow_verifier_turn",
            {
                "run_id": run_id,
                "step_id": step2_id,
                "phase": "prepare",
                "scope": "integrated",
                "env_profile": env_profile,
                "verification_commands": [
                    {"id": "i2", "argv": [sys.executable, "-c", "print('i2')"], "cwd": str(project), "timeout_sec": 15, "criteria": ["AC-LIB"]}
                ],
            },
        )
        out_i2 = tmp_path / "receipts_i2"
        collect_verify_receipt(Path(vi2["artifact"]["manifest_path"]), out_i2)
        dispatcher.dispatch(
            "workflow_verifier_turn",
            {
                "run_id": run_id,
                "step_id": step2_id,
                "phase": "record",
                "artifact_id": vi2["artifact"]["id"],
                "verdict": "PASS",
                "receipt_files": [str(out_i2 / "receipt.json")],
            },
        )

        # Both steps are in integrated_review -> workflow_closeout_run advances [step1_id, step2_id] in topological order!
        closeout = dispatcher.dispatch(
            "workflow_closeout_run",
            {"run_id": run_id, "summary": "All slices verified and closed out"},
        )
        assert closeout["run"]["status"] == "completed"
        assert closeout["advanced_steps"] == [step1_id, step2_id]
        assert set(closeout["cleaned_workspaces"]) == {prep1["workspace"]["id"], prep2["workspace"]["id"]}
        assert not ws1_path.exists()
        assert not ws2_path.exists()
        assert all(w["status"] == "stopped" for w in db.list_workers(run_id=run_id))

        # Stop hook no longer blocks after run is completed
        stop_after = dispatcher.dispatch(
            "session_hook_check",
            {"session_id": "sess_macro_e2e", "event_name": "Stop", "cwd": str(project)},
        )
        assert stop_after["should_block_stop"] is False

    def test_teamwork_milestone_explicit_false_does_not_deadlock(self, env):
        project, _, db, board, _, dispatcher = env
        flow = WorkflowService(db)

        # Teamwork run for native explicit False vs default True
        run2 = dispatcher.dispatch(
            "run_create",
            {"mode": "teamwork", "title": "Teamwork Native Milestone", "project_root": str(project)},
        )
        run2_id = run2["id"]
        dispatcher.dispatch(
            "task_packet_save",
            {
                "run_id": run2_id,
                "title": "Native Milestones",
                "goal": "Explicit False vs default True",
                "file_ownership": {"nat_a": ["main.py"], "nat_b": ["lib.py"]},
                "acceptance_criteria": ["AC-1", "AC-2"],
            },
        )
        board.worker_register(run2_id, "nat_a", "coder", agent_id="/root/nat_a")
        board.worker_register(run2_id, "nat_b", "coder", agent_id="/root/nat_b")
        board.worker_register(run2_id, "nat_v", "verifier", agent_id="/root/nat_v")

        default_ms = flow.step_save(run2_id, "m-default", "nat_a", "nat_v", kind="milestone", criteria=["AC-1"])
        assert bool(default_ms["handoff_required"]) is True

        single_root_ms = flow.step_save(
            run2_id,
            "m-single-root",
            "nat_b",
            "nat_v",
            kind="milestone",
            criteria=["AC-2"],
            handoff_required=False,
        )
        assert bool(single_root_ms["handoff_required"]) is False


# ===========================================================================
# 4. Duplicate os.walk Elimination, controls_only, Chinese @mention & Daemon Env/CWD
# ===========================================================================


class TestPerformanceMentionsAndColdStart:
    def test_controls_only_zero_os_walk_and_single_plan_readiness_per_board_status(self, env, monkeypatch):
        project, _, db, board, _, dispatcher = env
        run = dispatcher.dispatch(
            "run_create",
            {"mode": "swarm", "title": "OS Walk Count", "project_root": str(project)},
        )
        run_id = run["id"]
        dispatcher.dispatch(
            "task_packet_save",
            {
                "run_id": run_id,
                "title": "Walk Packet",
                "goal": "Eliminate duplicate walks",
                "file_ownership": {"w1": ["main.py"]},
                "acceptance_criteria": ["AC-WALK"],
            },
        )
        dispatcher.dispatch(
            "workflow_prepare_dispatch",
            {
                "run_id": run_id,
                "create_workspace": False,
                "owner": "w1",
                "task_name": "w1",
                "verifier": "v1",
                "step_key": "walk-step",
                "instruction": "No-op",
            },
        )

        walk_calls = 0
        real_walk = os.walk

        def counted_walk(*args, **kwargs):
            nonlocal walk_calls
            walk_calls += 1
            return real_walk(*args, **kwargs)

        monkeypatch.setattr(os, "walk", counted_walk)
        fast = dispatcher.dispatch("board_status", {"run_id": run_id, "controls_only": True})
        assert set(fast.keys()) == {"run_id", "run", "workers", "control_requests"}
        assert walk_calls == 0

        readiness_calls = 0
        real_plan_readiness = WorkflowService.plan_readiness

        def counted_readiness(self_wf, rid):
            nonlocal readiness_calls
            readiness_calls += 1
            return real_plan_readiness(self_wf, rid)

        monkeypatch.setattr(WorkflowService, "plan_readiness", counted_readiness)
        fixed_now = time.time()
        compact = board.board_status(run_id, compact=True, now=fixed_now)
        assert readiness_calls == 1
        assert compact["progress_status"] == classify_run(
            db,
            run_id,
            now=fixed_now,
            precomputed_native=NativeActionStore(db).project(run_id),
            precomputed_readiness=compact["plan_readiness"],
        )

    def test_chinese_adjacent_mentions_parsed_and_emails_ignored(self):
        assert extract_mentions("移交@verifier") == ["verifier"]
        assert extract_mentions("已完成修改，移交@verifier_1请复核并抄送@coder-2。") == ["verifier_1", "coder-2"]
        assert extract_mentions("联系邮箱 user@example.com 不应触发提及") == []

    def test_windows_daemon_cwd_and_case_insensitive_git_venv_stripping(self, tmp_path: Path, monkeypatch):
        import orchestrator.windows_daemon as win_daemon

        captured: dict[str, Any] = {}

        class _FakeStartup:
            def __init__(self):
                self.ShowWindow = None
                self.CreateFlags = None
                self.EnvironmentVariables = None

            def SpawnInstance_(self):
                return self

        class _FakeInputs:
            def __init__(self):
                self.CommandLine = None
                self.CurrentDirectory = None
                self.ProcessStartupInformation = None

            def SpawnInstance_(self):
                return self

        class _FakeMethod:
            def __init__(self, inputs):
                self.InParameters = inputs

        class _FakeOutput:
            ReturnValue = 0
            ProcessId = os.getpid()

        class _FakeProcess:
            def __init__(self, inputs):
                self._inputs = inputs

            def Methods_(self, name):
                assert name == "Create"
                return _FakeMethod(self._inputs)

            def ExecMethod_(self, name, inputs):
                assert name == "Create"
                captured["cwd"] = inputs.CurrentDirectory
                captured["env"] = list(inputs.ProcessStartupInformation.EnvironmentVariables)
                return _FakeOutput()

        startup_obj = _FakeStartup()
        inputs_obj = _FakeInputs()

        class _FakeService:
            def Get(self, name):
                if name == "Win32_ProcessStartup":
                    return startup_obj
                if name == "Win32_Process":
                    return _FakeProcess(inputs_obj)
                raise AssertionError(name)

        fake_pythoncom = MagicMock()
        fake_pythoncom.COINIT_APARTMENTTHREADED = 2
        monkeypatch.setattr(win_daemon, "pythoncom", fake_pythoncom)
        monkeypatch.setattr(win_daemon, "GetObject", lambda _moniker: _FakeService())

        data_dir = tmp_path / "isolated_daemon_home"
        raw_env = {
            "TRI_ORCH_DATA_DIR": str(data_dir),
            "PATH": os.environ.get("PATH", ""),
            "GIT_DIR": "/bad/.git",
            "git_work_tree": "/bad/tree",
            "GIT_INDEX_FILE": "/bad/index",
            "virtual_env": "/bad/venv",
        }
        proc = win_daemon.spawn_daemon([sys.executable, "-m", "orchestrator.daemon", "--port", "29199"], raw_env)
        assert proc.pid == os.getpid()
        proc._handle.Close()
        assert captured["cwd"] == str(data_dir)
        assert data_dir.is_dir()
        env_keys_upper = {entry.split("=", 1)[0].upper() for entry in captured["env"]}
        assert env_keys_upper.isdisjoint({"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "VIRTUAL_ENV"})

    def test_fetch_health_fast_probe_skips_http_timeout_when_port_is_free(self, tmp_path: Path, monkeypatch):
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = int(s.getsockname()[1])

        def _forbid_http(*_args, **_kwargs):
            raise AssertionError("httpx.Client should not be called when _probe_first sees an unbound port")

        monkeypatch.setattr(httpx, "Client", _forbid_http)
        cfg = Settings(host="127.0.0.1", port=free_port, data_dir=tmp_path / "cold_data")
        assert fetch_health(cfg, _probe_first=True) is None
