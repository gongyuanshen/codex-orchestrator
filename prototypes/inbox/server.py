"""Isolated inbox HTTP addon; never launches or changes the production daemon."""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from orchestrator.config import Settings, default_data_dir
from orchestrator.daemon import _json_error, _map_exception_to_response, _parse_json_body, create_app
from orchestrator.db import NotFoundError

from prototypes.inbox.store import InboxConflictError, InboxStore


INBOX_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "inbox_send",
        "description": "Commit one durable addressed message and board post; retry with the same key and payload is idempotent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"}, "sender": {"type": "string"},
                "recipient": {"type": "string"}, "key": {"type": "string"},
                "kind": {"type": "string", "enum": ["task", "rework", "review", "result", "notice"]},
                "summary": {"type": "string"}, "body_md": {"type": "string"},
            },
            "required": ["run_id", "sender", "recipient", "key", "kind", "summary", "body_md"],
            "additionalProperties": False,
        },
    },
    {
        "name": "inbox_get",
        "description": "Read a durable message and its receipt history by stable message ID.",
        "inputSchema": {
            "type": "object", "properties": {"message_id": {"type": "string"}},
            "required": ["message_id"], "additionalProperties": False,
        },
    },
    {
        "name": "inbox_ack",
        "description": "Record recipient acceptance or terminal receipt with nonempty evidence; acceptance is not completion.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string"}, "recipient": {"type": "string"},
                "status": {"type": "string", "enum": ["accepted", "completed", "failed"]},
                "evidence": {"type": "string", "minLength": 1},
            },
            "required": ["message_id", "recipient", "status", "evidence"],
            "additionalProperties": False,
        },
    },
    {
        "name": "inbox_wait",
        "description": "Wait asynchronously for durable nonterminal messages without acknowledging them. Accepted work requires reconciliation, not blind replay.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"}, "recipient": {"type": "string"},
                "timeout_ms": {"type": "integer", "minimum": 0, "maximum": 25000, "default": 20000},
            },
            "required": ["run_id", "recipient"], "additionalProperties": False,
        },
    },
]
_INBOX_NAMES = {tool["name"] for tool in INBOX_TOOL_DEFINITIONS}


def validate_isolation(settings: Settings) -> None:
    """Reject production aliases before create_app can open or migrate a database."""
    if not ipaddress.ip_address(settings.host).is_loopback:
        raise ValueError("Inbox prototype must bind to a loopback IP address")
    if settings.port == 18790:
        raise ValueError("Port 18790 belongs to production, not the inbox prototype")
    production_dirs = [default_data_dir()]
    if os.environ.get("TRI_ORCH_DATA_DIR", "").strip():
        production_dirs.append(Path(os.path.expandvars(os.environ["TRI_ORCH_DATA_DIR"])).expanduser())
    for production in production_dirs:
        if settings.data_dir.resolve() == production.resolve():
            raise ValueError("Inbox data directory aliases the production directory")
        prod_db = production / "state.db"
        if settings.db_path.resolve() == prod_db.resolve():
            raise ValueError("Inbox database aliases the production database")
        if settings.db_path.exists() and prod_db.exists() and settings.db_path.samefile(prod_db):
            raise ValueError("Inbox database is linked to the production database")


def _snapshot(state: Any, run_id: str, recipient: str) -> dict[str, Any]:
    run = state.db.get_run(run_id)
    if run is None:
        raise NotFoundError(f"Run not found: {run_id}")
    worker = state.db.get_worker_by_name(run_id, recipient)
    if worker is None:
        raise NotFoundError(f"Worker not found in run {run_id}: {recipient}")
    if run["status"] != "active":
        return {"status": "run_closed", "messages": []}
    if worker["status"] in {"stopped", "killed"}:
        return {"status": "worker_paused", "messages": []}
    if any(control["worker_id"] == worker["id"] and control["status"] == "pending"
           for control in state.db.list_worker_controls(run_id)):
        return {"status": "control_requested", "messages": []}
    messages = state.inbox.pending(run_id, recipient)
    return {"status": "messages" if messages else "timeout", "messages": messages}


