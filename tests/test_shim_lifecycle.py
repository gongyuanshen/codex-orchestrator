"""Real SDK job shutdown must not own the shared Windows daemon."""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from test_shim_concurrency import _terminate_pid

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows SDK Job Object lifetime")


def parameters(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    data = tmp_path / "中文 space" / "data"
    env = dict(os.environ, TRI_ORCH_HOST="127.0.0.1", TRI_ORCH_PORT=str(port),
               TRI_ORCH_DATA_DIR=str(data), TRI_ORCH_WATCHDOG_INTERVAL_SEC="60",
               PYTHONDONTWRITEBYTECODE="1")
    return StdioServerParameters(command=sys.executable, args=["-m", "orchestrator.mcp_shim"],
                                 env=env, cwd=str(tmp_path)), data, f"http://127.0.0.1:{port}"


def cleanup(data):
    if (data / "daemon.pid.json").exists():
        _terminate_pid(json.loads((data / "daemon.pid.json").read_text(encoding="utf-8"))["pid"])


@pytest.mark.parametrize("count", [1, 5])
def test_sdk_short_sessions_leave_same_daemon_alive(tmp_path, count):
    params, data, url = parameters(tmp_path)
    async def exercise():
        ready = [asyncio.Event() for _ in range(count)]
        first_closed = asyncio.Event()
        async def client(index):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("run_list", {"limit": 1})
                    assert not result.is_error, result
                    with httpx.Client(trust_env=False) as http:
                        pid = http.get(url + "/api/health").json()["pid"]
                    ready[index].set()
                    await asyncio.gather(*(event.wait() for event in ready))
                    if index:
                        await first_closed.wait()
                        # A fresh RPC may auto-restart a dead daemon; health FIRST forbids that false PASS.
                        with httpx.Client(trust_env=False) as http:
                            assert http.get(url + "/api/health").json()["pid"] == pid
                        result = await session.call_tool("run_list", {"limit": 1})
                        assert not result.is_error, result
            if index == 0:
                first_closed.set()
            with httpx.Client(trust_env=False) as http:
                assert http.get(url + "/api/health").json()["pid"] == pid
            return pid
        pids = await asyncio.wait_for(asyncio.gather(*(client(i) for i in range(count))), 45)
        assert len(set(pids)) == 1
    try:
        asyncio.run(exercise())
    finally:
        cleanup(data)


def test_force_killed_sdk_shim_leaves_daemon_alive(tmp_path):
    params, data, url = parameters(tmp_path)
    marker = tmp_path / "shim-pid.txt"
    params.args = ["-c", f"import os; from pathlib import Path; Path({str(marker)!r}).write_text(str(os.getpid())); from orchestrator.mcp_shim import main; main()"]
    async def exercise():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("run_list", {"limit": 1})
                assert not result.is_error, result
                with httpx.Client(trust_env=False) as http:
                    pid = http.get(url + "/api/health").json()["pid"]
                subprocess.run(["taskkill", "/PID", marker.read_text(), "/F"], check=True, capture_output=True)
        with httpx.Client(trust_env=False) as http:
            assert http.get(url + "/api/health").json()["pid"] == pid
    try:
        asyncio.run(exercise())
    finally:
        cleanup(data)


def test_wmi_start_failure_is_explicit_without_subprocess_fallback(tmp_path, monkeypatch):
    from orchestrator.windows_daemon import spawn_daemon
    def forbidden(*args, **kwargs):
        pytest.fail("WMI failure must not fall back to a job-owned subprocess")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    with pytest.raises(OSError, match="Win32_Process.Create return=9"):
        spawn_daemon([str(tmp_path / "missing.exe"), "-m", "orchestrator.daemon"], dict(os.environ))


def test_wmi_unavailable_is_explicit_without_subprocess_fallback(tmp_path, monkeypatch):
    import orchestrator.windows_daemon as launcher
    import pywintypes
    def unavailable(*args):
        raise pywintypes.com_error(-2147217394, "Injected WMI unavailable", None, None)
    monkeypatch.setattr(launcher, "GetObject", unavailable)
    def forbidden(*args, **kwargs):
        pytest.fail("WMI failure must not fall back to a job-owned subprocess")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    with pytest.raises(pywintypes.com_error, match="WMI unavailable"):
        launcher.spawn_daemon([sys.executable, "-m", "orchestrator.daemon"], dict(os.environ))


def test_wmi_daemon_keeps_user_identity_outside_sdk_job_without_visible_window(tmp_path):
    import win32api
    import win32con
    import win32gui
    import win32job
    import win32process
    import win32security
    from mcp.os.win32.utilities import _process_jobs
    params, data, url = parameters(tmp_path)
    async def exercise():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                assert not (await session.call_tool("run_list", {"limit": 1})).is_error
                with httpx.Client(trust_env=False) as http:
                    health = http.get(url + "/api/health").json()
                assert Path(health["data_dir"]) == data
                handle = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, health["pid"])
                try:
                    sdk_jobs = list(_process_jobs.items())
                    assert len(sdk_jobs) == 1
                    sdk_process, sdk_job = sdk_jobs[0]
                    shim_handle = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, sdk_process.pid)
                    try:
                        assert win32job.IsProcessInJob(shim_handle, sdk_job)
                    finally:
                        shim_handle.Close()
                    # venv launchers may own a separate job; only the client's job must be absent.
                    assert not win32job.IsProcessInJob(handle, sdk_job)
                    token = win32security.OpenProcessToken(handle, win32con.TOKEN_QUERY)
                    try:
                        child_user = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
                    finally:
                        token.Close()
                    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
                    try:
                        own_user = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
                    finally:
                        token.Close()
                    assert child_user == own_user
                    windows = []
                    win32gui.EnumWindows(lambda hwnd, _: windows.append(hwnd)
                                         if win32process.GetWindowThreadProcessId(hwnd)[1] == health["pid"]
                                         and win32gui.IsWindowVisible(hwnd) else None, None)
                    assert windows == []
                finally:
                    handle.Close()
    try:
        asyncio.run(exercise())
    finally:
        cleanup(data)


def test_wmi_bootstrap_preserves_startup_failure_in_daemon_log(tmp_path):
    from orchestrator.windows_daemon import spawn_daemon
    data = tmp_path / "日志 space"
    data.mkdir()
    env = dict(os.environ, TRI_ORCH_DATA_DIR=str(data), TRI_ORCH_PORT="not-a-port")
    process = spawn_daemon([sys.executable, "-m", "orchestrator.daemon"], env)
    assert process.wait(timeout=10) != 0
    assert process.poll() != 0
    assert "not-a-port" in (data / "daemon.log").read_text(encoding="utf-8")
