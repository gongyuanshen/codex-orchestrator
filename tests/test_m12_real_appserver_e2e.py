"""Real ``codex.exe 0.166.0-alpha.5.1`` + loopback Responses SSE E2E suite (M12).

Exercises ``AppServerStdioClient`` and ``PipelineRunner`` against the real local
``codex`` binary in isolated ``%TEMP%`` ``CODEX_HOME`` and ``data_dir`` directories,
backed by an in-process loopback OpenAI ``/v1/responses`` SSE server.

Strictly isolated: NEVER touches ``127.0.0.1:18790``, ``%LOCALAPPDATA%\\tri-orchestrator``,
or ``%USERPROFILE%\\.codex``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
import http.server
import json
import os
from pathlib import Path
import re
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any
import uuid

import httpx
import pytest
import uvicorn

from orchestrator.appserver_client import (
    AppServerStdioClient,
    BUILTIN_VERIFIER_OUTPUT_SCHEMA,
    resolve_appserver_argv,
)
from orchestrator.challenge_runner import (
    CODEX_WORKER_ISOLATION_ARGS,
    discover_local_windows_codex_exe,
    resolve_codex_executable,
)
from orchestrator.config import Settings
from orchestrator.daemon import create_app
from orchestrator.mcp_shim import _is_pid_alive
from orchestrator.pipeline_runner import PipelineRunner
from orchestrator.workspace_mgr import _rmtree_safe


def _find_real_codex_bin() -> str | None:
    direct = discover_local_windows_codex_exe()
    if direct:
        return direct
    try:
        resolved, _, _ = resolve_codex_executable(None)
        return resolved
    except Exception:
        return None


REAL_CODEX_BIN = _find_real_codex_bin()
pytestmark = pytest.mark.skipif(
    REAL_CODEX_BIN is None,
    reason="Real codex binary not installed on this machine",
)


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _init_temp_git_repo(repo_dir: Path, files: dict[str, str]) -> Path:
    repo_dir.mkdir(parents=True, exist_ok=True)
    flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0

    def _git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo_dir), *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            stdin=subprocess.DEVNULL,
            creationflags=flags,
        )

    _git("init")
    _git("config", "user.name", "m12-real-e2e")
    _git("config", "user.email", "m12-real-e2e@localhost")
    for rel, content in files.items():
        full = repo_dir / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
    _git("add", "-A")
    _git("commit", "-m", "Initial baseline")
    return repo_dir


@contextmanager
def _ephemeral_daemon(tmp_path: Path) -> Iterator[Settings]:
    port = _pick_free_port()
    port_start = _pick_free_port()
    data_dir = (tmp_path / "daemon_data").resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        host="127.0.0.1",
        port=port,
        data_dir=data_dir,
        stall_sec=120,
        watchdog_interval_sec=30,
        port_range=(max(20000, min(60000, port_start)), 65000),
    )
    app = create_app(settings, start_watchdog=False, manage_pid_file=True)
    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        log_config=None,
        access_log=False,
        log_level="error",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="ephemeral-m12-daemon", daemon=True)
    thread.start()

    deadline = time.monotonic() + 10.0
    healthy = False
    while time.monotonic() < deadline:
        try:
            with httpx.Client(trust_env=False, timeout=0.5) as client:
                resp = client.get(f"{settings.base_url}/api/health")
                if resp.status_code == 200 and resp.json().get("status") == "ok":
                    healthy = True
                    break
        except Exception:
            pass
        time.sleep(0.05)

    if not healthy:
        server.should_exit = True
        thread.join(timeout=5.0)
        raise RuntimeError(f"Ephemeral daemon failed to start at {settings.base_url}")

    try:
        yield settings
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)


@contextmanager
def _sandbox_compatible_temp_dir() -> Iterator[Path]:
    """Create a temp root via plain ``Path.mkdir`` so Windows ``%TEMP%`` DACLs inherit.

    Python 3.12+ ``tempfile.mkdtemp(0o700)`` (used by pytest ``tmp_path``) sets a
    protected DACL restricted to ``BUILTIN\\Administrators`` when running as built-in
    Administrator, blocking ``CodexSandboxUsers`` / restricted-token traversal of the
    parent directory. Plain ``Path.mkdir`` inherits ``%TEMP%``'s standard sandbox ACLs,
    matching real project roots and ``%LOCALAPPDATA%\\tri-orchestrator`` directories.
    """
    base = (Path(tempfile.gettempdir()).resolve() / f"tri_m12_real_{uuid.uuid4().hex[:12]}").resolve()
    base.mkdir(parents=True, exist_ok=False)
    try:
        yield base
    finally:
        for _ in range(5):
            try:
                _rmtree_safe(base)
                break
            except OSError:
                time.sleep(0.1)


def _extract_message_text(item: dict[str, Any]) -> str:
    content = item.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "\n".join(parts)
    return ""


def _extract_input_texts(body: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in body.get("input") or []:
        if isinstance(item, dict) and item.get("type") == "message":
            txt = _extract_message_text(item)
            if txt:
                chunks.append(txt)
    return "\n".join(chunks)


def _extract_latest_user_text(body: dict[str, Any]) -> str:
    for item in reversed(body.get("input") or []):
        if isinstance(item, dict) and item.get("type") == "message" and item.get("role") == "user":
            return _extract_message_text(item)
    return ""


def _extract_criteria_from_verifier_prompt(prompt_text: str) -> list[str]:
    criteria: list[str] = []
    in_crit = False
    for line in prompt_text.splitlines():
        if line.startswith("Acceptance Criteria"):
            in_crit = True
            continue
        if in_crit:
            if line.startswith("- "):
                criteria.append(line[2:].strip())
            elif not line.strip():
                in_crit = False
    return criteria


def _build_sse_message_bytes(text: str) -> bytes:
    rid = f"resp_{uuid.uuid4().hex[:10]}"
    mid = f"msg_{uuid.uuid4().hex[:10]}"
    msg_item = {
        "id": mid,
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    }
    events = [
        ("response.created", {"type": "response.created", "response": {"id": rid, "status": "in_progress", "output": []}}),
        ("response.output_item.added", {"type": "response.output_item.added", "output_index": 0, "item": {"id": mid, "type": "message", "status": "in_progress", "role": "assistant", "content": []}}),
        ("response.output_text.delta", {"type": "response.output_text.delta", "item_id": mid, "output_index": 0, "content_index": 0, "delta": text}),
        ("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": msg_item}),
        ("response.completed", {"type": "response.completed", "response": {"id": rid, "status": "completed", "output": [msg_item], "usage": {"input_tokens": 32, "output_tokens": 16, "total_tokens": 48}}}),
    ]
    return "".join(f"event: {ev}\ndata: {json.dumps(pl, ensure_ascii=False)}\n\n" for ev, pl in events).encode("utf-8")


def _build_sse_exec_command_bytes(cmd_str: str, workdir: str) -> bytes:
    rid = f"resp_{uuid.uuid4().hex[:10]}"
    fc_id = f"fc_{uuid.uuid4().hex[:10]}"
    call_id = f"call_{uuid.uuid4().hex[:10]}"
    args_json = json.dumps({"cmd": cmd_str, "workdir": workdir, "login": False}, ensure_ascii=False)
    fc_item = {
        "id": fc_id,
        "type": "function_call",
        "status": "completed",
        "call_id": call_id,
        "name": "exec_command",
        "arguments": args_json,
    }
    events = [
        ("response.created", {"type": "response.created", "response": {"id": rid, "status": "in_progress", "output": []}}),
        ("response.output_item.added", {"type": "response.output_item.added", "output_index": 0, "item": {"id": fc_id, "type": "function_call", "status": "in_progress", "call_id": call_id, "name": "exec_command", "arguments": ""}}),
        ("response.function_call_arguments.delta", {"type": "response.function_call_arguments.delta", "item_id": fc_id, "output_index": 0, "delta": args_json}),
        ("response.function_call_arguments.done", {"type": "response.function_call_arguments.done", "item_id": fc_id, "output_index": 0, "arguments": args_json}),
        ("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": fc_item}),
        ("response.completed", {"type": "response.completed", "response": {"id": rid, "status": "completed", "output": [fc_item], "usage": {"input_tokens": 32, "output_tokens": 24, "total_tokens": 56}}}),
    ]
    return "".join(f"event: {ev}\ndata: {json.dumps(pl, ensure_ascii=False)}\n\n" for ev, pl in events).encode("utf-8")


class _LoopbackResponsesServer:
    """Thread-safe loopback OpenAI ``/v1/responses`` SSE server for real ``codex.exe`` tests."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests: list[dict[str, Any]] = []
        self.mode: str = "normal"
        self.hang_started = threading.Event()
        self.release_hang = threading.Event()
        self._httpd: socketserver.ThreadingTCPServer | None = None
        self._thread: threading.Thread | None = None
        self.port: int = 0

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def start(self) -> None:
        parent = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:
                return

            def do_GET(self) -> None:
                self.send_response(405)
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(length) if length > 0 else b"{}"
                body = json.loads(raw_body.decode("utf-8"))
                with parent.lock:
                    parent.requests.append({"path": self.path, "body": body})
                    cur_mode = parent.mode

                if cur_mode == "http_401":
                    err_bytes = json.dumps(
                        {
                            "error": {
                                "message": "Incorrect API key provided: sk-invalid-401.",
                                "type": "invalid_request_error",
                                "code": "invalid_api_key",
                            }
                        }
                    ).encode("utf-8")
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(err_bytes)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(err_bytes)
                    return

                prompt_text = _extract_input_texts(body)
                latest_user_text = _extract_latest_user_text(body)
                input_items = body.get("input") or []
                last_item = input_items[-1] if input_items else {}
                has_tool_output = isinstance(last_item, dict) and last_item.get("type") == "function_call_output"

                if "HANG_STREAM_FOR_INTERRUPT" in latest_user_text and not has_tool_output:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    first_ev = (
                        'event: response.created\n'
                        'data: {"type":"response.created","response":{"id":"r_hang","status":"in_progress","output":[]}}\n\n'
                    ).encode("utf-8")
                    try:
                        self.wfile.write(first_ev)
                        self.wfile.flush()
                        parent.hang_started.set()
                        parent.release_hang.wait(timeout=15.0)
                    except OSError:
                        pass
                    return

                # Structured verifier turn (outputSchema present in text.format)
                text_fmt = ((body.get("text") or {}).get("format")) or {}
                if text_fmt.get("type") == "json_schema":
                    all_passed = "Hard Command Collector All Passed: True" in latest_user_text
                    verdict = "PASS" if all_passed else "FAIL"
                    dim = "PASS" if verdict == "PASS" else "FAIL"
                    criteria = _extract_criteria_from_verifier_prompt(latest_user_text) or ["default_criterion"]
                    structured_reply = json.dumps(
                        {
                            "verdict": verdict,
                            "summary": f"Real codex.exe blind verifier audited receipt ({verdict}).",
                            "review_dimensions": {
                                "specification": dim,
                                "code": dim,
                                "stress": dim,
                                "evidence": dim,
                            },
                            "criteria_results": [
                                {"criterion": c, "status": dim} for c in criteria
                            ],
                            "findings": [] if verdict == "PASS" else ["Verification command failed"],
                        },
                        ensure_ascii=False,
                    )
                    sse_bytes = _build_sse_message_bytes(structured_reply)
                elif "WRITE_PROBE_FILE:" in latest_user_text and not has_tool_output:
                    m_dir = re.search(r"WRITE_PROBE_DIR:\s*(.+)", latest_user_text)
                    wdir = m_dir.group(1).strip() if m_dir else "."
                    target_probe = str((Path(wdir) / "probe_written.txt").resolve()).replace("\\", "/")
                    py_cmd = (
                        f'"{sys.executable}" -c '
                        f"\"import pathlib; pathlib.Path('{target_probe}').write_text('real_sandbox_ok\\n', encoding='utf-8')\""
                    )
                    if os.name == "nt":
                        py_cmd = f"& {py_cmd}"
                    sse_bytes = _build_sse_exec_command_bytes(py_cmd, wdir)
                elif "# Pipeline Step: step_base " in latest_user_text and not has_tool_output:
                    m_ws = re.search(r"Isolated Workspace Directory:\s*(.+)", latest_user_text)
                    ws_dir = m_ws.group(1).strip() if m_ws else "."
                    target_base = str((Path(ws_dir) / "calc_base.py").resolve()).replace("\\", "/")
                    py_cmd = (
                        f'"{sys.executable}" -c '
                        f"\"import pathlib; pathlib.Path('{target_base}').write_text('def base_val(x: int) -> int:\\n    return x + 10\\n', encoding='utf-8')\""
                    )
                    if os.name == "nt":
                        py_cmd = f"& {py_cmd}"
                    sse_bytes = _build_sse_exec_command_bytes(py_cmd, ws_dir)
                elif "# Pipeline Step: step_combined " in latest_user_text and not has_tool_output:
                    m_ws = re.search(r"Isolated Workspace Directory:\s*(.+)", latest_user_text)
                    ws_dir = m_ws.group(1).strip() if m_ws else "."
                    target_comb = str((Path(ws_dir) / "calc_combined.py").resolve()).replace("\\", "/")
                    py_cmd = (
                        f'"{sys.executable}" -c '
                        f"\"import pathlib; pathlib.Path('{target_comb}').write_text('from calc_base import base_val\\n\\ndef combined_val(x: int) -> int:\\n    return base_val(x) * 3\\n', encoding='utf-8')\""
                    )
                    if os.name == "nt":
                        py_cmd = f"& {py_cmd}"
                    sse_bytes = _build_sse_exec_command_bytes(py_cmd, ws_dir)
                elif "ECHO_TOKEN:" in latest_user_text and not has_tool_output:
                    m_tok = re.search(r"ECHO_TOKEN:\s*([A-Za-z0-9_-]+)", latest_user_text)
                    tok = m_tok.group(1) if m_tok else "none"
                    has_prev = "SECRET_MARKER_ALPHA" in prompt_text
                    sse_bytes = _build_sse_message_bytes(f"ACK:{tok}:prev={has_prev}")
                else:
                    sse_bytes = _build_sse_message_bytes("Completed turn cleanly via real codex.exe.")

                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(sse_bytes)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(sse_bytes)

        class ReusableTCPServer(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self._httpd = ReusableTCPServer(("127.0.0.1", 0), Handler)
        self.port = int(self._httpd.server_address[1])
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.release_hang.set()
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None


@contextmanager
def _loopback_responses_server() -> Iterator[_LoopbackResponsesServer]:
    srv = _LoopbackResponsesServer()
    srv.start()
    try:
        yield srv
    finally:
        srv.stop()


def _setup_isolated_codex_home(
    base_dir: Path,
    responses_base_url: str,
    *,
    plant_decoy_mcp_and_hooks: bool = False,
) -> tuple[Path, dict[str, str], Path]:
    """Create an isolated ``CODEX_HOME`` in ``base_dir`` pointing to ``responses_base_url``."""
    codex_home = (base_dir / "isolated_codex_home").resolve()
    codex_home.mkdir(parents=True, exist_ok=True)
    hook_marker = (base_dir / "hook_leaked.marker").resolve()

    extra_toml = ""
    if plant_decoy_mcp_and_hooks:
        # Plant decoy MCP server, live web_search, and multi_agent in config.toml + hooks.json
        # to prove AppServerStdioClient CLI isolation flags block 100% of them!
        extra_toml = (
            'web_search = "live"\n'
            "\n[features]\n"
            "multi_agent = true\n"
            "hooks = true\n"
            "plugins = true\n"
            "\n[mcp_servers.decoy_should_never_load]\n"
            f'command = "{sys.executable.replace(chr(92), "/")}"\n'
            'args = ["-c", "import time; time.sleep(30)"]\n'
        )
        hooks_payload = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": f'"{sys.executable}" -c "import pathlib; pathlib.Path({str(hook_marker)!r}).write_text(\'leaked\')"',
                            }
                        ]
                    }
                ],
                "UserPromptSubmit": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": f'"{sys.executable}" -c "import pathlib; pathlib.Path({str(hook_marker)!r}).write_text(\'leaked\')"',
                            }
                        ]
                    }
                ],
            }
        }
        (codex_home / "hooks.json").write_text(json.dumps(hooks_payload), encoding="utf-8")

    config_toml = (
        'model = "gpt-5"\n'
        'model_provider = "mock_loopback"\n'
        f"{extra_toml}\n"
        "[model_providers.mock_loopback]\n"
        'name = "Loopback Test Responses"\n'
        f'base_url = "{responses_base_url}"\n'
        'wire_api = "responses"\n'
    )
    (codex_home / "config.toml").write_text(config_toml, encoding="utf-8")

    env = {
        "CODEX_HOME": str(codex_home),
        "OPENAI_API_KEY": "sk-loopback-e2e",
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
        "HTTP_PROXY": "",
        "HTTPS_PROXY": "",
        "ALL_PROXY": "",
    }
    return codex_home, env, hook_marker