async def wait_for_inbox(state: Any, run_id: str, recipient: str, timeout_ms: int = 20000) -> dict[str, Any]:
    """EventBus is a lossy signal; SQLite is checked initially, on wake, and at deadline."""
    for name, value in (("run_id", run_id), ("recipient", recipient)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a nonempty string")
    if type(timeout_ms) is not int or not 0 <= timeout_ms <= 25000:
        raise ValueError("timeout_ms must be an integer from 0 through 25000 (not bool)")
    task = asyncio.current_task()
    state.inbox_wait_tasks.add(task)
    bus = state.event_bus
    subscription, queue = bus.subscribe(run_id=run_id)
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    try:
        while True:
            query = asyncio.create_task(asyncio.to_thread(_snapshot, state, run_id, recipient))
            try:
                result = await asyncio.shield(query)
            except asyncio.CancelledError:
                # Finish the short read before lifespan may close its SQLite connection.
                await query
                raise
            if result["status"] != "timeout":
                return result
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return result
            try:
                await asyncio.wait_for(queue.get(), timeout=remaining)
            except TimeoutError:
                # A lost/overflowed event must not conceal committed work at the deadline.
                continue
    finally:
        bus.unsubscribe(subscription)
        state.inbox_wait_tasks.discard(task)


async def _until_disconnect(request: Request) -> None:
    while True:
        event = await request.receive()
        if event["type"] == "http.disconnect":
            return


async def _request_wait(request: Request, arguments: dict[str, Any]) -> dict[str, Any] | None:
    waiting = asyncio.create_task(wait_for_inbox(request.app.state, **arguments))
    disconnected = asyncio.create_task(_until_disconnect(request))
    try:
        done, _ = await asyncio.wait((waiting, disconnected), return_when=asyncio.FIRST_COMPLETED)
        if disconnected in done:
            disconnected.result()
            return None
        return waiting.result()
    finally:
        waiting.cancel()
        disconnected.cancel()
        await asyncio.gather(waiting, disconnected, return_exceptions=True)


def make_app(settings: Settings) -> Starlette:
    validate_isolation(settings)
    app = create_app(settings, start_watchdog=False, manage_pid_file=False)
    original_lifespan = app.router.lifespan_context
    original_rpc = next(route.endpoint for route in app.routes if route.path == "/api/rpc")

    @asynccontextmanager
    async def lifespan(application: Starlette):
        # Recheck path aliases at startup, not only when the application is constructed.
        validate_isolation(settings)
        async with original_lifespan(application):
            application.state.inbox = InboxStore(application.state.db, application.state.board)
            application.state.inbox.initialize()
            application.state.inbox_wait_tasks = set()
            try:
                yield
            finally:
                tasks = tuple(application.state.inbox_wait_tasks)
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def api_tools(request: Request) -> JSONResponse:
        return JSONResponse({"tools": request.app.state.dispatcher.list_tools() + INBOX_TOOL_DEFINITIONS})

    async def api_rpc(request: Request) -> Response:
        try:
            body = await _parse_json_body(request)
            name = body.get("tool") or body.get("name")
            if not isinstance(name, str) or name.strip() not in _INBOX_NAMES:
                return await original_rpc(request)
            name = name.strip()
            arguments = body.get("arguments", {})
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be a JSON object")
            state = request.app.state
            if name == "inbox_wait":
                result = await _request_wait(request, arguments)
                if result is None:
                    return Response(status_code=499)
            else:
                method = getattr(state.inbox, name.removeprefix("inbox_"))
                result = await asyncio.to_thread(method, **arguments)
                # Store methods return only after their atomic transaction commits.
                if name == "inbox_send" and not result["duplicate"]:
                    message = result["message"]
                    state.event_bus.publish("inbox_message", {"message": message}, run_id=message["run_id"])
                elif name == "inbox_ack":
                    state.event_bus.publish("inbox_receipt", {"message": result}, run_id=result["run_id"])
            return JSONResponse({"ok": True, "tool": name, "result": result})
        except InboxConflictError as exc:
            return _json_error(409, str(exc), error_type=type(exc).__name__)
        except Exception as exc:
            return _map_exception_to_response(exc)

    app.router.lifespan_context = lifespan
    replacements = {
        "/api/rpc": Route("/api/rpc", api_rpc, methods=["POST"]),
        "/api/tools": Route("/api/tools", api_tools, methods=["GET"]),
    }
    app.router.routes[:] = [replacements.get(route.path, route) for route in app.routes]
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Isolated durable inbox prototype server")
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--port", type=int, default=19891)
    args = parser.parse_args(argv)
    settings = Settings(host="127.0.0.1", port=args.port, data_dir=args.data_dir)
    app = make_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
