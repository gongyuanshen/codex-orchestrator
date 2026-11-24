"""Transport tests for ambiguous workflow dispatch outcomes."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import mcp.types as types
import pytest

from orchestrator import mcp_shim as shim
from orchestrator.board import BoardService
from orchestrator.config import Settings
from orchestrator.db import Database
from orchestrator.rpc_handlers import RpcDispatcher
from orchestrator.workspace_mgr import WorkspaceManager


def _mcp_handler(settings, monkeypatch):
    monkeypatch.setattr(shim, "Server", lambda name, **handlers: handlers)
    return shim.create_mcp_server(settings)["on_call_tool"]


@pytest.mark.parametrize("missing_ticket", [None, "action_id", "message_id"])
def test_dispatch_success_contract_over_daemon_http(tmp_path, monkeypatch, missing_ticket):
    from test_daemon_web import _RunningServer, _free_port

    settings = Settings(host="127.0.0.1", port=_free_port(), data_dir=tmp_path / "runtime")
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("VALUE = 1", encoding="utf-8")
    with _RunningServer(settings) as server:
        rpc = server.app.state.dispatcher
        run_id = rpc.dispatch("run_create", {
            "mode": "boost", "title": "Dispatch contract", "project_root": str(project),
        })["id"]
        rpc.dispatch("task_packet_save", {
            "run_id": run_id, "title": "Dispatch contract", "goal": "Check the HTTP result",
            "file_ownership": {"author": ["main.py"]}, "acceptance_criteria": ["AC-1"],
        })
        args = dict(run_id=run_id, owner="author", verifier="verifier", step_key="one")
        if missing_ticket is None:
            args.update(dispatch_action="accept_local", agent_id="/root", advance_evidence="Reviewed packet")
        else:
            args.update(dispatch_action="dispatch", task_name="author", message_key="ticket")
            dispatch = rpc.dispatch

            def drop_ticket(name, arguments):
                result = dispatch(name, arguments)
                result.pop(missing_ticket)  # Lose one field only after the real dispatch commits.
                return result

            monkeypatch.setattr(rpc, "dispatch", drop_ticket)

        handler = _mcp_handler(settings, monkeypatch)

        async def call():
            try:
                return await handler(None, types.CallToolRequestParams(
                    name="workflow_prepare_dispatch", arguments=args,
                ))
            finally:
                await shim.aclose_rpc_clients()

        result = asyncio.run(call())
        payload = json.loads(result.content[0].text)
        with rpc.db.read_connection() as conn:
            steps = conn.execute("SELECT id,state FROM workflow_steps WHERE run_id=?", (run_id,)).fetchall()
            assert len(steps) == 1
            for table in ("inbox_messages", "native_actions"):
                count = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE run_id=?", (run_id,)).fetchone()[0]
                assert count == (0 if missing_ticket is None else 1)
        if missing_ticket is None:
            assert not result.is_error, payload
            assert tuple(steps[0]) == (payload["step"]["id"], "implementing")
            assert payload["step"]["state"] == "implementing"
        else:
            assert result.is_error and payload["dispatch_outcome"] == "unknown"
            assert payload["status_code"] == 200 and "Malformed" in payload["error"]


def test_dispatch_read_timeout_reports_correlation_once_over_http(tmp_path, monkeypatch):
    requests = []
    release = threading.Event()

    class HeldResponse(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            release.wait(10)  # No response until the test observes the client's timeout.

        def log_message(self, *args):
            return

    async def no_health_probe(settings):
        return None

    actual_post = httpx.AsyncClient.post

    async def post_with_test_timeout(self, url, **kwargs):
        return await actual_post(self, url, **kwargs, timeout=1.0)

    monkeypatch.setattr(shim, "_ensure_daemon_healthy_cached", no_health_probe)
    monkeypatch.setattr(httpx.AsyncClient, "post", post_with_test_timeout)
    with ThreadingHTTPServer(("127.0.0.1", 0), HeldResponse) as server:
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        try:
            settings = Settings(host="127.0.0.1", port=server.server_port, data_dir=tmp_path)
            handler = _mcp_handler(settings, monkeypatch)
            args = {"run_id": " run_1 ", "sender": " operator ", "message_key": " job-1 ", "key": "legacy"}
            result = asyncio.run(handler(None, types.CallToolRequestParams(
                name="workflow_prepare_dispatch", arguments=args,
            )))
        finally:
            release.set()
            server.shutdown()
            worker.join(timeout=5)
            assert not worker.is_alive()

    assert requests == [{"tool": "workflow_prepare_dispatch", "arguments": args}]
    assert result.is_error is True
    error = json.loads(result.content[0].text)
    assert "ReadTimeout" in error["error"]
    assert error["dispatch_outcome"] == "unknown"
    assert error["recovery"] == {
        "tool": "inbox_get",
        "arguments": {"run_id": "run_1", "sender": "operator", "message_key": "job-1"},
    }
    assert "Do not" in error["recovery_instructions"]


@pytest.mark.parametrize(
    ("extra_args", "exception_type", "expected_key"),
    [
        ({"key": " legacy "}, httpx.RemoteProtocolError, "legacy"),
        ({"step_key": "slice"}, httpx.ReadTimeout, None),
        ({"message_key": "job-1"}, httpx.ConnectTimeout, None),
    ],
)
def test_dispatch_error_does_not_invent_lookup_or_conflate_connect_timeout(
    tmp_path, monkeypatch, extra_args, exception_type, expected_key,
):
    attempts = []

    async def no_health_probe(settings):
        return None

    async def fail_post(self, url, **kwargs):
        attempts.append(kwargs["json"])
        raise exception_type("injected transport error")

    monkeypatch.setattr(shim, "_ensure_daemon_healthy_cached", no_health_probe)
    monkeypatch.setattr(httpx.AsyncClient, "post", fail_post)
    handler = _mcp_handler(Settings(data_dir=tmp_path), monkeypatch)
    args = {"run_id": "run_1", **extra_args}
    result = asyncio.run(handler(None, types.CallToolRequestParams(
        name="workflow_prepare_dispatch", arguments=args,
    )))

    assert attempts == [{"tool": "workflow_prepare_dispatch", "arguments": args}]
    assert result.is_error is True
    error = json.loads(result.content[0].text)
    assert exception_type.__name__ in error["error"]
    if exception_type is httpx.ConnectTimeout:
        assert "dispatch_outcome" not in error
    else:
        assert error["dispatch_outcome"] == "unknown"
    if expected_key:
        assert error["recovery"]["arguments"] == {
            "run_id": "run_1", "sender": "coordinator", "message_key": expected_key,
        }
    else:
        assert "recovery" not in error
    if "step_key" in args:
        assert error["dispatch_context"] == {
            "run_id": "run_1", "sender": "coordinator", "step_key": "slice",
        }


@pytest.mark.parametrize(("status", "response", "unknown"), [
    (409, {"ok": False, "detail": "Reconcile previous key with inbox_get"}, False),
    (500, {"ok": False, "detail": "Post-commit projection failed"}, True),
    (200, {"ok": True, "result": {}}, True),
])
def test_dispatch_http_response_preserves_definite_rejection_or_reports_unknown(
    tmp_path, monkeypatch, status, response, unknown,
):
    async def no_health_probe(settings):
        return None

    async def inject_response(self, url, **kwargs):
        return httpx.Response(status, json=response)

    monkeypatch.setattr(shim, "_ensure_daemon_healthy_cached", no_health_probe)
    monkeypatch.setattr(httpx.AsyncClient, "post", inject_response)
    handler = _mcp_handler(Settings(data_dir=tmp_path), monkeypatch)
    result = asyncio.run(handler(None, types.CallToolRequestParams(
        name="workflow_prepare_dispatch",
        arguments={"run_id": "run_1", "message_key": "job-1"},
    )))

    assert result.is_error is True
    error = json.loads(result.content[0].text)
    assert error["status_code"] == status
    if status >= 400:
        assert error["error"] == response["detail"]
    if unknown:
        assert error["dispatch_outcome"] == "unknown"
        assert error["recovery"]["arguments"]["message_key"] == "job-1"
    else:
        assert "dispatch_outcome" not in error and "recovery" not in error


@pytest.mark.parametrize("explicit_key", [True, False])
def test_dispatch_result_delivery_error_preserves_recovery_identity(tmp_path, monkeypatch, explicit_key):
    settings = Settings(data_dir=tmp_path / "runtime")
    project = tmp_path / "candidate"
    project.mkdir()
    (project / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    db = Database(settings=settings)
    dispatcher = RpcDispatcher(db, BoardService(db, settings), WorkspaceManager(db, settings))
    run_id = dispatcher.dispatch("run_create", {
        "mode": "boost", "title": "Lost result delivery", "project_root": str(project),
    })["id"]

    prepared = {}

    async def forward_to_dispatcher(settings, tool_name, arguments, *, startup_lock):
        prepared.update(dispatcher.dispatch(tool_name, arguments))
        return {"ok": True, "result": prepared}

    def fail_delivery(*args, **kwargs):
        raise OSError("injected local result delivery failure")

    monkeypatch.setattr(shim, "_forward_rpc", forward_to_dispatcher)
    monkeypatch.setattr(shim, "_deliver_tool_result", fail_delivery)
    handler = _mcp_handler(settings, monkeypatch)
    try:
        result = asyncio.run(handler(None, types.CallToolRequestParams(
            name="workflow_prepare_dispatch",
            arguments={
                "run_id": run_id, "step_key": "slice",
                "owner": "author", "task_name": "author", "verifier": "verifier",
                "instruction": "Inspect app.py", "file_ownership": {"author": ["app.py"]},
                "acceptance_criteria": ["AC-1"],
                **({"message_key": "job-1"} if explicit_key else {}),
            },
        )))
    finally:
        db.close()

    assert result.is_error is True
    error = json.loads(result.content[0].text)
    assert "OSError" in error["error"]
    assert error["dispatch_outcome"] == "prepared"
    assert error["recovery"]["arguments"] == {
        "run_id": run_id, "sender": "coordinator", "message_key": prepared["message_key"],
    }

def test_recover_dispatch_key_inside_options():
    from orchestrator.mcp_shim import _dispatch_recovery
    result = _dispatch_recovery({"run_id": "run-1", "options": {"key": "ticket-1"}}, "unknown")
    assert result["recovery"] == {"tool": "inbox_get", "arguments": {
        "run_id": "run-1", "sender": "coordinator", "message_key": "ticket-1"}}
