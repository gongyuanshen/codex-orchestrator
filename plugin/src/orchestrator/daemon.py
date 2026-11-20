"""Singleton HTTP/SSE daemon server (Starlette + Uvicorn) for tri-orchestrator."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, AsyncIterator

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from orchestrator.board import BoardService
from orchestrator import __version__, codex_bridge
from orchestrator.config import Settings, get_settings
from orchestrator.db import Database, NotFoundError, SCHEMA_VERSION, VALID_RUN_MODES, require_runtime_schema
from orchestrator.inbox import InboxConflictError, request_wait
from orchestrator.workflow import WorkflowConflictError
from orchestrator.rpc_handlers import EventBus, RpcDispatcher
from orchestrator.watchdog import StallWatchdog
from orchestrator.workspace_mgr import (
    WORKSPACES_DIRNAME,
    WorkspaceCleanupGuardError,
    WorkspaceConflictError,
    WorkspaceError,
    WorkspaceManager,
)

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
ALLOWED_MANUAL_WORKER_STATUSES = frozenset({"stopped", "killed"})


def _configure_daemon_logging(settings: Settings) -> None:
    """Route daemon and uvicorn logs to daemon.log so detached Windows processes never fail on stdout/isatty."""
    settings.ensure_data_dir()
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_tri_orch_daemon_handler", False):
            return
    file_handler = RotatingFileHandler(
        settings.daemon_log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler._tri_orch_daemon_handler = True  # type: ignore[attr-defined]
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)


def _write_pid_file(settings: Settings, *, started_at: float) -> None:
    """Atomically write daemon.pid.json metadata."""
    settings.ensure_data_dir()
    payload = {
        "pid": os.getpid(),
        "host": settings.host,
        "port": settings.port,
        "url": settings.base_url,
        "data_dir": str(settings.data_dir),
        "started_at": started_at,
    }
    tmp_path = settings.pid_path.with_suffix(".pid.tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp_path, settings.pid_path)


def _remove_pid_file_if_owner(settings: Settings) -> None:
    """Remove daemon.pid.json only if it belongs to the current PID."""
    pid_path = settings.pid_path
    if not pid_path.exists():
        return
    try:
        raw = json.loads(pid_path.read_text(encoding="utf-8"))
        if int(raw.get("pid", -1)) == os.getpid():
            pid_path.unlink(missing_ok=True)
    except Exception:
        pass


def _json_error(
    status_code: int,
    detail: str,
    *,
    error_type: str = "Error",
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "ok": False,
        "detail": detail,
        "error": detail,
        "error_type": error_type,
    }
    if extra:
        body.update(extra)
    return JSONResponse(body, status_code=status_code)


def _map_exception_to_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, (NotFoundError, FileNotFoundError)):
        return _json_error(404, str(exc), error_type=type(exc).__name__)
    if isinstance(exc, (WorkspaceCleanupGuardError, WorkspaceConflictError, InboxConflictError, WorkflowConflictError)):
        return _json_error(409, str(exc), error_type=type(exc).__name__)
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return _json_error(400, str(exc), error_type=type(exc).__name__)
    if isinstance(exc, WorkspaceError):
        return _json_error(500, str(exc), error_type=type(exc).__name__)
    logger.exception("Unhandled error in daemon endpoint")
    return _json_error(500, str(exc), error_type=type(exc).__name__)


async def _parse_json_body(request: Request, *, allow_empty: bool = False) -> dict[str, Any]:
    raw = await request.body()
    if not raw or not raw.strip():
        if allow_empty:
            return {}
        raise ValueError("Request JSON body is required")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid JSON body: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("JSON body must be an object")
    return parsed


def _format_utc_iso(ts: float | int | None) -> str:
    if ts is None:
        return ""
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _safe_filename_segment(raw: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in raw)
    return cleaned.strip("-") or "run"


def _launch_explorer(target_dir: Path) -> None:
    """Launch Windows Explorer on a validated directory without shell interpolation."""
    explorer_bin = shutil.which("explorer.exe") or "explorer.exe"
    subprocess.Popen(
        [explorer_bin, str(target_dir)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


_RUN_MODE_LABELS: dict[str, str] = {
    "boost": "极速模式",
    "teamwork": "协同模式",
    "swarm": "蜂窝模式",
}
_RUN_STATUS_LABELS: dict[str, str] = {
    "active": "进行中",
    "completed": "已完成",
    "failed": "已失败",
    "archived": "已归档",
}
_TASK_PACKET_STATUS_LABELS: dict[str, str] = {
    "active": "生效中",
    "frozen": "已冻结",
    "completed": "已完成",
    "archived": "已归档",
    "superseded": "已替换",
    "draft": "草稿",
}
_WORKER_STATUS_LABELS: dict[str, str] = {
    "waiting": "待命",
    "working": "执行中",
    "stopped": "已暂停",
    "killed": "已终止",
}
_ROLE_LABELS: dict[str, str] = {
    "coordinator": "主协调者",
    "interviewer": "需求访谈",
    "planner": "架构规划",
    "investigator": "代码勘探",
    "coder": "开发实现",
    "verifier": "独立验收",
    "swarm_worker": "蜂窝执行",
    "human": "人工干预",
    "system": "系统监控",
    "watchdog": "看门狗",
    "agent": "协作代理",
}
_WORKSPACE_MODE_LABELS: dict[str, str] = {
    "git_clone": "Git 本地克隆",
    "snapshot": "目录快照",
}
_WORKSPACE_STATUS_LABELS: dict[str, str] = {
    "active": "活跃",
    "verified": "已验证",
    "merged": "已合入",
    "cleaned": "已清理",
    "conflict": "有冲突",
}
_CHANNEL_LABELS: dict[str, str] = {
    "main": "主频道",
    "human": "人工",
}
_MSG_TYPE_LABELS: dict[str, str] = {
    "info": "通知",
    "handoff": "交接",
    "diff": "差异",
    "test": "测试",
    "alert": "告警",
    "human": "人工",
}


def _display_label(
    mapping: dict[str, str], value: Any, *, fallback: str | None = None
) -> str:
    raw = str(value or "").strip()
    if not raw:
        return fallback if fallback is not None else "-"
    return mapping.get(raw, raw)


def _format_model_effort(model: Any, effort: Any) -> str:
    m = str(model or "").strip()
    e = str(effort or "").strip()
    if not m:
        return "-"
    return f"{m} ({e})" if e else m


def build_markdown_report(bundle: dict[str, Any]) -> str:
    """Render a localized Chinese Markdown run report while preserving raw dynamic values."""
    run = bundle["run"]
    project = bundle.get("project") or {}
    packets = bundle.get("task_packets") or []
    workers = bundle.get("workers") or []
    workspaces = bundle.get("workspaces") or []
    messages = bundle.get("messages") or []
    helper_subagents = bundle.get("helper_subagents") or []
    session_subagents = bundle.get("session_subagents") or []

    lines: list[str] = [
        f"# 三重编排器运行报告：{run.get('title') or run['id']}",
        "",
        f"- **运行 ID**：`{run['id']}`",
        f"- **编排模式**：`{_display_label(_RUN_MODE_LABELS, run.get('mode'))}`",
        f"- **运行状态**：`{_display_label(_RUN_STATUS_LABELS, run.get('status'))}`",
        f"- **所属项目**：`{project.get('name', '')}` (`{project.get('root_path', '')}`)",
        *(
            [f"- **主对话模型**：`{_format_model_effort(run.get('codex_model'), run.get('codex_reasoning_effort'))}`"]
            if run.get("codex_model")
            else []
        ),
        *(
            [f"- **触发时模型快照**：`{_format_model_effort(run.get('trigger_model'), run.get('trigger_reasoning_effort'))}`"]
            if run.get("trigger_model")
            else []
        ),
        f"- **创建时间 (UTC)**：`{_format_utc_iso(run.get('created_at'))}`",
        f"- **更新时间 (UTC)**：`{_format_utc_iso(run.get('updated_at'))}`",
        "",
    ]

    workflow = bundle.get("workflow")
    if workflow is not None:
        label = "结构化验收门禁" if run.get("workflow_version") else "旧流程，未启用结构化门禁"
        lines.extend([f"## 工作流状态（{label}）", ""])
        for step in workflow["steps"]:
            lines.append(f"- `{step['step_key']}`：`{step['state']}`；返工 {step['attempt']}/3；修订 {step['revision']}")
        for blocker in workflow["blockers"]:
            lines.append(f"- **阻塞**：{blocker}")
        if "inbox" in bundle:
            lines.append(f"- **未处置业务收件**：{bundle['inbox']['blocking_count']}")
        lines.append("")

    if packets:
        latest = packets[-1]
        lines.extend(
            [
                f"## 任务契约 (v{latest.get('version', 1)})",
                "",
                f"- **标题**：{latest.get('title', '')}",
                f"- **状态**：`{_display_label(_TASK_PACKET_STATUS_LABELS, latest.get('status'))}`",
                f"- **核心目标**：{latest.get('goal', '')}",
                "",
                "### 文件所有权矩阵",
                "",
                "```json",
                json.dumps(latest.get("file_ownership", {}), ensure_ascii=False, indent=2),
                "```",
                "",
                "### 验收标准",
                "",
            ]
        )
        criteria = latest.get("acceptance_criteria") or []
        if isinstance(criteria, list) and criteria:
            for item in criteria:
                lines.append(f"- {item}")
        else:
            lines.append("- （无）")
        lines.append("")

    lines.extend(["## 成员列表", ""])
    if workers:
        lines.append("| 名称 | 职责角色 | 模型 / 推理强度 | 状态 | 工作区 ID | 当前任务 | 最近心跳 (UTC) |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
        for w in workers:
            model_text = _format_model_effort(w.get("model"), w.get("reasoning_effort")).replace("|", "/")
            lines.append(
                f"| `{w['name']}` | `{_display_label(_ROLE_LABELS, w.get('role'))}` | "
                f"`{model_text}` | "
                f"`{_display_label(_WORKER_STATUS_LABELS, w.get('status'))}` | "
                f"`{w.get('workspace_id') or '-'}` | {str(w.get('current_task') or '').replace('|', '/')} | "
                f"`{_format_utc_iso(w.get('last_heartbeat_at'))}` |"
            )
    else:
        lines.append("_暂无已注册成员。_")
    lines.append("")

    if helper_subagents or session_subagents:
        lines.extend(["## 辅助子代理（L2+ 与未登记原生子代理）", ""])
        lines.append("| 归属 | 层级 | 路径 / 昵称 | 角色 | 模型 / 推理强度 |")
        lines.append("| :--- | :--- | :--- | :--- | :--- |")
        for sub in (*helper_subagents, *session_subagents):
            parent_name = sub.get("parent_worker_name") or "coordinator"
            owner_text = (
                "主会话（未登记）"
                if sub.get("registered") is False and parent_name == "coordinator"
                else f"`{str(parent_name).replace('|', '/')}`"
            )
            depth = sub.get("depth")
            depth_text = f"L{depth}" if depth is not None else "-"
            raw_path = str(sub.get("agent_path") or sub.get("thread_id") or "-").replace("|", "/")
            nickname = str(sub.get("agent_nickname") or "").strip().replace("|", "/")
            path_text = f"`{raw_path}` ({nickname})" if nickname else f"`{raw_path}`"
            role_text = _display_label(_ROLE_LABELS, sub.get("agent_role"), fallback="-").replace("|", "/")
            model_text = _format_model_effort(sub.get("model"), sub.get("reasoning_effort")).replace("|", "/")
            lines.append(f"| {owner_text} | `{depth_text}` | {path_text} | `{role_text}` | `{model_text}` |")
        lines.append("")

    lines.extend(["## 隔离工作区", ""])
    if workspaces:
        lines.append("| ID | 所属成员 | 隔离模式 | 状态 | 分支 | 端口 | 本地路径 |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
        for ws in workspaces:
            lines.append(
                f"| `{ws['id']}` | `{ws['worker_name']}` | "
                f"`{_display_label(_WORKSPACE_MODE_LABELS, ws.get('mode'))}` | "
                f"`{_display_label(_WORKSPACE_STATUS_LABELS, ws.get('status'))}` | "
                f"`{ws.get('branch_name') or '-'}` | `{ws.get('assigned_port') or '-'}` | "
                f"`{ws['path']}` |"
            )
    else:
        lines.append("_暂无隔离工作区。_")
    lines.append("")

    lines.extend(["## 消息板 (`#seq`)", ""])
    if messages:
        for msg in messages:
            mentions = ", ".join(f"@{m}" for m in (msg.get("mentions") or []))
            channel_label = _display_label(_CHANNEL_LABELS, msg.get("channel"))
            role_label = _display_label(
                _ROLE_LABELS, msg.get("sender_role") or "agent", fallback="协作代理"
            )
            msg_type_label = _display_label(_MSG_TYPE_LABELS, msg.get("msg_type"))
            lines.extend(
                [
                    f"### #{msg['seq']} [{channel_label}] {msg['sender']}（{role_label}）— `{msg_type_label}`",
                    f"- **时间 (UTC)**：`{_format_utc_iso(msg.get('created_at'))}`",
                    *( [f"- **@提及**：{mentions}"] if mentions else [] ),
                    *( [f"- **摘要**：{msg['summary']}"] if msg.get("summary") else [] ),
                    "",
                    msg.get("body_md") or msg.get("summary") or "",
                    "",
                ]
            )
    else:
        lines.append("_暂无消息记录。_")
    lines.append("")
    return "\n".join(lines)


_render_run_markdown = build_markdown_report


def _resolve_live_model_fields(
    live: dict[str, Any] | None,
    stored: dict[str, Any],
) -> tuple[str | None, str | None, str | None, str | None]:
    """Resolve model metadata preferring live Codex state, falling back safely to snapshots."""
    stored_model = stored.get("model")
    stored_effort = stored.get("reasoning_effort")
    stored_provider = stored.get("model_provider")
    if live is None:
        return (
            stored_model,
            stored_effort,
            stored_provider,
            "snapshot" if stored_model else None,
        )

    live_model = live.get("model")
    live_effort = live.get("reasoning_effort")
    live_provider = live.get("model_provider")
    model = live_model if live_model is not None else stored_model
    same_or_fallback = live_model is None or stored_model is None or live_model == stored_model
    reasoning_effort = live_effort if live_effort is not None else (stored_effort if same_or_fallback else None)
    model_provider = live_provider if live_provider is not None else (stored_provider if same_or_fallback else None)
    model_source = "live" if live_model else ("snapshot" if stored_model else None)
    return model, reasoning_effort, model_provider, model_source


def _pick_closest_subagent(
    candidates: list[dict[str, Any]], worker_created_at: Any
) -> dict[str, Any]:
    if len(candidates) == 1 or worker_created_at is None:
        return candidates[0]
    target = float(worker_created_at)
    return min(
        candidates,
        key=lambda node: (
            0 if node.get("created_at") is not None else 1,
            abs(float(node["created_at"]) - target) if node.get("created_at") is not None else 0.0,
            float(node.get("created_at") or 0.0),
            str(node.get("thread_id") or ""),
        ),
    )


def _match_worker_live_node(
    worker: dict[str, Any],
    *,
    session_id: str | None,
    root_thread: dict[str, Any] | None,
    by_id: dict[str, dict[str, Any]],
    depth1_by_path: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any] | None, str | None, bool]:
    persisted_tid = worker.get("codex_thread_id")
    agent_id = worker.get("agent_id")
    agent_path = agent_id or worker.get("pending_agent_path")
    is_root_worker = agent_path == "/root"

    if persisted_tid and persisted_tid in by_id:
        return by_id[persisted_tid], persisted_tid, is_root_worker
    if is_root_worker and session_id and root_thread is not None:
        return root_thread, session_id, True
    if agent_path and agent_path in depth1_by_path:
        matched = _pick_closest_subagent(depth1_by_path[agent_path], worker.get("created_at"))
        return matched, matched["thread_id"], False
    if isinstance(agent_id, str) and len(agent_id) == 36 and agent_id in by_id:
        matched = by_id[agent_id]
        return matched, matched["thread_id"], False
    return None, persisted_tid, is_root_worker


def _collect_worker_subagents(
    worker_id: str,
    worker_name: str,
    worker_thread_id: str | None,
    *,
    children_by_parent: dict[str, list[dict[str, Any]]],
    stored_for_worker: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    live_desc_by_id: dict[str, dict[str, Any]] = {}
    if worker_thread_id:
        queue = deque(children_by_parent.get(worker_thread_id, []))
        seen = {worker_thread_id}
        while queue:
            child = queue.popleft()
            tid = child["thread_id"]
            if tid in seen:
                continue
            seen.add(tid)
            if (child.get("depth") or 0) >= 2:
                live_desc_by_id[tid] = child
            queue.extend(children_by_parent.get(tid, []))

    result: list[dict[str, Any]] = []
    for tid in set(live_desc_by_id) | set(stored_for_worker):
        live = live_desc_by_id.get(tid)
        stored = stored_for_worker.get(tid) or {}
        model, reasoning_effort, model_provider, model_source = _resolve_live_model_fields(live, stored)
        src = live if live is not None else stored
        edge_status = (
            live.get("edge_status")
            if live is not None and live.get("edge_status") is not None
            else stored.get("edge_status")
        )
        result.append({
            "thread_id": tid,
            "parent_thread_id": src.get("parent_thread_id") if src.get("parent_thread_id") is not None else stored.get("parent_thread_id"),
            "parent_worker_id": worker_id,
            "parent_worker_name": worker_name,
            "depth": src.get("depth") if src.get("depth") is not None else stored.get("depth"),
            "agent_path": (src.get("agent_path") if src.get("agent_path") is not None else stored.get("agent_path")) or "",
            "agent_role": src.get("agent_role") if src.get("agent_role") is not None else stored.get("agent_role"),
            "agent_nickname": src.get("agent_nickname") if src.get("agent_nickname") is not None else stored.get("agent_nickname"),
            "model": model,
            "reasoning_effort": reasoning_effort,
            "model_provider": model_provider,
            "edge_status": edge_status,
            "status": edge_status,
            "model_source": model_source,
            "created_at": src.get("created_at") if src.get("created_at") is not None else stored.get("created_at"),
            "updated_at": src.get("updated_at") if src.get("updated_at") is not None else stored.get("updated_at"),
        })
    result.sort(
        key=lambda s: (
            int(s.get("depth") or 0),
            float(s.get("created_at") or 0.0),
            str(s.get("thread_id") or ""),
        )
    )
    return result


def _collect_session_subagents(
    database: Database,
    dashboard_run: dict[str, Any],
    session_id: str,
    live_tree: list[dict[str, Any]],
    *,
    children_by_parent: dict[str, list[dict[str, Any]]],
    matched_worker_thread_ids: set[str],
) -> list[dict[str, Any]]:
    registered_paths = database.list_session_registered_agent_paths(session_id)
    ordered_runs = database.list_session_runs_ordered(session_id)
    run_id = dashboard_run["id"]
    idx = next((i for i, r in enumerate(ordered_runs) if r["id"] == run_id), None)
    if idx is not None:
        run_created = float(ordered_runs[idx]["created_at"])
        win_start: float | None = run_created - 10.0 if idx == 0 else run_created
        win_end: float | None = (
            float(ordered_runs[idx + 1]["created_at"]) if idx + 1 < len(ordered_runs) else None
        )
    else:
        raw_created = dashboard_run.get("created_at")
        win_start = float(raw_created) - 10.0 if raw_created is not None else None
        win_end = None

    queue: deque[tuple[dict[str, Any], str]] = deque()
    for node in live_tree:
        if node.get("depth") != 1:
            continue
        npath = node.get("agent_path") or ""
        ntid = node["thread_id"]
        if npath in registered_paths or ntid in registered_paths or ntid in matched_worker_thread_ids:
            continue
        node_created = node.get("created_at")
        if node_created is not None and win_start is not None:
            ts = float(node_created)
            if ts < win_start or (win_end is not None and ts >= win_end):
                continue
        queue.append((node, "coordinator"))

    collected: list[dict[str, Any]] = []
    seen: set[str] = set()
    while queue:
        node, parent_name = queue.popleft()
        tid = node["thread_id"]
        if tid in seen:
            continue
        seen.add(tid)
        collected.append({
            **node,
            "registered": False,
            "parent_worker_id": None,
            "parent_worker_name": parent_name,
            "status": node.get("edge_status"),
            "model_source": "live" if node.get("model") else None,
        })
        next_parent = node.get("agent_path") or node.get("agent_nickname") or parent_name
        for child in children_by_parent.get(tid, []):
            if (child.get("depth") or 0) >= 2 and child["thread_id"] not in seen:
                queue.append((child, next_parent))

    collected.sort(
        key=lambda s: (
            int(s.get("depth") or 0),
            float(s.get("created_at") or 0.0),
            str(s.get("thread_id") or ""),
        )
    )
    return collected


def _build_model_summary(
    dashboard_run: dict[str, Any],
    enriched_workers: list[dict[str, Any]],
    helper_subagents: list[dict[str, Any]],
    session_subagents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    roles_by_key: dict[tuple[str, str | None], set[str]] = {}
    counts: dict[tuple[str, str | None], int] = {}

    def _add(model_val: Any, effort_val: Any, role_val: str) -> None:
        m = str(model_val or "").strip()
        if not m:
            return
        e = str(effort_val).strip() or None if effort_val is not None else None
        key = (m, e)
        counts[key] = counts.get(key, 0) + 1
        roles_by_key.setdefault(key, set()).add(role_val)

    if dashboard_run.get("codex_model"):
        root_model = dashboard_run["codex_model"]
        root_effort = dashboard_run.get("codex_reasoning_effort")
        if root_effort is None and dashboard_run.get("trigger_model") == root_model:
            root_effort = dashboard_run.get("trigger_reasoning_effort")
    elif dashboard_run.get("trigger_model"):
        root_model = dashboard_run["trigger_model"]
        root_effort = dashboard_run.get("trigger_reasoning_effort")
    else:
        root_model = None
        root_effort = None

    _add(root_model, root_effort, "coordinator")

    session_id = dashboard_run.get("session_id")
    for w in enriched_workers:
        if (
            w.get("agent_id") == "/root"
            and session_id is not None
            and w.get("codex_thread_id") == session_id
            and root_model
        ):
            continue
        _add(w.get("model"), w.get("reasoning_effort"), str(w.get("role") or "worker"))

    for sub in helper_subagents:
        _add(sub.get("model"), sub.get("reasoning_effort"), str(sub.get("agent_role") or "subagent"))

    for sub in session_subagents:
        _add(sub.get("model"), sub.get("reasoning_effort"), str(sub.get("agent_role") or "subagent"))

    return [
        {
            "model": m,
            "reasoning_effort": e,
            "count": n,
            "roles": sorted(roles_by_key[(m, e)]),
        }
        for (m, e), n in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0][0], item[0][1] or ""),
        )
    ]


def _enrich_snapshot_agents(
    database: Database,
    dashboard_run: dict[str, Any],
    workers: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Read-only enrichment of workers, L2+ helper subagents, unregistered session subagents, and model summary."""
    stored_subagents = database.list_worker_subagents(dashboard_run["id"])
    stored_by_worker: dict[str, dict[str, dict[str, Any]]] = {}
    for row in stored_subagents:
        parent_wid = row.get("parent_worker_id")
        tid = row.get("thread_id")
        if parent_wid and tid:
            stored_by_worker.setdefault(str(parent_wid), {})[str(tid)] = row

    session_id = dashboard_run.get("session_id")
    if session_id:
        root_thread = codex_bridge.get_thread_meta(session_id)
        live_tree = codex_bridge.get_session_subagents(session_id)
    else:
        root_thread = None
        live_tree = []

    by_id: dict[str, dict[str, Any]] = {}
    depth1_by_path: dict[str, list[dict[str, Any]]] = {}
    children_by_parent: dict[str, list[dict[str, Any]]] = {}
    for node in live_tree:
        tid = node["thread_id"]
        by_id[tid] = node
        if node.get("depth") == 1 and node.get("agent_path"):
            depth1_by_path.setdefault(node["agent_path"], []).append(node)
        parent_tid = node.get("parent_thread_id")
        if parent_tid:
            children_by_parent.setdefault(parent_tid, []).append(node)

    enriched_workers: list[dict[str, Any]] = []
    helper_subagents: list[dict[str, Any]] = []
    matched_worker_thread_ids: set[str] = set()

    for w in workers:
        matched_live, codex_thread_id, is_root_worker = _match_worker_live_node(
            w,
            session_id=session_id,
            root_thread=root_thread,
            by_id=by_id,
            depth1_by_path=depth1_by_path,
        )
        if matched_live is not None and codex_thread_id:
            matched_worker_thread_ids.add(codex_thread_id)
        model, reasoning_effort, model_provider, model_source = _resolve_live_model_fields(matched_live, w)
        agent_nickname = matched_live.get("agent_nickname") if matched_live is not None else None

        is_root_or_session = is_root_worker or (session_id is not None and codex_thread_id == session_id)
        if is_root_or_session:
            worker_subagents: list[dict[str, Any]] = []
        else:
            worker_subagents = _collect_worker_subagents(
                w["id"],
                w["name"],
                codex_thread_id,
                children_by_parent=children_by_parent,
                stored_for_worker=stored_by_worker.get(w["id"], {}),
            )
            helper_subagents.extend(worker_subagents)

        enriched_workers.append({
            **w,
            "codex_thread_id": codex_thread_id,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "model_provider": model_provider,
            "agent_nickname": agent_nickname,
            "model_source": model_source,
            "subagents": worker_subagents,
        })

    session_subagents: list[dict[str, Any]] = []
    if session_id and live_tree:
        session_subagents = _collect_session_subagents(
            database,
            dashboard_run,
            session_id,
            live_tree,
            children_by_parent=children_by_parent,
            matched_worker_thread_ids=matched_worker_thread_ids,
        )

    model_summary = _build_model_summary(
        dashboard_run, enriched_workers, helper_subagents, session_subagents
    )
    return enriched_workers, helper_subagents, session_subagents, model_summary


