"""Unified RPC dispatcher, MCP tool JSON schemas, and thread-safe SSE EventBus."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from orchestrator.board import BoardService
from orchestrator.native_actions import NativeActionStore
from orchestrator.inbox import INBOX_TOOL_DEFINITIONS, InboxStore
from orchestrator.workflow import WorkflowService
from orchestrator.db import (
    VALID_MSG_TYPES,
    VALID_RUN_MODES,
    VALID_RUN_STATUSES,
    VALID_WORKER_ROLES,
    VALID_WORKER_STATUSES,
    Database,
    NotFoundError,
)
from orchestrator.workspace_mgr import (
    VALID_DEP_MODES,
    VALID_MERGE_STRATEGIES,
    WorkspaceManager,
)


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
            if filter_run_id and target_run_id and filter_run_id != target_run_id:
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
        **{key: {"type": "string"} for key in ("run_id", "source_id", "recipient", "action_id", "claim_token", "source_digest", "evidence_ref", "evidence_digest", "result")},
        "source_type": {"type": "string", "enum": ["inbox", "control", "human"]},
        "action_type": {"type": "string", "enum": ["followup_task", "send_message", "interrupt_agent"]},
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
                "verification_commands": {"type": "array", "minItems": 1, "items": {
                    "type": "object", "properties": {
                        "id": {"type": "string", "minLength": 1},
                        "argv": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                        "cwd": {"type": "string"}, "timeout_sec": {"type": "number", "exclusiveMinimum": 0},
                        "env_keys": {"type": "array", "uniqueItems": True, "items": {"type": "string"}},
                        "criteria": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "string"}}},
                    "required": ["id", "argv", "cwd", "timeout_sec", "env_keys", "criteria"], "additionalProperties": False}},
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
                "verifier": {"type": "string"}, "verdict": {"type": "string", "enum": ["PASS", "FAIL"]},
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
])


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
        schema = next(tool["inputSchema"] for tool in TOOL_DEFINITIONS if tool["name"] == tool_name)
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

    # ------------------------------------------------------------------
    # Run & Task Packet Handlers
    # ------------------------------------------------------------------

    def _handle_run_create(self, args: dict[str, Any]) -> dict[str, Any]:
        mode = self._require_str(args, "mode").strip()
        title = self._require_str(args, "title").strip()
        project_root = args.get("project_root")
        project_id = args.get("project_id")
        is_git = args.get("is_git")

        if project_root is not None and is_git is None:
            root_path = Path(str(project_root)).resolve(strict=False)
            if root_path.exists() and root_path.is_dir():
                detected_git, _, _, _ = WorkspaceManager._detect_git_repo(root_path)
                is_git = detected_git

        run = self.db.create_run(
            mode=mode,
            title=title,
            project_id=str(project_id) if project_id is not None else None,
            project_root=str(project_root) if project_root is not None else None,
            project_name=(
                str(args["project_name"]) if args.get("project_name") is not None else None
            ),
            is_git=bool(is_git) if is_git is not None else None,
            status=str(args.get("status") or "active"),
            metadata=args.get("metadata"),
        )
        self._emit("run_updated", {"run_id": run["id"], "run": run}, run_id=run["id"])
        return run

    def _handle_run_update(self, args: dict[str, Any]) -> dict[str, Any]:
        run_id = self._require_str(args, "run_id")
        run = self.db.update_run(
            run_id,
            status=args.get("status"),
            title=args.get("title"),
            metadata=args.get("metadata"),
        )
        self._emit("run_updated", {"run_id": run["id"], "run": run}, run_id=run["id"])
        return run

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
        return self.board.board_status(
            run_id,
            reader_name=args.get("reader_name"),
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
            status=str(args.get("status") or "waiting"),
            workspace_id=args.get("workspace_id"),
            current_task=str(args.get("current_task") or ""),
            stall_threshold_sec=(
                int(raw_threshold) if raw_threshold is not None else None
            ),
        )
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
        self._emit("workflow_updated", {"run_id": step["run_id"], "step": step}, run_id=step["run_id"])
        return step

    def _handle_workflow_step_advance(self, args: dict[str, Any]) -> dict[str, Any]:
        step = self.workflow.step_advance(**args)
        self._emit("workflow_updated", {"run_id": step["run_id"], "step": step}, run_id=step["run_id"])
        return step

    def _handle_artifact_capture(self, args: dict[str, Any]) -> dict[str, Any]:
        artifact = self.workflow.capture(**args)
        run_id = self.workflow.get_step(artifact["step_id"])["run_id"]
        self._emit("workflow_updated", {"run_id": run_id, "artifact": artifact}, run_id=run_id)
        return artifact

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
        self._emit(
            "workspace_updated",
            {"run_id": ws["run_id"], "workspace": ws},
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