@pytest.fixture
def sandbox_tmp_path() -> Iterator[Path]:
    with _sandbox_compatible_temp_dir() as base:
        yield base


def test_real_codex_isolation_no_mcp_or_hook_leakage_and_sandbox_contracts(
    sandbox_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_path = sandbox_tmp_path
    monkeypatch.delenv("CODEX_BIN", raising=False)
    argv_resolved = resolve_appserver_argv()
    for token in CODEX_WORKER_ISOLATION_ARGS:
        assert token in argv_resolved
    if os.name == "nt":
        assert 'windows.sandbox="unelevated"' in argv_resolved
        assert argv_resolved[0].lower().endswith("codex.exe")

    work_dir = (tmp_path / "workspace_real").resolve()
    work_dir.mkdir(parents=True)

    with _loopback_responses_server() as sse_srv:
        _, env, hook_marker = _setup_isolated_codex_home(
            tmp_path,
            sse_srv.base_url,
            plant_decoy_mcp_and_hooks=True,
        )

        async def _exercise() -> None:
            async with AppServerStdioClient(env=env, cwd=work_dir, request_timeout_sec=15.0) as client:
                assert client.server_info is not None
                user_agent = str(client.server_info.get("userAgent") or "")
                assert "0.166.0" in user_agent or "codex" in user_agent.lower()

                # 1) Author thread MUST have workspaceWrite (never silently coerced to readOnly!)
                author = await client.start_author_thread(work_dir, model="gpt-5", effort="medium")
                assert author.raw_response["sandbox"]["type"] == "workspaceWrite"
                assert author.raw_response["reasoningEffort"] == "medium"

                # 2) Verifier thread MUST have readOnly + high reasoningEffort
                verifier = await client.start_verifier_thread(work_dir, model="gpt-5", effort="high")
                assert verifier.raw_response["sandbox"]["type"] == "readOnly"
                assert verifier.raw_response["reasoningEffort"] == "high"

                # 3) Author turn executes real sandboxed exec_command writing probe_written.txt
                auth_turn = await client.run_turn(
                    author.thread_id,
                    f"WRITE_PROBE_FILE: yes\nWRITE_PROBE_DIR: {work_dir}\n",
                    cwd=work_dir,
                    model="gpt-5",
                    timeout_sec=25.0,
                )
                assert auth_turn.status == "completed"
                probe_file = work_dir / "probe_written.txt"
                assert probe_file.is_file(), f"Author workspaceWrite failed to write {probe_file}"
                assert "real_sandbox_ok" in probe_file.read_text(encoding="utf-8")

                # 4) Verifier turn enforces readOnlySandboxPolicy + wire-sanitized BUILTIN_VERIFIER_OUTPUT_SCHEMA
                ver_turn = await client.run_turn(
                    verifier.thread_id,
                    "Acceptance Criteria:\n- sandbox_verified\n\nHard Command Collector All Passed: True\n",
                    cwd=work_dir,
                    role="verifier",
                    model="gpt-5",
                    timeout_sec=20.0,
                )
                assert ver_turn.status == "completed"
                assert ver_turn.structured_output is not None
                assert ver_turn.structured_output["verdict"] == "PASS"
                assert ver_turn.structured_output["criteria_results"] == [
                    {"criterion": "sandbox_verified", "status": "PASS"}
                ]

        asyncio.run(_exercise())

        # Zero hook execution leakage
        assert not hook_marker.exists(), "Decoy hook executed despite --disable hooks!"

        # Inspect captured wire requests sent by real codex.exe to /v1/responses
        assert len(sse_srv.requests) >= 3
        first_tools = sse_srv.requests[0]["body"].get("tools") or []
        tool_names = {str(t.get("name") or t.get("type") or "") for t in first_tools}
        assert not any(n.startswith("mcp__") or "decoy" in n.lower() for n in tool_names), (
            f"External MCP server tool leaked into worker thread: {tool_names}"
        )
        assert "spawn_agent" not in tool_names
        assert "web_search" not in tool_names

        verifier_req = sse_srv.requests[-1]["body"]
        fmt = verifier_req["text"]["format"]
        assert fmt["type"] == "json_schema"
        assert fmt["strict"] is True
        wire_schema = fmt["schema"]
        assert "$schema" not in wire_schema
        assert "title" not in wire_schema
        assert wire_schema["required"] == BUILTIN_VERIFIER_OUTPUT_SCHEMA["required"]


def test_real_codex_live_interrupt_read_and_cross_process_resume(sandbox_tmp_path: Path) -> None:
    tmp_path = sandbox_tmp_path
    work_dir = (tmp_path / "workspace_resume").resolve()
    work_dir.mkdir(parents=True)

    with _loopback_responses_server() as sse_srv:
        _, env, _ = _setup_isolated_codex_home(tmp_path, sse_srv.base_url)

        async def _run_both_phases() -> None:
            async with AppServerStdioClient(
                codex_bin=REAL_CODEX_BIN, env=env, cwd=work_dir, request_timeout_sec=15.0
            ) as client1:
                assert client1.pid is not None
                pid1 = client1.pid
                author = await client1.start_author_thread(work_dir, model="gpt-5", effort="medium")
                tid = author.thread_id

                t1 = await client1.run_turn(
                    tid,
                    "Remember SECRET_MARKER_ALPHA. ECHO_TOKEN: turn1",
                    cwd=work_dir,
                    model="gpt-5",
                    timeout_sec=15.0,
                )
                assert t1.status == "completed"
                assert "ACK:turn1" in t1.final_message

                started_turn_ids: list[str] = []

                async def _trigger_interrupt() -> float:
                    deadline = time.monotonic() + 10.0
                    while (not started_turn_ids or not sse_srv.hang_started.is_set()) and time.monotonic() < deadline:
                        await asyncio.sleep(0.02)
                    assert started_turn_ids
                    t0 = time.monotonic()
                    await client1.interrupt_turn(tid, started_turn_ids[0])
                    return time.monotonic() - t0

                int_task = asyncio.create_task(_trigger_interrupt())
                t2 = await client1.run_turn(
                    tid,
                    "HANG_STREAM_FOR_INTERRUPT",
                    cwd=work_dir,
                    model="gpt-5",
                    on_turn_started=started_turn_ids.append,
                    timeout_sec=15.0,
                )
                int_latency = await int_task
                sse_srv.release_hang.set()
                assert int_latency < 1.5
                assert t2.status == "interrupted"

            # Verify first codex.exe process reaped cleanly
            reap_deadline = time.monotonic() + 3.0
            while _is_pid_alive(pid1) and time.monotonic() < reap_deadline:
                await asyncio.sleep(0.02)
            assert not _is_pid_alive(pid1)

            # Spawn a brand-new codex.exe process and verify read_thread -> resume_thread -> Turn 3
            async with AppServerStdioClient(
                codex_bin=REAL_CODEX_BIN, env=env, cwd=work_dir, request_timeout_sec=15.0
            ) as client2:
                read_res = await client2.read_thread(tid)
                assert read_res.status == "notLoaded"

                resumed = await client2.resume_thread(
                    tid,
                    cwd=work_dir,
                    role="author",
                    model="gpt-5",
                    effort="medium",
                )
                assert resumed.status == "idle"
                assert resumed.raw_response["sandbox"]["type"] == "workspaceWrite"
                assert resumed.raw_response["reasoningEffort"] == "medium"

                t3 = await client2.run_turn(
                    tid,
                    "ECHO_TOKEN: turn3_after_resume",
                    cwd=work_dir,
                    model="gpt-5",
                    timeout_sec=15.0,
                )
                assert t3.status == "completed"
                # Turn 1 history ("SECRET_MARKER_ALPHA") was preserved across processes!
                assert t3.final_message == "ACK:turn3_after_resume:prev=True"

        asyncio.run(_run_both_phases())


def test_real_codex_pipeline_turn_failed_preserves_budget_and_2step_e2e_completion(
    sandbox_tmp_path: Path,
) -> None:
    tmp_path = sandbox_tmp_path
    repo = _init_temp_git_repo(
        tmp_path / "real_2step_repo",
        {
            "calc_base.py": "def base_val(x: int) -> int:\n    return 0\n",
            "tests/test_base.py": (
                "from calc_base import base_val\n\n"
                "def test_base() -> None:\n"
                "    assert base_val(5) == 15\n"
            ),
            "calc_combined.py": "def combined_val(x: int) -> int:\n    return 0\n",
            "tests/test_combined.py": (
                "from calc_combined import combined_val\n\n"
                "def test_combined() -> None:\n"
                "    assert combined_val(5) == 45\n"
            ),
        },
    )

    with _loopback_responses_server() as sse_srv, _ephemeral_daemon(tmp_path) as settings:
        _, codex_env, _ = _setup_isolated_codex_home(
            tmp_path,
            sse_srv.base_url,
            plant_decoy_mcp_and_hooks=True,
        )

        spec = {
            "project_root": str(repo),
            "mode": "swarm",
            "title": "Real codex.exe 2-Step Pipeline E2E",
            "objective": "Implement calc_base and calc_combined on real codex.exe",
            "model": "gpt-5",
            "dep_mode": "none",
            "max_workers": 1,
            "steps": [
                {
                    "step_key": "step_base",
                    "title": "Implement base_val",
                    "instruction": "Implement base_val(x) returning x + 10 in calc_base.py",
                    "file_ownership": ["calc_base.py"],
                    "acceptance_criteria": ["base_val(5) == 15"],
                    "commands": [
                        {
                            "id": "pytest_base",
                            "argv": [sys.executable, "-m", "pytest", "-q", "tests/test_base.py"],
                            "cwd": ".",
                            "timeout_sec": 30,
                            "criteria": ["base_val(5) == 15"],
                        }
                    ],
                },
                {
                    "step_key": "step_combined",
                    "title": "Implement combined_val",
                    "instruction": "Implement combined_val(x) returning base_val(x) * 3 in calc_combined.py",
                    "dependencies": ["step_base"],
                    "file_ownership": ["calc_combined.py"],
                    "acceptance_criteria": ["combined_val(5) == 45"],
                    "commands": [
                        {
                            "id": "pytest_combined",
                            "argv": [sys.executable, "-m", "pytest", "-q", "tests/test_combined.py"],
                            "cwd": ".",
                            "timeout_sec": 30,
                            "criteria": ["combined_val(5) == 45"],
                        }
                    ],
                },
            ],
            "integration_commands": [
                {
                    "id": "full_suite",
                    "argv": [
                        sys.executable,
                        "-m",
                        "pytest",
                        "-q",
                        "tests/test_base.py",
                        "tests/test_combined.py",
                    ],
                    "cwd": ".",
                    "timeout_sec": 30,
                    "criteria": ["combined_val(5) == 45"],
                }
            ],
        }

        # Phase 1: Force real codex.exe to hit HTTP 401 -> turn status == "failed"
        # Verify PipelineRunner aborts cleanly WITHOUT burning rework attempt budget!
        sse_srv.mode = "http_401"
        sum_fail_path = tmp_path / "real_401_summary.json"
        runner_fail = PipelineRunner(
            settings=settings,
            explicit_daemon_url=True,
            codex_bin=REAL_CODEX_BIN,
            appserver_env=codex_env,
            output_path=sum_fail_path,
        )
        rc_fail, sum_fail = asyncio.run(runner_fail.execute(spec=spec))
        assert rc_fail != 0
        assert sum_fail["status"] == "turn_failed"
        assert "401" in (sum_fail["error"] or "") or "invalid_api_key" in (sum_fail["error"] or "")
        run_id = sum_fail["run_id"]
        assert run_id

        with httpx.Client(base_url=settings.base_url, trust_env=False) as http:
            snap_mid = http.get(f"/api/runs/{run_id}/snapshot").json()
        db_steps_mid = {s["step_key"]: s for s in snap_mid["workflow"]["steps"]}
        assert db_steps_mid["step_base"]["attempt"] == 0, "API 401 must NEVER advance rework attempt!"
        assert db_steps_mid["step_base"]["state"] == "implementing"
        assert db_steps_mid["step_combined"]["state"] == "planned"

        # Phase 2: Restore loopback Responses SSE server and --resume-run to full 2-step completion!
        sse_srv.mode = "normal"
        sum_ok_path = tmp_path / "real_ok_summary.json"
        runner_ok = PipelineRunner(
            settings=settings,
            explicit_daemon_url=True,
            codex_bin=REAL_CODEX_BIN,
            appserver_env=codex_env,
            output_path=sum_ok_path,
        )
        rc_ok, sum_ok = asyncio.run(runner_ok.execute(resume_run_id=run_id))
        assert rc_ok == 0, f"Real 2-step codex.exe pipeline failed: {sum_ok}"
        assert sum_ok["status"] == "completed"
        assert sum_ok["resumed"] is True

        steps_by_key = {s["step_key"]: s for s in sum_ok["steps"]}
        assert steps_by_key["step_base"]["state"] == "done"
        assert steps_by_key["step_base"]["reworks"] == 0
        assert steps_by_key["step_combined"]["state"] == "done"
        assert steps_by_key["step_combined"]["reworks"] == 0

        # Verify merged files in main repository
        assert "return x + 10" in (repo / "calc_base.py").read_text(encoding="utf-8")
        assert "return base_val(x) * 3" in (repo / "calc_combined.py").read_text(encoding="utf-8")

        # Verify persisted daemon snapshot: run completed, workspaces cleaned, distinct real UUID bindings
        with httpx.Client(base_url=settings.base_url, trust_env=False) as http:
            snap_done = http.get(f"/api/runs/{run_id}/snapshot").json()
        assert snap_done["run"]["status"] == "completed"
        for st in snap_done["workflow"]["steps"]:
            assert st["state"] == "done"
            assert st["owner_agent_id"].startswith("appserver://")
            assert st["verifier_agent_id"].startswith("appserver://")
            assert st["owner_agent_id"] != st["verifier_agent_id"]
        for ws in snap_done["workspaces"]:
            assert ws["status"] == "cleaned"
            assert not Path(ws["path"]).exists()
