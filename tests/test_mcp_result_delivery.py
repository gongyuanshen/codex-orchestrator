"""Real stdio/HTTP regression for Codex's bounded JSON-RPC response frames."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from mcp_types import CLIENT_CAPABILITIES_META_KEY, CLIENT_INFO_META_KEY, PROTOCOL_VERSION_META_KEY
from orchestrator import __version__
from orchestrator.db import SCHEMA_VERSION
from orchestrator.mcp_shim import REQUIRED_CAPABILITIES


@contextmanager
def shim_session(tmp_path, result, *, fail_delivery=False, error=False, protocol="2026-07-28"):
    data = tmp_path / "data"
    data.mkdir()
    if fail_delivery:
        (data / "results").write_text("deliberately blocked artifact directory")
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = {"status": "ok", "pid": os.getpid(),
                    "port": self.server.server_port, "data_dir": str(data),
                    "software_version": __version__, "schema_version": SCHEMA_VERSION,
                    "capabilities": sorted(REQUIRED_CAPABILITIES)}
            self.reply(200, body)

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(request["tool"])
            if request["tool"] == "workspace_diff":
                self.reply(500 if error else 200,
                           {"ok": False, "error": result} if error else {"ok": True, "result": result})
            else:
                self.reply(200, {"ok": True, "result": {"runs": [], "count": 0}})

        def reply(self, status, body):
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               PYTHONDONTWRITEBYTECODE="1", TRI_ORCH_HOST="127.0.0.1",
               TRI_ORCH_PORT=str(server.server_port), TRI_ORCH_DATA_DIR=str(data),
               PYTHONPATH=str(Path(__file__).parents[1] / "plugin/src"))
    proc = subprocess.Popen([sys.executable, "-B", "-m", "orchestrator.mcp_shim"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    replies = queue.Queue()
    frames = []

    def drain():
        while line := proc.stdout.readline(8 * 1024 * 1024 + 1):
            replies.put(line)
            if not line.endswith(b"\n"):
                return
        replies.put(b"")

    reader = threading.Thread(target=drain)
    reader.start()

    def send(message):
        if protocol == "2026-07-28":
            message.setdefault("params", {})["_meta"] = {
                PROTOCOL_VERSION_META_KEY: protocol, CLIENT_CAPABILITIES_META_KEY: {},
                CLIENT_INFO_META_KEY: {"name": "large-result-regression", "version": "1"},
            }
        proc.stdin.write(json.dumps(message).encode() + b"\n")
        proc.stdin.flush()

    def receive():
        raw = replies.get(timeout=20)
        assert raw, f"unexpected EOF, process exit={proc.poll()}"
        assert raw.endswith(b"\n"), "response exceeded Codex's 8 MiB stdio line budget"
        frames.append(raw)
        message = json.loads(raw)
        assert message["jsonrpc"] == "2.0"
        return message

    def call(name, request_id):
        send({"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
              "params": {"name": name, "arguments": {"workspace_id": "ws-test"} if name == "workspace_diff" else {}}})
        return receive()["result"]

    try:
        if protocol == "2026-07-28":
            send({"jsonrpc": "2.0", "id": 1, "method": "server/discover"})
            discovered = receive()
            assert discovered["id"] == 1
            assert protocol in discovered["result"]["supportedVersions"]
        else:
            send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": protocol, "capabilities": {},
                "clientInfo": {"name": "large-result-regression", "version": "1"}}})
            initialized = receive()
            assert initialized["id"] == 1
            assert initialized["result"]["protocolVersion"] == protocol
            send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        yield call, frames, data, calls
        proc.stdin.close()
        proc.wait(timeout=10)
        assert proc.returncode == 0
        assert proc.stderr.read() == b""
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        try:
            reader.join(timeout=5)
            assert not reader.is_alive(), "stdio reader did not exit after shim termination"
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                stream.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


@pytest.mark.parametrize("protocol", ["2025-03-26", "2026-07-28"])
def test_large_unicode_escaped_result_is_complete_file_and_session_survives(tmp_path, protocol):
    payload = {"diff": '\\"\n中文' * 900_000, "files": ["source.cs"], "dirty": True}
    expected = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    assert len(expected) > 8 * 1024 * 1024
    with shim_session(tmp_path, payload, protocol=protocol) as (call, frames, data, calls):
        response = call("workspace_diff", 2)
        assert response["isError"] is False
        assert len(frames[-1]) < 1024 * 1024
        delivery = json.loads(response["content"][0]["text"])
        assert delivery["delivery"] == "file"
        artifact = Path(delivery["result_file"])
        assert artifact.is_absolute() and artifact.parent == data / "results"
        assert artifact.read_bytes() == expected
        assert delivery["bytes"] == len(expected)
        assert delivery["sha256"] == hashlib.sha256(expected).hexdigest()
        assert delivery["tool"] == "workspace_diff"
        assert "repeat" in delivery["instructions"].lower()
        next_result = call("run_list", 3)
        assert json.loads(next_result["content"][0]["text"]) == {"runs": [], "count": 0}
        assert calls == ["workspace_diff", "run_list"]
        assert all(len(frame) < 1024 * 1024 for frame in frames)


def test_artifact_write_failure_is_explicit_and_session_survives(tmp_path):
    with shim_session(tmp_path, {"diff": "x" * (2 * 1024 * 1024)}, fail_delivery=True) as (call, frames, data, calls):
        response = call("workspace_diff", 2)
        assert response["isError"] is True
        error = json.loads(response["content"][0]["text"])["error"]
        assert "delivery" in error.lower()
        assert "repeat" in error.lower()
        assert call("run_list", 3)["isError"] is False
        assert calls == ["workspace_diff", "run_list"]
        assert max(map(len, frames)) < 1024 * 1024


def test_large_error_is_not_mislabeled_success(tmp_path):
    message = "large backend error " * 100_000
    with shim_session(tmp_path, message, error=True) as (call, frames, data, calls):
        response = call("workspace_diff", 2)
        assert response["isError"] is True
        assert len(frames[-1]) < 1024 * 1024
        delivery = json.loads(response["content"][0]["text"])
        original = json.loads(Path(delivery["result_file"]).read_bytes())
        assert original == {"error": message, "status_code": 500}
        assert call("run_list", 3)["isError"] is False


@pytest.mark.parametrize("payload", [None, "hello 中文", [1, True, {"name": "value"}], {"files": [], "diff": ""}])
def test_small_result_schema_unchanged(tmp_path, payload):
    with shim_session(tmp_path, payload) as (call, frames, data, calls):
        response = call("workspace_diff", 2)
        assert response["isError"] is False
        assert json.loads(response["content"][0]["text"]) == payload
        assert not (data / "results").exists()


def test_wire_escaping_not_just_artifact_bytes_determines_delivery(tmp_path):
    from orchestrator.config import Settings
    from orchestrator.mcp_shim import _deliver_tool_result

    payload = "\\" * 400_000
    assert len(json.dumps(payload).encode()) < 1024 * 1024
    result = _deliver_tool_result(Settings(data_dir=tmp_path), "workspace_diff", payload)
    delivery = json.loads(result.content[0].text)
    assert delivery["delivery"] == "file"
    assert json.loads(Path(delivery["result_file"]).read_bytes()) == payload


def test_serialization_failure_is_visible_without_replaying_rpc(tmp_path, monkeypatch):
    import mcp.types as types
    import orchestrator.mcp_shim as shim
    from orchestrator.config import Settings

    outcomes = iter([{"ok": True, "result": object()}, {"ok": True, "result": [1, 2]}])

    async def forward(*args, **kwargs):
        return next(outcomes)

    monkeypatch.setattr(shim, "_forward_rpc", forward)
    monkeypatch.setattr(shim, "Server", lambda name, **handlers: handlers)
    handler = shim.create_mcp_server(Settings(data_dir=tmp_path))["on_call_tool"]

    async def exercise():
        params = types.CallToolRequestParams(name="run_list", arguments={})
        first = await handler(None, params)
        assert first.is_error is True
        assert "TypeError" in first.content[0].text
        second = await handler(None, params)
        assert second.is_error is False
        assert json.loads(second.content[0].text) == [1, 2]

    asyncio.run(exercise())
