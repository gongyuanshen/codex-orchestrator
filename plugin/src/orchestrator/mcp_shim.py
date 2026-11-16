"""Stdio MCP shim for tri-orchestrator with Windows mutex singleton daemon auto-spawn."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import socket
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Iterator

import httpx
import mcp.server.stdio
import mcp.types as types
from mcp.server.lowlevel.server import Server
from mcp.server.models import InitializationOptions

from orchestrator import __version__
from orchestrator.config import Settings, get_settings
from orchestrator.db import SCHEMA_VERSION
from orchestrator.rpc_handlers import get_tool_definitions

if os.name == "nt":
    import msvcrt
else:  # pragma: no cover - POSIX fallback
    import fcntl

logger = logging.getLogger("orchestrator.mcp_shim")

# Codex's modern stdio transport closes on an 8 MiB line. Leave room for the RPC envelope.
INLINE_RESULT_BYTES = 1024 * 1024 - 1024
REQUIRED_CAPABILITIES = frozenset({"durable_inbox_v1", "workflow_gates_v1", "async_inbox_wait_v1", "workflow_modes_v2"})


def configure_shim_logging(settings: Settings) -> None:
    """Configure shim logging strictly to shim.log (never to stdout)."""
    settings.ensure_data_dir()
    shim_logger = logging.getLogger("orchestrator.mcp_shim")
    for handler in list(shim_logger.handlers):
        if getattr(handler, "_tri_orch_shim_handler", False):
            return
    file_handler = RotatingFileHandler(
        settings.shim_log_path,
        maxBytes=2 * 1024 * 1024,
        backupCount=2,
        encoding="utf-8",
    )
    file_handler._tri_orch_shim_handler = True  # type: ignore[attr-defined]
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] pid=%(process)d %(message)s")
    )
    shim_logger.setLevel(logging.INFO)
    shim_logger.propagate = False
    shim_logger.addHandler(file_handler)


class HealthUnavailableError(RuntimeError):
    """A bound endpoint has not answered the health probe."""


class UpgradeRequiredError(RuntimeError):
    """The live daemon does not implement this client's formal contract."""


def fetch_health(settings: Settings, *, timeout_sec: float = 0.8) -> dict[str, Any] | None:
    """Return parsed /api/health payload when daemon is healthy, else None."""
    url = f"{settings.base_url}/api/health"
    try:
        with httpx.Client(trust_env=False, timeout=timeout_sec) as client:
            resp = client.get(url)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        # Exclusive bind proves the local endpoint is unused, even when Windows
        # delays connection refusal beyond the health timeout.
        with socket.socket(socket.AF_INET6 if ":" in settings.host else socket.AF_INET, socket.SOCK_STREAM) as probe:
            if os.name == "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                probe.bind((settings.host, settings.port))
            except OSError as exc:
                raise HealthUnavailableError(f"Listener at {url} failed health connection") from exc
        return None
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict) or data.get("status") != "ok":
        raise RuntimeError(f"Invalid daemon health at {url}: {data!r}")
    if (not isinstance(data.get("pid"), int) or not _is_pid_alive(data["pid"])
            or data.get("port") != settings.port
            or Path(data.get("data_dir", "")).resolve() != settings.data_dir.resolve()):
        raise RuntimeError(f"Daemon identity mismatch at {url}: {data!r}")
    if settings.pid_path.exists():
        owner = json.loads(settings.pid_path.read_text(encoding="utf-8"))
        if owner.get("pid") != data["pid"]:
            raise RuntimeError(f"Daemon PID mismatch at {url}: {data!r}")
    capabilities = data.get("capabilities")
    if (data.get("software_version") != __version__
            or data.get("schema_version") != SCHEMA_VERSION
            or not isinstance(capabilities, list)
            or not all(isinstance(item, str) for item in capabilities)
            or not REQUIRED_CAPABILITIES.issubset(capabilities)):
        raise UpgradeRequiredError(
            f"Daemon upgrade required at {url}: expected software={__version__}, "
            f"schema={SCHEMA_VERSION}, capabilities={sorted(REQUIRED_CAPABILITIES)}; "
            f"observed software={data.get('software_version')!r}, "
            f"schema={data.get('schema_version')!r}, capabilities={capabilities!r}. "
            "Finish the controlled upgrade after active tasks stop; this client will not restart the live daemon."
        )
    return data


