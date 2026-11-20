"""Model visibility tests for hook_runner, rpc_handlers, and SSE worker_updated propagation."""

from __future__ import annotations

import asyncio
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any

import pytest

from orchestrator import codex_bridge, hook_runner, rpc_handlers
from orchestrator.board import BoardService
from orchestrator.config import Settings
from orchestrator.db import Database
from orchestrator.rpc_handlers import (
    INTERNAL_RPC_DEFINITIONS,
    TOOL_DEFINITIONS,
    EventBus,
    RpcDispatcher,
    _resolve_live_model_and_effort,
)
from orchestrator.verify_runner import collect
from orchestrator.workspace_mgr import WorkspaceManager


def _create_state5_db(
    codex_home: Path,
    *,
    threads: list[tuple[Any, ...]],
    edges: list[tuple[str, str, str | None]],
) -> Path:
    codex_home.mkdir(parents=True, exist_ok=True)
    db_path = codex_home / "state_5.sqlite"
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.executescript(
            """
            DROP TABLE IF EXISTS thread_spawn_edges;
            DROP TABLE IF EXISTS threads;
            DROP TABLE IF EXISTS project_roots;
            DROP TABLE IF EXISTS projects;
            CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE project_roots (
                project_id TEXT NOT NULL REFERENCES projects(id),
                position INTEGER NOT NULL,
                path TEXT NOT NULL,
                PRIMARY KEY (project_id, position)
            );
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                cwd TEXT,
                project_id TEXT,
                name TEXT,
                title TEXT,
                first_user_message TEXT,
                thread_source TEXT,
                source TEXT,
                agent_path TEXT,
                agent_role TEXT,
                agent_nickname TEXT,
                model TEXT,
                reasoning_effort TEXT,
                model_provider TEXT,
                created_at REAL,
                updated_at REAL
            );
            CREATE TABLE thread_spawn_edges (
                parent_thread_id TEXT NOT NULL,
                child_thread_id TEXT NOT NULL,
                status TEXT,
                PRIMARY KEY (parent_thread_id, child_thread_id)
            );
            """
        )
        conn.executemany(
            "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            threads,
        )
        conn.executemany(
            "INSERT INTO thread_spawn_edges VALUES (?, ?, ?)",
            edges,
        )
    return db_path


def _bump_mtime(path: Path) -> None:
    ns = path.stat().st_mtime_ns + 2_000_000_000
    os.utime(path, ns=(ns, ns))


