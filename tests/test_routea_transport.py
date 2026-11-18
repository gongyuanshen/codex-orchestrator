"""Formal inbox transport and version-coherent singleton acceptance."""

import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from orchestrator import __version__
from orchestrator.config import Settings
from orchestrator.daemon import create_app
from orchestrator.db import SCHEMA_VERSION
import orchestrator.mcp_shim as shim


def test_health_advertises_formal_backend_contract(tmp_path):
    async def check():
        app = create_app(Settings(data_dir=tmp_path), start_watchdog=False)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                result = (await client.get("/api/health")).json()
                assert result["software_version"] == __version__
                assert result["schema_version"] == SCHEMA_VERSION
                assert {"durable_inbox_v1", "workflow_gates_v1", "async_inbox_wait_v1", "verification_receipts_v1"} <= set(result["capabilities"])

    asyncio.run(check())


@pytest.mark.parametrize("fault", ["old", "future_schema", "missing_wait", "missing_receipts", "wrong_software"])
def test_incompatible_live_daemon_is_not_restarted(tmp_path, monkeypatch, fault):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = {"status": "ok", "pid": os.getpid(), "port": self.server.server_port,
                    "data_dir": str(tmp_path), "software_version": __version__,
                    "schema_version": SCHEMA_VERSION,
                    "capabilities": list(shim.REQUIRED_CAPABILITIES)}
            if fault == "old":
                for field in ("software_version", "schema_version", "capabilities"):
                    del body[field]
            elif fault == "future_schema":
                body["schema_version"] += 1
            elif fault == "missing_wait":
                body["capabilities"].remove("async_inbox_wait_v1")
            elif fault == "missing_receipts":
                body["capabilities"].remove("verification_receipts_v1")
            else:
                body["software_version"] = "0.0.0-old"
            raw = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            return

    def forbidden_spawn(settings):
        raise AssertionError("A live incompatible service must never be restarted")

    monkeypatch.setattr(shim, "_spawn_detached_daemon", forbidden_spawn)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        settings = Settings(port=server.server_port, data_dir=tmp_path)
        with pytest.raises(RuntimeError, match="upgrade|Upgrade"):
            shim.ensure_daemon_running(settings)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_real_http_fault_is_preserved_without_rpc_replay(tmp_path):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            raw = json.dumps({"status": "ok", "pid": os.getpid(), "port": self.server.server_port,
                              "data_dir": str(tmp_path), "software_version": __version__,
                              "schema_version": SCHEMA_VERSION,
                              "capabilities": list(shim.REQUIRED_CAPABILITIES)}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            calls.append(json.loads(body))
            raw = b"Actual upstream plaintext failure"
            self.send_response(500)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        settings = Settings(port=server.server_port, data_dir=tmp_path)
        result = asyncio.run(shim._forward_rpc(settings, "inbox_send", {"key": "one-write"}, startup_lock=asyncio.Lock()))
        assert result == {"ok": False, "error": "Actual upstream plaintext failure", "status_code": 500}
        assert calls == [{"tool": "inbox_send", "arguments": {"key": "one-write"}}]
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