def probe_health(settings: Settings, *, timeout_sec: float = 0.8) -> bool:
    """Return True if the singleton daemon responds healthy on /api/health."""
    return fetch_health(settings, timeout_sec=timeout_sec) is not None


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return int(exit_code.value) == STILL_ACTIVE
            return True
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _cleanup_stale_pid_file(settings: Settings) -> None:
    """Remove daemon.pid.json if it points to a dead process."""
    pid_path = settings.pid_path
    if not pid_path.exists():
        return
    try:
        data = json.loads(pid_path.read_text(encoding="utf-8"))
        pid = int(data.get("pid", -1))
    except Exception:
        pid = -1
    if pid <= 0 or not _is_pid_alive(pid):
        logger.warning("Cleaning stale daemon.pid.json (pid=%s)", pid)
        try:
            pid_path.unlink(missing_ok=True)
        except OSError:
            pass


@contextmanager
def _acquire_daemon_lock(
    lock_path: Path, *, timeout_sec: float = 15.0
) -> Iterator[None]:
    """Acquire an OS-level exclusive byte lock on daemon.lock with stale-lock safety."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o666)
    locked = False
    deadline = time.monotonic() + max(1.0, float(timeout_sec))
    try:
        while True:
            try:
                if os.name == "nt":
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:  # pragma: no cover
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Timed out waiting for daemon mutex lock: {lock_path}"
                    )
                time.sleep(0.05)

        # Write owner diagnostic marker starting at offset 1 so byte 0 lock stays clean.
        try:
            marker = f"pid={os.getpid()} ts={time.time():.3f}\n".ljust(63).encode("ascii")
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, b" " + marker)
        except OSError:
            pass

        yield
    finally:
        if locked:
            try:
                if os.name == "nt":
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:  # pragma: no cover
                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)


def _resolve_daemon_executable() -> str:
    """Resolve pythonw.exe on Windows when available so no console/terminal window appears."""
    exe = Path(sys.executable)
    if os.name == "nt" and exe.name.lower() == "python.exe":
        pythonw = exe.with_name("pythonw.exe")
        if pythonw.is_file():
            return str(pythonw)
    return str(exe)


def _spawn_detached_daemon(settings: Settings) -> subprocess.Popen[bytes]:
    """Spawn python -m orchestrator.daemon completely detached from stdio pipes."""
    settings.ensure_data_dir()
    cmd = [
        _resolve_daemon_executable(),
        "-m",
        "orchestrator.daemon",
        "--host",
        settings.host,
        "--port",
        str(settings.port),
        "--data-dir",
        str(settings.data_dir),
    ]
    env = os.environ.copy()
    env["TRI_ORCH_HOST"] = settings.host
    env["TRI_ORCH_PORT"] = str(settings.port)
    env["TRI_ORCH_DATA_DIR"] = str(settings.data_dir)
    env["TRI_ORCH_STALL_SEC"] = str(settings.stall_sec)
    env["TRI_ORCH_WATCHDOG_INTERVAL_SEC"] = str(settings.watchdog_interval_sec)

    base_flags = 0
    startupinfo = None
    if os.name == "nt":
        # Never combine DETACHED_PROCESS with CREATE_NO_WINDOW; Win32 ignores CREATE_NO_WINDOW
        # and Windows 11 Default Terminal pops a visible WindowsTerminal window for child stubs.
        base_flags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) | int(
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW", 0))
        startupinfo.wShowWindow = int(getattr(subprocess, "SW_HIDE", 0))

    breakaway_flag = int(getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000))
    flag_candidates = (
        [base_flags | breakaway_flag, base_flags]
        if os.name == "nt" and breakaway_flag
        else [base_flags]
    )

    log_fh = settings.daemon_log_path.open("ab")
    try:
        last_exc: OSError | None = None
        proc: subprocess.Popen[bytes] | None = None
        for flags in flag_candidates:
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=log_fh,
                    stderr=log_fh,
                    env=env,
                    close_fds=True,
                    creationflags=flags,
                    startupinfo=startupinfo,
                )
                break
            except OSError as exc:
                last_exc = exc
        if proc is None:
            assert last_exc is not None
            raise last_exc
    finally:
        log_fh.close()
    return proc


def ensure_daemon_running(
    settings: Settings | None = None,
    *,
    startup_timeout_sec: float = 10.0,
    lock_timeout_sec: float = 15.0,
) -> dict[str, Any]:
    """Ensure singleton daemon is reachable; acquire daemon.lock and spawn detached if needed."""
    cfg = settings or get_settings()
    health = fetch_health(cfg)
    if health is not None:
        return {"spawned": False, "pid": health.get("pid"), "url": cfg.base_url}

    with _acquire_daemon_lock(cfg.lock_path, timeout_sec=lock_timeout_sec):
        health = fetch_health(cfg)
        if health is not None:
            return {"spawned": False, "pid": health.get("pid"), "url": cfg.base_url}

        if cfg.pid_path.exists():
            owner = json.loads(cfg.pid_path.read_text(encoding="utf-8"))
            if _is_pid_alive(int(owner["pid"])):
                raise RuntimeError(f"Daemon pid={owner['pid']} alive but unhealthy at {cfg.base_url}")
        _cleanup_stale_pid_file(cfg)
        logger.info("Daemon offline; spawning detached daemon on %s", cfg.base_url)
        proc = _spawn_detached_daemon(cfg)

        deadline = time.monotonic() + max(1.0, float(startup_timeout_sec))
        while time.monotonic() < deadline:
            try:
                health = fetch_health(cfg, timeout_sec=0.5)
            except HealthUnavailableError:
                # Our child may have bound its socket while this probe was connecting.
                logger.info("Spawned daemon still starting at %s", cfg.base_url)
                health = None
            if health is not None:
                # On Windows uv/venv launcher stubs, proc.pid is the stub while /api/health reports real daemon PID.
                daemon_pid = health.get("pid") or proc.pid
                logger.info("Detached daemon ready (pid=%s)", daemon_pid)
                return {"spawned": True, "pid": daemon_pid, "url": cfg.base_url}
            rc = proc.poll()
            if rc is not None and rc != 0:
                health = fetch_health(cfg, timeout_sec=0.5)
                if health is not None:
                    return {
                        "spawned": False,
                        "pid": health.get("pid"),
                        "url": cfg.base_url,
                    }
                raise RuntimeError(
                    f"Daemon process exited prematurely with code {rc}; inspect {cfg.daemon_log_path}"
                )
            time.sleep(0.1)

    raise TimeoutError(
        f"Daemon failed health check on {cfg.base_url}/api/health within {startup_timeout_sec}s"
    )


async def _forward_rpc(
    settings: Settings,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    startup_lock: asyncio.Lock,
) -> dict[str, Any]:
    async with startup_lock:
        await asyncio.to_thread(ensure_daemon_running, settings)

    url = f"{settings.base_url}/api/rpc"
    payload = {"tool": tool_name, "arguments": arguments}
    async with httpx.AsyncClient(
        trust_env=False,
        timeout=httpx.Timeout(60.0, connect=5.0),
    ) as client:
        # Never replay an RPC: a missing response does not imply the write failed.
        resp = await client.post(url, json=payload)

    try:
        body = resp.json()
    except ValueError:
        body = {"ok": False, "error": resp.text}

    if resp.status_code >= 400 or not body.get("ok", False):
        err_msg = body.get("detail") or body.get("error") or f"HTTP {resp.status_code}"
        return {"ok": False, "error": str(err_msg), "status_code": resp.status_code}
    return {"ok": True, "result": body.get("result")}


def _deliver_tool_result(
    settings: Settings, tool_name: str, payload: Any, *, is_error: bool = False,
) -> types.CallToolResult:
    """Keep small results inline; deliver the complete large result as an atomic local file."""
    text = json.dumps(payload, ensure_ascii=False, indent=None if is_error else 2)
    result = types.CallToolResult(
        content=[types.TextContent(type="text", text=text)], is_error=is_error,
    )
    # Measure the actual MCP encoding: escaped JSON text can be much larger than its file.
    wire_bytes = len(result.model_dump_json(by_alias=True, exclude_unset=True).encode("utf-8"))
    if wire_bytes <= INLINE_RESULT_BYTES:
        return result

    encoded = text.encode("utf-8")
    directory = (settings.data_dir / "results").resolve()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{uuid.uuid4().hex}.json"
    temporary = path.with_suffix(".json.tmp")
    with temporary.open("xb") as output:
        output.write(encoded)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)
    delivery = {
        "delivery": "file", "tool": tool_name, "result_file": str(path),
        "bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest(),
        "instructions": "Read the full original JSON result from result_file. Nothing was truncated. "
                        "Do not repeat a write RPC to retrieve its result.",
    }
    logger.info("RPC tool=%s delivery=file bytes=%s wire_bytes=%s path=%s is_error=%s",
                tool_name, len(encoded), wire_bytes, path, is_error)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(delivery, ensure_ascii=False))],
        is_error=is_error,
    )


def create_mcp_server(settings: Settings | None = None) -> Server[Any]:
    """Create the low-level MCP stdio Server wired to the singleton daemon HTTP RPC bridge."""
    cfg = settings or get_settings()
    startup_lock = asyncio.Lock()

    async def handle_list_tools(
        ctx: Any, params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        del ctx, params
        tools = [
            types.Tool(
                name=td["name"],
                description=td.get("description", ""),
                input_schema=td["inputSchema"],
            )
            for td in get_tool_definitions()
        ]
        return types.ListToolsResult(tools=tools)

    async def handle_call_tool(
        ctx: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        del ctx
        arguments = dict(params.arguments or {})
        try:
            outcome = await _forward_rpc(
                cfg, params.name, arguments, startup_lock=startup_lock,
            )
        except Exception as exc:
            logger.exception("RPC tool=%s url=%s failed", params.name, cfg.base_url)
            outcome = {"ok": False, "error": f"{params.name} at {cfg.base_url}: {type(exc).__name__}: {exc}"}
        is_error = not outcome.get("ok")
        if is_error:
            logger.error("RPC tool=%s url=%s failed status_code=%s",
                         params.name, cfg.base_url, outcome.get("status_code"))
            payload = {"error": outcome.get("error", "RPC error"),
                       "status_code": outcome.get("status_code")}
        else:
            payload = outcome.get("result")
        try:
            return await asyncio.to_thread(
                _deliver_tool_result, cfg, params.name, payload, is_error=is_error,
            )
        except Exception as exc:
            logger.exception("RPC result delivery tool=%s url=%s failed", params.name, cfg.base_url)
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps({
                    "error": f"Result delivery failed ({type(exc).__name__}); inspect shim.log. "
                             "The RPC may already have completed. Do not repeat a write RPC.",
                    "tool": params.name,
                }))],
                is_error=True,
            )

    return Server(
        "tri-orchestrator",
        version=__version__,
        instructions="Shared SQLite WAL message board, task packets, and isolated workspace tools for Codex orchestration.",
        on_list_tools=handle_list_tools,
        on_call_tool=handle_call_tool,
    )


async def async_main(settings: Settings | None = None) -> None:
    cfg = settings or get_settings()
    configure_shim_logging(cfg)
    # Probe and ensure singleton daemon is running before handling stdio requests.
    try:
        await asyncio.to_thread(ensure_daemon_running, cfg)
    except UpgradeRequiredError:
        # A catalog backed by an incompatible live daemon must not look ready.
        raise
    except Exception as exc:
        logger.exception("Initial daemon startup probe failed: %s", exc)

    server = create_mcp_server(cfg)
    init_options: InitializationOptions = server.create_initialization_options()
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, init_options)


def main() -> int:
    cfg = get_settings()
    configure_shim_logging(cfg)
    try:
        asyncio.run(async_main(cfg))
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        logger.exception("mcp_shim fatal error")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