def _drain_events(queue: asyncio.Queue[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


def test_hook_runner_forwards_model_and_turn_id_without_altering_stdout(monkeypatch):
    calls: list[tuple[str, str, dict[str, Any], float]] = []

    def fake_rpc(url: str, tool: str, arguments: dict[str, Any], *, timeout_sec: float):
        calls.append((url, tool, dict(arguments), timeout_sec))
        if arguments.get("event_name") == "Stop":
            return {
                "bound": True,
                "run_id": "run-1",
                "run_status": "active",
                "progress_classification": "ready",
                "should_block_stop": True,
                "stop_reason": "Continue pending work",
                "blocked_key": "run-1:1:abc",
                "previous_blocked_key": "",
            }
        return {
            "bound": True,
            "run_id": "run-1",
            "resume_context": "Resume run-1 state",
            "should_block_stop": False,
        }

    monkeypatch.setattr(hook_runner, "call_daemon_rpc", fake_rpc)
    env = {"TRI_ORCH_PORT": "19810"}

    # 1. Ordinary UserPromptSubmit with model -> forwards model, writes no stdout
    out_ord = hook_runner.build_hook_stdout_json(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "sess-1",
            "cwd": "G:/repo",
            "turn_id": "turn-ord",
            "model": "  gpt-6.2-sol  ",
            "prompt": "Check status",
        },
        env=env,
    )
    assert out_ord is None
    assert calls[-1][2]["model"] == "gpt-6.2-sol"
    assert calls[-1][2]["turn_id"] == "turn-ord"

    # 2. Explicit $swarm UserPromptSubmit with model -> forwards model, unchanged stdout schema
    out_mode = hook_runner.build_hook_stdout_json(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "sess-1",
            "cwd": "G:/repo",
            "turn_id": "turn-mode",
            "model": "gpt-6.2-sol",
            "prompt": "$swarm run tests",
        },
        env=env,
    )
    assert out_mode is not None
    assert set(out_mode.keys()) == {"hookSpecificOutput"}
    assert set(out_mode["hookSpecificOutput"].keys()) == {"hookEventName", "additionalContext"}
    assert out_mode["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert calls[-1][2]["model"] == "gpt-6.2-sol"

    # 3. Legacy UserPromptSubmit lacking model -> omits model key
    out_legacy = hook_runner.build_hook_stdout_json(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "sess-1",
            "cwd": "G:/repo",
            "turn_id": "turn-legacy",
            "prompt": "$boost run",
        },
        env=env,
    )
    assert out_legacy is not None
    assert "model" not in calls[-1][2]

    # 4. SessionStart (startup & compact), PostCompact, Stop with model + turn_id
    out_ss_start = hook_runner.build_hook_stdout_json(
        {
            "hook_event_name": "SessionStart",
            "session_id": "sess-1",
            "cwd": "G:/repo",
            "source": "startup",
            "turn_id": " turn-ss ",
            "model": " gpt-6.2-sol ",
        },
        env=env,
    )
    assert out_ss_start is not None
    assert set(out_ss_start.keys()) == {"hookSpecificOutput"}
    assert out_ss_start["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert calls[-1][2]["model"] == "gpt-6.2-sol"
    assert calls[-1][2]["turn_id"] == "turn-ss"

    out_ss_compact = hook_runner.build_hook_stdout_json(
        {
            "hook_event_name": "SessionStart",
            "session_id": "sess-1",
            "cwd": "G:/repo",
            "source": "compact",
            "model": "gpt-6.2-sol",
        },
        env=env,
    )
    assert out_ss_compact is not None
    assert set(out_ss_compact.keys()) == {"hookSpecificOutput"}
    assert calls[-1][2]["model"] == "gpt-6.2-sol"
    assert "turn_id" not in calls[-1][2]

    out_pc = hook_runner.build_hook_stdout_json(
        {
            "hook_event_name": "PostCompact",
            "session_id": "sess-1",
            "cwd": "G:/repo",
            "turn_id": "turn-pc",
            "model": "gpt-6.2-sol",
        },
        env=env,
    )
    assert out_pc == {"systemMessage": "Resume run-1 state"}
    assert calls[-1][2]["model"] == "gpt-6.2-sol"
    assert calls[-1][2]["turn_id"] == "turn-pc"

    out_stop = hook_runner.build_hook_stdout_json(
        {
            "hook_event_name": "Stop",
            "session_id": "sess-1",
            "cwd": "G:/repo",
            "turn_id": "turn-stop",
            "model": "gpt-6.2-sol",
            "stop_hook_active": False,
        },
        env=env,
    )
    assert out_stop == {"decision": "block", "reason": "Continue pending work"}
    assert calls[-1][2]["model"] == "gpt-6.2-sol"
    assert calls[-1][2]["turn_id"] == "turn-stop"

    # 5. Legacy Stop lacking model and turn_id omits both keys
    out_stop_legacy = hook_runner.build_hook_stdout_json(
        {
            "hook_event_name": "Stop",
            "session_id": "sess-1",
            "cwd": "G:/repo",
            "stop_hook_active": False,
        },
        env=env,
    )
    assert out_stop_legacy == {"decision": "block", "reason": "Continue pending work"}
    assert "model" not in calls[-1][2]
    assert "turn_id" not in calls[-1][2]

    # 6. Guardrails preserved even when model is present
    before = len(calls)
    assert (
        hook_runner.build_hook_stdout_json(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "sess-sub",
                "agent_id": "/root/coder_1",
                "turn_id": "t-sub",
                "model": "gpt-6.2-sol",
                "prompt": "$swarm",
            },
            env=env,
        )
        is None
    )
    assert (
        hook_runner.build_hook_stdout_json(
            {
                "hook_event_name": "SessionStart",
                "session_id": "sess-1",
                "source": "clear",
                "model": "gpt-6.2-sol",
            },
            env=env,
        )
        is None
    )
    assert (
        hook_runner.build_hook_stdout_json(
            {
                "hook_event_name": "Stop",
                "session_id": "sess-1",
                "stop_hook_active": True,
                "model": "gpt-6.2-sol",
            },
            env=env,
        )
        is None
    )
    assert len(calls) == before


