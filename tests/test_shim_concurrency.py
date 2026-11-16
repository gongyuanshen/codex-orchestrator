"""Concurrency integration tests for Stdio MCP Shim singleton daemon locking."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx

from orchestrator.config import Settings
from orchestrator.mcp_shim import _is_pid_alive, ensure_daemon_running
from orchestrator.rpc_handlers import TOOL_DEFINITIONS


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _terminate_pid(pid: int) -> None:
    if pid <= 0 or not _is_pid_alive(pid):
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            check=False,
        )
    else:  # pragma: no cover
        os.kill(pid, 9)
    deadline = time.monotonic() + 5.0
    while _is_pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)


def _run_shim_session(
    idx: int,
    barrier: threading.Barrier,
    env: dict[str, str],
    project_root: Path,
) -> dict[str, Any]:
    """Launch an isolated mcp_shim subprocess at barrier, perform MCP handshake + tools/list + tools/call."""
    barrier.wait(timeout=10.0)
    proc = subprocess.Popen(
        [sys.executable, "-m", "orchestrator.mcp_shim"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        encoding="utf-8",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None

    stdout_lines: list[str] = []
    try:
        def send(msg: dict[str, Any]) -> None:
            assert proc.stdin is not None
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()

        def recv_json() -> dict[str, Any]:
            assert proc.stdout is not None
            raw = proc.stdout.readline()
            assert raw, f"Shim {idx} EOF unexpectedly (returncode={proc.poll()})"
            stdout_lines.append(raw)
            return json.loads(raw)

        send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": f"shim-client-{idx}", "version": "1.0"},
                },
            }
        )
        init_resp = recv_json()
        assert init_resp["id"] == 1
        assert "result" in init_resp

        send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools_resp = recv_json()
        assert tools_resp["id"] == 2
        tools = tools_resp["result"]["tools"]

        send({"jsonrpc": "2.0", "id": 20, "method": "tools/call",
              "params": {"name": "unknown_transport_acceptance_tool", "arguments": {}}})
        failed = recv_json()
        assert failed["id"] == 20
        assert failed["result"]["isError"] is True
        log = (Path(env["TRI_ORCH_DATA_DIR"]) / "shim.log").read_text(encoding="utf-8")
        assert "unknown_transport_acceptance_tool" in log

        # Also exercise a real RPC call through the shared daemon from every shim!
        send(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "run_create",
                    "arguments": {
                        "mode": "swarm",
                        "title": f"Concurrent Run {idx}",
                        "project_root": str(project_root),
                    },
                },
            }
        )
        call_resp = recv_json()
        assert call_resp["id"] == 3
        assert call_resp["result"]["isError"] is False
        run_payload = json.loads(call_resp["result"]["content"][0]["text"])

        proc.stdin.close()
        remaining_out, stderr_text = proc.communicate(timeout=10.0)
        if remaining_out:
            stdout_lines.extend(remaining_out.splitlines(keepends=True))
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5.0)

    # Strict stdout JSON-RPC purity verification
    parsed_all = []
    for raw_line in stdout_lines:
        stripped = raw_line.strip()
        assert stripped, "Empty line emitted on stdout"
        obj = json.loads(stripped)
        assert obj.get("jsonrpc") == "2.0"
        parsed_all.append(obj)

    return {
        "idx": idx,
        "returncode": proc.returncode,
        "stderr": stderr_text,
        "tool_names": [t["name"] for t in tools],
        "run_id": run_payload["id"],
        "stdout_count": len(parsed_all),
    }


def test_five_concurrent_shims_spawn_single_daemon_pure_stdout(tmp_path: Path) -> None:
    port = _free_port()
    data_dir = tmp_path / "shim_data"
    project_root = tmp_path / "proj"
    project_root.mkdir()

    env = os.environ.copy()
    env["TRI_ORCH_HOST"] = "127.0.0.1"
    env["TRI_ORCH_PORT"] = str(port)
    env["TRI_ORCH_DATA_DIR"] = str(data_dir)
    env["TRI_ORCH_WATCHDOG_INTERVAL_SEC"] = "60"

    expected_tools = {t["name"] for t in TOOL_DEFINITIONS}

    daemon_pid = 0
    try:
        barrier = threading.Barrier(5)
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [
                pool.submit(_run_shim_session, i, barrier, env, project_root)
                for i in range(5)
            ]
            results = [f.result(timeout=30.0) for f in futures]

        for res in results:
            assert res["returncode"] == 0, f"Shim {res['idx']} failed: {res['stderr']}"
            assert res["stderr"] == ""
            assert set(res["tool_names"]) == expected_tools
            assert len(res["tool_names"]) == len(TOOL_DEFINITIONS)

        # Verify exactly one singleton daemon wrote daemon.pid.json and is serving /api/health
        pid_file = data_dir / "daemon.pid.json"
        assert pid_file.exists()
        pid_meta = json.loads(pid_file.read_text(encoding="utf-8"))
        daemon_pid = int(pid_meta["pid"])
        assert daemon_pid > 0
        assert _is_pid_alive(daemon_pid)

        with httpx.Client(trust_env=False, timeout=5.0) as client:
            health = client.get(f"http://127.0.0.1:{port}/api/health")
            assert health.status_code == 200
            hdata = health.json()
            assert hdata["pid"] == daemon_pid
            assert hdata["port"] == port

            runs_resp = client.get(f"http://127.0.0.1:{port}/api/runs")
            assert runs_resp.status_code == 200
            recorded_ids = {r["id"] for r in runs_resp.json()["runs"]}
            assert {res["run_id"] for res in results}.issubset(recorded_ids)
    finally:
        if daemon_pid:
            _terminate_pid(daemon_pid)
        else:
            pid_file = data_dir / "daemon.pid.json"
            if pid_file.exists():
                try:
                    _terminate_pid(int(json.loads(pid_file.read_text())["pid"]))
                except Exception:
                    pass


def test_stale_pid_recovery_and_idempotent_reuse(tmp_path: Path) -> None:
    port = _free_port()
    data_dir = tmp_path / "stale_data"
    settings = Settings(
        host="127.0.0.1",
        port=port,
        data_dir=data_dir,
        watchdog_interval_sec=60,
    )
    settings.ensure_data_dir()
    settings.pid_path.write_text(
        json.dumps({"pid": 999999, "port": port}),
        encoding="utf-8",
    )

    spawned_pid = 0
    try:
        first = ensure_daemon_running(settings, startup_timeout_sec=10.0)
        assert first["spawned"] is True
        spawned_pid = int(first["pid"])
        assert _is_pid_alive(spawned_pid)

        pid_meta = json.loads(settings.pid_path.read_text(encoding="utf-8"))
        assert int(pid_meta["pid"]) == spawned_pid

        # Second call MUST reuse existing healthy daemon without spawning a new one
        second = ensure_daemon_running(settings, startup_timeout_sec=10.0)
        assert second["spawned"] is False

        with httpx.Client(trust_env=False, timeout=5.0) as client:
            hdata = client.get(f"{settings.base_url}/api/health").json()
            assert hdata["pid"] == spawned_pid
    finally:
        if spawned_pid:
            _terminate_pid(spawned_pid)


def test_live_pid_without_health_never_spawns(tmp_path, monkeypatch):
    import pytest
    import orchestrator.mcp_shim as shim
    cfg = Settings(host='127.0.0.1', port=_free_port(), data_dir=tmp_path)
    cfg.pid_path.write_text(json.dumps({'pid': os.getpid()}))
    spawned = []
    monkeypatch.setattr(shim, '_spawn_detached_daemon', lambda _: spawned.append(True))
    with pytest.raises(RuntimeError, match='alive'):
        shim.ensure_daemon_running(cfg)
    assert spawned == []


def test_rpc_read_timeout_is_not_replayed(tmp_path, monkeypatch):
    import asyncio
    import pytest
    import orchestrator.mcp_shim as shim
    cfg = Settings(host='127.0.0.1', port=_free_port(), data_dir=tmp_path)
    monkeypatch.setattr(shim, 'ensure_daemon_running', lambda _: {})
    calls = []
    async def post(self, url, **kwargs):
        calls.append(kwargs)
        raise httpx.ReadTimeout('reply lost after commit')
    monkeypatch.setattr(httpx.AsyncClient, 'post', post)
    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(shim._forward_rpc(cfg, 'board_post', {}, startup_lock=asyncio.Lock()))
    assert len(calls) == 1


def test_busy_listener_does_not_spawn(tmp_path, monkeypatch):
    import pytest
    import orchestrator.mcp_shim as shim
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        cfg = Settings(host='127.0.0.1', port=listener.getsockname()[1], data_dir=tmp_path)
        spawned = []
        monkeypatch.setattr(shim, '_spawn_detached_daemon', lambda _: spawned.append(True))
        with pytest.raises(httpx.ReadTimeout):
            shim.ensure_daemon_running(cfg)
        assert not spawned


def test_wrong_daemon_identity_rejected(tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import pytest
    import orchestrator.mcp_shim as shim
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'ok', 'pid': os.getpid(),
                                        'port': self.server.server_port,
                                        'data_dir': str(tmp_path / 'wrong')}).encode())
        def log_message(self, *args):
            return
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        cfg = Settings(host='127.0.0.1', port=server.server_port, data_dir=tmp_path)
        with pytest.raises(RuntimeError, match='identity mismatch'):
            shim.ensure_daemon_running(cfg)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
