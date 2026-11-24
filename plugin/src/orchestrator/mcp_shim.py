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
from collections.abc import AsyncGenerator
from contextlib import contextmanager
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Iterator
import weakref

import httpx
import mcp.server.stdio
import mcp.types as types
from mcp.server.lowlevel.server import Server
from mcp.server.models import InitializationOptions

from orchestrator import __version__
from orchestrator.config import Settings, get_settings
from orchestrator.db import SCHEMA_VERSION, require_runtime_schema
from orchestrator.rpc_handlers import get_mcp_tool_definitions

if os.name == "nt":
    import msvcrt
else:  # pragma: no cover - POSIX fallback
    import fcntl

logger = logging.getLogger("orchestrator.mcp_shim")

# Codex's modern stdio transport closes on an 8 MiB line. Leave room for the RPC envelope.
INLINE_RESULT_BYTES = 1024 * 1024 - 1024
HEALTH_CACHE_TTL_SEC = 2.5
REQUIRED_CAPABILITIES = frozenset({
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
    "macro_rpc_v1",
    "compact_board_v1",
    "workflow5_direct_spawn_v1",
    "session_hooks_v1",
})

_CONTRACT_REJECTION_MARKERS = (
    "upgrade required",
    "upgraderequired",
    "schema_version",
    "software_version",
    "schema mismatch",
    "newer schema",
    "unsupported schema",
    "missing capability",
    "required_capabilities",
    "capabilities",
    "unknown tool",
)


@dataclass
class _HealthCacheEntry:
    base_url: str
    data_dir: str
    pid: int
    had_pid_file: bool
    software_version: str
    schema_version: int
    capabilities: frozenset[str]
    verified_at: float
    expires_at: float


@dataclass
class _PooledClientEntry:
    client: httpx.AsyncClient
    loop_ref: weakref.ReferenceType[asyncio.AbstractEventLoop]
    base_url: str
    cleanup_agen: AsyncGenerator[None, None] | None = None


_HEALTH_CACHE: dict[tuple[str, str], _HealthCacheEntry] = {}
_POOLED_CLIENTS: dict[tuple[int, str], _PooledClientEntry] = {}


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


def fetch_health(settings: Settings, *, timeout_sec: float = 0.8, _probe_first: bool = False) -> dict[str, Any] | None:
    """Return parsed /api/health payload when daemon is healthy, else None."""
    # Fast-probe port availability via exclusive bind before expensive HTTP timeout.
    if _probe_first:
        with socket.socket(socket.AF_INET6 if ":" in settings.host else socket.AF_INET, socket.SOCK_STREAM) as probe:
            if os.name == "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                probe.bind((settings.host, settings.port))
            except OSError:
                pass  # Port occupied, there may be a daemon
            else:
                return None  # Port free, definitely no daemon
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


def _spawn_detached_daemon(settings: Settings):
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
    _strip_keys_upper = {'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'VIRTUAL_ENV'}
    env = {k: v for k, v in os.environ.items() if k.upper() not in _strip_keys_upper}
    env["TRI_ORCH_HOST"] = settings.host
    env["TRI_ORCH_PORT"] = str(settings.port)
    env["TRI_ORCH_DATA_DIR"] = str(settings.data_dir)
    env["TRI_ORCH_STALL_SEC"] = str(settings.stall_sec)
    env["TRI_ORCH_WATCHDOG_INTERVAL_SEC"] = str(settings.watchdog_interval_sec)

    if os.name == "nt":
        from orchestrator.windows_daemon import spawn_daemon

        # A shared service must not inherit the SDK's KILL_ON_JOB_CLOSE job.
        return spawn_daemon(cmd, env)

    with settings.daemon_log_path.open("ab") as log_fh:
        return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log_fh,
                                stderr=log_fh, env=env, close_fds=True,
                                start_new_session=True,
                                cwd=str(settings.data_dir))