def create_app(
    settings: Settings | None = None,
    *,
    db: Database | None = None,
    start_watchdog: bool = True,
    keepalive_sec: float = 15.0,
    manage_pid_file: bool = False,
    codex_watch_interval_sec: float | None = None,
) -> Starlette:
    """Build the Starlette HTTP/SSE application for tri-orchestrator."""
    cfg = settings or get_settings()
    owns_db = db is None
    watch_interval = (
        (1.0 if start_watchdog else 0.0)
        if codex_watch_interval_sec is None
        else float(codex_watch_interval_sec)
    )

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        started_at = time.time()
        cfg.ensure_data_dir()
        if owns_db:
            require_runtime_schema(cfg.db_path)
        database = db if db is not None else Database.from_settings(cfg)
        if owns_db:
            database.reconcile_codex_projects()
        board = BoardService(database, cfg)
        workspace_mgr = WorkspaceManager(database, cfg)
        event_bus = EventBus()
        dispatcher = RpcDispatcher(database, board, workspace_mgr, event_bus)
        dispatcher.native_actions.recover()
        watchdog = StallWatchdog(database, board, event_bus)
        shutdown_event = asyncio.Event()

        app.state.settings = cfg
        app.state.started_at = started_at
        app.state.db = database
        app.state.board = board
        app.state.workspace_mgr = workspace_mgr
        app.state.event_bus = event_bus
        app.state.dispatcher = dispatcher
        app.state.inbox = dispatcher.inbox
        app.state.workflow = dispatcher.workflow
        app.state.inbox_wait_tasks = set()
        app.state.watchdog = watchdog
        app.state.shutdown_event = shutdown_event

        if manage_pid_file:
            _write_pid_file(cfg, started_at=started_at)

        watchdog_task: asyncio.Task[None] | None = None
        if start_watchdog:
            watchdog_task = asyncio.create_task(watchdog.run())

        async def _watch_codex_catalog() -> None:
            last_fp = await asyncio.to_thread(codex_bridge.get_codex_state_fingerprint)
            while not shutdown_event.is_set():
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=watch_interval)
                    break
                except asyncio.TimeoutError:
                    pass
                try:
                    current_fp = await asyncio.to_thread(codex_bridge.get_codex_state_fingerprint)
                except Exception:
                    logger.exception("Failed to inspect Codex catalog fingerprint")
                    continue
                if current_fp != last_fp:
                    last_fp = current_fp
                    codex_bridge.invalidate_catalog_cache()
                    event_bus.publish(
                        "codex_catalog_updated",
                        {"changed_at": time.time()},
                        run_id=None,
                    )

        codex_watch_task: asyncio.Task[None] | None = None
        if watch_interval > 0:
            codex_watch_task = asyncio.create_task(_watch_codex_catalog())

        try:
            yield
        finally:
            shutdown_event.set()
            waiting = tuple(app.state.inbox_wait_tasks)
            for task in waiting:
                task.cancel()
            # A cancelled short DB read must finish before its connection is closed.
            await asyncio.gather(*waiting, return_exceptions=True)
            watchdog.stop()
            for bg_task in (watchdog_task, codex_watch_task):
                if bg_task is not None:
                    bg_task.cancel()
                    try:
                        await bg_task
                    except asyncio.CancelledError:
                        pass
            if manage_pid_file:
                _remove_pid_file_if_owner(cfg)
            if owns_db:
                database.close()

    # ------------------------------------------------------------------
    # Static Dashboard Routes
    # ------------------------------------------------------------------

    def _serve_web_file(filename: str, media_type: str) -> Response:
        target = (WEB_DIR / filename).resolve(strict=False)
        if not target.is_file():
            return _json_error(404, f"Static asset not found: {filename}", error_type="FileNotFoundError")
        return Response(
            content=target.read_bytes(),
            media_type=media_type,
            headers={"Cache-Control": "no-cache"},
        )

    async def web_index(request: Request) -> Response:
        del request
        return _serve_web_file("index.html", "text/html; charset=utf-8")

    async def web_styles(request: Request) -> Response:
        del request
        return _serve_web_file("styles.css", "text/css; charset=utf-8")

    async def web_app_js(request: Request) -> Response:
        del request
        return _serve_web_file("app.js", "application/javascript; charset=utf-8")

    # ------------------------------------------------------------------
    # Health & RPC Bridge
    # ------------------------------------------------------------------

    async def api_health(request: Request) -> JSONResponse:
        state = request.app.state
        return JSONResponse(
            {
                "status": "ok",
                "ok": True,
                "pid": os.getpid(),
                "host": state.settings.host,
                "port": state.settings.port,
                "data_dir": str(state.settings.data_dir),
                "started_at": state.started_at,
                "sse_subscribers": state.event_bus.subscriber_count(),
                "software_version": __version__,
                "schema_version": SCHEMA_VERSION,
                "capabilities": [
                    "durable_inbox_v1",
                    "workflow_gates_v1",
                    "async_inbox_wait_v1",
                    "workflow_modes_v2",
                    "native_actions_v1",
                    "verification_receipts_v1",
                    "plan_admission_v1",
                    "compact_handoff_v1",
                    "recipient_groups_v1",
                    "progress_classification_v1",
                    "workflow_reviews_v1",
                    "macro_rpc_v1",
                    "compact_board_v1",
                    "workflow5_direct_spawn_v1",
                    "session_hooks_v1",
                    "agent_model_tracking_v1",
                    "codex_catalog_sync_v1",
                ],
            }
        )

    async def api_tools(request: Request) -> JSONResponse:
        dispatcher: RpcDispatcher = request.app.state.dispatcher
        return JSONResponse({"tools": dispatcher.list_tools()})

    async def api_rpc(request: Request) -> Response:
        dispatcher: RpcDispatcher = request.app.state.dispatcher
        try:
            body = await _parse_json_body(request)
            tool_name = body.get("tool") or body.get("name")
            if not isinstance(tool_name, str) or not tool_name.strip():
                raise ValueError("Missing required 'tool' field in /api/rpc request")
            name = tool_name.strip()
            arguments = dispatcher.validate_arguments(name, body.get("arguments"))
            if name == "inbox_wait":
                result = await request_wait(request, arguments)
                if result is None:
                    return Response(status_code=499)
            else:
                result = await asyncio.to_thread(dispatcher.dispatch, name, arguments)
            return JSONResponse({"ok": True, "tool": tool_name.strip(), "result": result})
        except Exception as exc:
            return _map_exception_to_response(exc)

    # ------------------------------------------------------------------
    # Projects, Runs, Snapshot, Export
    # ------------------------------------------------------------------

    def _dashboard_projects(database: Database, include_merged: bool) -> list[dict[str, Any]]:
        activity = database.dashboard_project_activity()
        return [
            {
                **project,
                "display_name": project["codex_project_name"] or project["name"],
                **{key: counts.get(key, 0) for key in ("active_run_count", "active_session_count", "total_run_count")},
            }
            for project in database.list_projects()
            if include_merged or project["canonical_project_id"] is None
            for counts in [activity.get(project["canonical_project_id"] or project["id"], {})]
        ]

    def _concurrent_risk(canonical_id: str, activity: dict[str, dict[str, Any]]) -> dict[str, Any]:
        counts = activity.get(canonical_id, {})
        active_count = counts.get("active_run_count", 0)
        return {
            "canonical_project_id": canonical_id,
            "concurrent_project_active_count": active_count,
            "concurrent_local_write_risk": active_count > 1 and bool(counts["has_local_writer"]),
        }

    def _enrich_runs(
        database: Database, runs: list[dict[str, Any]], activity: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not runs:
            return []
        projects = {project["id"]: project for project in database.list_projects()}
        threads = codex_bridge.get_threads_meta(run["session_id"] for run in runs if run["session_id"])
        enriched = []
        for run in runs:
            project = projects[run["project_id"]]
            canonical = projects[project["canonical_project_id"] or project["id"]]
            thread = threads.get(run["session_id"]) or {}
            thread_name = thread.get("title")
            enriched.append({
                **run,
                **_concurrent_risk(canonical["id"], activity),
                "codex_thread_name": thread_name,
                "codex_project_name": canonical["codex_project_name"] or project["codex_project_name"]
                or thread.get("codex_project_name"),
                "session_display_name": thread_name or (run["title"] if run["session_id"] else None),
                "codex_model": thread.get("model"),
                "codex_reasoning_effort": thread.get("reasoning_effort"),
                "codex_model_provider": thread.get("model_provider"),
            })
        return enriched

    def _build_codex_catalog_projection(
        database: Database, *, mode: str | None = None
    ) -> dict[str, Any] | None:
        raw = codex_bridge.get_codex_catalog()
        if raw is None:
            return None

        effective_mode = None if mode in {None, "all"} else mode
        db_projects = database.list_projects()
        activity = database.dashboard_project_activity()
        db_by_root: dict[str, dict[str, Any]] = {}
        db_with_keys: list[tuple[str, dict[str, Any]]] = []
        for proj in db_projects:
            key = codex_bridge._path_key(proj["root_path"])
            db_with_keys.append((key, proj))
            existing = db_by_root.get(key)
            if existing is None or (
                existing["canonical_project_id"] is not None
                and proj["canonical_project_id"] is None
            ):
                db_by_root[key] = proj

        all_runs = database.list_runs(limit=None)
        enriched_runs = _enrich_runs(database, all_runs, activity)
        runs_by_session: dict[str, list[dict[str, Any]]] = {}
        for run in enriched_runs:
            sid = run.get("session_id")
            if sid:
                runs_by_session.setdefault(str(sid), []).append(run)

        projected_projects: list[dict[str, Any]] = []
        global_active_runs: list[dict[str, Any]] = []
        concurrent_risks: list[dict[str, Any]] = []

        for cp in raw["projects"]:
            root_key = codex_bridge._path_key(cp["root_path"])
            root_prefix = root_key.rstrip("/") + "/"
            db_proj = db_by_root.get(root_key)
            proj_id = db_proj["id"] if db_proj else f"codex:{cp['codex_project_id']}"
            db_project_id = db_proj["id"] if db_proj else None

            matching_activity_ids: set[str] = {
                str(proj["canonical_project_id"] or proj["id"])
                for p_key, proj in db_with_keys
                if p_key == root_key or p_key.startswith(root_prefix)
            }

            threads_out: list[dict[str, Any]] = []
            proj_all_runs: list[dict[str, Any]] = []
            proj_active_runs_all_modes = 0
            proj_active_sessions: set[str] = set()
            proj_total_runs = 0

            for ct in cp["threads"]:
                raw_thread_runs = sorted(
                    runs_by_session.get(ct["session_id"], []),
                    key=lambda r: (r.get("session_seq") or 0, r.get("updated_at") or 0.0),
                    reverse=True,
                )
                decorated_all: list[dict[str, Any]] = []
                for r in raw_thread_runs:
                    orig_canonical = r.get("canonical_project_id")
                    if r.get("status") == "active" and orig_canonical:
                        matching_activity_ids.add(str(orig_canonical))
                    copy_r = {
                        **r,
                        "canonical_project_id": proj_id,
                        "codex_project_id": cp["codex_project_id"],
                        "codex_project_name": cp["name"],
                        "codex_thread_name": ct["title"],
                        "session_display_name": ct["title"],
                        "codex_model": ct["model"] if ct["model"] is not None else r.get("codex_model"),
                        "codex_reasoning_effort": (
                            ct["reasoning_effort"]
                            if ct["reasoning_effort"] is not None
                            else r.get("codex_reasoning_effort")
                        ),
                        "codex_model_provider": (
                            ct["model_provider"]
                            if ct["model_provider"] is not None
                            else r.get("codex_model_provider")
                        ),
                    }
                    decorated_all.append(copy_r)

                active_in_thread = [r for r in decorated_all if r.get("status") == "active"]
                active_count = len(active_in_thread)
                if active_count > 0:
                    proj_active_sessions.add(ct["session_id"])
                    proj_active_runs_all_modes += active_count
                global_active_runs.extend(active_in_thread)

                visible_runs = (
                    decorated_all
                    if effective_mode is None
                    else [r for r in decorated_all if r.get("mode") == effective_mode]
                )
                proj_total_runs += len(visible_runs)
                proj_all_runs.extend(decorated_all)

                threads_out.append({
                    **ct,
                    "project_id": proj_id,
                    "active_count": active_count,
                    "runs": visible_runs,
                })

            has_local_writer = any(
                bool(activity.get(cid, {}).get("has_local_writer"))
                for cid in matching_activity_ids
            )
            concurrent_risk = proj_active_runs_all_modes > 1 and has_local_writer
            for r in proj_all_runs:
                r["concurrent_project_active_count"] = proj_active_runs_all_modes
                r["concurrent_local_write_risk"] = concurrent_risk
            if concurrent_risk:
                concurrent_risks.append({
                    "canonical_project_id": proj_id,
                    "concurrent_project_active_count": proj_active_runs_all_modes,
                    "concurrent_local_write_risk": True,
                })

            projected_projects.append({
                "id": proj_id,
                "codex_project_id": cp["codex_project_id"],
                "db_project_id": db_project_id,
                "name": cp["name"],
                "display_name": cp["name"],
                "root_path": cp["root_path"],
                "position": cp["position"],
                "is_git": cp["is_git"],
                "project_kind": "workspace",
                "canonical_project_id": None,
                "active_run_count": proj_active_runs_all_modes,
                "active_session_count": len(proj_active_sessions),
                "total_run_count": proj_total_runs,
                "thread_count": len(threads_out),
                "threads": threads_out,
            })

        global_active_runs.sort(
            key=lambda r: (r.get("updated_at") or 0.0, r.get("created_at") or 0.0),
            reverse=True,
        )
        return {
            "available": True,
            "projects": projected_projects,
            "global_active_runs": global_active_runs,
            "concurrent_risks": concurrent_risks,
        }

    async def api_codex_catalog(request: Request) -> JSONResponse:
        database: Database = request.app.state.db
        mode = request.query_params.get("mode") or "all"
        if mode != "all" and mode not in VALID_RUN_MODES:
            return _json_error(400, f"Invalid mode: {mode!r}", error_type="ValueError")

        def build() -> dict[str, Any]:
            projection = _build_codex_catalog_projection(database, mode=mode)
            if projection is None:
                return {
                    "available": False,
                    "projects": [],
                    "global_active_runs": [],
                    "concurrent_risks": [],
                }
            return projection

        return JSONResponse(await asyncio.to_thread(build))

    async def api_projects(request: Request) -> JSONResponse:
        database: Database = request.app.state.db
        include_merged = request.query_params.get("include_merged", "1")
        if include_merged not in {"0", "1"}:
            return _json_error(400, "include_merged must be 0 or 1", error_type="ValueError")

        def build() -> dict[str, Any]:
            projects = _dashboard_projects(database, include_merged == "1")
            payload: dict[str, Any] = {"projects": projects}
            codex_catalog = _build_codex_catalog_projection(database)
            if codex_catalog is not None:
                payload["codex_catalog"] = codex_catalog
            return payload

        return JSONResponse(await asyncio.to_thread(build))

    async def api_runs(request: Request) -> JSONResponse:
        database: Database = request.app.state.db
        qp = request.query_params
        project_id = qp.get("project_id") or None
        status = qp.get("status") or None
        mode = qp.get("mode") or None
        raw_limit = qp.get("limit")
        try:
            limit = int(raw_limit) if raw_limit else 100
        except ValueError:
            return _json_error(400, f"Invalid limit: {raw_limit!r}", error_type="ValueError")

        def build() -> dict[str, Any]:
            effective_mode = None if mode == "all" else mode
            if project_id and project_id.startswith("codex:"):
                projection = _build_codex_catalog_projection(database, mode=mode or "all")
                matched: list[dict[str, Any]] = []
                if projection is not None:
                    for proj in projection["projects"]:
                        if proj["id"] == project_id:
                            for thread in proj["threads"]:
                                for run in thread["runs"]:
                                    if status is not None and run.get("status") != status:
                                        continue
                                    matched.append(run)
                            break
                matched.sort(
                    key=lambda r: (r.get("updated_at") or 0.0, r.get("created_at") or 0.0),
                    reverse=True,
                )
                return {"runs": matched[: max(1, limit)]}

            runs = database.list_runs(
                project_id=project_id, status=status, mode=effective_mode,
                limit=limit, include_merged=True,
            )
            enriched = _enrich_runs(database, runs, database.dashboard_project_activity())
            if project_id is not None:
                projection = _build_codex_catalog_projection(database, mode=mode or "all")
                if projection is not None:
                    existing_ids = {r["id"] for r in enriched}
                    for proj in projection["projects"]:
                        if proj["id"] == project_id or proj.get("db_project_id") == project_id:
                            for thread in proj["threads"]:
                                for run in thread["runs"]:
                                    if run["id"] in existing_ids:
                                        continue
                                    if status is not None and run.get("status") != status:
                                        continue
                                    enriched.append(run)
                                    existing_ids.add(run["id"])
                            enriched.sort(
                                key=lambda r: (r.get("updated_at") or 0.0, r.get("created_at") or 0.0),
                                reverse=True,
                            )
                            enriched = enriched[: max(1, limit)]
                            break
            return {"runs": enriched}

        return JSONResponse(await asyncio.to_thread(build))

    async def api_sessions(request: Request) -> JSONResponse:
        database: Database = request.app.state.db
        qp = request.query_params
        project_id = qp.get("project_id") or "all"
        status = qp.get("status", "all")
        mode = qp.get("mode", "all")
        if status not in {"active", "all"} or mode not in VALID_RUN_MODES | {"all"}:
            return _json_error(400, "Invalid session status or mode", error_type="ValueError")

        def build() -> dict[str, Any]:
            project = database.get_project(project_id) if project_id != "all" else None
            selected = database.list_runs(
                project_id=(project["canonical_project_id"] or project_id) if project else (
                    None if project_id == "all" else project_id
                ),
                status=None if status == "all" else status,
                mode=None if mode == "all" else mode,
                include_merged=True,
                limit=None,
            )
            global_active = database.list_runs(status="active", limit=None) if project_id != "all" or mode != "all" else [
                run for run in selected if run["status"] == "active"
            ]
            activity = database.dashboard_project_activity()
            by_id = {run["id"]: run for run in (*selected, *global_active)}
            enriched = {run["id"]: run for run in _enrich_runs(database, list(by_id.values()), activity)}
            groups: dict[tuple[str, str | None], dict[str, Any]] = {}
            for raw in selected:
                run = enriched[raw["id"]]
                canonical_id, session_id = run["canonical_project_id"], run["session_id"]
                group = groups.setdefault((canonical_id, session_id), {
                    "project_id": canonical_id,
                    "canonical_project_id": canonical_id,
                    "session_id": session_id if session_id is not None else "unbound",
                    "codex_thread_name": run["codex_thread_name"],
                    "session_display_name": run["session_display_name"],
                    "codex_model": run.get("codex_model"),
                    "codex_reasoning_effort": run.get("codex_reasoning_effort"),
                    "codex_model_provider": run.get("codex_model_provider"),
                    "runs": [],
                    "active_count": 0,
                })
                group["runs"].append(run)
                group["active_count"] += run["status"] == "active"
            sessions = sorted(
                groups.values(),
                key=lambda group: (group["active_count"] > 0, group["runs"][0]["updated_at"]),
                reverse=True,
            )
            for group in sessions:
                group["runs"].sort(key=lambda run: (run["session_seq"], run["updated_at"]), reverse=True)
                model_run = next((r for r in group["runs"] if r.get("codex_model") is not None), group["runs"][0])
                group["codex_model"] = model_run.get("codex_model")
                group["codex_reasoning_effort"] = model_run.get("codex_reasoning_effort")
                group["codex_model_provider"] = model_run.get("codex_model_provider")
            payload: dict[str, Any] = {
                "sessions": sessions,
                "global_active_runs": [enriched[run["id"]] for run in global_active],
                "concurrent_risks": [
                    risk for canonical_id in sorted(activity)
                    if (risk := _concurrent_risk(canonical_id, activity))["concurrent_local_write_risk"]
                ],
            }
            codex_catalog = _build_codex_catalog_projection(database, mode=mode)
            if codex_catalog is not None:
                payload["codex_catalog"] = codex_catalog
            return payload

        return JSONResponse(await asyncio.to_thread(build))

    async def api_run(request: Request) -> JSONResponse:
        database: Database = request.app.state.db
        run_id = request.path_params["run_id"]
        try:
            run = await asyncio.to_thread(database.get_run, run_id)
            if run is None:
                raise NotFoundError(f"Run not found: {run_id}")
            project = await asyncio.to_thread(database.get_project, run["project_id"])
            return JSONResponse({"run": run, "project": project})
        except Exception as exc:
            return _map_exception_to_response(exc)

    def _build_run_snapshot_sync(state: Any, run_id: str) -> dict[str, Any]:
        database: Database = state.db
        board: BoardService = state.board

        run = database.get_run(run_id)
        if run is None:
            raise NotFoundError(f"Run not found: {run_id}")
        project = database.get_project(run["project_id"])
        [dashboard_run] = _enrich_runs(database, [run], database.dashboard_project_activity())
        packets = database.list_task_packets(run_id)
        latest_packet = packets[-1] if packets else None
        status_summary = board.board_status(run_id)
        messages = database.query_board_messages(run_id, since_seq=0, limit=10000)

        # Disk checks are explicitly requested through workspace diff/status.
        # An omitted count means not inspected, never a fabricated clean result.
        workspaces = status_summary["workspaces"]
        enriched_workers, helper_subagents, session_subagents, model_summary = _enrich_snapshot_agents(
            database, dashboard_run, status_summary["workers"]
        )

        channels = [ch["channel"] for ch in status_summary["channels"]]
        for default_ch in ("main", "human"):
            if default_ch not in channels:
                channels.append(default_ch)

        return {
            "run": dashboard_run,
            "session_turns": database.list_run_session_turns(run),
            "project": project,
            "task_packet": latest_packet,
            "task_packets": packets,
            "workers": enriched_workers,
            "helper_subagents": helper_subagents,
            "session_subagents": session_subagents,
            "model_summary": model_summary,
            "workspaces": workspaces,
            "messages": messages,
            "channels": channels,
            "latest_seq": status_summary["latest_seq"],
            "stalled_workers": status_summary["stalled_workers"],
            "control_requests": status_summary["control_requests"],
            "workflow": status_summary["workflow"],
            "inbox": status_summary["inbox"],
            "native_actions": status_summary["native_actions"],
            "resume_context": status_summary["resume_context"],
            "plan_readiness": status_summary["plan_readiness"],
            "progress_status": status_summary["progress_status"],
        }

    async def api_run_snapshot(request: Request) -> JSONResponse:
        run_id = request.path_params["run_id"]
        try:
            snapshot = await asyncio.to_thread(
                _build_run_snapshot_sync, request.app.state, run_id
            )
            return JSONResponse(snapshot)
        except Exception as exc:
            return _map_exception_to_response(exc)

    async def api_run_export(request: Request) -> Response:
        run_id = request.path_params["run_id"]
        fmt = (request.query_params.get("format") or "md").strip().lower()
        if fmt not in {"md", "json"}:
            return _json_error(
                400,
                f"Unsupported export format {fmt!r}; expected 'md' or 'json'",
                error_type="ValueError",
            )
        try:
            snapshot = await asyncio.to_thread(
                _build_run_snapshot_sync, request.app.state, run_id
            )
        except Exception as exc:
            return _map_exception_to_response(exc)

        short = _safe_filename_segment(run_id[:16])
        if fmt == "json":
            payload = {
                "exported_at_utc": _format_utc_iso(time.time()),
                **snapshot,
            }
            return Response(
                content=json.dumps(payload, ensure_ascii=False, indent=2),
                media_type="application/json; charset=utf-8",
                headers={
                    "Content-Disposition": f'attachment; filename="tri-orchestrator-run-{short}.json"'
                },
            )

        md_text = build_markdown_report(snapshot)
        return Response(
            content=md_text,
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="tri-orchestrator-run-{short}.md"'
            },
        )

    # ------------------------------------------------------------------
    # SSE Live Stream
    # ------------------------------------------------------------------

    async def api_events(request: Request) -> StreamingResponse:
        event_bus: EventBus = request.app.state.event_bus
        shutdown_event: asyncio.Event = request.app.state.shutdown_event
        run_id = request.query_params.get("run_id") or None
        sub_id, queue = event_bus.subscribe(run_id=run_id)

        async def event_stream() -> AsyncIterator[str]:
            try:
                yield "retry: 2000\n\n: connected\n\n"
                while not shutdown_event.is_set():
                    if await request.is_disconnected():
                        break
                    get_task = asyncio.create_task(queue.get())
                    stop_task = asyncio.create_task(shutdown_event.wait())
                    try:
                        done, pending = await asyncio.wait(
                            {get_task, stop_task},
                            timeout=keepalive_sec,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        for p in pending:
                            p.cancel()
                        if stop_task in done or shutdown_event.is_set():
                            break
                        if get_task in done:
                            item = get_task.result()
                            data = item["data"]
                            if run_id is None and item["event"] == "run_updated" and isinstance(data.get("run"), dict):
                                data = {**data, "run": {
                                    key: value for key, value in data["run"].items()
                                    if key in {"id", "project_id", "mode", "title", "status", "session_id",
                                               "session_seq", "created_at", "updated_at", "workflow_version",
                                               "trigger_model", "trigger_reasoning_effort"}
                                }}
                            data_json = json.dumps(data, ensure_ascii=False)
                            yield f"event: {item['event']}\ndata: {data_json}\n\n"
                        else:
                            yield ": keepalive\n\n"
                    except asyncio.CancelledError:
                        get_task.cancel()
                        stop_task.cancel()
                        raise
            finally:
                event_bus.unsubscribe(sub_id)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream; charset=utf-8",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ------------------------------------------------------------------
    # Safe Actions & Workspace Diff
    # ------------------------------------------------------------------

    async def api_human_message(request: Request) -> JSONResponse:
        run_id = request.path_params["run_id"]
        dispatcher: RpcDispatcher = request.app.state.dispatcher
        board: BoardService = request.app.state.board
        try:
            body = await _parse_json_body(request)
            raw_body = body.get("body_md")
            if raw_body is None:
                raw_body = body.get("message") if body.get("message") is not None else body.get("text")
            if not isinstance(raw_body, str) or not raw_body.strip():
                raise ValueError("Human message 'body_md' must be a non-empty string")

            channel = str(body.get("channel") or "human").strip() or "human"
            summary = str(body.get("summary") or "").strip()
            mentions = body.get("mentions")
            if mentions is not None and not isinstance(mentions, list):
                raise ValueError("'mentions' must be a list of strings")

            pointer = await asyncio.to_thread(
                dispatcher.dispatch,
                "board_post",
                {
                    "run_id": run_id,
                    "sender": "human",
                    "sender_role": "human",
                    "channel": channel,
                    "msg_type": "human",
                    "summary": summary,
                    "body_md": raw_body,
                    "mentions": mentions,
                },
            )
            full_msg = await asyncio.to_thread(board.get_message, int(pointer["seq"]))
            return JSONResponse(
                {
                    "ok": True,
                    "seq": pointer["seq"],
                    "pointer": pointer,
                    "message": full_msg,
                }
            )
        except Exception as exc:
            return _map_exception_to_response(exc)

    async def api_workspace_diff(request: Request) -> JSONResponse:
        workspace_id = request.path_params["workspace_id"]
        database: Database = request.app.state.db
        workspace_mgr: WorkspaceManager = request.app.state.workspace_mgr
        try:
            ws = await asyncio.to_thread(database.get_workspace, workspace_id)
            if ws is None:
                raise NotFoundError(f"Workspace not found: {workspace_id}")
            if ws["status"] == "cleaned" and not Path(ws["path"]).exists():
                return JSONResponse(
                    {
                        "workspace_id": workspace_id,
                        "mode": ws["mode"],
                        "status": "cleaned",
                        "branch_name": ws["branch_name"],
                        "base_commit": ws["base_commit"],
                        "files": [],
                        "file_statuses": [],
                        "status_files": [],
                        "diff": "",
                        "commits_ahead": 0,
                        "dirty": False,
                        "unmerged_count": 0,
                        "summary": "Workspace cleaned",
                    }
                )
            diff_data = await asyncio.to_thread(workspace_mgr.workspace_diff, workspace_id)
            enriched = {
                **diff_data,
                "status_files": diff_data.get("file_statuses", []),
                "unmerged_count": len(diff_data.get("files") or []),
            }
            return JSONResponse(enriched)
        except Exception as exc:
            return _map_exception_to_response(exc)

    async def api_workspace_merge_preview(request: Request) -> JSONResponse:
        workspace_id = request.path_params["workspace_id"]
        dispatcher: RpcDispatcher = request.app.state.dispatcher
        try:
            body = await _parse_json_body(request, allow_empty=True)
            result = await asyncio.to_thread(
                dispatcher.dispatch,
                "workspace_merge_preview",
                {
                    "workspace_id": workspace_id,
                    "target_branch": body.get("target_branch"),
                },
            )
            return JSONResponse(result)
        except Exception as exc:
            return _map_exception_to_response(exc)

    async def api_worker_status(request: Request) -> JSONResponse:
        worker_id = request.path_params["worker_id"]
        state = request.app.state
        try:
            body = await _parse_json_body(request)
            next_status = body.get("status")
            if not isinstance(next_status, str) or next_status.strip() not in ALLOWED_MANUAL_WORKER_STATUSES:
                raise ValueError("Worker control only allows 'stopped' or 'killed'")
            control = await asyncio.to_thread(
                state.db.request_worker_control, worker_id, next_status.strip()
            )
            state.event_bus.publish(
                "worker_control_updated",
                {"run_id": control["run_id"], "control_request": control},
                run_id=control["run_id"],
            )
            return JSONResponse(
                {"ok": True, "control_request": control, "workspace_preserved": True},
                status_code=202,
            )
        except Exception as exc:
            return _map_exception_to_response(exc)

    def _cleanup_workspace_guarded_sync(state: Any, workspace_id: str) -> dict[str, Any]:
        database: Database = state.db
        workspace_mgr: WorkspaceManager = state.workspace_mgr
        event_bus: EventBus = state.event_bus

        ws = database.get_workspace(workspace_id)
        if ws is None:
            raise NotFoundError(f"Workspace not found: {workspace_id}")
        if ws["status"] != "merged":
            raise WorkspaceCleanupGuardError(
                f"Refusing cleanup for workspace {workspace_id}: status is {ws['status']!r}, must be 'merged'"
            )
        ws_status = workspace_mgr.workspace_status(workspace_id)
        if ws_status.get("dirty") or ws_status.get("has_uncommitted_changes"):
            raise WorkspaceCleanupGuardError(
                f"Refusing cleanup for workspace {workspace_id}: workspace has uncommitted changes ({ws_status.get('uncommitted_files')})"
            )

        res = workspace_mgr.workspace_cleanup(workspace_id, force=False)
        updated_ws = database.get_workspace(workspace_id)
        if updated_ws is not None:
            event_bus.publish(
                "workspace_updated",
                {"run_id": updated_ws["run_id"], "workspace": updated_ws},
                run_id=updated_ws["run_id"],
            )
        return {
            "ok": True,
            "workspace": updated_ws,
            "result": res,
            "message": "Merged clean workspace directory removed.",
        }

    async def api_workspace_cleanup(request: Request) -> JSONResponse:
        workspace_id = request.path_params["workspace_id"]
        try:
            # Consume optional JSON body safely, never honoring any force flag.
            await _parse_json_body(request, allow_empty=True)
            payload = await asyncio.to_thread(
                _cleanup_workspace_guarded_sync, request.app.state, workspace_id
            )
            return JSONResponse(payload)
        except Exception as exc:
            return _map_exception_to_response(exc)

    def _validate_and_open_explorer_sync(state: Any, workspace_id: str) -> dict[str, Any]:
        database: Database = state.db
        ws = database.get_workspace(workspace_id)
        if ws is None:
            raise NotFoundError(f"Workspace not found: {workspace_id}")
        if ws["status"] == "cleaned":
            raise WorkspaceConflictError(f"Workspace {workspace_id} has already been cleaned")

        raw_path = Path(ws["path"])
        ws_path = raw_path.resolve(strict=False)
        if not ws_path.exists() or not ws_path.is_dir():
            raise FileNotFoundError(f"Workspace directory does not exist: {ws_path}")

        run = database.get_run(ws["run_id"])
        project = database.get_project(run["project_id"]) if run else None
        roots = [project["root_path"], run["execution_root"]] if project else []
        manifest = ws["snapshot_manifest"]
        if isinstance(manifest, dict) and manifest.get("project_root"):
            # Existing workspaces may be anchored at an explicit source or Git toplevel.
            roots.append(manifest["project_root"])
        allowed_parents = [
            Path(root).resolve(strict=False) / WORKSPACES_DIRNAME for root in roots if root
        ]
        if not any(parent in ws_path.parents for parent in allowed_parents):
            raise PermissionError(
                f"Refusing to open non-workspace directory outside {WORKSPACES_DIRNAME}: {ws_path}"
            )

        _launch_explorer(ws_path)
        return {
            "ok": True,
            "workspace_id": workspace_id,
            "path": str(ws_path),
        }

    async def api_workspace_open_explorer(request: Request) -> JSONResponse:
        workspace_id = request.path_params["workspace_id"]
        try:
            await _parse_json_body(request, allow_empty=True)
            res = await asyncio.to_thread(
                _validate_and_open_explorer_sync, request.app.state, workspace_id
            )
            return JSONResponse(res)
        except PermissionError as exc:
            return _json_error(403, str(exc), error_type="PermissionError")
        except Exception as exc:
            return _map_exception_to_response(exc)

    # ------------------------------------------------------------------
    # Explicit Forbidden Dangerous Endpoints Guard
    # ------------------------------------------------------------------

    async def api_forbidden_operation(request: Request) -> JSONResponse:
        return _json_error(
            403,
            f"Operation {request.method} {request.url.path} is explicitly forbidden: git push, reset --hard, and unmerged force-delete are disabled.",
            error_type="ForbiddenOperationError",
        )

    routes = [
        Route("/", web_index, methods=["GET"]),
        Route("/boost", web_index, methods=["GET"]),
        Route("/teamwork", web_index, methods=["GET"]),
        Route("/swarm", web_index, methods=["GET"]),
        Route("/styles.css", web_styles, methods=["GET"]),
        Route("/app.js", web_app_js, methods=["GET"]),
        Route("/api/health", api_health, methods=["GET"]),
        Route("/api/tools", api_tools, methods=["GET"]),
        Route("/api/rpc", api_rpc, methods=["POST"]),
        Route("/api/projects", api_projects, methods=["GET"]),
        Route("/api/codex/catalog", api_codex_catalog, methods=["GET"]),
        Route("/api/sessions", api_sessions, methods=["GET"]),
        Route("/api/runs", api_runs, methods=["GET"]),
        Route("/api/runs/{run_id}", api_run, methods=["GET"]),
        Route("/api/runs/{run_id}/snapshot", api_run_snapshot, methods=["GET"]),
        Route("/api/runs/{run_id}/export", api_run_export, methods=["GET"]),
        Route("/api/runs/{run_id}/human-message", api_human_message, methods=["POST"]),
        Route("/api/events", api_events, methods=["GET"]),
        Route("/api/workspaces/{workspace_id}/diff", api_workspace_diff, methods=["GET"]),
        Route("/api/workspaces/{workspace_id}/merge-preview", api_workspace_merge_preview, methods=["POST"]),
        Route("/api/workspaces/{workspace_id}/cleanup", api_workspace_cleanup, methods=["POST"]),
        Route("/api/workspaces/{workspace_id}/open-explorer", api_workspace_open_explorer, methods=["POST"]),
        Route("/api/workers/{worker_id}/status", api_worker_status, methods=["POST"]),
        # Explicitly disallowed dangerous operations
        Route("/api/git/push", api_forbidden_operation, methods=["POST", "PUT", "DELETE"]),
        Route("/api/git/reset", api_forbidden_operation, methods=["POST", "PUT", "DELETE"]),
        Route("/api/workspaces/{workspace_id}/push", api_forbidden_operation, methods=["POST"]),
        Route("/api/workspaces/{workspace_id}/reset", api_forbidden_operation, methods=["POST"]),
        Route("/api/workspaces/{workspace_id}/force-delete", api_forbidden_operation, methods=["POST", "DELETE"]),
        Route("/api/workspaces/{workspace_id}", api_forbidden_operation, methods=["DELETE"]),
    ]

    return Starlette(debug=False, routes=routes, lifespan=lifespan)


def run_daemon(settings: Settings | None = None) -> None:
    """Start the singleton Uvicorn daemon server blocking until shutdown."""
    cfg = settings or get_settings()
    _configure_daemon_logging(cfg)
    app = create_app(cfg, start_watchdog=True, manage_pid_file=True)
    logger.info("Starting tri-orchestrator daemon at %s (data_dir=%s)", cfg.base_url, cfg.data_dir)
    config = uvicorn.Config(
        app,
        host=cfg.host,
        port=cfg.port,
        log_config=None,
        access_log=False,
        log_level="warning",
    )
    # Bind before lifespan can replace the live owner's PID file.
    family = socket.AF_INET6 if ":" in cfg.host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as listener:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((cfg.host, cfg.port))
        server = uvicorn.Server(config)
        server.run(sockets=[listener])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="tri-orchestrator singleton HTTP/SSE daemon")
    parser.add_argument("--host", default=None, help="Bind host (default TRI_ORCH_HOST or 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="Bind port (default TRI_ORCH_PORT or 18790)")
    parser.add_argument("--data-dir", default=None, help="Runtime data directory override")
    args = parser.parse_args(argv)

    cfg = get_settings(data_dir=args.data_dir)
    if args.host is not None or args.port is not None:
        cfg = Settings(
            host=args.host if args.host is not None else cfg.host,
            port=args.port if args.port is not None else cfg.port,
            data_dir=cfg.data_dir,
            stall_sec=cfg.stall_sec,
            watchdog_interval_sec=cfg.watchdog_interval_sec,
            port_range=cfg.port_range,
        )
    run_daemon(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
