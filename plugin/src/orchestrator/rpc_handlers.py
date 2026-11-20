"""Unified RPC dispatcher, MCP tool JSON schemas, and thread-safe SSE EventBus."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from orchestrator import codex_bridge
from orchestrator.board import BoardService, _strip_artifact_manifests, _strip_step_manifests
from orchestrator.native_actions import NativeActionStore, digest as native_digest
from orchestrator.inbox import INBOX_TOOL_DEFINITIONS, InboxStore
from orchestrator.workflow import ENV_PROFILES, WorkflowConflictError, WorkflowService
from orchestrator.db import (
    VALID_MSG_TYPES,
    VALID_RUN_MODES,
    VALID_RUN_STATUSES,
    VALID_WORKER_ROLES,
    VALID_WORKER_STATUSES,
    Database,
    NotFoundError,
)
from orchestrator.gc import _dir_size_bytes
from orchestrator.workspace_mgr import (
    VALID_DEP_MODES,
    VALID_MERGE_STRATEGIES,
    WORKSPACES_DIRNAME,
    WorkspaceCleanupGuardError,
    WorkspaceManager,
    _is_reparse_or_symlink,
    _rmtree_safe,
)


def _discover_cli_executables() -> dict[str, str | None]:
    """Resolve exact paths for tri-orchestrator CLI scripts next to sys.executable."""
    scripts_dir = Path(sys.executable).parent
    ext = ".exe" if os.name == "nt" else ""
    result: dict[str, str | None] = {}
    for name in ("tri-orchestrator-verify", "tri-orchestrator-challenge"):
        candidate = scripts_dir / (name + ext)
        if candidate.is_file():
            result[name.replace("tri-orchestrator-", "")] = str(candidate)
        else:
            found = shutil.which(name)
            result[name.replace("tri-orchestrator-", "")] = found
    return result


def _clean_opt_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _extract_workspace_copy_stats(ws: dict[str, Any]) -> dict[str, Any]:
    """Extract copied_bytes / file_count metadata from workspace record or snapshot_manifest."""
    stats: dict[str, Any] = {}
    manifest = ws.get("snapshot_manifest") if isinstance(ws.get("snapshot_manifest"), dict) else {}
    for key in ("copied_bytes", "file_count"):
        if key in ws and ws[key] is not None:
            stats[key] = ws[key]
        elif key in manifest and manifest[key] is not None:
            stats[key] = manifest[key]
    return stats


def _compact_workspace_payload(
    ws: dict[str, Any], *, extra_stats: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Strip large snapshot manifests from workspace payload while preserving copy stats."""
    compact = {
        k: v
        for k, v in ws.items()
        if k not in {"snapshot_manifest", "snapshot_manifest_json"}
    }
    merged_stats = _extract_workspace_copy_stats(ws)
    if extra_stats:
        for key in ("copied_bytes", "file_count"):
            if key in extra_stats and extra_stats[key] is not None:
                merged_stats[key] = extra_stats[key]
    compact.update(merged_stats)
    return compact


def _resolve_live_model_and_effort(
    session_id: str | None, preferred_model: str | None = None
) -> tuple[str | None, str | None]:
    """Resolve model and matching reasoning_effort without pairing mismatched live effort."""
    preferred = _clean_opt_str(preferred_model)
    live = codex_bridge.get_thread_meta(session_id) if session_id else None
    live_model = _clean_opt_str(live.get("model")) if live else None
    live_effort = _clean_opt_str(live.get("reasoning_effort")) if live else None
    resolved_model = preferred or live_model
    resolved_effort = (
        live_effort
        if (live and resolved_model and live_model == resolved_model)
        else None
    )
    return resolved_model, resolved_effort


