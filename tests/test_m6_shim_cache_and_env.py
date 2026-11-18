"""M6 tests for mcp_shim connection pooling, safe 2.5s health cache, and verify_runner env matching."""

from __future__ import annotations

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
from typing import Any

import httpx
import pytest

from orchestrator import __version__
from orchestrator.artifacts import capture_content
from orchestrator.config import Settings
from orchestrator.db import SCHEMA_VERSION
import orchestrator.mcp_shim as shim
from orchestrator.verify_runner import (
    ENV_PROFILE_KEYS,
    build_isolated_env,
    collect,
    validate_receipt,
)


class _ControlledDaemonServer:
    """Thread-safe local HTTP server recording health probes, RPC calls, and TCP client ports."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.lock = threading.Lock()
        self.health_calls = 0
        self.rpc_calls: list[dict[str, Any]] = []
        self.rpc_client_ports: list[int] = []
        self.health_override: dict[str, Any] = {}
        self.rpc_status: int = 200
        self.rpc_body: dict[str, Any] | str = {"ok": True, "result": {"runs": [], "count": 0}}
        self.close_rpc_connection: bool = False

        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:
                if self.path != "/api/health":
                    self.send_error(404)
                    return
                with outer.lock:
                    outer.health_calls += 1
                    body = {
                        "status": "ok",
                        "pid": os.getpid(),
                        "port": self.server.server_port,
                        "data_dir": str(outer.data_dir),
                        "software_version": __version__,
                        "schema_version": SCHEMA_VERSION,
                        "capabilities": sorted(shim.REQUIRED_CAPABILITIES),
                    }
                    body.update(outer.health_override)
                raw = json.dumps(body).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self) -> None:
                if self.path != "/api/rpc":
                    self.send_error(404)
                    return
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                with outer.lock:
                    outer.rpc_calls.append(payload)
                    outer.rpc_client_ports.append(int(self.client_address[1]))
                    status = outer.rpc_status
                    resp_body = outer.rpc_body
                    should_close = outer.close_rpc_connection
                if should_close:
                    self.close_connection = True
                    return
                if isinstance(resp_body, str):
                    raw = resp_body.encode("utf-8")
                    content_type = "text/plain; charset=utf-8"
                else:
                    raw = json.dumps(resp_body).encode("utf-8")
                    content_type = "application/json"
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *_args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def port(self) -> int:
        return int(self._server.server_port)

    def close(self) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5)
        self._server.server_close()


def test_shim_required_capabilities_include_m6_m8_m9_contracts() -> None:
    expected_new = {
        "macro_rpc_v1",
        "compact_board_v1",
        "workflow5_direct_spawn_v1",
        "session_hooks_v1",
    }
    expected_existing = {
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
    }
    assert expected_existing | expected_new <= shim.REQUIRED_CAPABILITIES
    assert len(shim.REQUIRED_CAPABILITIES) >= 15


def test_shim_reuses_pooled_async_client_and_caches_health_within_2_5s(tmp_path: Path) -> None:
    shim.invalidate_health_cache()
    daemon = _ControlledDaemonServer(tmp_path)
    settings = Settings(host="127.0.0.1", port=daemon.port, data_dir=tmp_path)

    async def exercise() -> None:
        lock = asyncio.Lock()
        first = await shim._forward_rpc(settings, "run_list", {"limit": 1}, startup_lock=lock)
        client_after_first = await shim._get_or_create_rpc_client(settings)
        second = await shim._forward_rpc(settings, "run_list", {"limit": 2}, startup_lock=lock)
        third = await shim._forward_rpc(settings, "run_list", {"limit": 3}, startup_lock=lock)
        client_after_third = await shim._get_or_create_rpc_client(settings)

        assert first["ok"] and second["ok"] and third["ok"]
        assert daemon.health_calls == 1, "Health probe must be cached within 2.5s window"
        assert len(daemon.rpc_calls) == 3
        assert client_after_first is client_after_third
        assert not client_after_first.is_closed
        # HTTP/1.1 keep-alive over pooled AsyncClient reuses the exact same TCP socket!
        assert len(set(daemon.rpc_client_ports)) == 1

        await shim.aclose_rpc_clients()
        assert client_after_first.is_closed

    try:
        asyncio.run(exercise())
    finally:
        daemon.close()
        shim.invalidate_health_cache()


def test_shim_health_cache_expires_after_2_5s(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shim.invalidate_health_cache()
    daemon = _ControlledDaemonServer(tmp_path)
    settings = Settings(host="127.0.0.1", port=daemon.port, data_dir=tmp_path)

    fake_now = [100.0]
    monkeypatch.setattr(shim.time, "monotonic", lambda: fake_now[0])

    async def exercise() -> None:
        lock = asyncio.Lock()
        await shim._forward_rpc(settings, "run_list", {}, startup_lock=lock)
        assert daemon.health_calls == 1

        fake_now[0] = 102.4  # Still within 2.5s TTL
        await shim._forward_rpc(settings, "run_list", {}, startup_lock=lock)
        assert daemon.health_calls == 1

        fake_now[0] = 102.51  # Expired past 2.5s TTL
        await shim._forward_rpc(settings, "run_list", {}, startup_lock=lock)
        assert daemon.health_calls == 2
        await shim.aclose_rpc_clients()

    try:
        asyncio.run(exercise())
    finally:
        daemon.close()
        shim.invalidate_health_cache()


def test_shim_invalidates_cache_immediately_on_http_500_and_catches_schema_mismatch(
    tmp_path: Path,
) -> None:
    shim.invalidate_health_cache()
    daemon = _ControlledDaemonServer(tmp_path)
    settings = Settings(host="127.0.0.1", port=daemon.port, data_dir=tmp_path)

    async def exercise() -> None:
        lock = asyncio.Lock()
        ok_res = await shim._forward_rpc(settings, "run_list", {}, startup_lock=lock)
        assert ok_res["ok"] is True
        assert daemon.health_calls == 1

        # Inject HTTP 500 on /api/rpc within the 2.5s cache window.
        with daemon.lock:
            daemon.rpc_status = 500
            daemon.rpc_body = {"ok": False, "detail": "Simulated upstream 500"}
        err_res = await shim._forward_rpc(settings, "board_post", {"text": "once"}, startup_lock=lock)
        assert err_res == {"ok": False, "error": "Simulated upstream 500", "status_code": 500}
        assert len(daemon.rpc_calls) == 2  # Never replayed!

        # Immediately mutate daemon health to incompatible future schema (still < 2.5s).
        with daemon.lock:
            daemon.rpc_status = 200
            daemon.rpc_body = {"ok": True, "result": {}}
            daemon.health_override = {"schema_version": SCHEMA_VERSION + 1}

        with pytest.raises(shim.UpgradeRequiredError, match="Daemon upgrade required"):
            await shim._forward_rpc(settings, "run_list", {}, startup_lock=lock)
        assert daemon.health_calls == 2
        assert len(daemon.rpc_calls) == 2  # Blocked before /api/rpc!
        await shim.aclose_rpc_clients()

    try:
        asyncio.run(exercise())
    finally:
        daemon.close()
        shim.invalidate_health_cache()


def test_shim_invalidates_cache_and_resets_pooled_client_on_transport_error(
    tmp_path: Path,
) -> None:
    shim.invalidate_health_cache()
    daemon = _ControlledDaemonServer(tmp_path)
    settings = Settings(host="127.0.0.1", port=daemon.port, data_dir=tmp_path)

    async def exercise() -> None:
        lock = asyncio.Lock()
        await shim._forward_rpc(settings, "run_list", {}, startup_lock=lock)
        first_client = await shim._get_or_create_rpc_client(settings)
        assert daemon.health_calls == 1

        # Drop connection abruptly during /api/rpc to trigger httpx RemoteProtocolError / ReadError.
        with daemon.lock:
            daemon.close_rpc_connection = True
        with pytest.raises(httpx.HTTPError):
            await shim._forward_rpc(settings, "run_list", {}, startup_lock=lock)

        assert first_client.is_closed, "Broken pooled client must be closed and discarded immediately"

        # Restore normal RPC; next call within 2.5s MUST re-run health check and allocate fresh client.
        with daemon.lock:
            daemon.close_rpc_connection = False
        recovered = await shim._forward_rpc(settings, "run_list", {}, startup_lock=lock)
        assert recovered["ok"] is True
        assert daemon.health_calls == 2
        second_client = await shim._get_or_create_rpc_client(settings)
        assert second_client is not first_client
        assert not second_client.is_closed
        await shim.aclose_rpc_clients()

    try:
        asyncio.run(exercise())
    finally:
        daemon.close()
        shim.invalidate_health_cache()


def test_shim_detects_daemon_pid_swap_within_2_5s_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shim.invalidate_health_cache()
    daemon = _ControlledDaemonServer(tmp_path)
    settings = Settings(host="127.0.0.1", port=daemon.port, data_dir=tmp_path)
    settings.ensure_data_dir()
    settings.pid_path.write_text(
        json.dumps({"pid": os.getpid(), "port": daemon.port}),
        encoding="utf-8",
    )

    async def exercise() -> None:
        lock = asyncio.Lock()
        first = await shim._forward_rpc(settings, "run_list", {}, startup_lock=lock)
        assert first["ok"] is True
        assert daemon.health_calls == 1

        # Case A: daemon.pid.json swapped to a new PID within 2.5s window.
        settings.pid_path.write_text(
            json.dumps({"pid": 999999, "port": daemon.port}),
            encoding="utf-8",
        )
        with pytest.raises(RuntimeError, match="PID mismatch"):
            await shim._forward_rpc(settings, "run_list", {}, startup_lock=lock)
        assert daemon.health_calls == 2
        assert len(daemon.rpc_calls) == 1  # Prevented stale RPC from reaching mismatched daemon!

        # Restore matching pid_path and re-prime cache.
        settings.pid_path.write_text(
            json.dumps({"pid": os.getpid(), "port": daemon.port}),
            encoding="utf-8",
        )
        second = await shim._forward_rpc(settings, "run_list", {}, startup_lock=lock)
        assert second["ok"] is True
        assert daemon.health_calls == 3

        # Case B: cached PID itself dies within 2.5s window (_is_pid_alive -> False).
        real_alive = shim._is_pid_alive
        dead_flag = [True]
        monkeypatch.setattr(
            shim, "_is_pid_alive", lambda pid: False if dead_flag[0] else real_alive(pid)
        )
        with pytest.raises(RuntimeError, match="identity mismatch"):
            await shim._forward_rpc(settings, "run_list", {}, startup_lock=lock)
        assert daemon.health_calls == 4
        dead_flag[0] = False
        await shim.aclose_rpc_clients()

    try:
        asyncio.run(exercise())
    finally:
        daemon.close()
        shim.invalidate_health_cache()


def test_shim_connect_error_and_timeout_invalidate_cache_without_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shim.invalidate_health_cache()
    daemon = _ControlledDaemonServer(tmp_path)
    settings = Settings(host="127.0.0.1", port=daemon.port, data_dir=tmp_path)

    async def exercise() -> None:
        lock = asyncio.Lock()
        await shim._forward_rpc(settings, "run_list", {}, startup_lock=lock)
        c1 = await shim._get_or_create_rpc_client(settings)
        assert daemon.health_calls == 1

        real_post = httpx.AsyncClient.post
        attempts = 0

        async def inject_connect_err(self: httpx.AsyncClient, url: str, **kwargs: Any) -> Any:
            nonlocal attempts
            attempts += 1
            raise httpx.ConnectError("simulated connect reset")

        monkeypatch.setattr(httpx.AsyncClient, "post", inject_connect_err)
        with pytest.raises(httpx.ConnectError, match="simulated connect reset"):
            await shim._forward_rpc(settings, "board_post", {"text": "x"}, startup_lock=lock)
        assert attempts == 1
        assert c1.is_closed

        # Restore real post: next call inside 2.5s MUST re-probe health and create new client.
        monkeypatch.setattr(httpx.AsyncClient, "post", real_post)
        res = await shim._forward_rpc(settings, "run_list", {}, startup_lock=lock)
        assert res["ok"] is True
        assert daemon.health_calls == 2
        c2 = await shim._get_or_create_rpc_client(settings)
        assert c2 is not c1 and not c2.is_closed
        await shim.aclose_rpc_clients()

    try:
        asyncio.run(exercise())
    finally:
        daemon.close()
        shim.invalidate_health_cache()


def test_shim_invalidates_cache_on_rpc_contract_rejection(tmp_path: Path) -> None:
    shim.invalidate_health_cache()
    daemon = _ControlledDaemonServer(tmp_path)
    settings = Settings(host="127.0.0.1", port=daemon.port, data_dir=tmp_path)

    async def exercise() -> None:
        lock = asyncio.Lock()
        await shim._forward_rpc(settings, "run_list", {}, startup_lock=lock)
        assert daemon.health_calls == 1

        # Simulate /api/rpc rejecting an M6 macro tool or schema mismatch with 400/409.
        with daemon.lock:
            daemon.rpc_status = 400
            daemon.rpc_body = {
                "ok": False,
                "detail": "Unknown tool: workflow_prepare_dispatch",
                "error_type": "ValueError",
            }
        rejected = await shim._forward_rpc(
            settings, "workflow_prepare_dispatch", {}, startup_lock=lock
        )
        assert rejected["ok"] is False
        assert rejected["status_code"] == 400

        # Next call within 2.5s must re-probe /api/health because cache was invalidated.
        with daemon.lock:
            daemon.rpc_status = 200
            daemon.rpc_body = {"ok": True, "result": {"runs": []}}
            daemon.health_override = {"capabilities": ["durable_inbox_v1"]}
        with pytest.raises(shim.UpgradeRequiredError, match="macro_rpc_v1"):
            await shim._forward_rpc(settings, "run_list", {}, startup_lock=lock)
        assert daemon.health_calls == 2
        await shim.aclose_rpc_clients()

    try:
        asyncio.run(exercise())
    finally:
        daemon.close()
        shim.invalidate_health_cache()


def test_shim_across_multiple_asyncio_runs_and_settings_cleans_up_without_warnings(
    tmp_path: Path,
) -> None:
    shim.invalidate_health_cache()
    daemon_a = _ControlledDaemonServer(tmp_path / "a")
    daemon_b = _ControlledDaemonServer(tmp_path / "b")
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    cfg_a = Settings(host="127.0.0.1", port=daemon_a.port, data_dir=tmp_path / "a")
    cfg_b = Settings(host="127.0.0.1", port=daemon_b.port, data_dir=tmp_path / "b")

    try:
        for cfg in (cfg_a, cfg_b, cfg_a):
            res = asyncio.run(
                shim._forward_rpc(cfg, "run_list", {}, startup_lock=asyncio.Lock())
            )
            assert res["ok"] is True
        assert daemon_a.health_calls == 1
        assert daemon_b.health_calls == 1
    finally:
        daemon_a.close()
        daemon_b.close()
        shim.invalidate_health_cache()


def test_verify_runner_case_insensitive_env_lookup_cross_platform() -> None:
    assert ENV_PROFILE_KEYS["windows_minimal_py"] == (
        "SYSTEMROOT",
        "SystemRoot",
        "COMSPEC",
        "PATHEXT",
        "PATH",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "PYTHONUTF8",
        "PYTHONIOENCODING",
    )
    assert ENV_PROFILE_KEYS["posix_minimal_py"] == (
        "PATH",
        "HOME",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "PYTHONUTF8",
        "PYTHONIOENCODING",
    )

    host_env = {
        "SystemRoot": r"C:\Windows",
        "Path": r"C:\Windows\System32",
        "Temp": r"C:\Users\Admin\AppData\Local\Temp",
        "UNLISTED_SECRET": "never-leak-this",
    }

    # Uppercase allowlist key matches mixed-case Windows host key; optional profile keys unset in host don't fail.
    env_from_profile = build_isolated_env(
        ENV_PROFILE_KEYS["windows_minimal_py"],
        host_env,
        case_insensitive=True,
        command_id="py-check",
    )
    assert "UNLISTED_SECRET" not in env_from_profile
    upper_map = {k.upper(): v for k, v in env_from_profile.items()}
    assert upper_map["SYSTEMROOT"] == r"C:\Windows"
    assert upper_map["PATH"] == r"C:\Windows\System32"
    assert upper_map["TEMP"] == r"C:\Users\Admin\AppData\Local\Temp"
    # Deduplicated: must NOT contain both 'SYSTEMROOT' and 'SystemRoot' simultaneously.
    assert len([k for k in env_from_profile if k.upper() == "SYSTEMROOT"]) == 1

    # Reverse direction: mixed-case allowlist key matches uppercase host key.
    env_mixed = build_isolated_env(
        ["SystemRoot"],
        {"SYSTEMROOT": r"D:\WinNT", "EXTRA": "hidden"},
        case_insensitive=True,
    )
    assert env_mixed == {"SystemRoot": r"D:\WinNT"}

    # POSIX case-sensitive mode rejects wrong casing for custom keys.
    with pytest.raises(ValueError, match="required environment keys missing"):
        build_isolated_env(
            ["SYSTEMROOT"],
            {"SystemRoot": "/nowhere"},
            case_insensitive=False,
            command_id="posix-cmd",
        )

    # Custom required secret missing still raises even in case-insensitive mode.
    with pytest.raises(ValueError, match="MY_CUSTOM_TOKEN"):
        build_isolated_env(
            ["SystemRoot", "MY_CUSTOM_TOKEN"],
            host_env,
            case_insensitive=True,
            command_id="win-cmd",
        )


def test_verify_runner_windows_minimal_py_prevents_winerror_10106_without_serializing_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("M6_UNDECLARED_SECRET", "top-secret-must-not-leak")
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)

    source = tmp_path / "source"
    source.mkdir()
    (source / "mod.py").write_text("VALUE = 42\n", encoding="utf-8")

    env_keys = list(
        ENV_PROFILE_KEYS["windows_minimal_py" if os.name == "nt" else "posix_minimal_py"]
    )
    code = (
        "import asyncio, os, socket, ssl; "
        "assert 'M6_UNDECLARED_SECRET' not in os.environ; "
        "s = socket.socket(); s.close(); "
        "print('socket-ok')"
    )
    manifest = {
        "schema_version": 1,
        "evidence_level": "local_collector_v1",
        "run_id": "run_m6",
        "step_id": "step_m6",
        "artifact_id": "art_m6",
        "scope": "candidate",
        "packet_version": 1,
        "attempt": 1,
        "contract_revision": 1,
        "verifier": "verifier_1",
        "verifier_agent_id": "/root/verifier_1",
        "artifact": capture_content(source),
        "commands": [
            {
                "id": "socket_probe",
                "argv": [sys.executable, "-c", code],
                "cwd": str(tmp_path),
                "timeout_sec": 10,
                "env_keys": env_keys,
                "criteria": ["socket_init_works"],
            }
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_text = json.dumps(manifest)
    manifest_path.write_text(manifest_text, encoding="utf-8")

    out_dir = tmp_path / "evidence_out"
    receipt = collect(manifest_path, out_dir)
    assert receipt["status"] == "finished", receipt
    assert receipt["commands"][0]["exit_code"] == 0
    assert Path(receipt["commands"][0]["stdout"]["path"]).read_text("utf-8").strip() == "socket-ok"
    validate_receipt(manifest, out_dir / "receipt.json", require_success=True)

    receipt_text = (out_dir / "receipt.json").read_text("utf-8")
    assert "top-secret-must-not-leak" not in receipt_text
    if os.name == "nt" and os.environ.get("SYSTEMROOT"):
        assert os.environ["SYSTEMROOT"] not in manifest_text
        assert os.environ["SYSTEMROOT"] not in receipt_text