def ensure_daemon_running(
    settings: Settings | None = None,
    *,
    startup_timeout_sec: float = 10.0,
    lock_timeout_sec: float = 15.0,
) -> dict[str, Any]:
    """Ensure singleton daemon is reachable; acquire daemon.lock and spawn detached if needed."""
    cfg = settings or get_settings()
    # Offline health probes bind exclusively; serialize them with startup too.
    with _acquire_daemon_lock(cfg.lock_path, timeout_sec=lock_timeout_sec):
        # Fast probe first when no live PID file exists to avoid 0.8s HTTP SYN timeout.
        has_live_pid = False
        if cfg.pid_path.exists():
            try:
                _pid_meta = json.loads(cfg.pid_path.read_text(encoding="utf-8"))
                has_live_pid = _is_pid_alive(int(_pid_meta.get("pid", -1)))
            except Exception:
                has_live_pid = False
        health = fetch_health(cfg, _probe_first=not has_live_pid)
        if health is not None:
            return {"spawned": False, "pid": health.get("pid"), "url": cfg.base_url}

        if cfg.pid_path.exists():
            owner = json.loads(cfg.pid_path.read_text(encoding="utf-8"))
            if _is_pid_alive(int(owner["pid"])):
                raise RuntimeError(f"Daemon pid={owner['pid']} alive but unhealthy at {cfg.base_url}")
        require_runtime_schema(cfg.db_path)
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


def _health_cache_key(settings: Settings) -> tuple[str, str]:
    return (settings.base_url, str(settings.data_dir.resolve()))


def invalidate_health_cache(settings: Settings | None = None) -> None:
    """Immediately drop cached health verification (for a target endpoint or globally)."""
    if settings is None:
        _HEALTH_CACHE.clear()
        return
    _HEALTH_CACHE.pop(_health_cache_key(settings), None)


def _check_cached_health(settings: Settings) -> tuple[_HealthCacheEntry | None, bool]:
    """Return `(valid_entry, pid_invalidated)` using local-only monotonic & PID checks."""
    key = _health_cache_key(settings)
    entry = _HEALTH_CACHE.get(key)
    if entry is None:
        return None, False

    now = time.monotonic()
    if now < entry.verified_at or now >= entry.expires_at:
        _HEALTH_CACHE.pop(key, None)
        return None, False

    if (
        entry.software_version != __version__
        or entry.schema_version != SCHEMA_VERSION
        or not REQUIRED_CAPABILITIES.issubset(entry.capabilities)
    ):
        _HEALTH_CACHE.pop(key, None)
        return None, False

    if not _is_pid_alive(entry.pid):
        _HEALTH_CACHE.pop(key, None)
        return None, True

    has_pid_file = settings.pid_path.exists()
    if entry.had_pid_file and not has_pid_file:
        _HEALTH_CACHE.pop(key, None)
        return None, True
    if has_pid_file:
        try:
            owner = json.loads(settings.pid_path.read_text(encoding="utf-8"))
            if owner.get("pid") != entry.pid:
                _HEALTH_CACHE.pop(key, None)
                return None, True
        except Exception:
            _HEALTH_CACHE.pop(key, None)
            return None, True

    return entry, False


async def _client_lifecycle_guard(
    key: tuple[int, str], entry: _PooledClientEntry
) -> AsyncGenerator[None, None]:
    """Ensure pooled AsyncClient is closed cleanly before its bound event loop shuts down."""
    try:
        yield
    finally:
        if _POOLED_CLIENTS.get(key) is entry:
            _POOLED_CLIENTS.pop(key, None)
        if not entry.client.is_closed:
            await entry.client.aclose()


async def _close_pooled_entry(key: tuple[int, str], entry: _PooledClientEntry) -> None:
    if _POOLED_CLIENTS.get(key) is entry:
        _POOLED_CLIENTS.pop(key, None)
    agen = entry.cleanup_agen
    entry.cleanup_agen = None
    if agen is not None:
        await agen.aclose()
    if not entry.client.is_closed:
        await entry.client.aclose()


def _prune_dead_loop_clients() -> None:
    stale_keys = [
        k
        for k, v in list(_POOLED_CLIENTS.items())
        if (loop := v.loop_ref()) is None or loop.is_closed()
    ]
    for k in stale_keys:
        _POOLED_CLIENTS.pop(k, None)