class EventBus:
    """Thread-safe pub/sub fan-out bus bridging sync worker threads to async SSE clients."""

    def __init__(self, max_queue_size: int = 256) -> None:
        self._lock = threading.Lock()
        self._max_queue_size = max(16, int(max_queue_size))
        self._next_id = 1
        # subscriber_id -> (loop, queue, run_id_filter)
        self._subscribers: dict[
            int,
            tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict[str, Any]], str | None],
        ] = {}

    def subscribe(
        self, run_id: str | None = None
    ) -> tuple[int, asyncio.Queue[dict[str, Any]]]:
        """Register an async SSE subscriber for the current running event loop."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=self._max_queue_size
        )
        normalized_run_id = run_id.strip() if isinstance(run_id, str) and run_id.strip() else None
        with self._lock:
            sub_id = self._next_id
            self._next_id += 1
            self._subscribers[sub_id] = (loop, queue, normalized_run_id)
        return sub_id, queue

    def unsubscribe(self, sub_id: int) -> None:
        """Remove an SSE subscriber cleanly."""
        with self._lock:
            self._subscribers.pop(sub_id, None)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def publish(
        self,
        event: str,
        data: dict[str, Any],
        *,
        run_id: str | None = None,
    ) -> int:
        """Publish an event from any thread or coroutine to matching SSE subscribers."""
        payload = dict(data)
        target_run_id = run_id or (
            str(payload["run_id"]) if payload.get("run_id") else None
        )
        message = {
            "event": event,
            "data": payload,
            "timestamp": time.time(),
        }

        with self._lock:
            targets = list(self._subscribers.items())

        try:
            current_loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        delivered = 0
        stale_ids: list[int] = []

        for sub_id, (sub_loop, queue, filter_run_id) in targets:
            if filter_run_id and filter_run_id != target_run_id:
                continue
            if sub_loop.is_closed():
                stale_ids.append(sub_id)
                continue

            def _put(q: asyncio.Queue[dict[str, Any]] = queue, item: dict[str, Any] = message) -> None:
                if q.full():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                try:
                    q.put_nowait(item)
                except asyncio.QueueFull:
                    pass

            try:
                if current_loop is sub_loop:
                    _put()
                else:
                    sub_loop.call_soon_threadsafe(_put)
                delivered += 1
            except RuntimeError:
                stale_ids.append(sub_id)

        if stale_ids:
            with self._lock:
                for sub_id in stale_ids:
                    self._subscribers.pop(sub_id, None)

        return delivered

    # Alias for callers using emit()
    emit = publish


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "run_create",
        "description": "Create an orchestration run (boost/teamwork/swarm) and auto-register its project root.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": sorted(VALID_RUN_MODES),
                    "description": "Orchestration workflow mode.",
                },
                "title": {"type": "string", "description": "Short run title."},
                "project_root": {
                    "type": "string",
                    "description": "Absolute project directory path (auto-registers project if needed).",
                },
                "project_id": {
                    "type": "string",
                    "description": "Existing project ID (alternative to project_root).",
                },
                "project_name": {"type": "string", "description": "Optional display name."},
                "is_git": {"type": "boolean", "description": "Optional git repo override."},
                "status": {
                    "type": "string",
                    "enum": sorted(VALID_RUN_STATUSES),
                    "default": "active",
                },
                "metadata": {"type": "object", "description": "Optional JSON metadata."},
                "session_id": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Optional root host session ID to bind for lifecycle hooks.",
                },
                "turn_id": {"type": "string", "description": "Codex turn that triggered this run."},
                "trigger_prompt": {"type": "string", "description": "Prompt that triggered this run."},
                "execution_root": {"type": "string", "description": "Execution directory at or below the registered project root."},
            },
            "required": ["mode", "title"],
            "additionalProperties": False,
        },
    },
    {
        "name": "run_update",
        "description": "Update an existing run's status, title, or metadata.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "status": {"type": "string", "enum": sorted(VALID_RUN_STATUSES)},
                "title": {"type": "string"},
                "metadata": {"type": "object"},
            },
            "required": ["run_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "run_list",
        "description": "List orchestration runs, optionally filtered by project_id, status, or mode.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "status": {"type": "string", "enum": sorted(VALID_RUN_STATUSES)},
                "mode": {"type": "string", "enum": sorted(VALID_RUN_MODES)},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "task_packet_save",
        "description": "Persist a monotonically versioned task packet (frozen goal, file ownership matrix, acceptance criteria).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "title": {"type": "string"},
                "goal": {"type": "string"},
                "file_ownership": {
                    "description": "Mutually exclusive file ownership map or list.",
                },
                "acceptance_criteria": {
                    "description": "List of verifiable acceptance criteria.",
                },
                "status": {"type": "string", "default": "active"},
            },
            "required": ["run_id", "title", "goal"],
            "additionalProperties": False,
        },
    },
    {
        "name": "task_packet_get",
        "description": "Retrieve the latest (or specific version) task packet for a run.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "version": {"type": "integer", "minimum": 1},
            },
            "required": ["run_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "board_post",
        "description": "Post a message/report/diff summary to the #seq SQLite board and return a compact pointer.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "sender": {"type": "string"},
                "summary": {"type": "string", "default": ""},
                "body_md": {"type": "string", "default": ""},
                "channel": {"type": "string", "default": "main"},
                "sender_role": {"type": "string", "default": ""},
                "mentions": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "msg_type": {
                    "type": "string",
                    "enum": sorted(VALID_MSG_TYPES),
                    "default": "info",
                },
            },
            "required": ["run_id", "sender"],
            "additionalProperties": False,
        },
    },
    {
        "name": "board_read",
        "description": "Incrementally read board messages after since_seq or reader cursor, with optional channel/@mention filters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "since_seq": {"type": "integer", "minimum": 0},
                "channel": {"type": "string"},
                "mention": {"type": "string"},
                "reader_name": {"type": "string"},
                "advance_cursor": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100},
            },
            "required": ["run_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "board_status",
        "description": "Get global run status: workers, stall flags, unread counts, channels, and latest task packet.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "reader_name": {"type": "string"},
                "compact": {
                    "type": "boolean",
                    "default": False,
                    "description": "Deduplicate native_actions, strip repeated body_md and raw *_json strings, and omit workspaces by default.",
                },
                "include_workspaces": {
                    "type": "boolean",
                    "description": "Explicitly include or omit workspaces list (defaults to False under compact=True, True under compact=False).",
                },
                "controls_only": {
                    "type": "boolean",
                    "default": False,
                    "description": "Fast path returning only run_id, run, workers, and control_requests with zero disk scan.",
                },
            },
            "required": ["run_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "board_channel_list",
        "description": "List active message board channels and message counts for a run.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
            },
            "required": ["run_id"],
            "additionalProperties": False,
        },
    },
    {"name": "worker_control_ack", "description": "Acknowledge native control execution with evidence.",
     "inputSchema": {"type": "object", "properties": {
         "request_id": {"type": "string"}, "outcome": {"type": "string", "enum": ["applied", "failed"]},
         "result": {"type": "string", "minLength": 1}},
         "required": ["request_id", "outcome", "result"], "additionalProperties": False}},
    {
        "name": "worker_register",
        "description": "Register or update a worker node in a run with role, status, task, and stall threshold.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "task_name": {"type": "string", "pattern": "^[a-z0-9_-]{1,48}$"},
                "pending_agent_path": {"type": "string", "pattern": "^/root/[a-z0-9_-]{1,48}$"},
                "run_id": {"type": "string"},
                "name": {"type": "string"},
                "role": {"type": "string", "enum": sorted(VALID_WORKER_ROLES)},
                "status": {
                    "type": "string",
                    "enum": sorted(VALID_WORKER_STATUSES),
                    "default": "waiting",
                },
                "workspace_id": {"type": "string"},
                "current_task": {"type": "string", "default": ""},
                "stall_threshold_sec": {"type": "integer", "minimum": 1},
            },
            "required": ["run_id", "name", "role"],
            "additionalProperties": False,
        },
    },
    {
        "name": "worker_heartbeat",
        "description": "Refresh a worker's heartbeat timestamp and clear any stall alert flag.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "worker_name": {"type": "string"},
                "worker_id": {"type": "string"},
                "current_task": {"type": "string"},
                "status": {"type": "string", "enum": sorted(VALID_WORKER_STATUSES)},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "worker_status_update",
        "description": "Transition worker status among waiting | working | stopped | killed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": sorted(VALID_WORKER_STATUSES)},
                "run_id": {"type": "string"},
                "worker_name": {"type": "string"},
                "worker_id": {"type": "string"},
                "current_task": {"type": "string"},
                "workspace_id": {"type": "string"},
            },
            "required": ["status"],
            "additionalProperties": False,
        },
    },
    {
        "name": "workspace_create",
        "description": "Create an isolated git clone --local (or non-git snapshot) workspace under .swarm-workspaces and allocate a test port.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "worker_name": {"type": "string"},
                "project_root": {"type": "string"},
                "copy_env": {"type": "boolean", "default": True},
                "dep_mode": {
                    "type": "string",
                    "enum": sorted(VALID_DEP_MODES),
                    "default": "none",
                },
            },
            "required": ["run_id", "worker_name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "workspace_diff",
        "description": "Return changed files, status codes, and unified diff text for a workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
            },
            "required": ["workspace_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "workspace_status",
        "description": "Return git/snapshot dirty status, commits ahead, and changed file lists for a workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
            },
            "required": ["workspace_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "workspace_merge_preview",
        "description": "Run non-destructive merge conflict preview (git merge-tree or 3-way snapshot) without mutating main repo.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
                "target_branch": {"type": "string"},
            },
            "required": ["workspace_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "workspace_merge",
        "description": "Merge or cherry-pick a clean verified workspace branch back into the main repository (aborts on conflict).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
                "target_branch": {"type": "string"},
                "strategy": {
                    "type": "string",
                    "enum": sorted(VALID_MERGE_STRATEGIES),
                    "default": "merge",
                },
                "reconcile": {"type": "boolean", "default": False},
            },
            "required": ["workspace_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "workspace_cleanup",
        "description": "Safely delete a workspace directory ONLY when status is 'merged' and working tree is clean.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
                "force": {"type": "boolean", "default": False},
            },
            "required": ["workspace_id"],
            "additionalProperties": False,
        },
    },
]

TOOL_DEFINITIONS.append({
    "name": "native_action_update",
    "description": "Root-coordinator native handoff ledger; never invokes host tools or acknowledges business work.",
    "inputSchema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["claim", "begin", "record", "reconcile", "claim_group", "begin_group", "record_group", "reconcile_group"]},
        "group_id": {"type": "string"},
        "members": {"type": "array", "minItems": 1, "items": {"type": "object"}},
        **{key: {"type": "string"} for key in ("run_id", "source_id", "recipient", "action_id", "claim_token", "source_digest", "evidence_ref", "evidence_digest", "result", "agent_id")},
        "source_type": {"type": "string", "enum": ["inbox", "control", "human"]},
        "action_type": {"type": "string", "enum": ["spawn_agent", "followup_task", "send_message", "interrupt_agent"]},
        "expected_revision": {"type": "integer", "minimum": 1},
        "cancel": {"type": "boolean", "default": False},
        "outcome": {"type": "string", "enum": ["confirmed", "failed", "not_called", "rejected", "unknown"]}
    }, "required": ["action"], "additionalProperties": False}
})
TOOL_DEFINITIONS.extend(INBOX_TOOL_DEFINITIONS + [
    {
        "name": "workflow_step_save",
        "description": "Create or revise a packet-bound work step; existing revisions require an optimistic revision check.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"}, "step_key": {"type": "string"},
                "owner": {"type": "string"}, "verifier": {"type": "string"},
                "workspace_id": {"type": "string"}, "step_id": {"type": "string"},
                "dependencies": {"type": "array", "items": {"type": "string"}},
                "kind": {"type": "string", "enum": ["task", "milestone"], "default": "task"},
                "expected_revision": {"type": "integer", "minimum": 0},
                "handoff_required": {"type": "boolean", "default": False},
                "criteria": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "complexity": {"type": "string", "enum": ["normal", "high"], "default": "normal"},
                "execution_kind": {"type": "string", "enum": ["native", "coordinator_local"], "default": "native"},
            },
            "required": ["run_id", "step_key", "owner", "verifier"], "additionalProperties": False,
        },
    },
    {
        "name": "workflow_step_advance",
        "description": "Advance one step through enforced gates, preserving evidence and the expected current revision.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "step_id": {"type": "string"},
                "action": {"type": "string", "enum": ["dispatch", "accept", "submit", "challenge", "start_rework",
                    "begin_integrated_review", "complete", "handoff", "ack_handoff", "reconcile", "observe", "reverify", "local_revision_boundary"]},
                "expected_revision": {"type": "integer", "minimum": 0},
                "evidence": {"type": "string"}, "agent_id": {"type": "string"},
                "artifact_id": {"type": "string"}, "recipient_agent_id": {"type": "string"},
                "handoff_digest": {"type": "string"}, "next_responsibility": {"type": "string"},
                "forbidden_actions": {"type": "array", "items": {"type": "string"}},
                "decision_seqs": {"type": "array", "items": {"type": "integer", "minimum": 1}},
                "observation": {
                    "type": "object",
                    "properties": {
                        "agent_id": {"type": "string"}, "state": {"type": "string"},
                        "observed_at": {"type": "number"}, "evidence": {"type": "string"},
                    },
                    "required": ["agent_id", "state", "observed_at", "evidence"], "additionalProperties": False,
                },
            },
            "required": ["step_id", "action", "expected_revision"], "additionalProperties": False,
        },
    },
    {
        "name": "artifact_capture",
        "description": "Capture a backend-generated candidate or integrated source fingerprint before independent verification.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "step_id": {"type": "string"},
                "scope": {"type": "string", "enum": ["candidate", "integrated"], "default": "candidate"},
                "paths": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "env_profile": {
                    "type": "string",
                    "enum": sorted(ENV_PROFILES),
                    "default": "none",
                    "description": "Optional explicit environment key allowlist template (stores key names only, never values).",
                },
                "verification_commands": {"type": "array", "minItems": 1, "items": {
                    "type": "object", "properties": {
                        "id": {"type": "string", "minLength": 1},
                        "argv": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                        "cwd": {"type": "string"}, "timeout_sec": {"type": "number", "exclusiveMinimum": 0},
                        "env_keys": {"type": "array", "uniqueItems": True, "items": {"type": "string"}, "default": []},
                        "criteria": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "string"}}},
                    "required": ["id", "argv", "cwd", "timeout_sec", "criteria"], "additionalProperties": False}},
            },
            "required": ["step_id"], "additionalProperties": False,
        },
    },
    {
        "name": "verification_record",
        "description": "Append immutable independent verification against a captured artifact; a receipt or board PASS is not a gate.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "step_id": {"type": "string"}, "artifact_id": {"type": "string"},
                "verifier": {"type": "string"},
                "verdict": {"type": "string", "enum": ["PASS", "FAIL"]},
                "key": {"type": "string"},
                "criteria_results": {"type": "array", "items": {
                    "type": "object", "properties": {"criterion": {"type": "string"}, "status": {"type": "string"}},
                    "required": ["criterion", "status"], "additionalProperties": False}},
                "evidence_files": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "receipt_files": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "resolved_finding_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                "dismissed_finding_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                "review_dimensions": {"type": "object", "properties": {
                    dimension: {"type": "string", "enum": ["PASS", "FAIL", "NOT_RUN"]}
                    for dimension in ("specification", "code", "stress", "evidence")},
                    "required": ["specification", "code", "stress", "evidence"], "additionalProperties": False},
            },
            "required": ["step_id", "artifact_id", "verifier", "verdict", "criteria_results", "evidence_files", "key"],
            "additionalProperties": False,
        },
    },
    {
        "name": "workflow_review",
        "description": "Append a read-only challenger's mapped finding; the original verifier owns FAIL, rework and resolution.",
        "inputSchema": {"type": "object", "properties": {
            "step_id": {"type": "string"}, "expected_revision": {"type": "integer", "minimum": 0},
            "agent_id": {"type": "string"}, "criterion": {"type": "string"}, "evidence": {"type": "string"}},
            "required": ["step_id", "expected_revision", "agent_id", "criterion", "evidence"], "additionalProperties": False},
    },
    {
        "name": "workflow_prepare_dispatch",
        "description": "Atomically prepare workspace, worker registration, step save/advance, inbox delivery, and native claim+begin in one SQLite transaction.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "dispatch_action": {
                    "type": "string",
                    "enum": ["dispatch", "start_rework", "request_verify"],
                    "default": "dispatch",
                },
                "create_workspace": {"type": "boolean", "default": False},
                "workspace_id": {"type": "string"},
                "workspace_base_mode": {
                    "type": "string",
                    "enum": ["auto", "git_clone", "snapshot"],
                    "default": "auto",
                },
                "copy_env": {"type": "boolean", "default": True},
                "dep_mode": {"type": "string", "enum": sorted(VALID_DEP_MODES), "default": "none"},
                "recipient": {"type": "string"},
                "role": {"type": "string", "enum": sorted(VALID_WORKER_ROLES)},
                "task_name": {"type": "string", "pattern": "^[a-z0-9_-]{1,48}$"},
                "pending_agent_path": {"type": "string", "pattern": "^/root/[a-z0-9_-]{1,48}$"},
                "agent_id": {"type": "string"},
                "worker_status": {"type": "string", "enum": sorted(VALID_WORKER_STATUSES)},
                "current_task": {"type": "string"},
                "stall_threshold_sec": {"type": "integer", "minimum": 1},
                "verifier_task_name": {"type": "string", "pattern": "^[a-z0-9_-]{1,48}$"},
                "verifier_agent_id": {"type": "string"},
                "save_step": {"type": "boolean"},
                "step_id": {"type": "string"},
                "step_key": {"type": "string"},
                "title": {"type": "string"},
                "goal": {"type": "string"},
                "instruction": {"type": "string"},
                "owner": {"type": "string"},
                "verifier": {"type": "string"},
                "file_ownership": {},
                "acceptance_criteria": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "criteria": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "commands": {"type": "array", "items": {}},
                "dependencies": {"type": "array", "items": {"type": "string"}},
                "step_kind": {"type": "string", "enum": ["task", "milestone"], "default": "task"},
                "handoff_required": {"type": "boolean", "default": False},
                "complexity": {"type": "string", "enum": ["normal", "high"], "default": "normal"},
                "execution_kind": {"type": "string", "enum": ["native", "coordinator_local"], "default": "native"},
                "expected_step_revision": {"type": "integer", "minimum": 0},
                "advance_evidence": {"type": "string"},
                "advance_agent_id": {"type": "string"},
                "observation": {
                    "type": "object",
                    "properties": {
                        "agent_id": {"type": "string"},
                        "state": {"type": "string"},
                        "observed_at": {"type": "number"},
                        "evidence": {"type": "string"},
                    },
                    "required": ["state", "evidence"],
                    "additionalProperties": False,
                },
                "sender": {"type": "string", "default": "coordinator"},
                "message_key": {"type": "string"},
                "key": {"type": "string"},
                "kind": {"type": "string", "enum": ["task", "rework", "review", "result", "notice"]},
                "subject": {"type": "string"},
                "summary": {"type": "string"},
                "body_md": {"type": "string"},
                "session_id": {"type": "string", "minLength": 1},
            },
            "required": ["run_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "native_action_record_call",
        "description": "Atomically persist host call receipt JSON to disk, compute 64-hex SHA-256 digest, and record/reconcile native action (backfilling workflow5 spawn bindings).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "action_id": {"type": "string"},
                "group_id": {"type": "string"},
                "members": {"type": "array", "minItems": 1, "items": {"type": "object"}},
                "claim_token": {"type": "string"},
                "expected_revision": {"type": "integer", "minimum": 1},
                "outcome": {
                    "type": "string",
                    "enum": ["confirmed", "failed", "not_called", "rejected", "unknown"],
                },
                "raw_response": {
                    "description": "Raw host tool return payload (JSON object or string).",
                },
                "agent_id": {"type": "string"},
                "error_text": {"type": "string"},
                "reconcile": {"type": "boolean", "default": False},
                "cancel": {"type": "boolean", "default": False},
            },
            "required": ["run_id", "outcome", "raw_response"],
            "additionalProperties": False,
        },
    },
    {
        "name": "workflow_author_turn",
        "description": "Atomically bundle author inbox acknowledgement and workflow step accept/submit in one transaction.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "step_id": {"type": "string"},
                "message_id": {"type": "string"},
                "phase": {"type": "string", "enum": ["accept", "submit"]},
                "author": {"type": "string"},
                "recipient": {"type": "string"},
                "expected_revision": {"type": "integer", "minimum": 0},
                "summary": {"type": "string"},
                "evidence": {"type": "string"},
                "body_md": {"type": "string", "description": "Detailed body for board note when post_board_note is true."},
                "artifact_ids": {"type": "array", "items": {"type": "string"}},
                "post_board_note": {"type": "boolean", "default": False},
            },
            "required": ["run_id", "step_id", "message_id", "phase"],
            "additionalProperties": False,
        },
    },
    {
        "name": "workflow_verifier_turn",
        "description": "Atomic verifier macro: prepare (ack inbox + set working + artifact_capture) or record (auto-challenge + verification_record + ack completed + set waiting).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "step_id": {"type": "string"},
                "phase": {"type": "string", "enum": ["prepare", "record"]},
                "message_id": {"type": "string", "description": "Inbox message_id; auto-located if omitted."},
                "scope": {"type": "string", "enum": ["candidate", "integrated"]},
                "verifier": {"type": "string"},
                "paths": {"type": "array", "items": {"type": "string"}},
                "verification_commands": {"type": "array", "items": {"type": "object"}},
                "env_profile": {"type": "string"},
                "artifact_id": {"type": "string"},
                "verdict": {"type": "string", "enum": ["PASS", "FAIL"]},
                "criteria_results": {"type": "array", "items": {"type": "object"}},
                "evidence_files": {"type": "array", "items": {"type": "string"}},
                "key": {"type": "string"},
                "receipt_files": {"type": "array", "items": {"type": "string"}},
                "review_dimensions": {"type": "object"},
                "resolved_finding_ids": {"type": "array", "items": {"type": "string"}},
                "dismissed_finding_ids": {"type": "array", "items": {"type": "string"}},
                "challenge_evidence": {"type": "string"},
                "summary": {"type": "string"},
                "post_board_note": {"type": "boolean", "default": False},
                "body_md": {"type": "string"},
            },
            "required": ["run_id", "step_id", "phase"],
            "additionalProperties": False,
        },
    },
    {
        "name": "workflow_closeout_run",
        "description": "Single guarded RPC to complete all integrated_review steps, clean up workspaces, stop workers, and mark run completed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "summary": {"type": "string"},
            },
            "required": ["run_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "session_bind",
        "description": "Bind a root host session_id to an orchestration run_id and project_root for lifecycle hooks.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "minLength": 1},
                "run_id": {"type": "string", "minLength": 1},
                "project_root": {"type": "string"},
            },
            "required": ["session_id", "run_id"],
            "additionalProperties": False,
        },
    },
])

# Internal /api/rpc endpoints omitted from MCP tools/list to avoid LLM schema token tax.
INTERNAL_RPC_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "session_hook_check",
        "description": "Record eligible Codex turns, return compact resume context, and evaluate the per-Run Stop gate.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "event_name": {"type": "string"},
                "cwd": {"type": "string"},
                "turn_id": {"type": "string"},
                "model": {"type": "string"},
                "prompt_preview": {"type": "string"},
                "has_mode_invocation": {"type": "boolean"},
                "stop_hook_active": {"type": "boolean", "default": False},
                "mark_stop_blocked": {"type": "boolean", "default": True},
            },
            "required": ["session_id", "event_name"],
            "additionalProperties": False,
        },
    },
]
ALL_RPC_DEFINITIONS: list[dict[str, Any]] = TOOL_DEFINITIONS + INTERNAL_RPC_DEFINITIONS


def get_tool_definitions() -> list[dict[str, Any]]:
    """Return the same formal tool catalog for HTTP and MCP."""
    return [dict(item) for item in TOOL_DEFINITIONS]


class RpcDispatcher:
    """Unified synchronous business handlers; inbox waiting stays on the async HTTP path."""

    def __init__(
        self,
        db: Database,
        board: BoardService,
        workspace_mgr: WorkspaceManager,
        event_bus: Any | None = None,
    ) -> None:
        self.db = db
        self.board = board
        self.workspace_mgr = workspace_mgr
        self.event_bus = event_bus
        self.native_actions = NativeActionStore(db)
        self.inbox = InboxStore(db, board)
        self.workflow = WorkflowService(db)
        self._handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "run_create": self._handle_run_create,
            "run_update": self._handle_run_update,
            "run_list": self._handle_run_list,
            "task_packet_save": self._handle_task_packet_save,
            "task_packet_get": self._handle_task_packet_get,
            "board_post": self._handle_board_post,
            "board_read": self._handle_board_read,
            "board_status": self._handle_board_status,
            "native_action_update": self._handle_native_action_update,
            "board_channel_list": self._handle_board_channel_list,
            "worker_control_ack": self._handle_worker_control_ack,
            "worker_register": self._handle_worker_register,
            "worker_heartbeat": self._handle_worker_heartbeat,
            "worker_status_update": self._handle_worker_status_update,
            "workspace_create": self._handle_workspace_create,
            "workspace_diff": self._handle_workspace_diff,
            "workspace_status": self._handle_workspace_status,
            "workspace_merge_preview": self._handle_workspace_merge_preview,
            "workspace_merge": self._handle_workspace_merge,
            "workspace_cleanup": self._handle_workspace_cleanup,
            "inbox_send": self._handle_inbox_send,
            "inbox_get": self._handle_inbox_get,
            "inbox_ack": self._handle_inbox_ack,
            "inbox_wait": self._async_wait_required,
            "workflow_step_save": self._handle_workflow_step_save,
            "workflow_step_advance": self._handle_workflow_step_advance,
            "artifact_capture": self._handle_artifact_capture,
            "verification_record": self._handle_verification_record,
            "workflow_review": self._handle_workflow_review,
            "workflow_prepare_dispatch": self._handle_workflow_prepare_dispatch,
            "native_action_record_call": self._handle_native_action_record_call,
            "workflow_author_turn": self._handle_workflow_author_turn,
            "workflow_verifier_turn": self._handle_workflow_verifier_turn,
            "workflow_closeout_run": self._handle_workflow_closeout_run,
            "session_bind": self._handle_session_bind,
            "session_hook_check": self._handle_session_hook_check,
        }

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(self._handlers.keys())

    def list_tools(self) -> list[dict[str, Any]]:
        """Return MCP tool definitions for tools/list."""
        return get_tool_definitions()

    def dispatch(
        self, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Validate tool_name and invoke the corresponding handler."""
        args = self.validate_arguments(tool_name, arguments)
        return self._handlers[tool_name](args)

    def validate_arguments(self, tool_name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        """Reject missing/unknown fields at the common HTTP/MCP boundary."""
        if tool_name not in self._handlers:
            raise ValueError(f"Unknown RPC tool: {tool_name!r}")
        if arguments is None:
            args: dict[str, Any] = {}
        elif isinstance(arguments, dict):
            args = arguments
        else:
            raise ValueError("Tool arguments must be a JSON object")
        schema = next(tool["inputSchema"] for tool in ALL_RPC_DEFINITIONS if tool["name"] == tool_name)
        unknown = set(args) - set(schema["properties"])
        if unknown:
            raise ValueError(f"Unknown arguments for {tool_name}: {sorted(unknown)}")
        missing = set(schema.get("required", [])) - set(args)
        if missing:
            raise ValueError(f"Missing required arguments for {tool_name}: {sorted(missing)}")
        return args

    # Alias for callers using call_tool()
    call_tool = dispatch

    def _emit(
        self, event: str, data: dict[str, Any], *, run_id: str | None = None
    ) -> None:
        if self.event_bus is None:
            return
        if hasattr(self.event_bus, "publish"):
            try:
                self.event_bus.publish(event, data, run_id=run_id)
            except TypeError:
                self.event_bus.publish(event, data)
        elif hasattr(self.event_bus, "emit"):
            self.event_bus.emit(event, data)

    @staticmethod
    def _require_str(args: dict[str, Any], key: str) -> str:
        val = args.get(key)
        if not isinstance(val, str) or not val.strip():
            raise ValueError(f"Missing or invalid required string argument {key!r}")
        return val

    def _sync_run_agent_models(self, run_id: str) -> dict[str, dict[str, Any]]:
        """Snapshot live Codex thread model metadata for run workers and their L2+ subagents."""
        run = self.db.get_run(run_id)
        if not run or not run.get("session_id"):
            return {}

        session_id = str(run["session_id"])
        root_thread = codex_bridge.get_thread_meta(session_id)
        subagents = codex_bridge.get_session_subagents(session_id)
        workers = self.db.list_workers(run_id=run_id)
        if not workers:
            return {}

        by_id: dict[str, dict[str, Any]] = {}
        depth1_by_path: dict[str, list[dict[str, Any]]] = {}
        children_by_parent: dict[str, list[dict[str, Any]]] = {}

        for node in subagents:
            tid = _clean_opt_str(node.get("thread_id"))
            if not tid:
                continue
            by_id[tid] = node
            node_path = _clean_opt_str(node.get("agent_path"))
            if node.get("depth") == 1 and node_path:
                depth1_by_path.setdefault(node_path, []).append(node)
            parent_tid = _clean_opt_str(node.get("parent_thread_id"))
            if parent_tid:
                children_by_parent.setdefault(parent_tid, []).append(node)

        refreshed: dict[str, dict[str, Any]] = {}
        subagent_rows: list[dict[str, Any]] = []
        seen_subagent_tids: set[str] = set()

        for worker in workers:
            agent_path = (
                _clean_opt_str(worker.get("agent_id"))
                or _clean_opt_str(worker.get("pending_agent_path"))
                or ""
            )
            is_root_worker = agent_path == "/root"
            existing_tid = _clean_opt_str(worker.get("codex_thread_id"))

            matched: dict[str, Any] | None = None
            matched_thread_id: str | None = None

            if existing_tid and existing_tid in by_id:
                matched = by_id[existing_tid]
                matched_thread_id = existing_tid
            elif is_root_worker:
                if root_thread is not None:
                    matched = root_thread
                    matched_thread_id = session_id
            elif agent_path and agent_path in depth1_by_path:
                candidates = depth1_by_path[agent_path]
                if len(candidates) == 1:
                    matched = candidates[0]
                else:
                    worker_created = worker.get("created_at") or 0.0
                    matched = min(
                        candidates,
                        key=lambda node: (
                            abs(
                                (
                                    node.get("created_at")
                                    if node.get("created_at") is not None
                                    else worker_created
                                )
                                - worker_created
                            ),
                            str(node.get("thread_id") or ""),
                        ),
                    )
                matched_thread_id = _clean_opt_str(matched.get("thread_id"))
            else:
                direct_agent_id = _clean_opt_str(worker.get("agent_id"))
                if direct_agent_id and direct_agent_id in by_id:
                    matched_thread_id = direct_agent_id
                    matched = by_id[direct_agent_id]

            if matched is None or not matched_thread_id:
                refreshed[worker["name"]] = worker
                continue

            updated_worker = self.db.update_worker_model_meta(
                worker["id"],
                codex_thread_id=matched_thread_id,
                model=_clean_opt_str(matched.get("model")),
                reasoning_effort=_clean_opt_str(matched.get("reasoning_effort")),
                model_provider=_clean_opt_str(matched.get("model_provider")),
            )
            refreshed[worker["name"]] = (
                updated_worker if updated_worker is not None else worker
            )

            if not is_root_worker and matched_thread_id != session_id:
                queue = deque(children_by_parent.get(matched_thread_id, ()))
                visited = {matched_thread_id}
                while queue:
                    child = queue.popleft()
                    child_tid = _clean_opt_str(child.get("thread_id"))
                    if not child_tid or child_tid in visited:
                        continue
                    visited.add(child_tid)
                    depth = int(child.get("depth") or 0)
                    if depth >= 2 and child_tid not in seen_subagent_tids:
                        seen_subagent_tids.add(child_tid)
                        subagent_rows.append(
                            {
                                "thread_id": child_tid,
                                "parent_worker_id": worker["id"],
                                "parent_thread_id": (
                                    _clean_opt_str(child.get("parent_thread_id"))
                                    or matched_thread_id
                                ),
                                "depth": depth,
                                "agent_path": child.get("agent_path") or "",
                                "agent_role": child.get("agent_role"),
                                "agent_nickname": child.get("agent_nickname"),
                                "model": child.get("model"),
                                "reasoning_effort": child.get("reasoning_effort"),
                                "model_provider": child.get("model_provider"),
                                "edge_status": child.get("edge_status"),
                                "created_at": child.get("created_at"),
                                "updated_at": child.get("updated_at"),
                            }
                        )
                    queue.extend(children_by_parent.get(child_tid, ()))

        if subagent_rows:
            self.db.upsert_worker_subagents(run_id, subagent_rows)

        return refreshed

    # ------------------------------------------------------------------
    # Run & Task Packet Handlers
    # ------------------------------------------------------------------

    def _handle_run_create(self, args: dict[str, Any]) -> dict[str, Any]:
        mode = self._require_str(args, "mode").strip()
        title = self._require_str(args, "title").strip()
        project_root, project_id = args.get("project_root"), args.get("project_id")
        session_id = args.get("session_id")
        if session_id is not None:
            session_id = self._require_str(args, "session_id").strip()
        turn_id, trigger_prompt = args.get("turn_id"), args.get("trigger_prompt")
        execution_root = args.get("execution_root") or ""
        project_fields = None

        thread = None
        if project_id is None and project_root is None and session_id:
            thread = codex_bridge.get_thread_meta(session_id)
            if thread:
                project_root = thread["codex_project_root"] or thread["cwd"] or None

        if project_root is not None and project_id is None:
            requested_root = Path(project_root).resolve(strict=False)
            root = requested_root
            is_git = args.get("is_git")
            if is_git is None:
                is_git = self._is_git_project_root(root)
            codex = codex_bridge.resolve_codex_project_for_path(root)
            codex_root = Path(codex["root_path"]).resolve(strict=False) if codex else None
            registered = None

            if not is_git and not (root / ".git").exists() and root != codex_root:
                registered_parents = [
                    (path, project)
                    for project in self.db.list_projects()
                    if project["project_kind"] == "workspace"
                    and (path := Path(project["root_path"]).resolve(strict=False)) in root.parents
                ]
                if registered_parents:
                    root, registered = max(registered_parents, key=lambda parent: len(parent[0].parts))
                if codex_root in requested_root.parents and (root == requested_root or root in codex_root.parents):
                    root = codex_root
                    registered = self.db.get_project_by_root(root)
                if root != requested_root:
                    execution_root = execution_root or str(requested_root)
                    is_git = registered["is_git"] if registered else self._is_git_project_root(root)

            codex_id = codex["project_id"] if codex else None
            codex_name = codex["name"] if codex else None
            if registered:
                codex_id = codex_id or registered["codex_project_id"]
                codex_name = codex_name or registered["codex_project_name"]
            if thread and not codex:
                codex_id = thread["codex_project_id"] or codex_id
                codex_name = thread["codex_project_name"] or codex_name

            name = (args.get("project_name") or "").strip() or None
            if root != requested_root:
                name = codex_name or (registered["name"] if registered else None)
            elif name is None and (root == codex_root or thread):
                name = codex_name
            project_fields = (root, name, is_git, codex_id, codex_name)

        previous_run_id = None
        with self.db.transaction():
            if project_fields is not None:
                root, name, is_git, codex_id, codex_name = project_fields
                project_id = self.db.ensure_project(
                    root, name=name, is_git=is_git,
                    codex_project_id=codex_id, codex_project_name=codex_name,
                )["id"]
            turn = None
            if session_id:
                turn = self.db.get_session_turn(session_id, turn_id) if turn_id else None
                if turn_id is None:
                    turns = self.db.list_session_turns(session_id)
                    project = self.db.get_project(project_id) if project_id else None
                    if turns and project:
                        candidate = turns[0]
                        cwd = candidate["cwd"]
                        scope = Path(project["root_path"]).resolve(strict=False)
                        if (not cwd or Path(cwd).resolve(strict=False).is_relative_to(scope)) and not \
                                self.db.has_run_trigger(session_id, candidate["turn_id"]):
                            turn = candidate
                            turn_id = turn["turn_id"]
                if turn:
                    previous_run_id = turn["active_run_id"]
                    if trigger_prompt is None:
                        trigger_prompt = turn["prompt_preview"]
            trigger_model = _clean_opt_str(turn.get("model")) if turn else None
            trigger_reasoning_effort = _clean_opt_str(turn.get("reasoning_effort")) if turn else None
            if session_id and (not trigger_model or not trigger_reasoning_effort):
                live_model, live_effort = _resolve_live_model_and_effort(session_id, trigger_model)
                if not trigger_model:
                    trigger_model = live_model
                if not trigger_reasoning_effort and trigger_model and live_model == trigger_model:
                    trigger_reasoning_effort = live_effort
                if turn and (
                    (not _clean_opt_str(turn.get("model")) and trigger_model)
                    or (not _clean_opt_str(turn.get("reasoning_effort")) and trigger_reasoning_effort)
                ):
                    self.db.update_session_turn_model(
                        session_id,
                        turn["turn_id"],
                        model=trigger_model,
                        reasoning_effort=trigger_reasoning_effort,
                    )
            run = self.db.create_run(
                mode=mode,
                title=title,
                project_id=project_id,
                status=str(args.get("status") or "active"),
                metadata=args.get("metadata"),
                session_id=session_id,
                turn_id=turn_id,
                trigger_prompt=trigger_prompt if trigger_prompt is not None else "",
                execution_root=str(Path(execution_root).resolve(strict=False)) if execution_root else "",
                trigger_model=trigger_model,
                trigger_reasoning_effort=trigger_reasoning_effort,
            )
        self._emit("run_updated", {"run_id": run["id"], "run": run}, run_id=run["id"])
        if session_id:
            self._emit("session_updated", {"run_id": run["id"], "session_id": session_id}, run_id=run["id"])
            if previous_run_id and previous_run_id != run["id"]:
                self._emit("session_updated", {"run_id": previous_run_id, "session_id": session_id,
                                               "turn_id": turn_id}, run_id=previous_run_id)
        return {**run, "cli_executables": _discover_cli_executables()}

    @staticmethod
    def _is_git_project_root(root: Path) -> bool:
        if not root.is_dir():
            return False
        is_git, repo_root, _, _ = WorkspaceManager._detect_git_repo(root)
        return is_git and repo_root == root

    def _handle_run_update(self, args: dict[str, Any]) -> dict[str, Any]:
        run_id = self._require_str(args, "run_id")
        run = self.db.update_run(
            run_id,
            status=args.get("status"),
            title=args.get("title"),
            metadata=args.get("metadata"),
        )
        self._sync_run_agent_models(run_id)
        self._emit("run_updated", {"run_id": run["id"], "run": run}, run_id=run["id"])
        return {**run, "cli_executables": _discover_cli_executables()}

    def _handle_run_list(self, args: dict[str, Any]) -> dict[str, Any]:
        runs = self.db.list_runs(
            project_id=args.get("project_id"),
            status=args.get("status"),
            mode=args.get("mode"),
            limit=int(args.get("limit") or 100),
        )
        return {"runs": runs, "count": len(runs)}

    def _handle_task_packet_save(self, args: dict[str, Any]) -> dict[str, Any]:
        run_id = self._require_str(args, "run_id")
        title = self._require_str(args, "title")
        goal = self._require_str(args, "goal")
        pkt = self.db.save_task_packet(
            run_id,
            title=title,
            goal=goal,
            file_ownership=args.get("file_ownership"),
            acceptance_criteria=args.get("acceptance_criteria"),
            status=str(args.get("status") or "active"),
        )
        self._emit(
            "task_packet_updated",
            {"run_id": run_id, "task_packet": pkt},
            run_id=run_id,
        )
        return pkt

    def _handle_task_packet_get(self, args: dict[str, Any]) -> dict[str, Any]:
        run_id = self._require_str(args, "run_id")
        if self.db.get_run(run_id) is None:
            raise NotFoundError(f"Run not found: {run_id}")
        raw_ver = args.get("version")
        version = int(raw_ver) if raw_ver is not None else None
        pkt = self.db.get_task_packet(run_id, version=version)
        if pkt is None:
            return {"run_id": run_id, "found": False, "task_packet": None}
        return {"found": True, "task_packet": pkt, **pkt}

    # ------------------------------------------------------------------
    # Message Board & Worker Lifecycle Handlers
    # ------------------------------------------------------------------

    def _handle_board_post(self, args: dict[str, Any]) -> dict[str, Any]:
        run_id = self._require_str(args, "run_id")
        sender = self._require_str(args, "sender")
        pointer = self.board.board_post(
            run_id,
            sender,
            summary=str(args.get("summary") or ""),
            body_md=str(args.get("body_md") or ""),
            channel=str(args.get("channel") or "main"),
            sender_role=str(args.get("sender_role") or ""),
            mentions=args.get("mentions"),
            msg_type=str(args.get("msg_type") or "info"),
        )
        full_msg = self.board.get_message(int(pointer["seq"]))
        if full_msg is not None:
            self._emit(
                "board_message",
                {"run_id": run_id, "message": full_msg},
                run_id=run_id,
            )
        return pointer

    def _handle_board_read(self, args: dict[str, Any]) -> dict[str, Any]:
        run_id = self._require_str(args, "run_id")
        raw_since = args.get("since_seq")
        return self.board.board_read(
            run_id,
            since_seq=int(raw_since) if raw_since is not None else None,
            channel=args.get("channel"),
            mention=args.get("mention"),
            reader_name=args.get("reader_name"),
            advance_cursor=bool(args.get("advance_cursor", False)),
            limit=int(args.get("limit") or 100),
        )

    def _handle_board_status(self, args: dict[str, Any]) -> dict[str, Any]:
        run_id = self._require_str(args, "run_id")
        compact = args.get("compact", False)
        if type(compact) is not bool:
            raise ValueError("compact must be a boolean")
        include_ws = args.get("include_workspaces")
        if include_ws is not None and type(include_ws) is not bool:
            raise ValueError("include_workspaces must be a boolean when provided")
        controls_only = args.get("controls_only", False)
        if type(controls_only) is not bool:
            raise ValueError("controls_only must be a boolean")
        return self.board.board_status(
            run_id,
            reader_name=args.get("reader_name"),
            compact=compact,
            include_workspaces=include_ws,
            controls_only=controls_only,
        )

    def _handle_board_channel_list(self, args: dict[str, Any]) -> dict[str, Any]:
        run_id = self._require_str(args, "run_id")
        return self.board.board_channel_list(run_id)

    def _handle_worker_control_ack(self, args: dict[str, Any]) -> dict[str, Any]:
        request = self.db.worker_control_ack(self._require_str(args, "request_id"), self._require_str(args, "outcome"), self._require_str(args, "result"))
        self._emit("worker_control_updated", {"run_id": request["run_id"], "control_request": request}, run_id=request["run_id"])
        if request["status"] == "applied":
            worker = self.db.get_worker(request["worker_id"])
            self._emit("worker_updated", {"run_id": request["run_id"], "worker": worker}, run_id=request["run_id"])
        return request

    def _handle_worker_register(self, args: dict[str, Any]) -> dict[str, Any]:
        run_id = self._require_str(args, "run_id")
        name = self._require_str(args, "name")
        role = self._require_str(args, "role")
        raw_threshold = args.get("stall_threshold_sec")
        worker = self.board.worker_register(
            run_id,
            name,
            role,
            agent_id=args.get("agent_id"),
            task_name=args.get("task_name"),
            pending_agent_path=args.get("pending_agent_path"),
            status=str(args.get("status") or "waiting"),
            workspace_id=args.get("workspace_id"),
            current_task=str(args.get("current_task") or ""),
            stall_threshold_sec=(
                int(raw_threshold) if raw_threshold is not None else None
            ),
        )
        refreshed = self._sync_run_agent_models(run_id)
        worker = refreshed.get(worker["name"], worker)
        self._emit(
            "worker_updated",
            {"run_id": worker["run_id"], "worker": worker},
            run_id=worker["run_id"],
        )
        return worker

    def _handle_worker_heartbeat(self, args: dict[str, Any]) -> dict[str, Any]:
        worker = self.board.worker_heartbeat(
            run_id=args.get("run_id"),
            worker_name=args.get("worker_name"),
            worker_id=args.get("worker_id"),
            current_task=args.get("current_task"),
            status=args.get("status"),
        )
        refreshed = self._sync_run_agent_models(worker["run_id"])
        worker = refreshed.get(worker["name"], worker)
        self._emit(
            "worker_updated",
            {"run_id": worker["run_id"], "worker": worker},
            run_id=worker["run_id"],
        )
        return worker

    def _handle_worker_status_update(self, args: dict[str, Any]) -> dict[str, Any]:
        status = self._require_str(args, "status")
        worker = self.board.worker_status_update(
            run_id=args.get("run_id"),
            worker_name=args.get("worker_name"),
            status=status,
            worker_id=args.get("worker_id"),
            current_task=args.get("current_task"),
            workspace_id=args.get("workspace_id"),
        )
        refreshed = self._sync_run_agent_models(worker["run_id"])
        worker = refreshed.get(worker["name"], worker)
        self._emit(
            "worker_updated",
            {"run_id": worker["run_id"], "worker": worker},
            run_id=worker["run_id"],
        )
        return worker

    @staticmethod
    def _async_wait_required(args: dict[str, Any]) -> dict[str, Any]:
        del args
        raise ValueError("inbox_wait requires the asynchronous /api/rpc path")

    def _handle_native_action_update(self, args: dict[str, Any]) -> dict[str, Any]:
        result = self.native_actions.update(**args)
        if 'group_id' in result:
            run_id = result['members'][0]['run_id']
            event = {'run_id': run_id, 'group_id': result['group_id'], 'members': [
                {'action_id': member['action_id'], 'status': member['status']} for member in result['members']]}
        else:
            run_id = result['run_id']
            event = {'run_id': run_id, 'action_id': result['action_id'], 'status': result['status']}
            if result.get('action_type') == 'spawn_agent' and args.get('action') in {'record', 'reconcile'}:
                refreshed = self._sync_run_agent_models(run_id)
                worker = refreshed.get(result['recipient']) or self.db.get_worker_by_name(run_id, result['recipient'])
                if worker is not None:
                    self._emit('worker_updated', {'run_id': run_id, 'worker': worker}, run_id=run_id)
                self._emit('workflow_updated', {'run_id': run_id}, run_id=run_id)
        self._emit('native_action_updated', event, run_id=run_id)
        return result

    def _handle_inbox_send(self, args: dict[str, Any]) -> dict[str, Any]:
        result = self.inbox.send(**args)
        if not result["duplicate"]:
            message = result["message"]
            self._emit("inbox_updated", {"run_id": message["run_id"], "message": message}, run_id=message["run_id"])
        return result

    def _handle_inbox_get(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.inbox.get(**args)

    def _handle_inbox_ack(self, args: dict[str, Any]) -> dict[str, Any]:
        message = self.inbox.ack(**args)
        self._emit("inbox_updated", {"run_id": message["run_id"], "message": message}, run_id=message["run_id"])
        return message

    def _handle_workflow_step_save(self, args: dict[str, Any]) -> dict[str, Any]:
        step = self.workflow.step_save(**args)
        run_id = step["run_id"]
        self._emit("workflow_updated", {"run_id": run_id, "step": step}, run_id=run_id)
        run = self.db.get_run(run_id)
        if run and run["session_id"]:
            self._emit("session_updated", {"run_id": run_id, "session_id": run["session_id"]}, run_id=run_id)
        return step

    def _handle_workflow_step_advance(self, args: dict[str, Any]) -> dict[str, Any]:
        step = self.workflow.step_advance(**args)
        self._emit("workflow_updated", {"run_id": step["run_id"], "step": step}, run_id=step["run_id"])
        return step

    def _handle_artifact_capture(self, args: dict[str, Any]) -> dict[str, Any]:
        artifact = self.workflow.capture(**args)
        run_id = self.workflow.get_step(artifact["step_id"])["run_id"]
        result = _strip_artifact_manifests(artifact)
        self._emit("workflow_updated", {"run_id": run_id, "artifact": result}, run_id=run_id)
        # Include CLI executables and ready-to-run verify template outside workspace root.
        cli_exes = _discover_cli_executables()
        verify_exe = cli_exes.get("verify")
        result["verify_executable"] = verify_exe
        result["cli_executables"] = cli_exes
        manifest_path = result.get("manifest_path")
        if manifest_path:
            suggested_out = str((self.db.db_path.parent / "verification-receipts" / result["id"]).resolve())
            exe_or_name = verify_exe or "tri-orchestrator-verify"
            result["verify_argv_template"] = [
                exe_or_name, "--manifest", manifest_path, "--output-dir", suggested_out,
            ]
            result["verify_command_hint"] = (
                f'"{exe_or_name}" --manifest "{manifest_path}" --output-dir "{suggested_out}"'
            )
        else:
            result["verify_argv_template"] = None
            result["verify_command_hint"] = None
        return result

    def _handle_verification_record(self, args: dict[str, Any]) -> dict[str, Any]:
        verification = self.workflow.record(**args)
        run_id = self.workflow.get_step(verification["step_id"])["run_id"]
        self._emit("workflow_updated", {"run_id": run_id, "verification": verification}, run_id=run_id)
        return verification

    def _handle_workflow_review(self, args: dict[str, Any]) -> dict[str, Any]:
        finding = self.workflow.review(**args)
        run_id = self.workflow.get_step(args['step_id'])['run_id']
        self._emit('workflow_updated', {'run_id': run_id, 'finding': finding}, run_id=run_id)
        return finding

    # ------------------------------------------------------------------
    # Isolated Workspace Handlers
    # ------------------------------------------------------------------

    def _handle_workspace_create(self, args: dict[str, Any]) -> dict[str, Any]:
        run_id = self._require_str(args, "run_id")
        worker_name = self._require_str(args, "worker_name")
        ws = self.workspace_mgr.workspace_create(
            run_id,
            worker_name,
            project_root=args.get("project_root"),
            copy_env=bool(args.get("copy_env", True)),
            dep_mode=str(args.get("dep_mode") or "none"),
        )
        copy_stats = _extract_workspace_copy_stats(ws)
        if copy_stats:
            ws = {**ws, **copy_stats}
        self._emit(
            "workspace_updated",
            {"run_id": ws["run_id"], "workspace": _compact_workspace_payload(ws)},
            run_id=ws["run_id"],
        )
        worker = self.db.get_worker_by_name(run_id, worker_name)
        if worker is not None:
            self._emit(
                "worker_updated",
                {"run_id": run_id, "worker": worker},
                run_id=run_id,
            )
        return ws

    def _handle_workspace_diff(self, args: dict[str, Any]) -> dict[str, Any]:
        workspace_id = self._require_str(args, "workspace_id")
        return self.workspace_mgr.workspace_diff(workspace_id)

    def _handle_workspace_status(self, args: dict[str, Any]) -> dict[str, Any]:
        workspace_id = self._require_str(args, "workspace_id")
        return self.workspace_mgr.workspace_status(workspace_id)

    def _handle_workspace_merge_preview(self, args: dict[str, Any]) -> dict[str, Any]:
        workspace_id = self._require_str(args, "workspace_id")
        return self.workspace_mgr.workspace_merge_preview(
            workspace_id,
            target_branch=args.get("target_branch"),
        )

    def _handle_workspace_merge(self, args: dict[str, Any]) -> dict[str, Any]:
        workspace_id = self._require_str(args, "workspace_id")
        res = self.workspace_mgr.workspace_merge(
            workspace_id,
            target_branch=args.get("target_branch"),
            strategy=str(args.get("strategy") or "merge"),
            reconcile=args.get("reconcile", False),
        )
        ws = self.db.get_workspace(workspace_id)
        if ws is not None:
            self._emit(
                "workspace_updated",
                {"run_id": ws["run_id"], "workspace": ws},
                run_id=ws["run_id"],
            )
        return res

    def _handle_workspace_cleanup(self, args: dict[str, Any]) -> dict[str, Any]:
        workspace_id = self._require_str(args, "workspace_id")
        res = self.workspace_mgr.workspace_cleanup(
            workspace_id,
            force=bool(args.get("force", False)),
        )
        ws = self.db.get_workspace(workspace_id)
        if ws is not None:
            self._emit(
                "workspace_updated",
                {"run_id": ws["run_id"], "workspace": ws},
                run_id=ws["run_id"],
            )
        return res

    # ------------------------------------------------------------------
    # M6 / M8 Macro RPC & M9 Session Hook Handlers
    # ------------------------------------------------------------------

    def _handle_workflow_prepare_dispatch(self, args: dict[str, Any]) -> dict[str, Any]:
        run_id = self._require_str(args, "run_id").strip()
        dispatch_action = str(args.get("dispatch_action") or "dispatch").strip()
        if dispatch_action not in {"dispatch", "start_rework", "request_verify"}:
            raise ValueError(
                f"Invalid dispatch_action {dispatch_action!r}; expected dispatch, start_rework, or request_verify"
            )
        create_ws = args.get("create_workspace", False)
        if type(create_ws) is not bool:
            raise ValueError("create_workspace must be a boolean")

        # Fence expired leases before entering the composite transaction so a rollback never erases fences.
        self.native_actions.expire()

        created_ws_dir: Path | None = None
        created_ws_parent: Path | None = None
        ws_parent_existed_before = True

        run_pre = self.db.get_run(run_id)
        if run_pre is None:
            raise NotFoundError(f"Run not found: {run_id}")
        proj_pre = self.db.get_project(run_pre["project_id"])
        if proj_pre is not None:
            ws_parent = Path(run_pre["execution_root"] or proj_pre["root_path"]).resolve(strict=False) / WORKSPACES_DIRNAME
            ws_parent_existed_before = ws_parent.exists()
            created_ws_parent = ws_parent

        try:
            with self.db.transaction() as conn:
                run = self.db.get_run(run_id)
                if run is None:
                    raise NotFoundError(f"Run not found: {run_id}")
                if run["status"] != "active":
                    raise WorkflowConflictError(f"Workflow run is not active: {run_id}")

                raw_step_id = args.get("step_id")
                existing_step: dict[str, Any] | None = None
                if isinstance(raw_step_id, str) and raw_step_id.strip():
                    try:
                        candidate_step = self.workflow.get_step(raw_step_id.strip())
                        if candidate_step["run_id"] != run_id:
                            raise WorkflowConflictError("Workflow step belongs to another run")
                        existing_step = candidate_step
                    except NotFoundError:
                        existing_step = None

                explicit_owner = args.get("owner").strip() if isinstance(args.get("owner"), str) and args.get("owner").strip() else None
                explicit_verifier = args.get("verifier").strip() if isinstance(args.get("verifier"), str) and args.get("verifier").strip() else None
                explicit_recipient = args.get("recipient").strip() if isinstance(args.get("recipient"), str) and args.get("recipient").strip() else None

                if dispatch_action in {"dispatch", "start_rework"}:
                    owner_name = explicit_owner or explicit_recipient or (existing_step["owner"] if existing_step else None)
                    if not owner_name:
                        raise ValueError("owner (or recipient / existing step_id) is required for dispatch/start_rework")
                    if explicit_owner and explicit_recipient and explicit_owner != explicit_recipient:
                        raise ValueError("owner and recipient conflict for author dispatch")
                    if existing_step is not None and not bool(args.get("save_step", False)) and owner_name != existing_step["owner"]:
                        raise WorkflowConflictError("Executed work must retain its original author and verifier bindings")
                    verifier_name = explicit_verifier or (existing_step["verifier"] if existing_step else None)
                    target_worker_name = owner_name
                    target_agent_id = args.get("agent_id")
                    target_task_name = args.get("task_name")
                else:
                    verifier_name = explicit_verifier or explicit_recipient or (existing_step["verifier"] if existing_step else None)
                    if not verifier_name:
                        raise ValueError("verifier (or recipient / existing step_id) is required for request_verify")
                    if explicit_verifier and explicit_recipient and explicit_verifier != explicit_recipient:
                        raise ValueError("verifier and recipient conflict for request_verify")
                    if existing_step is not None and not bool(args.get("save_step", False)) and verifier_name != existing_step["verifier"]:
                        raise WorkflowConflictError("Executed work must retain its original author and verifier bindings")
                    if (
                        args.get("agent_id") is not None
                        and args.get("verifier_agent_id") is not None
                        and args["agent_id"] != args["verifier_agent_id"]
                    ):
                        raise ValueError("agent_id and verifier_agent_id conflict for request_verify")
                    if (
                        args.get("task_name") is not None
                        and args.get("verifier_task_name") is not None
                        and args["task_name"] != args["verifier_task_name"]
                    ):
                        raise ValueError("task_name and verifier_task_name conflict for request_verify")
                    owner_name = explicit_owner or (existing_step["owner"] if existing_step else None)
                    target_worker_name = verifier_name
                    target_agent_id = (
                        args.get("agent_id")
                        if args.get("agent_id") is not None
                        else args.get("verifier_agent_id")
                    )
                    target_task_name = (
                        args.get("task_name")
                        if args.get("task_name") is not None
                        else args.get("verifier_task_name")
                    )

                # 1) Optional workspace creation
                ws: dict[str, Any] | None = None
                created_ws_stats: dict[str, Any] = {}
                requested_ws_id = args.get("workspace_id").strip() if isinstance(args.get("workspace_id"), str) and args.get("workspace_id").strip() else None
                if create_ws:
                    base_mode = str(args.get("workspace_base_mode") or "auto").strip()
                    if base_mode not in {"auto", "git_clone", "snapshot"}:
                        raise ValueError(f"Invalid workspace_base_mode: {base_mode!r}")
                    ws = self.workspace_mgr.workspace_create(
                        run_id,
                        target_worker_name,
                        copy_env=bool(args.get("copy_env", True)),
                        dep_mode=str(args.get("dep_mode") or "none"),
                    )
                    created_ws_stats = _extract_workspace_copy_stats(ws)
                    created_ws_dir = Path(ws["path"])
                    if base_mode != "auto" and ws["mode"] != base_mode:
                        raise ValueError(
                            f"Requested workspace_base_mode={base_mode!r}, but repository produced {ws['mode']!r}"
                        )
                    if requested_ws_id and requested_ws_id != ws["id"]:
                        if conn.execute("SELECT 1 FROM workspaces WHERE id = ?;", (requested_ws_id,)).fetchone():
                            raise ValueError(f"Workspace ID already exists: {requested_ws_id}")
                        old_ws_id = ws["id"]
                        conn.execute("UPDATE workspaces SET id = ? WHERE id = ?;", (requested_ws_id, old_ws_id))
                        conn.execute(
                            "UPDATE workers SET workspace_id = ? WHERE run_id = ? AND workspace_id = ?;",
                            (requested_ws_id, run_id, old_ws_id),
                        )
                        ws = self.db.get_workspace(requested_ws_id)
                elif requested_ws_id:
                    ws = self.db.get_workspace(requested_ws_id)
                    if ws is None:
                        raise NotFoundError(f"Workspace not found: {requested_ws_id}")
                elif existing_step and existing_step.get("workspace_id"):
                    ws = self.db.get_workspace(existing_step["workspace_id"])

                resolved_ws_id = ws["id"] if ws is not None else None

                # 2) Worker registration (supporting workflow5 pre-spawn task_name)
                existing_target_worker = self.db.get_worker_by_name(run_id, target_worker_name)
                if args.get("role") is not None:
                    resolved_role = str(args["role"]).strip()
                elif existing_target_worker is not None:
                    resolved_role = existing_target_worker["role"]
                elif dispatch_action == "request_verify":
                    resolved_role = "verifier"
                elif run["mode"] == "swarm":
                    resolved_role = "swarm_worker"
                else:
                    resolved_role = "coder"

                if args.get("worker_status") is not None:
                    resolved_worker_status = str(args["worker_status"]).strip()
                elif existing_target_worker is not None:
                    resolved_worker_status = existing_target_worker["status"]
                else:
                    resolved_worker_status = "waiting"

                raw_threshold = args.get("stall_threshold_sec")
                worker = self.board.worker_register(
                    run_id,
                    target_worker_name,
                    resolved_role,
                    agent_id=target_agent_id,
                    task_name=target_task_name,
                    pending_agent_path=args.get("pending_agent_path"),
                    status=resolved_worker_status,
                    workspace_id=resolved_ws_id if dispatch_action != "request_verify" else None,
                    current_task=str(args.get("current_task") or args.get("title") or ""),
                    stall_threshold_sec=int(raw_threshold) if raw_threshold is not None else None,
                )

                # In workflow5 (or when verifier_agent_id is given), ensure lazy/companion verifier is registered when creating a step.
                if dispatch_action != "request_verify" and verifier_name:
                    existing_ver_worker = self.db.get_worker_by_name(run_id, verifier_name)
                    if existing_ver_worker is None and (
                        run["workflow_version"] == 5
                        or args.get("verifier_agent_id") is not None
                    ):
                        self.board.worker_register(
                            run_id,
                            verifier_name,
                            "verifier",
                            agent_id=args.get("verifier_agent_id"),
                            task_name=args.get("verifier_task_name"),
                            status="waiting",
                        )
                    elif existing_ver_worker is not None and (
                        args.get("verifier_agent_id") is not None
                        or args.get("verifier_task_name") is not None
                    ):
                        self.board.worker_register(
                            run_id,
                            verifier_name,
                            existing_ver_worker["role"],
                            agent_id=args.get("verifier_agent_id"),
                            task_name=args.get("verifier_task_name"),
                            status=existing_ver_worker["status"],
                        )

                # If lazy verifier is now pre-binding a task_name on request_verify, sync pending_verifier_path on unbound steps.
                if (
                    dispatch_action == "request_verify"
                    and run["workflow_version"] == 5
                    and not worker["agent_id"]
                    and worker.get("pending_agent_path")
                ):
                    unbound_steps = conn.execute(
                        "SELECT id, checkpoint_json FROM workflow_steps WHERE run_id = ? AND verifier = ? AND verifier_agent_id = '';",
                        (run_id, target_worker_name),
                    ).fetchall()
                    for st_row in unbound_steps:
                        cp_data = json.loads(st_row["checkpoint_json"] or "{}")
                        if cp_data.get("pending_verifier_path") and cp_data["pending_verifier_path"] != worker["pending_agent_path"]:
                            raise WorkflowConflictError("Lazy verifier pre-spawn path conflicts with step checkpoint")
                        cp_data["pending_verifier_path"] = worker["pending_agent_path"]
                        conn.execute(
                            "UPDATE workflow_steps SET checkpoint_json = ? WHERE id = ?;",
                            (json.dumps(cp_data, ensure_ascii=False, sort_keys=True, separators=(",", ":")), st_row["id"]),
                        )
                    if existing_step is not None:
                        existing_step = self.workflow.get_step(existing_step["id"])

                # Auto-save initial task_packet ONLY if run has none and packet inputs were supplied.
                current_packet = self.db.get_task_packet(run_id)
                raw_criteria = args.get("criteria") if args.get("criteria") is not None else args.get("acceptance_criteria")
                if current_packet is None and args.get("file_ownership") is not None and raw_criteria is not None:
                    pkt_title = str(args.get("title") or run["title"]).strip()
                    pkt_goal = str(args.get("goal") or args.get("instruction") or args.get("body_md") or pkt_title).strip()
                    self.db.save_task_packet(
                        run_id,
                        title=pkt_title,
                        goal=pkt_goal,
                        file_ownership=args["file_ownership"],
                        acceptance_criteria=raw_criteria,
                    )

                # 3) Optional workflow_step_save
                should_save_step = existing_step is None or bool(args.get("save_step", False))
                if not should_save_step and existing_step is not None and dispatch_action in {"dispatch", "start_rework"}:
                    if (
                        any(
                            args.get(k) is not None
                            for k in ("step_key", "criteria", "acceptance_criteria", "dependencies", "complexity", "execution_kind")
                        )
                        or (
                            dispatch_action == "dispatch"
                            and resolved_ws_id is not None
                            and existing_step.get("workspace_id") != resolved_ws_id
                        )
                    ) and existing_step["state"] in {"planned", "rework"}:
                        should_save_step = True

                if should_save_step:
                    if not owner_name or not verifier_name:
                        raise ValueError("Both owner and verifier are required when creating or saving a workflow step")
                    resolved_step_key = (
                        existing_step["step_key"]
                        if existing_step is not None
                        else str(args.get("step_key") or raw_step_id or args.get("title") or "").strip()
                    )
                    if not resolved_step_key:
                        raise ValueError("step_key (or step_id / title) is required when creating a workflow step")
                    step = self.workflow.step_save(
                        run_id=run_id,
                        step_key=resolved_step_key,
                        owner=owner_name,
                        verifier=verifier_name,
                        workspace_id=resolved_ws_id if dispatch_action != "request_verify" else (existing_step["workspace_id"] if existing_step else None),
                        dependencies=args.get("dependencies") if args.get("dependencies") is not None else (existing_step["dependencies"] if existing_step else None),
                        kind=str(args.get("step_kind") or (existing_step["kind"] if existing_step else "task")),
                        step_id=existing_step["id"] if existing_step is not None else None,
                        expected_revision=(
                            int(args["expected_step_revision"])
                            if args.get("expected_step_revision") is not None
                            else (existing_step["revision"] if existing_step is not None else None)
                        ),
                        handoff_required=(
                            bool(args["handoff_required"])
                            if args.get("handoff_required") is not None
                            else bool(existing_step["handoff_required"] if existing_step else False)
                        ),
                        criteria=raw_criteria if raw_criteria is not None else (existing_step["criteria"] if existing_step else None),
                        complexity=str(args.get("complexity") or (existing_step["checkpoint"].get("complexity", "normal") if existing_step else "normal")),
                        execution_kind=str(args.get("execution_kind") or (existing_step["checkpoint"].get("execution_kind", "native") if existing_step else "native")),
                    )
                else:
                    assert existing_step is not None
                    if args.get("expected_step_revision") is not None and existing_step["revision"] != int(args["expected_step_revision"]):
                        raise WorkflowConflictError("Workflow revision conflict")
                    step = existing_step

                if args.get("commands") is not None:
                    cp_meta = dict(step["checkpoint"])
                    cp_meta["suggested_commands"] = args["commands"]
                    conn.execute(
                        "UPDATE workflow_steps SET checkpoint_json = ? WHERE id = ?;",
                        (json.dumps(cp_meta, ensure_ascii=False, sort_keys=True, separators=(",", ":")), step["id"]),
                    )
                    step = self.workflow.get_step(step["id"])

                # 4) workflow_step_advance
                if args.get("observation") is not None:
                    raw_obs = args["observation"]
                    if not isinstance(raw_obs, dict):
                        raise ValueError("observation must be an object")
                    obs_payload = dict(raw_obs)
                    if "agent_id" not in obs_payload:
                        obs_payload["agent_id"] = (
                            step["owner_agent_id"]
                            if dispatch_action in {"dispatch", "start_rework"}
                            else step["verifier_agent_id"]
                        )
                    if "observed_at" not in obs_payload:
                        obs_payload["observed_at"] = time.time()
                    step = self.workflow.step_advance(
                        step["id"],
                        "observe",
                        step["revision"],
                        observation=obs_payload,
                    )

                if dispatch_action in {"dispatch", "start_rework"}:
                    step = self.workflow.step_advance(
                        step["id"],
                        dispatch_action,
                        step["revision"],
                        evidence=args.get("advance_evidence"),
                        agent_id=args.get("advance_agent_id") or (target_agent_id if dispatch_action == "dispatch" else None),
                    )
                else:
                    # request_verify
                    if step["state"] == "ready_to_merge" and not step["workspace_id"]:
                        step = self.workflow.step_advance(
                            step["id"], "begin_integrated_review", step["revision"]
                        )
                    if step["state"] not in {"candidate_review", "integrated_review"}:
                        raise WorkflowConflictError(
                            f"Cannot prepare verification dispatch when step is in state {step['state']!r}"
                        )
                    self.workflow._packet(step)
                    allow_lazy = run["workflow_version"] == 5 and not bool(worker["agent_id"])
                    if allow_lazy and not worker.get("pending_agent_path"):
                        raise WorkflowConflictError(
                            f"Lazy verifier {target_worker_name!r} requires a pre-spawn task_name or bound agent_id"
                        )
                    self.workflow._bindings(step, dispatch=True, allow_lazy_verifier=allow_lazy)

                # 5) inbox_send
                sender = str(args.get("sender") or "coordinator").strip()
                default_kind = {
                    "dispatch": "task",
                    "start_rework": "rework",
                    "request_verify": "review",
                }[dispatch_action]
                msg_kind = str(args.get("kind") or default_kind).strip()
                msg_summary = str(
                    args.get("subject")
                    or args.get("summary")
                    or args.get("title")
                    or f"{step['step_key']}: {dispatch_action}"
                ).strip()
                raw_body = args.get("body_md") if args.get("body_md") is not None else args.get("instruction")
                if raw_body is None:
                    raw_body = msg_summary
                if not isinstance(raw_body, str) or not raw_body.strip():
                    raise ValueError("body_md or instruction must be a non-empty string")

                msg_key = str(
                    args.get("message_key")
                    or args.get("key")
                    or f"{step['id']}:{dispatch_action}:{step['state']}:v{step['packet_version']}:c{step['contract_revision']}:a{step['attempt']}:r{step['revision']}"
                ).strip()

                sent = self.inbox.send(
                    run_id=run_id,
                    sender=sender,
                    recipient=target_worker_name,
                    key=msg_key,
                    kind=msg_kind,
                    summary=msg_summary,
                    body_md=raw_body,
                    step_id=step["id"],
                )
                msg = sent["message"]

                # 6) native_action_update(claim) + native_action_update(begin)
                worker = self.db.get_worker_by_name(run_id, target_worker_name)
                assert worker is not None
                act_type = "spawn_agent" if not worker["agent_id"] else "followup_task"
                _snap, _skey, src_digest, _cdigest = self.native_actions._source(
                    conn, run_id, "inbox", msg["id"], target_worker_name, act_type
                )
                claimed = self.native_actions._update_transaction(
                    action="claim",
                    run_id=run_id,
                    source_type="inbox",
                    source_id=msg["id"],
                    recipient=target_worker_name,
                    action_type=act_type,
                    source_digest=src_digest,
                    _skip_expire=True,
                )
                assert claimed is not None
                begun = self.native_actions._update_transaction(
                    action="begin",
                    action_id=claimed["action_id"],
                    claim_token=claimed["claim_token"],
                    expected_revision=claimed["revision"],
                    _skip_expire=True,
                )
                assert begun is not None

                # 7) Optional session_id binding
                if args.get("session_id") is not None:
                    self.db.upsert_session_binding(str(args["session_id"]).strip(), run_id)

                step = self.workflow.get_step(step["id"])
                worker = self.db.get_worker_by_name(run_id, target_worker_name)
                assert worker is not None
                if resolved_ws_id is not None:
                    ws = self.db.get_workspace(resolved_ws_id)
        except BaseException:
            if created_ws_dir is not None:
                _rmtree_safe(created_ws_dir)
                if (
                    not ws_parent_existed_before
                    and created_ws_parent is not None
                    and created_ws_parent.is_dir()
                ):
                    try:
                        if not any(created_ws_parent.iterdir()):
                            created_ws_parent.rmdir()
                    except OSError:
                        pass
            raise

        target_agent_path = worker["agent_id"] or worker.get("pending_agent_path") or ""
        inferred_task_name = (
            target_agent_path.removeprefix("/root/")
            if target_agent_path.startswith("/root/")
            else args.get("task_name")
        )
        if begun["action_type"] == "spawn_agent":
            hint: dict[str, Any] = {
                "host_tool": "spawn_agent",
                "task_name": inferred_task_name,
                "target_agent_path": target_agent_path,
                "step_id": step["id"],
                "message_id": msg["id"],
                "workspace_path": ws["path"] if ws else None,
                "next_mcp_tool": "native_action_record_call",
            }
        else:
            hint = {
                "host_tool": "followup_task",
                "target": target_agent_path,
                "target_agent_path": target_agent_path,
                "step_id": step["id"],
                "message_id": msg["id"],
                "workspace_path": ws["path"] if ws else None,
                "next_mcp_tool": "native_action_record_call",
            }

        compact_step = _strip_step_manifests(step)
        compact_ws = (
            _compact_workspace_payload(ws, extra_stats=created_ws_stats)
            if ws is not None else None
        )
        refreshed = self._sync_run_agent_models(run_id)
        worker = refreshed.get(worker["name"], worker)
        if compact_ws is not None:
            self._emit("workspace_updated", {"run_id": run_id, "workspace": compact_ws}, run_id=run_id)
        self._emit("worker_updated", {"run_id": run_id, "worker": worker}, run_id=run_id)
        self._emit("workflow_updated", {"run_id": run_id, "step": compact_step}, run_id=run_id)
        self._emit("inbox_updated", {"run_id": run_id, "message": msg}, run_id=run_id)
        self._emit(
            "native_action_updated",
            {"run_id": run_id, "action_id": begun["action_id"], "status": begun["status"]},
            run_id=run_id,
        )
        if args.get("session_id") is not None:
            self._emit(
                "session_updated", {"run_id": run_id, "session_id": str(args["session_id"]).strip()}, run_id=run_id,
            )

        return {
            "step": compact_step,
            "workspace": compact_ws,
            "worker": worker,
            "message_id": msg["id"],
            "board_seq": msg["board_seq"],
            "action_id": begun["action_id"],
            "action_type": begun["action_type"],
            "target_agent_path": target_agent_path,
            "claim_token": begun["claim_token"],
            "expected_revision": begun["revision"],
            "lease_expires_at": begun["lease_expires_at"],
            "native_call_hint": hint,
        }

    def _handle_native_action_record_call(self, args: dict[str, Any]) -> dict[str, Any]:
        run_id = self._require_str(args, "run_id").strip()
        if self.db.get_run(run_id) is None:
            raise NotFoundError(f"Run not found: {run_id}")
        outcome = self._require_str(args, "outcome").strip()
        raw_response = args.get("raw_response")
        if isinstance(raw_response, str):
            if not raw_response.strip():
                raise ValueError("raw_response string must be non-empty")
        elif not isinstance(raw_response, dict):
            raise ValueError("raw_response must be a JSON object (dict) or non-empty string")

        reconcile = bool(args.get("reconcile", False))
        cancel = bool(args.get("cancel", False))
        action_id = args.get("action_id")
        group_id = args.get("group_id")
        members = args.get("members")

        is_single = isinstance(action_id, str) and bool(action_id.strip())
        is_group = isinstance(group_id, str) and bool(group_id.strip())
        if is_single == is_group:
            raise ValueError("Specify exactly one of action_id or group_id (+ members)")

        if is_single:
            clean_id = action_id.strip()
            expected_rev = args.get("expected_revision")
            if type(expected_rev) is not int or expected_rev < 1:
                raise ValueError("expected_revision must be an integer >= 1")
            row = self.native_actions.get(clean_id)
            if row["run_id"] != run_id:
                raise ValueError("action_id does not belong to run_id")
            rev_tag = expected_rev
            target_tag = clean_id
        else:
            if not isinstance(members, list) or not members:
                raise ValueError("members must be a non-empty list for group recording")
            clean_group_id = group_id.strip()
            first_member_id = members[0].get("action_id") if isinstance(members[0], dict) else None
            if not isinstance(first_member_id, str) or not first_member_id.strip():
                raise ValueError("Group members require action_id")
            first_row = self.native_actions.get(first_member_id.strip())
            if first_row["run_id"] != run_id:
                raise ValueError("group member does not belong to run_id")
            rev_tag = members[0].get("expected_revision", 1)
            target_tag = clean_group_id

        safe_run = re.sub(r"[^A-Za-z0-9._-]+", "_", run_id).strip("._-") or "run"
        safe_target = re.sub(r"[^A-Za-z0-9._-]+", "_", str(target_tag)).strip("._-") or "action"
        receipt_payload = {
            "schema_version": 1,
            "run_id": run_id,
            "target_id": target_tag,
            "expected_revision": rev_tag,
            "outcome": outcome,
            "raw_response": raw_response,
            "agent_id": args.get("agent_id"),
            "error_text": args.get("error_text"),
            "recorded_at": time.time(),
        }
        payload_bytes = (
            json.dumps(receipt_payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")

        receipt_dir = self.db.settings.data_dir / "receipts" / safe_run
        receipt_path = receipt_dir / f"{safe_target}-{rev_tag}.json"
        tmp_path = receipt_dir / f".{safe_target}-{rev_tag}.{uuid.uuid4().hex}.tmp"
        try:
            receipt_dir.mkdir(parents=True, exist_ok=True)
            with tmp_path.open("wb") as fh:
                fh.write(payload_bytes)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, receipt_path)
            written_bytes = receipt_path.read_bytes()
        except OSError as exc:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise RuntimeError(f"Failed to write native action receipt to disk: {exc}") from exc

        evidence_digest = hashlib.sha256(written_bytes).hexdigest()
        evidence_ref = str(receipt_path.resolve())

        if isinstance(raw_response, dict):
            result_str = json.dumps(raw_response, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        else:
            result_str = raw_response
        if args.get("error_text") and isinstance(args["error_text"], str) and args["error_text"].strip():
            if isinstance(raw_response, str):
                result_str = f"{raw_response} | error: {args['error_text'].strip()}"

        if is_single:
            op = "reconcile" if reconcile else "record"
            updated = self.native_actions.update(
                action=op,
                action_id=action_id.strip(),
                claim_token=args.get("claim_token"),
                expected_revision=args["expected_revision"],
                outcome=outcome,
                evidence_ref=evidence_ref,
                evidence_digest=evidence_digest,
                result=result_str,
                agent_id=args.get("agent_id"),
                cancel=cancel,
            )
            refreshed = self._sync_run_agent_models(run_id)
            if updated.get("action_type") == "spawn_agent":
                w = refreshed.get(updated["recipient"]) or self.db.get_worker_by_name(run_id, updated["recipient"])
                if w is not None:
                    self._emit("worker_updated", {"run_id": run_id, "worker": w}, run_id=run_id)
                self._emit("workflow_updated", {"run_id": run_id}, run_id=run_id)
            self._emit(
                "native_action_updated",
                {"run_id": run_id, "action_id": updated["action_id"], "status": updated["status"]},
                run_id=run_id,
            )
            return updated

        op_group = "reconcile_group" if reconcile else "record_group"
        updated_group = self.native_actions.update(
            action=op_group,
            group_id=group_id.strip(),
            members=members,
            outcome=outcome,
            evidence_ref=evidence_ref,
            evidence_digest=evidence_digest,
            result=result_str,
            cancel=cancel,
        )
        self._sync_run_agent_models(run_id)
        self._emit(
            "native_action_updated",
            {
                "run_id": run_id,
                "group_id": updated_group["group_id"],
                "members": [
                    {"action_id": m["action_id"], "status": m["status"]}
                    for m in updated_group["members"]
                ],
            },
            run_id=run_id,
        )
        return {
            **updated_group,
            "evidence_ref": evidence_ref,
            "evidence_digest": evidence_digest,
        }

    def _handle_workflow_author_turn(self, args: dict[str, Any]) -> dict[str, Any]:
        run_id = self._require_str(args, "run_id").strip()
        step_id = self._require_str(args, "step_id").strip()
        message_id = self._require_str(args, "message_id").strip()
        phase = self._require_str(args, "phase").strip()
        if phase not in {"accept", "submit"}:
            raise ValueError(f"Invalid phase {phase!r}; expected 'accept' or 'submit'")
        post_note = args.get("post_board_note", False)
        if type(post_note) is not bool:
            raise ValueError("post_board_note must be a boolean")

        board_pointer: dict[str, Any] | None = None
        with self.db.transaction() as conn:
            step = self.workflow.get_step(step_id)
            if step["run_id"] != run_id:
                raise WorkflowConflictError("Workflow step does not belong to run_id")
            if args.get("expected_revision") is not None and step["revision"] != int(args["expected_revision"]):
                raise WorkflowConflictError("Workflow revision conflict")

            actor = (
                args.get("recipient")
                or args.get("author")
                or step["owner"]
            )
            if not isinstance(actor, str) or not actor.strip():
                raise ValueError("Author/recipient must be non-empty")
            actor = actor.strip()
            if actor != step["owner"]:
                raise WorkflowConflictError("Only the step owner may execute workflow_author_turn")

            msg_before = self.inbox.get(message_id)
            if msg_before["run_id"] != run_id:
                raise WorkflowConflictError("Inbox message does not belong to run_id")
            if msg_before["step_id"] is not None and msg_before["step_id"] != step_id:
                raise WorkflowConflictError("Inbox message is bound to a different workflow step")

            raw_summary = str(args.get("summary") or "").strip()
            raw_evidence = str(args.get("evidence") or raw_summary or f"{phase} on {step['step_key']}").strip()

            if phase == "accept":
                ack_msg = self.inbox.ack(message_id, actor, "accepted", raw_evidence)
                step_now = self.workflow.get_step(step_id)
                if step_now["state"] == "dispatched":
                    step = self.workflow.step_advance(step_id, "accept", step_now["revision"])
                elif step_now["state"] == "implementing" and ack_msg["kind"] == "rework" and step_now["attempt"] >= 1:
                    self.workflow._packet(step_now)
                    self.workflow._bindings(step_now, dispatch=True, allow_lazy_verifier=True)
                    step = step_now
                else:
                    raise WorkflowConflictError(
                        f"Action accept is not permitted from workflow step state {step_now['state']!r}"
                    )
                # Auto-set author worker status to "working" and refresh heartbeat.
                worker_row = self.db.get_worker_by_name(run_id, actor)
                if worker_row is not None:
                    self.db.update_worker_heartbeat(
                        run_id=run_id, worker_name=actor,
                        status="working", heartbeat_at=time.time(),
                    )
            else:
                artifact_ids = args.get("artifact_ids")
                if artifact_ids is not None:
                    if not isinstance(artifact_ids, list) or any(not isinstance(aid, str) or not aid.strip() for aid in artifact_ids):
                        raise ValueError("artifact_ids must be a list of non-empty strings")
                    for aid in artifact_ids:
                        art = self.workflow._artifact(aid.strip())
                        if art["step_id"] != step_id:
                            raise WorkflowConflictError(f"Artifact {aid!r} does not belong to step {step_id}")

                if msg_before["status"] == "pending" and msg_before["kind"] == "rework" and step["state"] == "implementing" and step["attempt"] >= 1:
                    self.inbox.ack(message_id, actor, "accepted", raw_evidence)

                step = self.workflow.step_advance(step_id, "submit", step["revision"])
                if raw_summary or artifact_ids:
                    cp = dict(step["checkpoint"])
                    cp["last_submission"] = {
                        "summary": raw_summary,
                        "artifact_ids": list(artifact_ids or []),
                        "submitted_at": time.time(),
                    }
                    conn.execute(
                        "UPDATE workflow_steps SET checkpoint_json = ? WHERE id = ?;",
                        (json.dumps(cp, ensure_ascii=False, sort_keys=True, separators=(",", ":")), step_id),
                    )
                    step = self.workflow.get_step(step_id)

                ack_msg = self.inbox.ack(message_id, actor, "completed", raw_evidence)
                # Auto-set author worker status to "waiting".
                worker_row = self.db.get_worker_by_name(run_id, actor)
                if worker_row is not None:
                    self.db.update_worker_status(
                        status="waiting", run_id=run_id, worker_name=actor,
                    )
                # Post board note if requested or body_md is given.
                raw_body_md = args.get("body_md")
                should_post = post_note or (raw_body_md is not None and isinstance(raw_body_md, str) and raw_body_md.strip())
                if should_post:
                    note_summary = f"#verify {step['step_key']}: {raw_summary or 'candidate submitted'}"[:160]
                    note_body = (
                        str(raw_body_md).strip() if raw_body_md and str(raw_body_md).strip()
                        else (
                            f"@coordinator @{step['verifier']} `#verify` `{step['step_key']}` "
                            f"(step `{step_id}`, attempt `{step['attempt']}`): {raw_summary or raw_evidence}"
                        )
                    )
                    board_pointer = self.board.board_post(
                        run_id,
                        sender=actor,
                        sender_role=worker_row["role"] if worker_row else "coder",
                        mentions=["coordinator", step["verifier"]],
                        msg_type="handoff",
                        summary=note_summary,
                        body_md=note_body,
                    )

        compact_step = _strip_step_manifests(step)
        refreshed = self._sync_run_agent_models(run_id)
        updated_worker = refreshed.get(actor) or self.db.get_worker_by_name(run_id, actor)
        if updated_worker is not None:
            self._emit("worker_updated", {"run_id": run_id, "worker": updated_worker}, run_id=run_id)
        self._emit("workflow_updated", {"run_id": run_id, "step": compact_step}, run_id=run_id)
        self._emit("inbox_updated", {"run_id": run_id, "message": ack_msg}, run_id=run_id)
        if board_pointer is not None:
            full_msg = self.board.get_message(int(board_pointer["seq"]))
            if full_msg is not None:
                self._emit("board_message", {"run_id": run_id, "message": full_msg}, run_id=run_id)
        return {
            "step": compact_step,
            "message": ack_msg,
            "board_pointer": board_pointer,
        }

    def _handle_workflow_verifier_turn(self, args: dict[str, Any]) -> dict[str, Any]:
        """Atomic verifier macro: prepare or record phase (zero gate weakening)."""
        run_id = self._require_str(args, "run_id").strip()
        step_id = self._require_str(args, "step_id").strip()
        phase = self._require_str(args, "phase").strip()
        if phase not in {"prepare", "record"}:
            raise ValueError(f"Invalid phase {phase!r}; expected 'prepare' or 'record'")

        if phase == "prepare":
            scope = str(args.get("scope") or "candidate").strip()
            verifier_arg = args.get("verifier")
            ack_msg_out: dict[str, Any] | None = None
            created_manifest: Path | None = None
            try:
                with self.db.transaction() as conn:
                    step = self.workflow.get_step(step_id)
                    if step["run_id"] != run_id:
                        raise WorkflowConflictError("Step does not belong to run_id")
                    run = self.db.get_run(run_id)
                    if run is None or run["status"] != "active":
                        raise WorkflowConflictError("Run is not active")
                    verifier = str(verifier_arg).strip() if isinstance(verifier_arg, str) and verifier_arg.strip() else step["verifier"]
                    if verifier != step["verifier"]:
                        raise WorkflowConflictError("Only the bound independent verifier may execute workflow_verifier_turn")

                    # Auto-locate pending/accepted review inbox message if message_id not given.
                    message_id = args.get("message_id")
                    if not message_id:
                        rows = conn.execute(
                            "SELECT id FROM inbox_messages WHERE run_id=? AND step_id=? AND recipient=? AND kind='review' AND status IN ('pending','accepted') ORDER BY board_seq DESC LIMIT 1",
                            (run_id, step_id, verifier),
                        ).fetchall()
                        if rows:
                            message_id = rows[0]["id"]
                    resolved_message_id: str | None = None
                    if message_id:
                        clean_mid = str(message_id).strip()
                        msg = self.inbox.get(clean_mid)
                        if msg["run_id"] != run_id or (msg["step_id"] is not None and msg["step_id"] != step_id):
                            raise WorkflowConflictError("Inbox message does not match run_id/step_id")
                        if msg["status"] == "pending":
                            ack_msg_out = self.inbox.ack(clean_mid, verifier, "accepted", f"verifier_turn prepare {step['step_key']}")
                        else:
                            ack_msg_out = msg
                        resolved_message_id = clean_mid

                    # Set verifier worker status to working + refresh heartbeat.
                    worker = self.db.get_worker_by_name(run_id, verifier)
                    if worker is not None:
                        self.db.update_worker_heartbeat(
                            run_id=run_id, worker_name=verifier,
                            status="working", heartbeat_at=time.time(),
                        )

                    capture_kwargs: dict[str, Any] = {
                        "step_id": step_id,
                        "scope": scope,
                    }
                    for key_name in ("paths", "verification_commands", "env_profile"):
                        if args.get(key_name) is not None:
                            capture_kwargs[key_name] = args[key_name]
                    raw_artifact = self.workflow.capture(**capture_kwargs)
                    if raw_artifact.get("manifest_path"):
                        created_manifest = Path(raw_artifact["manifest_path"])
            except BaseException:
                if created_manifest is not None:
                    try:
                        created_manifest.unlink(missing_ok=True)
                    except OSError:
                        pass
                raise

            artifact = _strip_artifact_manifests(raw_artifact)
            cli_exes = _discover_cli_executables()
            verify_exe = cli_exes.get("verify")
            manifest_path = artifact.get("manifest_path")
            verify_argv_template: list[str] | None = None
            verify_hint: str | None = None
            if manifest_path:
                suggested_out = str((self.db.db_path.parent / "verification-receipts" / artifact["id"]).resolve())
                exe_or_name = verify_exe or "tri-orchestrator-verify"
                verify_argv_template = [
                    exe_or_name, "--manifest", manifest_path, "--output-dir", suggested_out,
                ]
                verify_hint = (
                    f'"{exe_or_name}" --manifest "{manifest_path}" --output-dir "{suggested_out}"'
                )
            artifact["verify_executable"] = verify_exe
            artifact["verify_argv_template"] = verify_argv_template
            artifact["verify_command_hint"] = verify_hint
            artifact["cli_executables"] = cli_exes

            refreshed = self._sync_run_agent_models(run_id)
            updated_worker = refreshed.get(verifier) or self.db.get_worker_by_name(run_id, verifier)
            if updated_worker is not None:
                self._emit("worker_updated", {"run_id": run_id, "worker": updated_worker}, run_id=run_id)
            if ack_msg_out is not None:
                self._emit("inbox_updated", {"run_id": run_id, "message": ack_msg_out}, run_id=run_id)
            self._emit("workflow_updated", {"run_id": run_id, "artifact": artifact}, run_id=run_id)

            return {
                "artifact": artifact,
                "verify_executable": verify_exe,
                "verify_argv_template": verify_argv_template,
                "verify_command_hint": verify_hint,
                "cli_executables": cli_exes,
                "message_id": resolved_message_id,
            }

        # phase == "record"
        artifact_id = self._require_str(args, "artifact_id").strip()
        verdict = self._require_str(args, "verdict").strip()
        evidence_files = args.get("evidence_files", [])
        receipt_files = args.get("receipt_files")
        key = args.get("key")

        board_pointer: dict[str, Any] | None = None
        ack_msg_final: dict[str, Any] | None = None
        resolved_message_id = None
        with self.db.transaction() as conn:
            step = self.workflow.get_step(step_id)
            if step["run_id"] != run_id:
                raise WorkflowConflictError("Step does not belong to run_id")
            run = self.db.get_run(run_id)
            if run is None or run["status"] != "active":
                raise WorkflowConflictError("Run is not active")
            verifier_name = str(args.get("verifier") or step["verifier"]).strip()
            if verifier_name != step["verifier"]:
                raise WorkflowConflictError("Only the bound independent verifier may execute workflow_verifier_turn")

            artifact = self.workflow._artifact(artifact_id)
            if args.get("scope") is not None and str(args["scope"]).strip() != artifact["scope"]:
                raise WorkflowConflictError(
                    f"Artifact {artifact_id} has scope {artifact['scope']!r}, expected {args['scope']!r}"
                )

            # Auto-locate and ack pending->accepted if needed.
            message_id = args.get("message_id")
            if not message_id:
                rows = conn.execute(
                    "SELECT id FROM inbox_messages WHERE run_id=? AND step_id=? AND recipient=? AND kind='review' AND status IN ('pending','accepted') ORDER BY board_seq DESC LIMIT 1",
                    (run_id, step_id, verifier_name),
                ).fetchall()
                if rows:
                    message_id = rows[0]["id"]
            if message_id:
                clean_mid = str(message_id).strip()
                msg = self.inbox.get(clean_mid)
                if msg["run_id"] != run_id or (msg["step_id"] is not None and msg["step_id"] != step_id):
                    raise WorkflowConflictError("Inbox message does not match run_id/step_id")
                if msg["status"] == "pending":
                    self.inbox.ack(clean_mid, verifier_name, "accepted", f"verifier_turn record {step['step_key']}")
                resolved_message_id = clean_mid

            # Auto-record Boost candidate challenge for this exact attempt/artifact if missing or stale.
            existing_ch = step["checkpoint"].get("challenge")
            needs_challenge = (
                not isinstance(existing_ch, dict)
                or existing_ch.get("agent_id") != step["verifier_agent_id"]
                or existing_ch.get("artifact_id") != artifact_id
                or any(existing_ch.get(k) != step[k] for k in ("packet_version", "attempt", "contract_revision"))
            )
            if run["mode"] == "boost" and artifact["scope"] == "candidate" and needs_challenge:
                challenge_evidence = str(
                    args.get("challenge_evidence") or args.get("summary") or f"Independent challenge for {step['step_key']}"
                ).strip()
                step = self.workflow.step_advance(
                    step_id, "challenge", step["revision"],
                    agent_id=step["verifier_agent_id"],
                    evidence=challenge_evidence,
                    artifact_id=artifact_id,
                )

            criteria_results = args.get("criteria_results")
            if criteria_results is None:
                if verdict == "PASS":
                    criteria_results = [{"criterion": c, "status": "PASS"} for c in step["criteria"]]
                else:
                    criteria_results = []

            # Auto-generate deterministic idempotent key if omitted.
            if not key or not str(key).strip():
                key = f"{step_id}:{artifact_id}:{step['attempt']}:{verdict}:{step['contract_revision']}"

            record = self.workflow.record(
                step_id=step_id,
                artifact_id=artifact_id,
                verifier=verifier_name,
                verdict=verdict,
                criteria_results=criteria_results,
                evidence_files=evidence_files,
                key=str(key).strip(),
                receipt_files=receipt_files,
                review_dimensions=args.get("review_dimensions"),
                resolved_finding_ids=args.get("resolved_finding_ids"),
                dismissed_finding_ids=args.get("dismissed_finding_ids"),
            )

            if resolved_message_id:
                msg_after = self.inbox.get(resolved_message_id)
                if msg_after["status"] == "accepted":
                    ack_msg_final = self.inbox.ack(
                        resolved_message_id, verifier_name, "completed",
                        f"verifier_turn record {verdict} {step['step_key']}",
                    )
                else:
                    ack_msg_final = msg_after

            worker = self.db.get_worker_by_name(run_id, verifier_name)
            if worker is not None:
                self.db.update_worker_status(
                    status="waiting", run_id=run_id, worker_name=verifier_name,
                )

            raw_summary = str(args.get("summary") or f"{verdict} {step['step_key']}").strip()
            if args.get("post_board_note", False) or args.get("body_md"):
                mentions = ["coordinator"] if verdict == "PASS" else ["coordinator", step["owner"]]
                mention_prefix = " ".join(f"@{m}" for m in mentions)
                note_body = str(args.get("body_md") or "").strip() or (
                    f"{mention_prefix} `#verify` `{step['step_key']}` {verdict} "
                    f"(step `{step_id}`, scope `{artifact['scope']}`, attempt `{step['attempt']}`): {raw_summary}"
                )
                board_pointer = self.board.board_post(
                    run_id,
                    sender=verifier_name,
                    sender_role="verifier",
                    channel="#verify",
                    mentions=mentions,
                    msg_type="test",
                    summary=f"#verify {step['step_key']} ({artifact['scope']}): {verdict}"[:160],
                    body_md=note_body,
                )

            step = self.workflow.get_step(step_id)

        compact_step = _strip_step_manifests(step)
        refreshed = self._sync_run_agent_models(run_id)
        updated_worker = refreshed.get(verifier_name) or self.db.get_worker_by_name(run_id, verifier_name)
        if updated_worker is not None:
            self._emit("worker_updated", {"run_id": run_id, "worker": updated_worker}, run_id=run_id)
        if ack_msg_final is not None:
            self._emit("inbox_updated", {"run_id": run_id, "message": ack_msg_final}, run_id=run_id)
        if board_pointer is not None:
            full_msg = self.board.get_message(int(board_pointer["seq"]))
            if full_msg is not None:
                self._emit("board_message", {"run_id": run_id, "message": full_msg}, run_id=run_id)
        self._emit("workflow_updated", {"run_id": run_id, "step": compact_step, "verification": record}, run_id=run_id)
        return {
            "verification": record,
            "step": compact_step,
            "message": ack_msg_final,
            "message_id": resolved_message_id,
            "board_pointer": board_pointer,
        }

    def _handle_workflow_closeout_run(self, args: dict[str, Any]) -> dict[str, Any]:
        """Single guarded RPC to complete a run after all integrated verifications pass."""
        run_id = self._require_str(args, "run_id").strip()
        run = self.db.get_run(run_id)
        if run is None:
            raise NotFoundError(f"Run not found: {run_id}")
        if run["status"] != "active":
            raise WorkflowConflictError(f"Run is not active (status={run['status']!r})")

        with self.db.read_connection() as conn:
            step_rows = [dict(row) for row in conn.execute(
                "SELECT * FROM workflow_steps WHERE run_id=? ORDER BY created_at,id", (run_id,)
            )]
            unresolved = conn.execute(
                "SELECT id FROM inbox_messages WHERE run_id=? AND (status='accepted' OR (status='pending' AND kind!='notice')) LIMIT 1",
                (run_id,),
            ).fetchone()
        if not step_rows or unresolved is not None:
            raise WorkflowConflictError("Missing workflow steps or unresolved inbox work")
        if any(c["status"] == "pending" for c in self.db.list_worker_controls(run_id)):
            raise WorkflowConflictError("Acknowledge pending controls before closeout")

        steps_by_id = {r["id"]: self.workflow.get_step(r["id"]) for r in step_rows}
        # Pre-validate all steps before mutating state or cleaning workspaces.
        for step in steps_by_id.values():
            if step["state"] == "done":
                self.workflow._valid_verification(step, "integrated")
            elif step["state"] == "integrated_review":
                if step["handoff_required"]:
                    raise WorkflowConflictError(
                        f"Step {step['step_key']} has handoff_required=True and must complete cross-coordinator handoff before closeout"
                    )
                self.workflow._valid_verification(step, "integrated")
            else:
                raise WorkflowConflictError(
                    f"Step {step['step_key']} is not ready for closeout (state={step['state']!r})"
                )

        # Topological sort over step dependencies.
        ordered_ids: list[str] = []
        visited: set[str] = set()

        def _visit(sid: str) -> None:
            if sid in visited:
                return
            visited.add(sid)
            st = steps_by_id.get(sid)
            if st is not None:
                for dep_id in st.get("dependencies", []):
                    if dep_id in steps_by_id:
                        _visit(dep_id)
                ordered_ids.append(sid)

        for sid in steps_by_id:
            _visit(sid)

        # Verify all merged workspaces are clean BEFORE advancing steps or deleting dirs.
        workspaces = self.db.list_workspaces(run_id=run_id)
        merged_ws = [ws for ws in workspaces if ws["status"] == "merged"]
        for ws in merged_ws:
            ws_status = self.workspace_mgr.workspace_status(ws["id"])
            if ws_status.get("dirty") or ws_status.get("has_uncommitted_changes"):
                raise WorkspaceCleanupGuardError(
                    f"Refusing closeout cleanup for workspace {ws['id']}: uncommitted changes ({ws_status.get('uncommitted_files')})"
                )

        advanced: list[str] = []
        for sid in ordered_ids:
            step = self.workflow.get_step(sid)
            if step["state"] == "integrated_review":
                step = self.workflow.step_advance(sid, "complete", step["revision"])
                advanced.append(sid)
                self._emit("workflow_updated", {"run_id": run_id, "step": _strip_step_manifests(step)}, run_id=run_id)

        cleaned_workspaces: list[str] = []
        freed_bytes = 0
        for ws in merged_ws:
            ws_path = Path(ws["path"])
            ws_bytes = (
                _dir_size_bytes(ws_path)
                if ws_path.exists() and not _is_reparse_or_symlink(ws_path)
                else 0
            )
            cleanup_res = self.workspace_mgr.workspace_cleanup(ws["id"], force=False)
            if not cleanup_res.get("already_cleaned"):
                freed_bytes += ws_bytes
            cleaned_workspaces.append(ws["id"])
            updated_ws = self.db.get_workspace(ws["id"])
            if updated_ws is not None:
                self._emit(
                    "workspace_updated",
                    {"run_id": run_id, "workspace": _compact_workspace_payload(updated_ws)},
                    run_id=run_id,
                )
        if cleaned_workspaces or freed_bytes > 0:
            self._emit(
                "storage_updated",
                {
                    "run_id": run_id,
                    "freed_bytes": int(freed_bytes),
                    "cleaned_workspaces": cleaned_workspaces,
                },
                run_id=run_id,
            )

        stopped_workers: list[str] = []
        stopped_entries: list[tuple[str, dict[str, Any]]] = []
        for w in self.db.list_workers(run_id=run_id):
            if w["status"] in {"waiting", "working"}:
                updated_w = self.db.update_worker_status(
                    status="stopped", run_id=run_id, worker_name=w["name"],
                )
                stopped_workers.append(w["name"])
                stopped_entries.append((w["name"], updated_w))

        refreshed = self._sync_run_agent_models(run_id)
        for worker_name, updated_w in stopped_entries:
            self._emit(
                "worker_updated",
                {"run_id": run_id, "worker": refreshed.get(worker_name, updated_w)},
                run_id=run_id,
            )

        board_pointer: dict[str, Any] | None = None
        raw_summary = args.get("summary")
        if isinstance(raw_summary, str) and raw_summary.strip():
            board_pointer = self.board.board_post(
                run_id,
                sender="coordinator",
                sender_role="coordinator",
                msg_type="info",
                summary=f"Run closeout: {raw_summary.strip()}"[:160],
                body_md=raw_summary.strip(),
            )
            full_msg = self.board.get_message(int(board_pointer["seq"]))
            if full_msg is not None:
                self._emit("board_message", {"run_id": run_id, "message": full_msg}, run_id=run_id)

        completed_run = self.db.update_run(run_id, status="completed")
        self._emit("run_updated", {"run_id": run_id, "run": completed_run}, run_id=run_id)
        return {
            "run": completed_run,
            "advanced_steps": advanced,
            "cleaned_workspaces": cleaned_workspaces,
            "freed_bytes": int(freed_bytes),
            "stopped_workers": stopped_workers,
            "board_pointer": board_pointer,
            "summary": str(raw_summary or "Run closeout complete").strip(),
        }

    def _handle_session_bind(self, args: dict[str, Any]) -> dict[str, Any]:
        session_id = self._require_str(args, "session_id").strip()
        run_id = self._require_str(args, "run_id").strip()
        binding = self.db.upsert_session_binding(
            session_id,
            run_id,
            project_root=args.get("project_root"),
        )
        self._sync_run_agent_models(run_id)
        self._emit("session_updated", {"run_id": run_id, "session_id": session_id}, run_id=run_id)
        return {"bound": True, **binding}

    def _handle_session_hook_check(self, args: dict[str, Any]) -> dict[str, Any]:
        empty = {
            "bound": False,
            "run_id": None,
            "project_root": None,
            "resume_context": "",
            "should_block_stop": False,
            "stop_reason": "",
            "blocked_key": "",
            "progress_classification": None,
        }
        session_id = args["session_id"]
        if not isinstance(session_id, str) or not session_id.strip():
            return empty

        session_id = session_id.strip()
        event_name = str(args["event_name"] or "").strip().lower()
        raw_cwd = args.get("cwd")
        cwd_text = raw_cwd.strip() if isinstance(raw_cwd, str) else ""
        cwd = Path(cwd_text).resolve(strict=False) if cwd_text else None
        bindings = self.db.list_session_bindings(session_id)
        matching = []
        for binding in bindings:
            run = self.db.get_run(binding["run_id"])
            if run is None or run["status"] != "active":
                if empty["run_id"] is None:
                    empty.update(run_id=binding["run_id"], project_root=binding["project_root"],
                                 progress_classification="inactive" if run else None)
                continue

            scopes = [Path(binding["project_root"]).resolve(strict=False)]
            if run["execution_root"]:
                scopes.append(Path(run["execution_root"]).resolve(strict=False))
            scope = max(
                (p for p in scopes if cwd is None or cwd.is_relative_to(p)),
                key=lambda p: len(p.parts),
                default=None,
            )
            if scope is None:
                if empty["run_id"] is None:
                    empty.update(run_id=run["id"], project_root=binding["project_root"])
                continue
            in_subagent_workspace = cwd is not None and any(
                os.path.normcase(part) == WORKSPACES_DIRNAME for part in cwd.relative_to(scope).parts
            )
            matching.append((binding, run, in_subagent_workspace, len(scope.parts) if cwd else 0))

        if event_name == "userpromptsubmit":
            selected = max(matching, key=lambda match: match[3], default=None)
            turn_id = (args.get("turn_id") or "").strip()
            invoked = args.get("has_mode_invocation")
            if turn_id and (invoked is True or bindings or self.db.list_session_turns(session_id)):
                turn_model, turn_effort = _resolve_live_model_and_effort(
                    session_id, args.get("model")
                )
                turn = self.db.upsert_session_turn(
                    session_id, turn_id, cwd=cwd_text, prompt_preview=args.get("prompt_preview", ""),
                    active_run_id=selected[1]["id"] if selected else None,
                    model=turn_model,
                    reasoning_effort=turn_effort,
                )
                self._emit(
                    "session_updated",
                    {"run_id": turn["active_run_id"], "session_id": session_id, "turn_id": turn_id},
                    run_id=turn["active_run_id"],
                )
            if selected is None:
                return empty
            binding, run, _, _ = selected
            # A passive turn uses only its attribution; the hook emits no resume context.
            if invoked is False:
                return {**empty, "bound": True, "run_id": run["id"], "project_root": binding["project_root"]}
            return self._session_run_hook_result(args, binding, run, check_stop=False)

        turn_id = _clean_opt_str(args.get("turn_id"))
        if turn_id and event_name in {"stop", "postcompact", "sessionstart"}:
            turn = self.db.get_session_turn(session_id, turn_id)
            if turn is not None and (not turn.get("model") or not turn.get("reasoning_effort")):
                preferred_turn_model = _clean_opt_str(args.get("model")) or _clean_opt_str(
                    turn.get("model")
                )
                resolved_model, resolved_effort = _resolve_live_model_and_effort(
                    session_id, preferred_turn_model
                )
                existing_turn_model = _clean_opt_str(turn.get("model"))
                safe_effort = (
                    resolved_effort
                    if (not existing_turn_model or existing_turn_model == resolved_model)
                    else None
                )
                if (not existing_turn_model and resolved_model) or (
                    not turn.get("reasoning_effort") and safe_effort
                ):
                    turn = self.db.update_session_turn_model(
                        session_id,
                        turn_id,
                        model=resolved_model if not existing_turn_model else None,
                        reasoning_effort=safe_effort,
                    )
            for _, run, _, _ in matching:
                if run.get("turn_id") == turn_id and not run.get("trigger_reasoning_effort"):
                    existing_run_model = _clean_opt_str(run.get("trigger_model"))
                    preferred_run_model = (
                        existing_run_model
                        or _clean_opt_str(args.get("model"))
                        or (_clean_opt_str(turn.get("model")) if turn is not None else None)
                    )
                    run_model, run_effort = _resolve_live_model_and_effort(
                        session_id, preferred_run_model
                    )
                    if existing_run_model:
                        if run_model == existing_run_model and run_effort:
                            self.db.update_run_trigger_model(
                                run["id"],
                                trigger_model=existing_run_model,
                                trigger_reasoning_effort=run_effort,
                            )
                    elif run_model:
                        self.db.update_run_trigger_model(
                            run["id"],
                            trigger_model=run_model,
                            trigger_reasoning_effort=run_effort,
                        )

        if not matching:
            return empty

        if event_name != "stop":
            binding, run, _, _ = matching[0]
            return self._session_run_hook_result(args, binding, run, check_stop=False)

        first = None
        for binding, run, in_subagent_workspace, _ in matching:
            result = self._session_run_hook_result(args, binding, run, check_stop=not in_subagent_workspace)
            if result["should_block_stop"]:
                if args.get("mark_stop_blocked") is False:
                    return result
                # Another Stop request may have marked the same binding while we read the board.
                with self.db.transaction():
                    current = self.db.get_session_binding(session_id, run["id"])
                    current_run = self.db.get_run(run["id"])
                    if (
                        current is not None
                        and current_run is not None
                        and current_run["status"] == "active"
                        and current["last_stop_blocked_key"] != result["blocked_key"]
                    ):
                        self.db.update_session_stop_blocked_key(session_id, result["blocked_key"], run_id=run["id"])
                        return result
                result.update(should_block_stop=False, stop_reason="")
            if first is None:
                first = result
        return first

    def _session_run_hook_result(
        self, args: dict[str, Any], binding: dict[str, Any], run: dict[str, Any], *, check_stop: bool,
    ) -> dict[str, Any]:
        run_id = run["id"]
        status_compact = self.board.board_status(run_id, compact=True)
        pkt = status_compact.get("latest_task_packet")
        pkt_ver = pkt["version"] if pkt else 0
        wf = status_compact["workflow"]
        unfinished = [
            f"{s['step_key']}:{s['state']}(a{s['attempt']})"
            for s in wf["steps"]
            if s["state"] != "done"
        ]
        native = status_compact["native_actions"]
        next_acts = native.get("next_actions", [])
        next_summaries = [
            f"{a['action_type']}->{a['recipient']}({a.get('summary') or a['source_id']})"
            for a in next_acts[:3]
        ]
        progress = status_compact["progress_status"]
        p_cls = progress["classification"]

        resume_parts = [
            f"[tri-orchestrator] run={run_id} mode={run['mode']} wf=v{run['workflow_version']} pkt=v{pkt_ver} status={p_cls}",
            f"steps_pending={','.join(unfinished) if unfinished else 'none'}",
            f"next_actions({len(next_acts)})={'; '.join(next_summaries) if next_summaries else 'none'}",
        ]
        if native.get("needs_reconciliation_count"):
            resume_parts.append(f"needs_reconciliation={native['needs_reconciliation_count']}")
        resume_text = " | ".join(resume_parts)
        if len(resume_text) > 800:
            resume_text = resume_text[:797] + "..."

        with self.db.read_connection() as conn:
            active_inbox_rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT id, step_id, recipient, kind, status FROM inbox_messages WHERE run_id=? AND status IN ('pending','accepted') ORDER BY board_seq",
                    (run_id,),
                ).fetchall()
            ]
            invoking_action_rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT action_id, recipient, action_type, status FROM native_actions WHERE run_id=? AND status IN ('claimed','invoking') ORDER BY action_id",
                    (run_id,),
                ).fetchall()
            ]

        latest_seq = status_compact["latest_seq"]
        summary_fp = native_digest(
            [
                [(s["id"], s["state"], s["revision"], s["attempt"]) for s in wf["steps"]],
                [(v["id"], v["verdict"]) for v in wf.get("verifications", [])],
                [(m["id"], m["status"]) for m in active_inbox_rows],
                [(a["source_type"], a["source_id"], a["source_digest"]) for a in next_acts],
                p_cls,
            ]
        )[:16]
        blocked_key = f"{run_id}:{latest_seq}:{summary_fp}"

        should_block = False
        stop_reason = ""

        if check_stop:
            stop_hook_active = bool(args.get("stop_hook_active", False))
            has_pending_controls = any(
                c["status"] == "pending" for c in status_compact.get("control_requests", [])
            )
            has_paused_workers = any(
                w["status"] in {"stopped", "killed"} for w in status_compact.get("workers", [])
            )
            hard_blocker_classes = {
                "inactive",
                "user_decision",
                "needs_reconciliation",
                "needs_clarification",
                "no_progress",
                "host_unknown",
            }
            non_blocking_classes = hard_blocker_classes | {"completed"}
            has_hard_blocker = (
                p_cls in hard_blocker_classes
                or bool(progress.get("blockers"))
                or bool(native.get("needs_reconciliation_count"))
                or bool(native.get("needs_clarification"))
                or any(s["state"] in {"needs_decision", "reconciliation_required"} for s in wf["steps"])
            )
            unblocked_actions = [a for a in next_acts if a["source_type"] != "control"]

            art_scope_by_id = {a["id"]: a["scope"] for a in wf.get("artifacts", [])}
            verifications = wf.get("verifications", [])
            actionable_transitions: list[str] = []
            for step_info in wf["steps"]:
                st_id = step_info["id"]
                state = step_info["state"]
                verifier_name = step_info["verifier"]
                owner_name = step_info["owner"]
                has_review_inflight = (
                    any(
                        m["step_id"] == st_id and m["recipient"] == verifier_name and m["kind"] == "review"
                        for m in active_inbox_rows
                    )
                    or any(a["recipient"] == verifier_name for a in invoking_action_rows)
                )
                has_author_inflight = (
                    any(
                        m["step_id"] == st_id and m["recipient"] == owner_name and m["kind"] in {"task", "rework"}
                        for m in active_inbox_rows
                    )
                    or any(a["recipient"] == owner_name for a in invoking_action_rows)
                )
                if state == "candidate_review" and not has_review_inflight:
                    actionable_transitions.append(
                        f"candidate_review -> {step_info['step_key']}: 等待派发验收 (@{verifier_name})"
                    )
                elif state == "ready_to_merge":
                    actionable_transitions.append(
                        f"ready_to_merge -> {step_info['step_key']}: 等待协调者合入"
                    )
                elif state == "rework" and not has_author_inflight:
                    actionable_transitions.append(
                        f"rework -> {step_info['step_key']}: 等待派发返工 (@{owner_name})"
                    )
                elif state == "integrated_review":
                    has_integrated_pass = any(
                        v["step_id"] == st_id
                        and v["verdict"] == "PASS"
                        and v.get("packet_version") == step_info["packet_version"]
                        and v.get("attempt") == step_info["attempt"]
                        and v.get("contract_revision") == step_info["contract_revision"]
                        and art_scope_by_id.get(v["artifact_id"]) == "integrated"
                        for v in verifications
                    )
                    if has_integrated_pass:
                        actionable_transitions.append(
                            f"integrated_review -> {step_info['step_key']}: 集成验收已通过，等待 complete / workflow_closeout_run"
                        )
                    elif not has_review_inflight:
                        actionable_transitions.append(
                            f"integrated_review -> {step_info['step_key']}: 等待派发集成验收 (@{verifier_name})"
                        )

            all_steps_done = bool(wf["steps"]) and all(s["state"] == "done" for s in wf["steps"])
            if all_steps_done and run["status"] == "active":
                actionable_transitions.append(
                    "workflow_closeout_run -> 所有步骤已完成，等待 closeout 收尾"
                )

            should_block_on_transitions = (
                bool(actionable_transitions)
                and not has_hard_blocker
                and not has_pending_controls
                and not has_paused_workers
            )

            if (
                run["status"] == "active"
                and not stop_hook_active
                and binding.get("last_stop_blocked_key") != blocked_key
                and not has_pending_controls
                and not has_paused_workers
                and not has_hard_blocker
                and (
                    (p_cls not in non_blocking_classes and len(unblocked_actions) > 0)
                    or should_block_on_transitions
                )
            ):
                should_block = True
                if unblocked_actions:
                    first_act = unblocked_actions[0]
                    act_desc = f"{first_act['action_type']} -> @{first_act['recipient']}: {first_act.get('summary') or first_act['source_id']}"
                    action_count = len(unblocked_actions)
                else:
                    act_desc = actionable_transitions[0]
                    action_count = len(actionable_transitions)
                stop_reason = (
                    f"Run {run_id} 仍有 {action_count} 项无阻塞待办动作（如 {act_desc[:120]}），"
                    "请按契约继续推进或明确说明阻塞原因。"
                )

        return {
            "bound": True,
            "run_id": run_id,
            "project_root": binding["project_root"],
            "resume_context": resume_text,
            "should_block_stop": should_block,
            "stop_reason": stop_reason,
            "blocked_key": blocked_key,
            "progress_classification": p_cls,
        }