def test_session_hook_check_model_schema_is_internal_only():
    public_names = {tool["name"] for tool in TOOL_DEFINITIONS}
    assert "session_hook_check" not in public_names
    schema = next(
        tool["inputSchema"]
        for tool in INTERNAL_RPC_DEFINITIONS
        if tool["name"] == "session_hook_check"
    )
    assert schema["additionalProperties"] is False
    assert schema["properties"]["model"] == {"type": "string"}
    assert "model" not in schema["required"]


def test_resolve_live_model_and_effort_and_mid_session_switch_backfill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    project = tmp_path / "repo"
    project.mkdir()

    db_state_path = _create_state5_db(
        codex_home,
        threads=[
            (
                "sess-switch",
                str(project),
                None,
                "Switch session",
                "Switch session",
                "hello",
                "user",
                "cli",
                "/root",
                None,
                None,
                "gpt-6.1-sol",
                "high",
                "openai",
                100.0,
                100.0,
            ),
        ],
        edges=[],
    )

    # Direct helper checks (including anti-mismatch and absent session)
    assert _resolve_live_model_and_effort("sess-switch") == ("gpt-6.1-sol", "high")
    assert _resolve_live_model_and_effort("sess-switch", "  gpt-6.2-sol ") == (
        "gpt-6.2-sol",
        None,
    )
    assert _resolve_live_model_and_effort(None, "gpt-6.2-sol") == ("gpt-6.2-sol", None)
    assert _resolve_live_model_and_effort("missing-sess") == (None, None)

    async def exercise() -> None:
        settings = Settings(data_dir=tmp_path / "data", port_range=(29200, 29230))
        with Database(settings=settings) as db:
            bus = EventBus()
            _, queue = bus.subscribe()
            dispatcher = RpcDispatcher(
                db, BoardService(db, settings), WorkspaceManager(db, settings), bus
            )

            # Turn 1 on initial model gpt-6.1-sol / high
            dispatcher.dispatch(
                "session_hook_check",
                {
                    "session_id": "sess-switch",
                    "event_name": "UserPromptSubmit",
                    "cwd": str(project),
                    "turn_id": "turn-1",
                    "model": "gpt-6.1-sol",
                    "prompt_preview": "$swarm phase 1",
                    "has_mode_invocation": True,
                },
            )
            await asyncio.sleep(0)
            events = _drain_events(queue)
            session_events = [e for e in events if e["event"] == "session_updated"]
            assert session_events == [
                {
                    "event": "session_updated",
                    "data": {
                        "run_id": None,
                        "session_id": "sess-switch",
                        "turn_id": "turn-1",
                    },
                    "timestamp": session_events[0]["timestamp"],
                }
            ]
            t1 = db.get_session_turn("sess-switch", "turn-1")
            assert t1 is not None
            assert (t1["model"], t1["reasoning_effort"]) == ("gpt-6.1-sol", "high")

            run1 = dispatcher.dispatch(
                "run_create",
                {
                    "mode": "swarm",
                    "title": "Run 1",
                    "project_root": str(project),
                    "session_id": "sess-switch",
                },
            )
            assert (run1["trigger_model"], run1["trigger_reasoning_effort"]) == (
                "gpt-6.1-sol",
                "high",
            )

            # Mid-session model switch: hook sees gpt-6.2-sol while state_5.sqlite STILL shows gpt-6.1-sol / high
            dispatcher.dispatch(
                "session_hook_check",
                {
                    "session_id": "sess-switch",
                    "event_name": "UserPromptSubmit",
                    "cwd": str(project),
                    "turn_id": "turn-2",
                    "model": "gpt-6.2-sol",
                    "prompt_preview": "$boost phase 2",
                    "has_mode_invocation": True,
                },
            )
            t2 = db.get_session_turn("sess-switch", "turn-2")
            assert t2 is not None
            assert t2["model"] == "gpt-6.2-sol"
            assert t2["reasoning_effort"] is None

            run2 = dispatcher.dispatch(
                "run_create",
                {
                    "mode": "boost",
                    "title": "Run 2",
                    "project_root": str(project),
                    "session_id": "sess-switch",
                    "turn_id": "turn-2",
                },
            )
            assert run2["trigger_model"] == "gpt-6.2-sol"
            assert run2["trigger_reasoning_effort"] is None

            # Stop while state_5.sqlite is STILL stale MUST keep reasoning_effort=None (never pair 'high')
            dispatcher.dispatch(
                "session_hook_check",
                {
                    "session_id": "sess-switch",
                    "event_name": "Stop",
                    "cwd": str(project),
                    "turn_id": "turn-2",
                    "model": "gpt-6.2-sol",
                },
            )
            assert db.get_session_turn("sess-switch", "turn-2")["reasoning_effort"] is None
            assert db.get_run(run2["id"])["trigger_reasoning_effort"] is None

            # Now state_5.sqlite flushes gpt-6.2-sol / ultra
            with closing(sqlite3.connect(db_state_path)) as conn, conn:
                conn.execute(
                    "UPDATE threads SET model = ?, reasoning_effort = ?, updated_at = ? WHERE id = ?",
                    ("gpt-6.2-sol", "ultra", 200.0, "sess-switch"),
                )
            _bump_mtime(db_state_path)

            # Stop backfills turn-2 and Run 2 without touching Run 1
            dispatcher.dispatch(
                "session_hook_check",
                {
                    "session_id": "sess-switch",
                    "event_name": "Stop",
                    "cwd": str(project),
                    "turn_id": "turn-2",
                    "model": "gpt-6.2-sol",
                },
            )
            t2_backfilled = db.get_session_turn("sess-switch", "turn-2")
            run2_backfilled = db.get_run(run2["id"])
            run1_after = db.get_run(run1["id"])
            assert (t2_backfilled["model"], t2_backfilled["reasoning_effort"]) == (
                "gpt-6.2-sol",
                "ultra",
            )
            assert (
                run2_backfilled["trigger_model"],
                run2_backfilled["trigger_reasoning_effort"],
            ) == ("gpt-6.2-sol", "ultra")
            assert (
                run1_after["trigger_model"],
                run1_after["trigger_reasoning_effort"],
            ) == ("gpt-6.1-sol", "high")

            # Scenario B: run_create backfills both turn-3 and Run 3 after state_5 updates
            dispatcher.dispatch(
                "session_hook_check",
                {
                    "session_id": "sess-switch",
                    "event_name": "UserPromptSubmit",
                    "cwd": str(project),
                    "turn_id": "turn-3",
                    "model": "gpt-6.3-codex",
                    "prompt_preview": "$swarm phase 3",
                    "has_mode_invocation": True,
                },
            )
            assert db.get_session_turn("sess-switch", "turn-3")["reasoning_effort"] is None

            with closing(sqlite3.connect(db_state_path)) as conn, conn:
                conn.execute(
                    "UPDATE threads SET model = ?, reasoning_effort = ?, updated_at = ? WHERE id = ?",
                    ("gpt-6.3-codex", "max", 300.0, "sess-switch"),
                )
            _bump_mtime(db_state_path)

            run3 = dispatcher.dispatch(
                "run_create",
                {
                    "mode": "swarm",
                    "title": "Run 3",
                    "project_root": str(project),
                    "session_id": "sess-switch",
                    "turn_id": "turn-3",
                },
            )
            assert (run3["trigger_model"], run3["trigger_reasoning_effort"]) == (
                "gpt-6.3-codex",
                "max",
            )
            t3_backfilled = db.get_session_turn("sess-switch", "turn-3")
            assert (t3_backfilled["model"], t3_backfilled["reasoning_effort"]) == (
                "gpt-6.3-codex",
                "max",
            )
            # Earlier runs stay intact
            assert (
                db.get_run(run1["id"])["trigger_model"],
                db.get_run(run1["id"])["trigger_reasoning_effort"],
            ) == ("gpt-6.1-sol", "high")
            assert (
                db.get_run(run2["id"])["trigger_model"],
                db.get_run(run2["id"])["trigger_reasoning_effort"],
            ) == ("gpt-6.2-sol", "ultra")

    asyncio.run(exercise())