async def _get_or_create_rpc_client(settings: Settings) -> httpx.AsyncClient:
    """Return a reusable AsyncClient bound to the active event loop and settings.base_url."""
    _prune_dead_loop_clients()
    loop = asyncio.get_running_loop()
    key = (id(loop), settings.base_url)
    existing = _POOLED_CLIENTS.get(key)
    if existing is not None:
        if (
            existing.loop_ref() is loop
            and not loop.is_closed()
            and existing.base_url == settings.base_url
            and not existing.client.is_closed
        ):
            return existing.client
        _POOLED_CLIENTS.pop(key, None)
        if existing.loop_ref() is loop and not existing.client.is_closed:
            await existing.client.aclose()

    client = httpx.AsyncClient(
        trust_env=False,
        timeout=httpx.Timeout(60.0, connect=5.0),
    )
    entry = _PooledClientEntry(
        client=client,
        loop_ref=weakref.ref(loop),
        base_url=settings.base_url,
    )
    agen = _client_lifecycle_guard(key, entry)
    entry.cleanup_agen = agen
    await agen.__anext__()
    _POOLED_CLIENTS[key] = entry
    return client


async def _reset_pooled_client(
    settings: Settings, *, failed_client: httpx.AsyncClient | None = None
) -> None:
    """Discard and close the pooled client for `settings` on the active loop."""
    _prune_dead_loop_clients()
    loop = asyncio.get_running_loop()
    key = (id(loop), settings.base_url)
    entry = _POOLED_CLIENTS.get(key)
    if entry is None:
        return
    if failed_client is not None and entry.client is not failed_client:
        return
    await _close_pooled_entry(key, entry)


async def aclose_rpc_clients() -> None:
    """Close all pooled RPC AsyncClients bound to the currently running event loop."""
    _prune_dead_loop_clients()
    loop = asyncio.get_running_loop()
    matching = [
        (key, entry)
        for key, entry in list(_POOLED_CLIENTS.items())
        if entry.loop_ref() is loop
    ]
    for key, entry in matching:
        await _close_pooled_entry(key, entry)


async def _ensure_daemon_healthy_cached(settings: Settings) -> None:
    cached, pid_invalidated = _check_cached_health(settings)
    if cached is not None:
        return
    if pid_invalidated:
        await _reset_pooled_client(settings)

    key = _health_cache_key(settings)
    previous = _HEALTH_CACHE.get(key)
    try:
        info = await asyncio.to_thread(ensure_daemon_running, settings)
    except BaseException:
        invalidate_health_cache(settings)
        await _reset_pooled_client(settings)
        raise

    if isinstance(info, dict) and isinstance(info.get("pid"), int) and info["pid"] > 0:
        pid = int(info["pid"])
        if previous is not None and previous.pid != pid:
            await _reset_pooled_client(settings)
        now = time.monotonic()
        _HEALTH_CACHE[key] = _HealthCacheEntry(
            base_url=settings.base_url,
            data_dir=str(settings.data_dir.resolve()),
            pid=pid,
            had_pid_file=settings.pid_path.exists(),
            software_version=__version__,
            schema_version=SCHEMA_VERSION,
            capabilities=frozenset(REQUIRED_CAPABILITIES),
            verified_at=now,
            expires_at=now + HEALTH_CACHE_TTL_SEC,
        )


def _classify_rpc_invalidation(
    status_code: int, body: dict[str, Any]
) -> tuple[bool, bool]:
    """Return `(invalidate_cache, reset_client)` for an `/api/rpc` HTTP response."""
    if status_code >= 500 or status_code == 426:
        return True, True
    if status_code >= 400 or not body.get("ok", False):
        error_type = str(body.get("error_type") or "")
        if error_type in {"UpgradeRequiredError", "SchemaMismatchError", "VersionMismatchError"}:
            return True, True
        err_text = str(body.get("detail") or body.get("error") or "").lower()
        if any(marker in err_text for marker in _CONTRACT_REJECTION_MARKERS):
            return True, False
    return False, False


def _is_local_dispatch(arguments: dict[str, Any]) -> bool:
    return str(arguments.get("dispatch_action") or "").strip() == "accept_local"


