"""Async stdio JSON-RPC client for ``codex app-server --listen stdio://`` (M11).

Manages persistent author (``workspace-write``) and independent verifier
(``read-only``) threads over newline-delimited JSON-RPC stdio, enforces Win32
Job Object lifecycle cleanup on Windows (``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``),
and deterministically declines all out-of-sandbox server approval/elicitation
requests within ``<100ms`` without hanging.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
import copy
import inspect
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from typing import Any

from orchestrator import __version__
from orchestrator.challenge_runner import (
    CODEX_WORKER_ISOLATION_ARGS,
    ChallengeValidationError,
    _WindowsJobGuard,
    _terminate_posix_group,
    discover_local_windows_codex_exe,
    resolve_codex_executable,
)

_discover_local_windows_codex_exe = discover_local_windows_codex_exe

CLIENT_NAME = "tri-orchestrator-pipeline"
CLIENT_VERSION = __version__
STDIO_STREAM_LIMIT_BYTES = 16 * 1024 * 1024

BUILTIN_VERIFIER_OUTPUT_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "TriOrchestratorPipelineVerifierOutput",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "verdict",
        "summary",
        "review_dimensions",
        "criteria_results",
        "findings",
    ],
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["PASS", "FAIL"],
        },
        "summary": {
            "type": "string",
            "minLength": 1,
        },
        "review_dimensions": {
            "type": "object",
            "additionalProperties": False,
            "required": ["specification", "code", "stress", "evidence"],
            "properties": {
                "specification": {
                    "type": "string",
                    "enum": ["PASS", "FAIL", "NOT_RUN"],
                },
                "code": {
                    "type": "string",
                    "enum": ["PASS", "FAIL", "NOT_RUN"],
                },
                "stress": {
                    "type": "string",
                    "enum": ["PASS", "FAIL", "NOT_RUN"],
                },
                "evidence": {
                    "type": "string",
                    "enum": ["PASS", "FAIL", "NOT_RUN"],
                },
            },
        },
        "criteria_results": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["criterion", "status"],
                "properties": {
                    "criterion": {"type": "string", "minLength": 1},
                    "status": {
                        "type": "string",
                        "enum": ["PASS", "FAIL", "NOT_RUN"],
                    },
                },
            },
        },
        "findings": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
}


class AppServerError(RuntimeError):
    """Base error for ``codex app-server`` stdio transport or protocol failures."""


class AppServerClosedError(AppServerError):
    """Raised when the underlying ``app-server`` child process closes unexpectedly."""


class AppServerRpcError(AppServerError):
    """Raised when ``app-server`` returns a JSON-RPC error object."""

    def __init__(
        self,
        method: str,
        error: Mapping[str, Any] | str,
        *,
        request_id: int | str | None = None,
    ) -> None:
        self.method = method
        self.request_id = request_id
        if isinstance(error, Mapping):
            self.code = error.get("code")
            self.rpc_message = str(error.get("message") or error)
            self.data = error.get("data")
        else:
            self.code = None
            self.rpc_message = str(error)
            self.data = None
        super().__init__(f"app-server {method} failed (code={self.code}): {self.rpc_message}")


class AttrDict(dict[str, Any]):
    """Dictionary subclass supporting attribute read access while remaining JSON-serializable."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def normalize_thread_id(thread_id: str) -> str:
    """Strip optional ``appserver://`` URI prefix and validate non-empty thread ID."""
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise ValueError("thread_id must be a non-empty string")
    cleaned = thread_id.strip()
    if cleaned.startswith("appserver://"):
        cleaned = cleaned.removeprefix("appserver://").strip()
    if not cleaned:
        raise ValueError(f"Invalid appserver thread identifier: {thread_id!r}")
    return cleaned


def format_appserver_agent_id(thread_id: str) -> str:
    """Format a thread ID into canonical ``appserver://<thread_id>`` worker identity."""
    return f"appserver://{normalize_thread_id(thread_id)}"


def _extract_thread_status(thread_obj: Mapping[str, Any] | None) -> str:
    """Extract normalized thread status (``notLoaded | idle | active | systemError``)."""
    if not isinstance(thread_obj, Mapping):
        return "unknown"
    raw_status = thread_obj.get("status")
    if isinstance(raw_status, str) and raw_status.strip():
        return raw_status.strip()
    if isinstance(raw_status, Mapping):
        status_type = raw_status.get("type")
        if isinstance(status_type, str) and status_type.strip():
            return status_type.strip()
    return "idle"