def test_sync_run_agent_models_snapshots_l1_l2_l3_and_emits_refreshed_worker_updated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    project = tmp_path / "repo"
    project.mkdir()
    (project / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")

    db_state_path = _create_state5_db(
        codex_home,
        threads=[
            (
                "sess-sync",
                str(project),
                None,
                "Root Session",
                "Root Session",
                "start",
                "user",
                "cli",
                "/root",
                None,
                None,
                "gpt-6.1-sol",
                "high",
                "openai",
                100.0,
                100.0,
            ),
            (
                "tid-coder-1",
                str(project),
                None,
                "coder_1",
                "coder_1",
                "code",
                "subagent",
                '{"subagent":{"thread_spawn":{}}}',
                "/root/coder_1",
                "coder",
                "Pythagoras",
                "gpt-6.2-sol",
                "xhigh",
                "openai",
                110.0,
                115.0,
            ),
            (
                "tid-verifier-1",
                str(project),
                None,
                "verifier_1",
                "verifier_1",
                "verify",
                "subagent",
                '{"subagent":{"thread_spawn":{}}}',
                "/root/verifier_1",
                "verifier",
                "Euclid",
                "gpt-6.1-mini",
                "medium",
                "openai",
                120.0,
                125.0,
            ),
            (
                "tid-helper-l2",
                str(project),
                None,
                "helper_l2",
                "helper_l2",
                "explore",
                "subagent",
                '{"subagent":{"thread_spawn":{}}}',
                "/root/coder_1/helper_a",
                "explorer",
                "Gauss",
                "gpt-5-mini",
                "low",
                "openai",
                111.0,
                112.0,
            ),
            (
                "tid-helper-l3",
                str(project),
                None,
                "helper_l3",
                "helper_l3",
                "deep explore",
                "subagent",
                '{"subagent":{"thread_spawn":{}}}',
                "/root/coder_1/helper_a/deep",
                "explorer",
                "Euler",
                "gpt-5-nano",
                "minimal",
                "openai",
                112.0,
                113.0,
            ),
            (
                "tid-adhoc",
                str(project),
                None,
                "adhoc",
                "adhoc",
                "search",
                "subagent",
                '{"subagent":{"thread_spawn":{}}}',
                "/root/adhoc_explorer",
                "explorer",
                "Ada",
                "gpt-5-mini",
                "low",
                "openai",
                114.0,
                114.0,
            ),
            (
                "tid-guardian",
                str(project),
                None,
                "guardian",
                "guardian",
                "guard",
                "guardian_review",
                "subagent",
                "/root/coder_1/guardian",
                "reviewer",
                "Guard",
                "gpt-5-mini",
                "low",
                "openai",
                115.0,
                115.0,
            ),
        ],
        edges=[
            ("sess-sync", "tid-coder-1", "completed"),
            ("sess-sync", "tid-verifier-1", "completed"),
            ("sess-sync", "tid-adhoc", "completed"),
            ("tid-coder-1", "tid-helper-l2", "completed"),
            ("tid-helper-l2", "tid-helper-l3", "completed"),
            ("tid-coder-1", "tid-guardian", "completed"),
        ],
    )

    async def exercise() -> None:
        settings = Settings(data_dir=tmp_path / "data", port_range=(29240, 29270))
        with Database(settings=settings) as db:
            bus = EventBus()
            _, queue = bus.subscribe()
            dispatcher = RpcDispatcher(
                db, BoardService(db, settings), WorkspaceManager(db, settings), bus
            )

            run = dispatcher.dispatch(
                "run_create",
                {
                    "mode": "boost",
                    "title": "Sync Test",
                    "project_root": str(project),
                    "session_id": "sess-sync",
                },
            )
            run_id = run["id"]

            dispatcher.dispatch(
                "task_packet_save",
                {
                    "run_id": run_id,
                    "title": "Sync Packet",
                    "goal": "Update mod.py",
                    "file_ownership": {"coder_1": ["mod.py"]},
                    "acceptance_criteria": ["mod.py updated"],
                },
            )

            # 1. /root coordinator worker_register -> matches root_thread, attaches ZERO worker_subagents!
            root_w = dispatcher.dispatch(
                "worker_register",
                {
                    "run_id": run_id,
                    "name": "coordinator",
                    "role": "coder",
                    "agent_id": "/root",
                },
            )
            assert root_w["codex_thread_id"] == "sess-sync"
            assert root_w["model"] == "gpt-6.1-sol"
            assert root_w["reasoning_effort"] == "high"
            assert root_w["model_provider"] == "openai"

            await asyncio.sleep(0)
            events = _drain_events(queue)
            worker_evts = [e for e in events if e["event"] == "worker_updated"]
            assert worker_evts[-1]["data"]["worker"]["model"] == "gpt-6.1-sol"
            assert db.list_worker_subagents(run_id) == []

            # 2. workflow_prepare_dispatch + native_action_record_call for coder_1
            prep = dispatcher.dispatch(
                "workflow_prepare_dispatch",
                {
                    "run_id": run_id,
                    "create_workspace": True,
                    "owner": "coder_1",
                    "task_name": "coder_1",
                    "verifier": "verifier_1",
                    "step_key": "edit-mod",
                    "instruction": "Edit mod.py",
                },
            )
            step_id = prep["step"]["id"]
            msg_id = prep["message_id"]
            _drain_events(queue)

            dispatcher.dispatch(
                "native_action_record_call",
                {
                    "run_id": run_id,
                    "action_id": prep["action_id"],
                    "claim_token": prep["claim_token"],
                    "expected_revision": prep["expected_revision"],
                    "outcome": "confirmed",
                    "raw_response": {"agent_id": "/root/coder_1", "agent_path": "/root/coder_1"},
                },
            )
            await asyncio.sleep(0)
            rec_evts = [e for e in _drain_events(queue) if e["event"] == "worker_updated"]
            assert rec_evts
            assert rec_evts[-1]["data"]["worker"]["codex_thread_id"] == "tid-coder-1"
            assert rec_evts[-1]["data"]["worker"]["model"] == "gpt-6.2-sol"
            assert rec_evts[-1]["data"]["worker"]["reasoning_effort"] == "xhigh"

            coder_w = db.get_worker_by_name(run_id, "coder_1")
            assert coder_w is not None
            subagents = db.list_worker_subagents(run_id)
            assert [s["thread_id"] for s in subagents] == ["tid-helper-l2", "tid-helper-l3"]
            assert all(s["parent_worker_id"] == coder_w["id"] for s in subagents)
            assert subagents[0]["depth"] == 2 and subagents[0]["model"] == "gpt-5-mini"
            assert subagents[1]["depth"] == 3 and subagents[1]["model"] == "gpt-5-nano"

            # 3. worker_heartbeat & worker_status_update emit refreshed worker dict
            hb_w = dispatcher.dispatch(
                "worker_heartbeat",
                {"run_id": run_id, "worker_name": "coder_1", "status": "working"},
            )
            assert hb_w["model"] == "gpt-6.2-sol"
            st_w = dispatcher.dispatch(
                "worker_status_update",
                {"run_id": run_id, "worker_name": "coder_1", "status": "waiting"},
            )
            assert st_w["model"] == "gpt-6.2-sol"
            await asyncio.sleep(0)
            hb_st_evts = [e for e in _drain_events(queue) if e["event"] == "worker_updated"]
            assert len(hb_st_evts) == 2
            assert all(e["data"]["worker"]["model"] == "gpt-6.2-sol" for e in hb_st_evts)

            # 4. workflow_author_turn (accept + submit) emits refreshed worker
            dispatcher.dispatch(
                "workflow_author_turn",
                {"run_id": run_id, "step_id": step_id, "message_id": msg_id, "phase": "accept"},
            )
            ws_dir = Path(prep["workspace"]["path"])
            (ws_dir / "mod.py").write_text("VALUE = 2\n", encoding="utf-8")
            dispatcher.dispatch(
                "workflow_author_turn",
                {
                    "run_id": run_id,
                    "step_id": step_id,
                    "message_id": msg_id,
                    "phase": "submit",
                    "summary": "Updated mod.py",
                },
            )
            await asyncio.sleep(0)
            auth_evts = [e for e in _drain_events(queue) if e["event"] == "worker_updated"]
            assert len(auth_evts) == 2
            assert all(e["data"]["worker"]["model"] == "gpt-6.2-sol" for e in auth_evts)

            # 5. Request verify + native_action_update(spawn_agent record) + workflow_verifier_turn
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
            _drain_events(queue)

            # Exercise _handle_native_action_update spawn_agent record directly
            dispatcher.dispatch(
                "native_action_update",
                {
                    "action": "record",
                    "action_id": ver_prep["action_id"],
                    "claim_token": ver_prep["claim_token"],
                    "expected_revision": ver_prep["expected_revision"],
                    "outcome": "confirmed",
                    "evidence_ref": str(tmp_path / "ver-receipt.json"),
                    "evidence_digest": "a" * 64,
                    "result": json.dumps({"agent_path": "/root/verifier_1"}),
                },
            )
            await asyncio.sleep(0)
            na_evts = [e for e in _drain_events(queue) if e["event"] == "worker_updated"]
            assert len(na_evts) == 1
            assert na_evts[0]["data"]["worker"]["codex_thread_id"] == "tid-verifier-1"
            assert na_evts[0]["data"]["worker"]["model"] == "gpt-6.1-mini"

            env_profile = "windows_minimal_py" if os.name == "nt" else "posix_minimal_py"
            cand = dispatcher.dispatch(
                "workflow_verifier_turn",
                {
                    "run_id": run_id,
                    "step_id": step_id,
                    "message_id": ver_prep["message_id"],
                    "phase": "prepare",
                    "scope": "candidate",
                    "env_profile": env_profile,
                    "verification_commands": [
                        {
                            "id": "check-cand",
                            "argv": [
                                sys.executable,
                                "-c",
                                "from pathlib import Path; import sys; assert Path(sys.argv[1]).read_text() == 'VALUE = 2\\n'",
                                str(ws_dir / "mod.py"),
                            ],
                            "cwd": str(ws_dir),
                            "timeout_sec": 15,
                            "criteria": ["mod.py updated"],
                        }
                    ],
                },
            )
            cand_out = tmp_path / "cand-receipts"
            collect(Path(cand["artifact"]["manifest_path"]), cand_out)
            dispatcher.dispatch(
                "workflow_verifier_turn",
                {
                    "run_id": run_id,
                    "step_id": step_id,
                    "phase": "record",
                    "artifact_id": cand["artifact"]["id"],
                    "verdict": "PASS",
                    "receipt_files": [str(cand_out / "receipt.json")],
                },
            )
            await asyncio.sleep(0)
            ver_evts = [e for e in _drain_events(queue) if e["event"] == "worker_updated"]
            assert len(ver_evts) == 2
            assert all(e["data"]["worker"]["model"] == "gpt-6.1-mini" for e in ver_evts)

            # Merge + integrated verify + closeout
            dispatcher.dispatch("workspace_merge", {"workspace_id": prep["workspace"]["id"]})
            iv_prep = dispatcher.dispatch(
                "workflow_prepare_dispatch",
                {
                    "run_id": run_id,
                    "dispatch_action": "request_verify",
                    "step_id": step_id,
                    "verifier": "verifier_1",
                    "instruction": "Verify integrated",
                },
            )
            dispatcher.dispatch(
                "native_action_record_call",
                {
                    "run_id": run_id,
                    "action_id": iv_prep["action_id"],
                    "claim_token": iv_prep["claim_token"],
                    "expected_revision": iv_prep["expected_revision"],
                    "outcome": "confirmed",
                    "raw_response": {"status": "queued"},
                },
            )
            integ = dispatcher.dispatch(
                "workflow_verifier_turn",
                {
                    "run_id": run_id,
                    "step_id": step_id,
                    "message_id": iv_prep["message_id"],
                    "phase": "prepare",
                    "scope": "integrated",
                    "env_profile": env_profile,
                    "verification_commands": [
                        {
                            "id": "check-integ",
                            "argv": [
                                sys.executable,
                                "-c",
                                "from pathlib import Path; import sys; assert Path(sys.argv[1]).read_text() == 'VALUE = 2\\n'",
                                str(project / "mod.py"),
                            ],
                            "cwd": str(project),
                            "timeout_sec": 15,
                            "criteria": ["mod.py updated"],
                        }
                    ],
                },
            )
            integ_out = tmp_path / "integ-receipts"
            collect(Path(integ["artifact"]["manifest_path"]), integ_out)
            dispatcher.dispatch(
                "workflow_verifier_turn",
                {
                    "run_id": run_id,
                    "step_id": step_id,
                    "phase": "record",
                    "artifact_id": integ["artifact"]["id"],
                    "verdict": "PASS",
                    "receipt_files": [str(integ_out / "receipt.json")],
                },
            )
            _drain_events(queue)

            # Add an L2 helper right before closeout to prove closeout syncs and emits refreshed stopped workers
            with closing(sqlite3.connect(db_state_path)) as conn, conn:
                conn.execute(
                    "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "tid-helper-late",
                        str(project),
                        None,
                        "late_helper",
                        "late_helper",
                        "late",
                        "subagent",
                        '{"subagent":{"thread_spawn":{}}}',
                        "/root/verifier_1/late_check",
                        "explorer",
                        "Fermat",
                        "gpt-5-mini",
                        "low",
                        "openai",
                        150.0,
                        150.0,
                    ),
                )
                conn.execute(
                    "INSERT INTO thread_spawn_edges VALUES (?, ?, ?)",
                    ("tid-verifier-1", "tid-helper-late", "completed"),
                )
            _bump_mtime(db_state_path)

            closeout = dispatcher.dispatch("workflow_closeout_run", {"run_id": run_id})
            assert closeout["run"]["status"] == "completed"
            await asyncio.sleep(0)
            close_evts = [e for e in _drain_events(queue) if e["event"] == "worker_updated"]
            by_name = {e["data"]["worker"]["name"]: e["data"]["worker"] for e in close_evts}
            assert by_name["coordinator"]["model"] == "gpt-6.1-sol"
            assert by_name["coder_1"]["model"] == "gpt-6.2-sol"
            assert by_name["verifier_1"]["model"] == "gpt-6.1-mini"
            assert {s["thread_id"] for s in db.list_worker_subagents(run_id)} == {
                "tid-helper-l2",
                "tid-helper-l3",
                "tid-helper-late",
            }

            # 6. Test session_bind and run_update syncing + duplicate path closest created_at
            run_late = dispatcher.dispatch(
                "run_create",
                {"mode": "swarm", "title": "Late bind", "project_root": str(project)},
            )
            dispatcher.dispatch(
                "worker_register",
                {
                    "run_id": run_late["id"],
                    "name": "reused",
                    "role": "swarm_worker",
                    "agent_id": "/root/reused",
                },
            )
            reused_worker = db.get_worker_by_name(run_late["id"], "reused")
            assert reused_worker["model"] is None

            with closing(sqlite3.connect(db_state_path)) as conn, conn:
                conn.executemany(
                    "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            "tid-reused-old",
                            str(project),
                            None,
                            "reused-old",
                            "reused-old",
                            "old",
                            "subagent",
                            '{"subagent":{"thread_spawn":{}}}',
                            "/root/reused",
                            "coder",
                            "Old",
                            "gpt-5",
                            "low",
                            "openai",
                            1.0,
                            1.0,
                        ),
                        (
                            "tid-reused-new",
                            str(project),
                            None,
                            "reused-new",
                            "reused-new",
                            "new",
                            "subagent",
                            '{"subagent":{"thread_spawn":{}}}',
                            "/root/reused",
                            "coder",
                            "New",
                            "gpt-6.2-sol",
                            "high",
                            "openai",
                            reused_worker["created_at"],
                            reused_worker["created_at"],
                        ),
                    ],
                )
                conn.executemany(
                    "INSERT INTO thread_spawn_edges VALUES (?, ?, ?)",
                    [
                        ("sess-sync", "tid-reused-old", "completed"),
                        ("sess-sync", "tid-reused-new", "completed"),
                    ],
                )
            _bump_mtime(db_state_path)

            dispatcher.dispatch(
                "session_bind",
                {"session_id": "sess-sync", "run_id": run_late["id"]},
            )
            bound_worker = db.get_worker_by_name(run_late["id"], "reused")
            assert bound_worker["codex_thread_id"] == "tid-reused-new"
            assert bound_worker["model"] == "gpt-6.2-sol"
            assert bound_worker["reasoning_effort"] == "high"

            # Add an L2 helper under tid-reused-new and trigger run_update to verify sync
            with closing(sqlite3.connect(db_state_path)) as conn, conn:
                conn.execute(
                    "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "tid-reused-helper",
                        str(project),
                        None,
                        "reused-helper",
                        "reused-helper",
                        "help",
                        "subagent",
                        '{"subagent":{"thread_spawn":{}}}',
                        "/root/reused/helper",
                        "explorer",
                        "Helper",
                        "gpt-5-mini",
                        "low",
                        "openai",
                        reused_worker["created_at"] + 1,
                        reused_worker["created_at"] + 1,
                    ),
                )
                conn.execute(
                    "INSERT INTO thread_spawn_edges VALUES (?, ?, ?)",
                    ("tid-reused-new", "tid-reused-helper", "completed"),
                )
            _bump_mtime(db_state_path)

            dispatcher.dispatch(
                "run_update",
                {"run_id": run_late["id"], "title": "Late bind updated"},
            )
            assert [s["thread_id"] for s in db.list_worker_subagents(run_late["id"])] == [
                "tid-reused-helper"
            ]

    asyncio.run(exercise())
