"""Real loopback HTTP plus stdio MCP checks; never register a desktop tool."""

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import socket

import pytest
import uvicorn

from orchestrator.config import Settings, default_data_dir
from orchestrator.mcp_shim import INLINE_RESULT_BYTES
from prototypes.inbox.client import call, session
from prototypes.inbox.mcp_bridge import validate_target
from prototypes.inbox.server import make_app


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:18790", "http://example.com:19891",
    "http://127.0.0.2:19891", "https://127.0.0.1:19891",
    "http://127.0.0.1", "http://127.0.0.1:0",
    "http://user@127.0.0.1:19891", "http://127.0.0.1:19891/api",
    "http://127.0.0.1:19891?x=1", "http://127.0.0.1:19891#x",
])
def test_target_rejects_nonprototype_urls(tmp_path, url):
    with pytest.raises(ValueError):
        validate_target(url, tmp_path / "data")


def test_target_rejects_production_directory():
    with pytest.raises(ValueError):
        validate_target("http://127.0.0.1:19891", default_data_dir())


def test_target_rejects_configured_production_directory(tmp_path, monkeypatch):
    production = tmp_path / "configured-production"
    monkeypatch.setenv("TRI_ORCH_DATA_DIR", str(production))
    with pytest.raises(ValueError):
        validate_target("http://127.0.0.1:19891", production)


def test_target_rejects_hardlinked_production_database(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    production = default_data_dir()
    production.mkdir(parents=True)
    (production / "state.db").write_bytes(b"isolated link identity fixture")
    alias = tmp_path / "alias"
    alias.mkdir()
    os.link(production / "state.db", alias / "state.db")
    with pytest.raises(ValueError):
        validate_target("http://127.0.0.1:19891", alias)
    assert (production / "state.db").read_bytes() == b"isolated link identity fixture"


def test_target_accepts_explicit_isolated_url(tmp_path):
    assert validate_target("http://127.0.0.1:19891/", tmp_path / "data") == "http://127.0.0.1:19891"


@asynccontextmanager
async def running_server(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        assert port != 18790
        data_dir = tmp_path / "data"
        app = make_app(Settings(port=port, data_dir=data_dir))
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                               log_level="error", access_log=False))
        serving = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(5):
                while not server.started:
                    if serving.done():
                        serving.result()
                        raise RuntimeError("Isolated HTTP server stopped before startup")
                    await asyncio.sleep(0.01)
            yield app, f"http://127.0.0.1:{port}", data_dir
        finally:
            server.should_exit = True
            await asyncio.wait_for(serving, 10)


def test_real_stdio_catalog_concurrent_wait_send_errors_and_large_result(tmp_path):
    async def scenario():
        async with running_server(tmp_path) as (app, url, data_dir):
            async with session(url, data_dir) as sdk:
                catalog = await sdk.list_tools()
                names = {tool.name for tool in catalog.tools}
                assert {"run_create", "inbox_send", "inbox_get", "inbox_ack", "inbox_wait"} <= names
                run = await call(sdk, "run_create", {"mode": "swarm", "title": "Real bridge check",
                    "project_root": str(tmp_path), "is_git": False})
                run_id = run["id"]
                await call(sdk, "worker_register", {"run_id": run_id, "name": "writer", "role": "coder"})
                waiting = asyncio.create_task(call(sdk, "inbox_wait", {
                    "run_id": run_id, "recipient": "writer", "timeout_ms": 5000}))
                async with asyncio.timeout(3):
                    while app.state.event_bus.subscriber_count() != 1:
                        await asyncio.sleep(0.005)
                arguments = {"run_id": run_id, "sender": "coordinator", "recipient": "writer",
                             "key": "message-1", "kind": "task", "summary": "Concurrent send",
                             "body_md": "A real SQLite-backed message."}
                sent = await call(sdk, "inbox_send", arguments)
                received = await asyncio.wait_for(waiting, 3)
                assert received["status"] == "messages"
                assert received["messages"][0]["id"] == sent["message"]["id"]
                assert received["messages"][0]["status"] == "pending"
                assert app.state.event_bus.subscriber_count() == 0

                conflict = await sdk.call_tool("inbox_send", {**arguments, "body_md": "Different body"})
                assert conflict.is_error
                error = json.loads(conflict.content[0].text)
                assert error["status_code"] == 409
                assert error["response"]["ok"] is False
                with pytest.raises(RuntimeError, match="409"):
                    await call(sdk, "inbox_send", {**arguments, "summary": "Changed summary"})
                invalid = await sdk.call_tool("inbox_wait", {
                    "run_id": run_id, "recipient": "writer", "timeout_ms": True})
                assert invalid.is_error

                detail = "full-detail-验收\n"
                large_body = detail * (INLINE_RESULT_BYTES // len(detail.encode("utf-8")) + 1)
                large = await sdk.call_tool("inbox_send", {
                    **arguments, "key": "large-message", "body_md": large_body})
                assert not large.is_error
                pointer = json.loads(large.content[0].text)
                assert pointer["delivery"] == "file"
                result_path = Path(pointer["result_file"])
                assert result_path.is_relative_to(data_dir.resolve() / "results")
                raw = result_path.read_bytes()
                assert hashlib.sha256(raw).hexdigest() == pointer["sha256"]
                payload = json.loads(raw)
                assert payload["message"]["body_md"] == large_body
                retrieved = await call(sdk, "inbox_get", {"message_id": payload["message"]["id"]})
                assert retrieved["body_md"] == large_body
    asyncio.run(scenario())
