"""M6 board_status(compact=True) deduplication, payload size reduction, and Web UI compatibility."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from orchestrator.board import BoardService
from orchestrator.config import Settings
from orchestrator.daemon import create_app
from orchestrator.db import Database
from orchestrator.rpc_handlers import RpcDispatcher
from orchestrator.workspace_mgr import WorkspaceManager


def _has_raw_json_key(value) -> bool:
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str) and k.endswith("_json"):
                return True
            if _has_raw_json_key(v):
                return True
    elif isinstance(value, list):
        return any(_has_raw_json_key(item) for item in value)
    return False


def _has_dict_key(value, target_key: str) -> bool:
    if isinstance(value, dict):
        if target_key in value:
            return True
        return any(_has_dict_key(v, target_key) for v in value.values())
    if isinstance(value, list):
        return any(_has_dict_key(item, target_key) for item in value)
    return False


def test_compact_board_eliminates_duplication_strips_body_md_and_reduces_size(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "service.py").write_text("def run():\n    return True\n", encoding="utf-8")
    settings = Settings(data_dir=tmp_path / "data", port_range=(28860, 28890))
    db = Database(settings=settings)
    board = BoardService(db, settings)
    ws_mgr = WorkspaceManager(db, settings)
    dispatcher = RpcDispatcher(db, board, ws_mgr)

    try:
        run = dispatcher.dispatch(
            "run_create",
            {
                "mode": "swarm",
                "title": "Compact Payload Benchmark",
                "project_root": str(project),
                "metadata": {"env": "isolated-test", "tags": ["m6", "compact"]},
            },
        )
        run_id = run["id"]
        dispatcher.dispatch(
            "task_packet_save",
            {
                "run_id": run_id,
                "title": "Large Task Packet",
                "goal": "Eliminate 4x body_md bloat and duplicate native_actions",
                "file_ownership": {
                    "worker_1": ["service.py", "src/module_a.py"],
                    "worker_2": ["src/module_b.py", "src/module_c.py"],
                },
                "acceptance_criteria": [
                    "AC-1: No top-level native_actions spread under compact=True",
                    "AC-2: Zero body_md inside next_actions and next_action_groups",
                    "AC-3: Strip *_json duplicate strings",
                ],
            },
        )

        # Register already-bound workers so pending inbox messages populate both next_actions AND next_action_groups!
        board.worker_register(run_id, "worker_1", "swarm_worker", agent_id="/root/worker_1")
        board.worker_register(run_id, "worker_2", "swarm_worker", agent_id="/root/worker_2")
        board.worker_register(run_id, "verifier_1", "verifier", agent_id="/root/verifier_1")

        ws1 = ws_mgr.workspace_create(run_id, "worker_1", copy_env=False)
        ws2 = ws_mgr.workspace_create(run_id, "worker_2", copy_env=False)

        step1 = dispatcher.workflow.step_save(
            run_id, "slice-1", "worker_1", "verifier_1", workspace_id=ws1["id"]
        )
        step1 = dispatcher.workflow.step_advance(step1["id"], "dispatch", step1["revision"])
        step2 = dispatcher.workflow.step_save(
            run_id, "slice-2", "worker_2", "verifier_1", workspace_id=ws2["id"]
        )
        step2 = dispatcher.workflow.step_advance(step2["id"], "dispatch", step2["revision"])

        long_body_1 = (
            "## Detailed Slice 1 Implementation Spec\n"
            + ("- Implement deterministic validation rules and edge-case guards across module_a.\n" * 45)
            + "UNIQUE_MARKER_BODY_ONE_9981"
        )
        long_body_2 = (
            "## Detailed Slice 2 Implementation Spec\n"
            + ("- Implement isolated transport framing and structured error mapping across module_b.\n" * 45)
            + "UNIQUE_MARKER_BODY_TWO_9982"
        )
        dispatcher.inbox.send(
            run_id,
            "coordinator",
            "worker_1",
            "dispatch-1",
            "task",
            "Implement Slice 1",
            long_body_1,
            step_id=step1["id"],
        )
        dispatcher.inbox.send(
            run_id,
            "coordinator",
            "worker_2",
            "dispatch-2",
            "task",
            "Implement Slice 2",
            long_body_2,
            step_id=step2["id"],
        )

        full_status = dispatcher.dispatch("board_status", {"run_id": run_id})
        compact_status = dispatcher.dispatch(
            "board_status", {"run_id": run_id, "compact": True}
        )
        compact_with_ws = dispatcher.dispatch(
            "board_status",
            {"run_id": run_id, "compact": True, "include_workspaces": True},
        )

        full_json = json.dumps(full_status, ensure_ascii=False)
        compact_json = json.dumps(compact_status, ensure_ascii=False)

        # In legacy compact=False mode, long_body_1 appears 4 times (top-level next_actions, top-level next_action_groups,
        # native_actions.next_actions, native_actions.next_action_groups).
        assert full_json.count("UNIQUE_MARKER_BODY_ONE_9981") == 4
        assert "next_actions" in full_status and "native_actions" in full_status
        assert "workspaces" in full_status and len(full_status["workspaces"]) == 2
        assert "file_ownership_json" in full_status["latest_task_packet"]

        # Under compact=True:
        # 1) Top-level native keys are NOT duplicated alongside "native_actions".
        for top_dup_key in (
            "next_actions",
            "next_action_groups",
            "needs_reconciliation",
            "needs_clarification",
            "native_action_groups",
            "counts",
        ):
            assert top_dup_key not in compact_status
        assert "native_actions" in compact_status

        # 2) Zero body_md in next_actions or next_action_groups; required action metadata preserved.
        assert "UNIQUE_MARKER_BODY_ONE_9981" not in compact_json
        assert "UNIQUE_MARKER_BODY_TWO_9982" not in compact_json
        assert not _has_dict_key(compact_status, "body_md")
        next_actions = compact_status["native_actions"]["next_actions"]
        assert len(next_actions) == 2
        for item in next_actions:
            assert {
                "source_id",
                "source_type",
                "source_digest",
                "summary",
                "step_id",
                "recipient",
                "action_type",
                "status",
            } <= set(item)

        # 3) No redundant *_json strings in run, latest_task_packet, workers, or workspaces.
        assert not _has_raw_json_key(compact_status["run"])
        assert not _has_raw_json_key(compact_status["latest_task_packet"])
        assert not _has_raw_json_key(compact_status["workers"])
        assert compact_status["latest_task_packet"]["file_ownership"]["worker_1"] == [
            "service.py",
            "src/module_a.py",
        ]

        # 4) Workspaces omitted by default under compact=True, included cleanly when include_workspaces=True.
        assert "workspaces" not in compact_status
        assert "workspaces" in compact_with_ws and len(compact_with_ws["workspaces"]) == 2
        assert not _has_raw_json_key(compact_with_ws["workspaces"])

        # 5) Significant JSON byte size reduction (>= 70%).
        full_bytes = len(full_json.encode("utf-8"))
        compact_bytes = len(compact_json.encode("utf-8"))
        reduction = 1.0 - (compact_bytes / full_bytes)
        assert reduction >= 0.70, f"Expected >=70% byte reduction, got {reduction:.2%} ({compact_bytes}/{full_bytes})"
    finally:
        db.close()


def test_web_routes_and_snapshot_preserve_default_non_compact_contract(tmp_path: Path):
    project = tmp_path / "web_project"
    project.mkdir()
    (project / "index.py").write_text("ok = 1\n", encoding="utf-8")
    settings = Settings(data_dir=tmp_path / "web_data", port_range=(28891, 28910))

    async def scenario():
        app = create_app(settings, start_watchdog=False, manage_pid_file=False)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                for route in ("/", "/boost", "/teamwork", "/swarm"):
                    res = await client.get(route)
                    assert res.status_code == 200
                    assert "text/html" in res.headers["content-type"]

                create_res = await client.post(
                    "/api/rpc",
                    json={
                        "tool": "run_create",
                        "arguments": {
                            "mode": "teamwork",
                            "title": "Web Snapshot Check",
                            "project_root": str(project),
                        },
                    },
                )
                assert create_res.status_code == 200
                run_id = create_res.json()["result"]["id"]

                snap_res = await client.get(f"/api/runs/{run_id}/snapshot")
                assert snap_res.status_code == 200
                snap = snap_res.json()
                assert snap["run"]["id"] == run_id
                assert snap["run"]["workflow_version"] == 5
                assert "workspaces" in snap
                assert "native_actions" in snap
                assert "progress_status" in snap

    asyncio.run(scenario())