def resolve_appserver_argv(
    *,
    codex_bin: str | Path | None = None,
    argv: Sequence[str] | None = None,
) -> list[str]:
    """Resolve full isolated subprocess ``argv`` for ``codex app-server --listen stdio://``."""
    if argv is not None:
        if (
            not isinstance(argv, Sequence)
            or isinstance(argv, (str, bytes))
            or not argv
            or any(not isinstance(part, str) or not part or "\0" in part for part in argv)
        ):
            raise ValueError("argv must be a non-empty sequence of non-empty strings without NUL")
        return list(argv)

    raw_bin = str(codex_bin) if codex_bin is not None else os.environ.get("CODEX_BIN")
    try:
        _, exec_prefix, _ = resolve_codex_executable(raw_bin)
    except ChallengeValidationError:
        if raw_bin is not None:
            raise ValueError(f"Codex executable not found: {raw_bin!r}") from None
        raise ValueError(
            "Codex executable not found on PATH, CODEX_BIN, or %LOCALAPPDATA%\\OpenAI\\Codex\\bin"
        ) from None

    subcommand: list[str] = [
        "app-server",
        *CODEX_WORKER_ISOLATION_ARGS,
    ]
    if os.name == "nt":
        subcommand.extend(["-c", 'windows.sandbox="unelevated"'])
    subcommand.extend(["--listen", "stdio://"])
    return [*exec_prefix, *subcommand]


class _ThreadContext:
    """Client-side metadata remembered for a persistent author or verifier thread."""

    __slots__ = ("thread_id", "role", "cwd", "model", "effort", "allow_network")

    def __init__(
        self,
        *,
        thread_id: str,
        role: str,
        cwd: str,
        model: str | None,
        effort: str | None,
        allow_network: bool,
    ) -> None:
        self.thread_id = thread_id
        self.role = role
        self.cwd = cwd
        self.model = model
        self.effort = effort
        self.allow_network = allow_network


class _ActiveTurnState:
    """In-flight streaming state for one ``turn/start`` -> ``turn/completed`` lifecycle."""

    def __init__(
        self,
        *,
        thread_id: str,
        on_progress: Callable[[dict[str, Any]], Any] | None = None,
        progress_interval_sec: float = 1.0,
    ) -> None:
        self.thread_id = thread_id
        self.turn_id: str | None = None
        self.status: str = "inProgress"
        self.delta_chunks: dict[str, list[str]] = {}
        self.completed_messages: list[str] = []
        self.interceptions: list[dict[str, Any]] = []
        self.needs_clarification: bool = False
        self.clarification_requests: list[dict[str, Any]] = []
        self.errors: list[dict[str, Any]] = []
        self.completed_payload: dict[str, Any] | None = None
        self.done_event = asyncio.Event()
        self.abort_error: BaseException | None = None
        self.on_progress = on_progress
        self.progress_interval_sec = max(0.05, float(progress_interval_sec))
        self.last_progress_at: float = 0.0

    def assembled_text(self) -> str:
        if self.completed_messages:
            return self.completed_messages[-1]
        if self.delta_chunks:
            parts = ["".join(chunks) for chunks in self.delta_chunks.values() if chunks]
            if parts:
                return "\n".join(parts)
        if isinstance(self.completed_payload, Mapping):
            turn_obj = self.completed_payload.get("turn")
            if isinstance(turn_obj, Mapping):
                for key in ("lastAgentMessage", "finalMessage", "text", "output"):
                    val = turn_obj.get(key)
                    if isinstance(val, str) and val.strip():
                        return val
                items = turn_obj.get("items")
                if isinstance(items, list):
                    extracted = [
                        txt
                        for item in items
                        if isinstance(item, Mapping)
                        and (txt := _extract_agent_message_text(item))
                    ]
                    if extracted:
                        return extracted[-1]
        return ""


def _extract_agent_message_text(item: Mapping[str, Any]) -> str | None:
    item_type = str(item.get("type") or "")
    if item_type not in {"agentMessage", "agent_message", "assistantMessage"}:
        return None
    direct_text = item.get("text")
    if isinstance(direct_text, str):
        return direct_text
    content = item.get("content")
    if isinstance(content, list):
        segments: list[str] = []
        for entry in content:
            if isinstance(entry, str):
                segments.append(entry)
            elif isinstance(entry, Mapping):
                seg_text = entry.get("text")
                if isinstance(seg_text, str):
                    segments.append(seg_text)
        if segments:
            return "".join(segments)
    return None