def _valid_dispatch_result(arguments: dict[str, Any], result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    if _is_local_dispatch(arguments):
        step = result.get("step")
        return (isinstance(step, dict) and isinstance(step.get("id"), str)
                and bool(step["id"].strip()) and step.get("state") == "implementing")
    return all(isinstance(result.get(key), str) and result[key].strip() for key in ("action_id", "message_id"))


def _dispatch_recovery(arguments: dict[str, Any], outcome: str) -> dict[str, Any]:
    if _is_local_dispatch(arguments):
        return {"dispatch_outcome": outcome, "dispatch_context": {"run_id": arguments.get("run_id"), "step_key": arguments.get("step_key")},
                "recovery_instructions": "Inspect board_status for this step_key before continuing; do not replay accept_local."}
    raw_run_id = arguments.get("run_id")
    run_id = raw_run_id.strip() if isinstance(raw_run_id, str) else ""
    sender = str(arguments.get("sender") or "coordinator").strip()
    options = arguments.get("options")
    raw_key = arguments.get("message_key") or arguments.get("key") or (options.get("key") if isinstance(options, dict) else None)
    key = raw_key.strip() if isinstance(raw_key, str) else ""
    instructions = "Do not invoke the native action or repeat workflow_prepare_dispatch before reconciliation."
    result: dict[str, Any] = {"dispatch_outcome": outcome}
    if run_id and sender and key:
        result["recovery"] = {
            "tool": "inbox_get",
            "arguments": {"run_id": run_id, "sender": sender, "message_key": key},
        }
        instructions = (
            "Look up the original dispatch with inbox_get. No visible ticket yet does not prove rollback. "
            + instructions
        )
    else:
        result["dispatch_context"] = {
            "run_id": run_id,
            "sender": sender,
            **{field: arguments[field] for field in ("step_id", "step_key") if field in arguments},
        }
        instructions = "No usable explicit message key; inspect the original run and step. " + instructions
    result["recovery_instructions"] = instructions
    return result


async def _forward_rpc(
    settings: Settings,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    startup_lock: asyncio.Lock,
) -> dict[str, Any]:
    async with startup_lock:
        await _ensure_daemon_healthy_cached(settings)
        client = await _get_or_create_rpc_client(settings)

    url = f"{settings.base_url}/api/rpc"
    payload = {"tool": tool_name, "arguments": arguments}
    is_dispatch = tool_name == "workflow_prepare_dispatch"
    try:
        # Never replay an RPC: a missing response does not imply the write failed.
        resp = await client.post(url, json=payload)
    except BaseException as exc:
        invalidate_health_cache(settings)
        await _reset_pooled_client(settings, failed_client=client)
        if is_dispatch and isinstance(
            exc, (httpx.ReadTimeout, httpx.ReadError, httpx.WriteTimeout, httpx.WriteError, httpx.RemoteProtocolError),
        ):
            logger.exception("RPC dispatch response unknown at %s", url)
            return {
                "ok": False,
                "error": f"{tool_name} at {settings.base_url}: {type(exc).__name__}: {exc}",
                **_dispatch_recovery(arguments, "unknown"),
            }
        raise

    try:
        body = resp.json()
    except ValueError:
        body = {"ok": False, "error": resp.text}
    if not isinstance(body, dict):
        body = {"ok": False, "error": str(body)}

    inv_cache, reset_pool = _classify_rpc_invalidation(resp.status_code, body)
    if inv_cache:
        invalidate_health_cache(settings)
    if reset_pool:
        await _reset_pooled_client(settings, failed_client=client)

    result = body.get("result")
    malformed_dispatch_success = is_dispatch and resp.is_success and (
        body.get("ok") is not True or not _valid_dispatch_result(arguments, result)
    )
    if resp.status_code >= 400 or not body.get("ok", False) or malformed_dispatch_success:
        err_msg = body.get("detail") or body.get("error") or (
            "Malformed workflow_prepare_dispatch success response" if malformed_dispatch_success
            else f"HTTP {resp.status_code}"
        )
        failure = {"ok": False, "error": str(err_msg), "status_code": resp.status_code}
        if is_dispatch and (resp.status_code >= 500 or malformed_dispatch_success):
            failure.update(_dispatch_recovery(arguments, "unknown"))
        return failure
    return {"ok": True, "result": result}


def _deliver_tool_result(
    settings: Settings, tool_name: str, payload: Any, *, is_error: bool = False,
) -> types.CallToolResult:
    """Keep small results inline; deliver the complete large result as an atomic local file."""
    # Compact format for inline MCP results (saves tokens); pretty for overflow files.
    text_compact = json.dumps(payload, ensure_ascii=False, indent=None, separators=(",", ":")) if not is_error else json.dumps(payload, ensure_ascii=False)
    result = types.CallToolResult(
        content=[types.TextContent(type="text", text=text_compact)], is_error=is_error,
    )
    # Measure the actual MCP encoding: escaped JSON text can be much larger than its file.
    wire_bytes = len(result.model_dump_json(by_alias=True, exclude_unset=True).encode("utf-8"))
    if wire_bytes <= INLINE_RESULT_BYTES:
        return result

    text_pretty = json.dumps(payload, ensure_ascii=False, indent=2)
    encoded = text_pretty.encode("utf-8")
    directory = settings.results_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{uuid.uuid4().hex}.json"
    temporary = path.with_suffix(".json.tmp")
    with temporary.open("xb") as output:
        output.write(encoded)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)
    # Prune stale overflow files beyond result_ttl_sec or keeping latest result_max_files.
    try:
        entries: list[tuple[Path, float, bool]] = []
        with os.scandir(directory) as it:
            for entry in it:
                if entry.name.endswith(".json") and entry.is_file(follow_symlinks=False):
                    try:
                        st = entry.stat(follow_symlinks=False)
                        candidate = Path(entry.path)
                        entries.append((candidate, float(st.st_mtime), candidate.name == path.name))
                    except OSError:
                        continue
        entries.sort(key=lambda item: (item[1], item[2], item[0].name))
        now = time.time()
        ttl_sec = int(settings.result_ttl_sec)
        max_files = int(settings.result_max_files)
        kept = 0
        for rf, rf_mtime, is_current in reversed(entries):
            if is_current:
                # Never prune the just-written result file before returning its path.
                kept += 1
                continue
            if kept >= max_files or (now - rf_mtime) >= ttl_sec:
                try:
                    rf.unlink(missing_ok=True)
                except OSError:
                    pass
            else:
                kept += 1
    except OSError:
        pass
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
        try:
            catalog = get_mcp_tool_definitions()
        except ValueError as exc:
            # An invalid TRI_ORCH_TOOL_TIER must not take the catalog offline; fall back to core.
            logger.warning("Invalid tool tier (%s); advertising the core tier", exc)
            catalog = get_mcp_tool_definitions("core")
        tools = [
            types.Tool(
                name=td["name"],
                description=td.get("description", ""),
                input_schema=td["inputSchema"],
            )
            for td in catalog
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
            payload = {"status_code": None, **{key: value for key, value in outcome.items() if key != "ok"}}
        else:
            payload = outcome.get("result")
        try:
            return await asyncio.to_thread(
                _deliver_tool_result, cfg, params.name, payload, is_error=is_error,
            )
        except Exception as exc:
            logger.exception("RPC result delivery tool=%s url=%s failed", params.name, cfg.base_url)
            error_payload = {
                "error": f"Result delivery failed ({type(exc).__name__}); inspect shim.log. "
                         "The RPC may already have completed. Do not repeat a write RPC.",
                "tool": params.name,
            }
            if params.name == "workflow_prepare_dispatch":
                if not is_error:
                    recovery_args = arguments
                    if isinstance(payload, dict) and payload.get("message_key"):
                        recovery_args = arguments | {"message_key": payload["message_key"]}
                    error_payload.update(_dispatch_recovery(recovery_args, "prepared"))
                elif outcome.get("dispatch_outcome") == "unknown":
                    error_payload.update(_dispatch_recovery(arguments, "unknown"))
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps(error_payload))],
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
        await _ensure_daemon_healthy_cached(cfg)
    except UpgradeRequiredError:
        # A catalog backed by an incompatible live daemon must not look ready.
        raise
    except Exception as exc:
        logger.exception("Initial daemon startup probe failed: %s", exc)

    server = create_mcp_server(cfg)
    init_options: InitializationOptions = server.create_initialization_options()
    try:
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, init_options)
    finally:
        await aclose_rpc_clients()


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
