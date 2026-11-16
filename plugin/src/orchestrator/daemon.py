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
from orchestrator import __version__
from orchestrator.config import Settings, get_settings
from orchestrator.db import Database, NotFoundError, SCHEMA_VERSION
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


def build_markdown_report(bundle: dict[str, Any]) -> str:
    """Render a localized Chinese Markdown run report while preserving raw dynamic values."""
    run = bundle["run"]
    project = bundle.get("project") or {}
    packets = bundle.get("task_packets") or []
    workers = bundle.get("workers") or []
    workspaces = bundle.get("workspaces") or []
    messages = bundle.get("messages") or []

    lines: list[str] = [
        f"# 三重编排器运行报告：{run.get('title') or run['id']}",
        "",
        f"- **运行 ID**：`{run['id']}`",
        f"- **编排模式**：`{_display_label(_RUN_MODE_LABELS, run.get('mode'))}`",
        f"- **运行状态**：`{_display_label(_RUN_STATUS_LABELS, run.get('status'))}`",
        f"- **所属项目**：`{project.get('name', '')}` (`{project.get('root_path', '')}`)",
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
        lines.append("| 名称 | 职责角色 | 状态 | 工作区 ID | 当前任务 | 最近心跳 (UTC) |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
        for w in workers:
            lines.append(
                f"| `{w['name']}` | `{_display_label(_ROLE_LABELS, w.get('role'))}` | "
                f"`{_display_label(_WORKER_STATUS_LABELS, w.get('status'))}` | "
                f"`{w.get('workspace_id') or '-'}` | {str(w.get('current_task') or '').replace('|', '/')} | "
                f"`{_format_utc_iso(w.get('last_heartbeat_at'))}` |"
            )
    else:
        lines.append("_暂无已注册成员。_")
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


def create_app(
    settings: Settings | None = None,
    *,
    db: Database | None = None,
    start_watchdog: bool = True,
    keepalive_sec: float = 15.0,
    manage_pid_file: bool = False,
) -> Starlette:
    """Build the Starlette HTTP/SSE application for tri-orchestrator."""
    cfg = settings or get_settings()
    owns_db = db is None

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        started_at = time.time()
        cfg.ensure_data_dir()
        database = db if db is not None else Database.from_settings(cfg)
        board = BoardService(database, cfg)
        workspace_mgr = WorkspaceManager(database, cfg)
        event_bus = EventBus()
        dispatcher = RpcDispatcher(database, board, workspace_mgr, event_bus)
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
            if watchdog_task is not None:
                watchdog_task.cancel()
                try:
                    await watchdog_task
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
                "capabilities": ["durable_inbox_v1", "workflow_gates_v1", "async_inbox_wait_v1", "workflow_modes_v2"],
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

    async def api_projects(request: Request) -> JSONResponse:
        database: Database = request.app.state.db
        projects = await asyncio.to_thread(database.list_projects)
        return JSONResponse({"projects": projects})

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
        runs = await asyncio.to_thread(
            database.list_runs,
            project_id=project_id,
            status=status,
            mode=mode,
            limit=limit,
        )
        return JSONResponse({"runs": runs})

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
        packets = database.list_task_packets(run_id)
        latest_packet = packets[-1] if packets else None
        status_summary = board.board_status(run_id)
        messages = database.query_board_messages(run_id, since_seq=0, limit=10000)

        # Disk checks are explicitly requested through workspace diff/status.
        # An omitted count means not inspected, never a fabricated clean result.
        workspaces = status_summary["workspaces"]
        workers = status_summary["workers"]

        channels = [ch["channel"] for ch in status_summary["channels"]]
        for default_ch in ("main", "human"):
            if default_ch not in channels:
                channels.append(default_ch)

        return {
            "run": run,
            "project": project,
            "task_packet": latest_packet,
            "task_packets": packets,
            "workers": workers,
            "workspaces": workspaces,
            "messages": messages,
            "channels": channels,
            "latest_seq": status_summary["latest_seq"],
            "stalled_workers": status_summary["stalled_workers"],
            "control_requests": status_summary["control_requests"],
            "workflow": status_summary["workflow"],
            "inbox": status_summary["inbox"],
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
                            data_json = json.dumps(item["data"], ensure_ascii=False)
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
        allowed = False
        if project is not None:
            proj_root = Path(project["root_path"]).resolve(strict=False)
            if ws_path.is_relative_to(proj_root / WORKSPACES_DIRNAME) or ws_path.parent.name == WORKSPACES_DIRNAME:
                allowed = True
        if not allowed and ws_path.parent.name == WORKSPACES_DIRNAME:
            allowed = True
        if not allowed:
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