class AppServerStdioClient:
    """Pure stdlib + ``asyncio`` client for ``codex app-server --listen stdio://``."""

    def __init__(
        self,
        *,
        codex_bin: str | Path | None = None,
        argv: Sequence[str] | None = None,
        env: Mapping[str, str] | None = None,
        cwd: str | Path | None = None,
        request_timeout_sec: float = 30.0,
    ) -> None:
        self._codex_bin = codex_bin
        self._explicit_argv = list(argv) if argv is not None else None
        self._env_overrides = dict(env) if env is not None else None
        self._cwd = str(Path(cwd).resolve()) if cwd is not None else None
        self._request_timeout_sec = max(1.0, float(request_timeout_sec))

        self._proc: asyncio.subprocess.Process | None = None
        self._win_guard: _WindowsJobGuard | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._progress_tasks: set[asyncio.Task[Any]] = set()
        self._response_tasks: set[asyncio.Task[Any]] = set()
        self._write_lock = asyncio.Lock()
        self._next_id = 1
        self._pending_requests: dict[int | str, asyncio.Future[Any]] = {}
        self._threads: dict[str, _ThreadContext] = {}
        self._wire_thread_aliases: dict[str, str] = {}
        self._logical_by_wire_thread: dict[str, str] = {}
        self._active_turns_by_thread: dict[str, _ActiveTurnState] = {}
        self._interceptions: list[dict[str, Any]] = []
        self._stderr_lines: list[str] = []
        self._server_info: dict[str, Any] | None = None
        self._started = False
        self._closed = False

    def register_thread_alias(self, logical_thread_id: str, wire_thread_id: str) -> None:
        """Map an immutable logical ``thread_id`` to a rehydrated underlying ``wire_thread_id``."""
        logical = normalize_thread_id(logical_thread_id)
        wire = normalize_thread_id(wire_thread_id)
        old_wire = self._wire_thread_aliases.get(logical)
        if old_wire and old_wire != wire:
            self._logical_by_wire_thread.pop(old_wire, None)
        if logical == wire:
            self._wire_thread_aliases.pop(logical, None)
            self._logical_by_wire_thread.pop(wire, None)
            return
        self._wire_thread_aliases[logical] = wire
        self._logical_by_wire_thread[wire] = logical
        if wire in self._threads and logical not in self._threads:
            w_ctx = self._threads[wire]
            self._threads[logical] = _ThreadContext(
                thread_id=logical,
                role=w_ctx.role,
                cwd=w_ctx.cwd,
                model=w_ctx.model,
                effort=w_ctx.effort,
                allow_network=w_ctx.allow_network,
            )

    def resolve_wire_thread_id(self, thread_id: str) -> str:
        logical = normalize_thread_id(thread_id)
        return self._wire_thread_aliases.get(logical, logical)

    def _resolve_logical_thread_id(self, raw_thread_id: str) -> str:
        norm = normalize_thread_id(raw_thread_id)
        return self._logical_by_wire_thread.get(norm, norm)

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode if self._proc is not None else None

    @property
    def server_info(self) -> dict[str, Any] | None:
        return self._server_info

    @property
    def interceptions(self) -> list[dict[str, Any]]:
        return list(self._interceptions)

    @property
    def interception_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for event in self._interceptions:
            method = str(event.get("method") or "unknown")
            counts[method] = counts.get(method, 0) + 1
        return counts

    @property
    def stderr_tail(self) -> str:
        return "\n".join(self._stderr_lines[-50:])

    async def __aenter__(self) -> AppServerStdioClient:
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    async def start(self) -> dict[str, Any]:
        """Spawn child ``app-server`` attached to Win32 Job Object and complete handshake."""
        if self._closed:
            raise AppServerClosedError("AppServerStdioClient has already been closed")
        if self._started and self._server_info is not None:
            return self._server_info

        cmd = resolve_appserver_argv(codex_bin=self._codex_bin, argv=self._explicit_argv)
        child_env = os.environ.copy()
        child_env.setdefault("PYTHONUTF8", "1")
        child_env.setdefault("PYTHONIOENCODING", "utf-8")
        if self._env_overrides:
            child_env.update(self._env_overrides)

        if os.name == "nt":
            self._win_guard = _WindowsJobGuard()
            self._win_guard.open_job()
            creationflags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) | int(
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cwd,
                env=child_env,
                limit=STDIO_STREAM_LIMIT_BYTES,
                creationflags=creationflags,
            )
            if self._proc.pid is not None:
                self._win_guard.attach_tree(self._proc.pid)
        else:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cwd,
                env=child_env,
                limit=STDIO_STREAM_LIMIT_BYTES,
                start_new_session=True,
            )

        self._reader_task = asyncio.create_task(
            self._reader_loop(), name="appserver-stdio-reader"
        )
        self._stderr_task = asyncio.create_task(
            self._stderr_loop(), name="appserver-stdio-stderr"
        )

        try:
            init_resp = await self._request(
                "initialize",
                {
                    "clientInfo": {
                        "name": CLIENT_NAME,
                        "version": CLIENT_VERSION,
                    },
                    "capabilities": {
                        "experimentalApi": True,
                    },
                },
                timeout_sec=self._request_timeout_sec,
            )
            await self._notify("initialized")
        except BaseException:
            await self.close()
            raise

        self._server_info = dict(init_resp) if isinstance(init_resp, Mapping) else {"result": init_resp}
        self._started = True
        return self._server_info

    async def close(self) -> None:
        """Idempotently close stdio pipes, reap process tree / Win32 Job Object, and free handles."""
        if self._closed:
            return
        self._closed = True

        proc = self._proc
        win_guard = self._win_guard

        if proc is not None and proc.stdin is not None:
            try:
                if not proc.stdin.is_closing():
                    proc.stdin.close()
            except OSError:
                pass

        if proc is not None and proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=0.2)
            except asyncio.TimeoutError:
                pass

        if win_guard is not None:
            await asyncio.to_thread(win_guard.terminate_all, force_sweep=True)
        elif proc is not None and proc.returncode is None and proc.pid is not None:
            if os.name != "nt":
                await asyncio.to_thread(_terminate_posix_group, proc.pid)
            else:  # pragma: no cover
                proc.kill()

        if proc is not None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()

        for task in (self._reader_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        if self._response_tasks:
            pending_resp = tuple(self._response_tasks)
            for rtask in pending_resp:
                rtask.cancel()
            await asyncio.gather(*pending_resp, return_exceptions=True)
            self._response_tasks.clear()

        if self._progress_tasks:
            pending_prog = tuple(self._progress_tasks)
            for ptask in pending_prog:
                ptask.cancel()
            await asyncio.gather(*pending_prog, return_exceptions=True)
            self._progress_tasks.clear()

        if proc is not None:
            transport = getattr(proc, "_transport", None)
            if transport is not None:
                try:
                    transport.close()
                except Exception:
                    pass
            await asyncio.sleep(0)

        if win_guard is not None:
            win_guard.close()
            self._win_guard = None

        closed_err = AppServerClosedError("AppServerStdioClient closed")
        for fut in list(self._pending_requests.values()):
            if not fut.done():
                fut.set_exception(closed_err)
        self._pending_requests.clear()

        for turn_state in list(self._active_turns_by_thread.values()):
            if not turn_state.done_event.is_set():
                turn_state.abort_error = closed_err
                turn_state.done_event.set()
        self._active_turns_by_thread.clear()

    # ------------------------------------------------------------------
    # Thread & Turn High-Level APIs
    # ------------------------------------------------------------------

    async def start_author_thread(
        self,
        cwd: str | Path,
        *,
        model: str | None = None,
        effort: str | None = None,
        allow_network: bool = False,
    ) -> AttrDict:
        """Start a persistent ``workspace-write`` author thread in ``cwd``."""
        await self._ensure_started()
        resolved_cwd = str(Path(cwd).resolve())
        params: dict[str, Any] = {
            "cwd": resolved_cwd,
            "approvalPolicy": "never",
            "sandbox": "workspace-write",
        }
        if model is not None:
            params["model"] = model
        if effort:
            params["config"] = {"model_reasoning_effort": effort}

        result = await self._request("thread/start", params)
        thread_obj = result.get("thread") if isinstance(result, Mapping) else None
        raw_tid = (
            thread_obj.get("id")
            if isinstance(thread_obj, Mapping)
            else (result.get("threadId") if isinstance(result, Mapping) else None)
        )
        if not isinstance(raw_tid, str) or not raw_tid.strip():
            raise AppServerError(f"thread/start did not return a valid thread.id: {result!r}")

        sandbox_info = result.get("sandbox") if isinstance(result, Mapping) else None
        sandbox_type = (
            sandbox_info.get("type")
            if isinstance(sandbox_info, Mapping)
            else (sandbox_info if isinstance(sandbox_info, str) else None)
        )
        if isinstance(sandbox_type, str) and sandbox_type.strip().lower() in {"readonly", "read-only"}:
            raise AppServerError(
                f"Author thread {raw_tid!r} requested workspace-write sandbox, "
                f"but app-server coerced sandbox to {sandbox_info!r} "
                '(configure windows.sandbox="unelevated" on Windows)'
            )

        tid = normalize_thread_id(raw_tid)
        self._threads[tid] = _ThreadContext(
            thread_id=tid,
            role="author",
            cwd=resolved_cwd,
            model=model,
            effort=effort or None,
            allow_network=bool(allow_network),
        )
        return AttrDict(
            thread_id=tid,
            agent_id=format_appserver_agent_id(tid),
            status=_extract_thread_status(thread_obj if isinstance(thread_obj, Mapping) else None),
            thread=thread_obj,
            raw_response=result,
        )

    async def start_verifier_thread(
        self,
        cwd: str | Path,
        *,
        model: str | None = None,
        effort: str | None = None,
    ) -> AttrDict:
        """Start an independent persistent ``read-only`` verifier thread in ``cwd``."""
        await self._ensure_started()
        resolved_cwd = str(Path(cwd).resolve())
        params: dict[str, Any] = {
            "cwd": resolved_cwd,
            "approvalPolicy": "never",
            "sandbox": "read-only",
        }
        if model is not None:
            params["model"] = model
        if effort:
            params["config"] = {"model_reasoning_effort": effort}

        result = await self._request("thread/start", params)
        thread_obj = result.get("thread") if isinstance(result, Mapping) else None
        raw_tid = (
            thread_obj.get("id")
            if isinstance(thread_obj, Mapping)
            else (result.get("threadId") if isinstance(result, Mapping) else None)
        )
        if not isinstance(raw_tid, str) or not raw_tid.strip():
            raise AppServerError(f"thread/start did not return a valid thread.id: {result!r}")

        tid = normalize_thread_id(raw_tid)
        self._threads[tid] = _ThreadContext(
            thread_id=tid,
            role="verifier",
            cwd=resolved_cwd,
            model=model,
            effort=effort or None,
            allow_network=False,
        )
        return AttrDict(
            thread_id=tid,
            agent_id=format_appserver_agent_id(tid),
            status=_extract_thread_status(thread_obj if isinstance(thread_obj, Mapping) else None),
            thread=thread_obj,
            raw_response=result,
        )

    async def read_thread(self, thread_id: str) -> AttrDict:
        """Read persistent thread metadata/status via ``thread/read`` without loading turns."""
        await self._ensure_started()
        tid = normalize_thread_id(thread_id)
        wire_tid = self.resolve_wire_thread_id(tid)
        result = await self._request(
            "thread/read",
            {"threadId": wire_tid, "includeTurns": False},
        )
        thread_obj = result.get("thread") if isinstance(result, Mapping) else None
        if isinstance(thread_obj, Mapping):
            status_str = _extract_thread_status(thread_obj)
        elif isinstance(result, Mapping) and isinstance(result.get("status"), str):
            status_str = result["status"].strip()
        else:
            status_str = "unknown"
        return AttrDict(
            thread_id=tid,
            wire_thread_id=wire_tid,
            agent_id=format_appserver_agent_id(tid),
            status=status_str,
            thread=thread_obj,
            raw_response=result,
        )

    async def resume_thread(
        self,
        thread_id: str,
        *,
        cwd: str | Path | None = None,
        role: str = "author",
        model: str | None = None,
        effort: str | None = None,
        allow_network: bool = False,
    ) -> AttrDict:
        """Resume an existing persistent thread via ``thread/resume``."""
        await self._ensure_started()
        tid = normalize_thread_id(thread_id)
        wire_tid = self.resolve_wire_thread_id(tid)
        params: dict[str, Any] = {
            "threadId": wire_tid,
            "excludeTurns": True,
        }
        resolved_cwd: str | None = None
        if cwd is not None:
            resolved_cwd = str(Path(cwd).resolve())
            params["cwd"] = resolved_cwd
            params["approvalPolicy"] = "never"
            params["sandbox"] = "read-only" if role == "verifier" else "workspace-write"
        if model is not None:
            params["model"] = model
        if effort:
            params["config"] = {"model_reasoning_effort": effort}

        result = await self._request("thread/resume", params)
        if role != "verifier" and isinstance(result, Mapping):
            sandbox_info = result.get("sandbox")
            sandbox_type = (
                sandbox_info.get("type")
                if isinstance(sandbox_info, Mapping)
                else (sandbox_info if isinstance(sandbox_info, str) else None)
            )
            if isinstance(sandbox_type, str) and sandbox_type.strip().lower() in {"readonly", "read-only"}:
                raise AppServerError(
                    f"Resumed author thread {tid!r} requested workspace-write sandbox, "
                    f"but app-server coerced sandbox to {sandbox_info!r}"
                )

        thread_obj = result.get("thread") if isinstance(result, Mapping) else None
        status_str = _extract_thread_status(thread_obj if isinstance(thread_obj, Mapping) else None)

        effective_cwd = resolved_cwd
        if effective_cwd is None and isinstance(result, Mapping) and isinstance(result.get("cwd"), str):
            effective_cwd = result["cwd"]
        if effective_cwd is None and tid in self._threads:
            effective_cwd = self._threads[tid].cwd
        if effective_cwd is None:
            effective_cwd = self._cwd or os.getcwd()

        prev_effort = self._threads[tid].effort if tid in self._threads else None
        self._threads[tid] = _ThreadContext(
            thread_id=tid,
            role=role,
            cwd=effective_cwd,
            model=model,
            effort=effort if effort is not None else prev_effort,
            allow_network=False if role == "verifier" else bool(allow_network),
        )
        return AttrDict(
            thread_id=tid,
            wire_thread_id=wire_tid,
            agent_id=format_appserver_agent_id(tid),
            status=status_str,
            thread=thread_obj,
            raw_response=result,
        )

    async def interrupt_turn(self, thread_id: str, turn_id: str) -> AttrDict:
        """Interrupt an active turn immediately via ``turn/interrupt``."""
        await self._ensure_started()
        tid = normalize_thread_id(thread_id)
        wire_tid = self.resolve_wire_thread_id(tid)
        if not isinstance(turn_id, str) or not turn_id.strip():
            raise ValueError("turn_id must be a non-empty string")
        clean_turn_id = turn_id.strip()
        result = await self._request(
            "turn/interrupt",
            {"threadId": wire_tid, "turnId": clean_turn_id},
        )
        return AttrDict(
            thread_id=tid,
            wire_thread_id=wire_tid,
            turn_id=clean_turn_id,
            raw_response=result,
        )

    async def run_turn(
        self,
        thread_id: str,
        prompt: str,
        *,
        cwd: str | Path | None = None,
        role: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        allow_network: bool | None = None,
        output_schema: dict[str, Any] | None = None,
        on_progress: Callable[[dict[str, Any]], Any] | None = None,
        progress_interval_sec: float = 1.0,
        on_turn_started: Callable[[str], Any] | None = None,
        timeout_sec: float | None = None,
    ) -> AttrDict:
        """Execute a complete ``turn/start`` -> ``turn/completed`` turn on ``thread_id``."""
        await self._ensure_started()
        tid = normalize_thread_id(thread_id)
        wire_tid = self.resolve_wire_thread_id(tid)
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if tid in self._active_turns_by_thread:
            raise AppServerError(f"Thread {tid} already has an active turn in flight")

        ctx = self._threads.get(tid) or self._threads.get(wire_tid)
        effective_role = role or (ctx.role if ctx is not None else "author")
        if cwd is not None:
            effective_cwd = str(Path(cwd).resolve())
        elif ctx is not None:
            effective_cwd = ctx.cwd
        else:
            effective_cwd = self._cwd or str(Path.cwd().resolve())

        effective_model = model if model is not None else (ctx.model if ctx is not None else None)
        if effort is not None:
            effective_effort = effort or None
        elif ctx is not None:
            effective_effort = ctx.effort
        else:
            effective_effort = None

        if allow_network is not None:
            effective_network = bool(allow_network)
        elif ctx is not None:
            effective_network = ctx.allow_network
        else:
            effective_network = False

        if effective_role == "verifier":
            sandbox_policy: dict[str, Any] = {
                "type": "readOnly",
                "networkAccess": False,
            }
            resolved_schema = (
                copy.deepcopy(output_schema)
                if output_schema is not None
                else copy.deepcopy(BUILTIN_VERIFIER_OUTPUT_SCHEMA)
            )
        else:
            sandbox_policy = {
                "type": "workspaceWrite",
                "writableRoots": [effective_cwd],
                "networkAccess": bool(effective_network),
            }
            resolved_schema = copy.deepcopy(output_schema) if output_schema is not None else None

        params: dict[str, Any] = {
            "threadId": wire_tid,
            "cwd": effective_cwd,
            "approvalPolicy": "never",
            "sandboxPolicy": sandbox_policy,
            "input": [{"type": "text", "text": prompt}],
        }
        if effective_effort:
            params["effort"] = effective_effort
        if effective_model is not None:
            params["model"] = effective_model
        if resolved_schema is not None:
            wire_schema = {
                k: v for k, v in resolved_schema.items() if k not in {"$schema", "title"}
            }
            params["outputSchema"] = wire_schema

        turn_state = _ActiveTurnState(
            thread_id=tid,
            on_progress=on_progress,
            progress_interval_sec=progress_interval_sec,
        )
        self._active_turns_by_thread[tid] = turn_state

        try:
            start_resp = await self._request(
                "turn/start",
                params,
                timeout_sec=self._request_timeout_sec,
            )
            turn_obj = start_resp.get("turn") if isinstance(start_resp, Mapping) else None
            started_turn_id = (
                turn_obj.get("id")
                if isinstance(turn_obj, Mapping)
                else (start_resp.get("turnId") if isinstance(start_resp, Mapping) else None)
            )
            if isinstance(started_turn_id, str) and started_turn_id.strip():
                turn_state.turn_id = started_turn_id.strip()

            if turn_state.turn_id and on_turn_started is not None:
                cb_res = on_turn_started(turn_state.turn_id)
                if inspect.isawaitable(cb_res):
                    await cb_res

            if isinstance(turn_obj, Mapping):
                initial_status = turn_obj.get("status")
                if initial_status in {"completed", "interrupted", "failed"} and turn_state.completed_payload is None:
                    # Handle synchronous turn completion if notifications already landed or were elided.
                    turn_state.status = str(initial_status)
                    turn_state.completed_payload = {"threadId": tid, "turn": dict(turn_obj)}
                    turn_state.done_event.set()

            if timeout_sec is not None:
                await asyncio.wait_for(turn_state.done_event.wait(), timeout=timeout_sec)
            else:
                await turn_state.done_event.wait()

            if turn_state.abort_error is not None:
                raise turn_state.abort_error

            final_text = turn_state.assembled_text()
            structured_output: dict[str, Any] | None = None
            if resolved_schema is not None and final_text.strip():
                try:
                    parsed = json.loads(final_text)
                    if isinstance(parsed, dict):
                        structured_output = parsed
                except json.JSONDecodeError:
                    structured_output = None

            resolved_turn_id = turn_state.turn_id or ""
            receipt: dict[str, Any] = {
                "thread_id": tid,
                "agent_id": format_appserver_agent_id(tid),
                "turn_id": resolved_turn_id,
                "role": effective_role,
                "status": turn_state.status,
                "needs_clarification": turn_state.needs_clarification,
                "interceptions_count": len(turn_state.interceptions),
                "turn_start": start_resp,
                "turn_completed": turn_state.completed_payload,
                "final_message": final_text,
            }
            if turn_state.errors:
                receipt["errors"] = list(turn_state.errors)

            return AttrDict(
                thread_id=tid,
                agent_id=format_appserver_agent_id(tid),
                turn_id=resolved_turn_id,
                status=turn_state.status,
                final_message=final_text,
                structured_output=structured_output,
                needs_clarification=turn_state.needs_clarification,
                clarification_requests=list(turn_state.clarification_requests),
                interceptions=list(turn_state.interceptions),
                errors=list(turn_state.errors),
                turn_start_response=start_resp,
                turn_completed=turn_state.completed_payload,
                raw_receipt=receipt,
            )
        finally:
            if self._active_turns_by_thread.get(tid) is turn_state:
                self._active_turns_by_thread.pop(tid, None)

    # ------------------------------------------------------------------
    # Internal JSON-RPC Transport & Deterministic Server Interception
    # ------------------------------------------------------------------

    async def _ensure_started(self) -> None:
        if self._closed:
            raise AppServerClosedError("AppServerStdioClient is closed")
        if not self._started:
            await self.start()

    async def _write_json(self, payload: Mapping[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None or self._proc.stdin.is_closing():
            raise AppServerClosedError("app-server stdin pipe is closed")
        encoded = (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        async with self._write_lock:
            if self._proc.stdin.is_closing():
                raise AppServerClosedError("app-server stdin pipe is closing")
            self._proc.stdin.write(encoded)
            await self._proc.stdin.drain()

    async def _notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = dict(params)
        await self._write_json(msg)

    async def _request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        timeout_sec: float | None = None,
    ) -> Any:
        if self._proc is None or self._closed:
            raise AppServerClosedError(f"Cannot call {method}: app-server is not running")
        req_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        self._pending_requests[req_id] = fut

        envelope: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
        }
        if params is not None:
            envelope["params"] = dict(params)

        try:
            await self._write_json(envelope)
            effective_timeout = timeout_sec if timeout_sec is not None else self._request_timeout_sec
            return await asyncio.wait_for(fut, timeout=effective_timeout)
        except BaseException:
            self._pending_requests.pop(req_id, None)
            raise

    def _match_turn_state(self, params: Mapping[str, Any] | None) -> _ActiveTurnState | None:
        has_explicit_selector = False
        if isinstance(params, Mapping):
            for key in ("threadId", "conversationId"):
                raw_tid = params.get(key)
                if isinstance(raw_tid, str) and raw_tid.strip():
                    has_explicit_selector = True
                    logical_tid = self._resolve_logical_thread_id(raw_tid)
                    hit = self._active_turns_by_thread.get(logical_tid)
                    if hit is not None:
                        return hit
            raw_turn_id = params.get("turnId")
            if not (isinstance(raw_turn_id, str) and raw_turn_id.strip()):
                turn_obj = params.get("turn")
                if isinstance(turn_obj, Mapping) and isinstance(turn_obj.get("id"), str):
                    raw_turn_id = turn_obj.get("id")
            if isinstance(raw_turn_id, str) and raw_turn_id.strip():
                has_explicit_selector = True
                clean_turn = raw_turn_id.strip()
                for candidate in self._active_turns_by_thread.values():
                    if candidate.turn_id == clean_turn:
                        return candidate
        if has_explicit_selector:
            return None
        if len(self._active_turns_by_thread) == 1:
            return next(iter(self._active_turns_by_thread.values()))
        return None

    async def _handle_server_request(
        self,
        req_id: int | str,
        method: str,
        params: Mapping[str, Any] | None,
        recv_perf: float,
    ) -> None:
        """Deterministically decline out-of-sandbox approvals/elicitations within <100ms."""
        turn_state = self._match_turn_state(params)
        params_dict = dict(params) if isinstance(params, Mapping) else {}

        response_envelope: dict[str, Any]
        action_label: str

        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            response_envelope = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"decision": "decline"},
            }
            action_label = "decline"
        elif method in {
            "execCommandApproval",
            "applyPatchApproval",
        }:
            response_envelope = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "decision": {
                        "denied": {
                            "rejection": "Declined by deterministic pipeline sandbox policy"
                        }
                    }
                },
            }
            action_label = "decline"
        elif method == "item/permissions/requestApproval":
            response_envelope = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"permissions": {}},
            }
            action_label = "decline_permissions"
        elif method == "mcpServer/elicitation/request":
            response_envelope = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"action": "decline", "content": None},
            }
            action_label = "decline"
        elif method == "item/tool/requestUserInput":
            response_envelope = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"answers": {}},
            }
            action_label = "needs_clarification"
            if turn_state is not None:
                turn_state.needs_clarification = True
                turn_state.clarification_requests.append(params_dict)
        else:
            response_envelope = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": f"Unsupported server request in deterministic pipeline: {method}",
                },
            }
            action_label = "unsupported_error"

        try:
            await self._write_json(response_envelope)
        except (OSError, AppServerClosedError):
            return
        latency_ms = round((time.perf_counter() - recv_perf) * 1000.0, 3)

        raw_req_tid = params_dict.get("threadId") or params_dict.get("conversationId")
        resolved_ev_tid = (
            turn_state.thread_id
            if turn_state is not None
            else (
                self._resolve_logical_thread_id(raw_req_tid)
                if isinstance(raw_req_tid, str) and raw_req_tid.strip()
                else raw_req_tid
            )
        )
        event = {
            "id": req_id,
            "method": method,
            "action": action_label,
            "latency_ms": latency_ms,
            "thread_id": resolved_ev_tid,
            "turn_id": turn_state.turn_id if turn_state is not None else params_dict.get("turnId"),
            "timestamp": time.time(),
        }
        self._interceptions.append(event)
        if turn_state is not None:
            turn_state.interceptions.append(event)

    def _schedule_progress(
        self, turn_state: _ActiveTurnState, event_name: str, summary: str
    ) -> None:
        if turn_state.on_progress is None:
            return
        now = time.monotonic()
        if (now - turn_state.last_progress_at) < turn_state.progress_interval_sec:
            return
        turn_state.last_progress_at = now
        payload = {
            "thread_id": turn_state.thread_id,
            "turn_id": turn_state.turn_id,
            "event": event_name,
            "summary": summary[:240],
        }
        try:
            res = turn_state.on_progress(payload)
            if inspect.isawaitable(res):
                task = asyncio.create_task(res)
                self._progress_tasks.add(task)
                task.add_done_callback(self._progress_tasks.discard)
        except Exception:
            pass

    def _handle_notification(self, method: str, params: Mapping[str, Any] | None) -> None:
        turn_state = self._match_turn_state(params)
        if turn_state is None:
            return
        p = params if isinstance(params, Mapping) else {}

        if method == "turn/started":
            turn_obj = p.get("turn")
            if isinstance(turn_obj, Mapping) and isinstance(turn_obj.get("id"), str):
                turn_state.turn_id = turn_obj["id"].strip()
            elif isinstance(p.get("turnId"), str):
                turn_state.turn_id = p["turnId"].strip()
            self._schedule_progress(turn_state, method, "turn started")
            return

        if method in {"item/agentMessage/delta", "agentMessage/delta"}:
            item_id = str(p.get("itemId") or "default")
            delta = p.get("delta")
            if isinstance(delta, str) and delta:
                turn_state.delta_chunks.setdefault(item_id, []).append(delta)
                self._schedule_progress(turn_state, method, delta.strip() or "streaming delta")
            return

        if method == "item/completed":
            item = p.get("item")
            if isinstance(item, Mapping):
                msg_text = _extract_agent_message_text(item)
                if msg_text is not None:
                    turn_state.completed_messages.append(msg_text)
                self._schedule_progress(
                    turn_state, method, f"completed {item.get('type', 'item')}"
                )
            return

        if method == "item/started":
            item = p.get("item")
            item_type = item.get("type", "item") if isinstance(item, Mapping) else "item"
            self._schedule_progress(turn_state, method, f"started {item_type}")
            return

        if method == "error":
            err_obj = p.get("error")
            turn_state.errors.append(dict(err_obj) if isinstance(err_obj, Mapping) else {"error": err_obj})
            return

        if method == "turn/completed":
            turn_obj = p.get("turn")
            if isinstance(turn_obj, Mapping):
                if isinstance(turn_obj.get("id"), str) and turn_obj["id"].strip():
                    turn_state.turn_id = turn_obj["id"].strip()
                raw_status = turn_obj.get("status")
                if isinstance(raw_status, str) and raw_status.strip():
                    turn_state.status = raw_status.strip()
                else:
                    turn_state.status = "completed"
                err_info = turn_obj.get("error")
                if err_info is not None:
                    turn_state.errors.append(
                        dict(err_info) if isinstance(err_info, Mapping) else {"error": err_info}
                    )
            else:
                turn_state.status = "completed"
            turn_state.completed_payload = dict(p)
            turn_state.done_event.set()

    async def _reader_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        stdout = self._proc.stdout
        eof_error: BaseException | None = None
        try:
            while not self._closed:
                line = await stdout.readline()
                if not line:
                    break
                recv_perf = time.perf_counter()
                raw_str = line.decode("utf-8", errors="replace").strip()
                if not raw_str:
                    continue
                try:
                    msg = json.loads(raw_str)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, Mapping):
                    continue

                has_id = "id" in msg and msg["id"] is not None
                has_method = isinstance(msg.get("method"), str) and bool(msg["method"].strip())

                if has_id and has_method:
                    resp_task = asyncio.create_task(
                        self._handle_server_request(
                            msg["id"],
                            msg["method"].strip(),
                            msg.get("params") if isinstance(msg.get("params"), Mapping) else None,
                            recv_perf,
                        )
                    )
                    self._response_tasks.add(resp_task)
                    resp_task.add_done_callback(self._response_tasks.discard)
                    continue

                if has_id and not has_method:
                    req_id = msg["id"]
                    fut = self._pending_requests.pop(req_id, None)
                    if fut is not None and not fut.done():
                        if msg.get("error") is not None:
                            fut.set_exception(
                                AppServerRpcError(
                                    "rpc",
                                    msg["error"],
                                    request_id=req_id,
                                )
                            )
                        else:
                            fut.set_result(msg.get("result"))
                    continue

                if has_method:
                    self._handle_notification(
                        msg["method"].strip(),
                        msg.get("params") if isinstance(msg.get("params"), Mapping) else None,
                    )
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            eof_error = exc
        finally:
            if not self._closed:
                tail = self.stderr_tail
                detail = f"app-server stdout closed unexpectedly (rc={self.returncode})"
                if tail:
                    detail += f": {tail}"
                err = eof_error or AppServerClosedError(detail)
                for fut in list(self._pending_requests.values()):
                    if not fut.done():
                        fut.set_exception(err)
                self._pending_requests.clear()
                for turn_state in list(self._active_turns_by_thread.values()):
                    if not turn_state.done_event.is_set():
                        turn_state.abort_error = err
                        turn_state.done_event.set()

    async def _stderr_loop(self) -> None:
        if self._proc is None or self._proc.stderr is None:
            return
        stderr = self._proc.stderr
        try:
            while not self._closed:
                line = await stderr.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").rstrip("\r\n")
                if decoded:
                    self._stderr_lines.append(decoded)
                    if len(self._stderr_lines) > 200:
                        del self._stderr_lines[:100]
        except asyncio.CancelledError:
            raise
        except BaseException:
            pass
