"""M9 comprehensive fail-first & regression suite for `tri-orchestrator-hook` (`v0.6.0`)."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import tomllib
from typing import Any

import pytest

from orchestrator.config import Settings
from orchestrator.daemon import create_app
from orchestrator.hook_runner import (
    MAX_RESUME_CONTEXT_CHARS,
    MAX_RPC_TIMEOUT_SEC,
    NON_BLOCKABLE_PROGRESS_STATUSES,
    SUPPORTED_HOOK_EVENTS,
    build_hook_stdout_json,
    clamp_timeout,
    compute_stop_blocked_key,
    detect_explicit_mode_invocation,
    evaluate_stop_from_board,
    format_resume_context_from_board,
    main as hook_main,
    resolve_rpc_url,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_ROOT / "plugin"


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _invoke_hook_inproc(
    payload: dict[str, Any] | str,
    *,
    env: dict[str, str] | None = None,
    argv: list[str] | None = None,
) -> tuple[int, str]:
    raw_in = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    stdin_buf = io.BytesIO(raw_in.encode("utf-8"))
    stdout_buf = io.BytesIO()

    class _StdStream:
        def __init__(self, buffer: io.BytesIO) -> None:
            self.buffer = buffer

    code = hook_main(
        argv or [],
        stdin=_StdStream(stdin_buf),  # type: ignore[arg-type]
        stdout=_StdStream(stdout_buf),  # type: ignore[arg-type]
        env=env if env is not None else {},
    )
    return code, stdout_buf.getvalue().decode("utf-8")


class _MockRpcServer:
    """Lightweight deterministic `/api/rpc` server for unit & guardrail matrix tests."""

    def __init__(self, responder) -> None:
        self.responder = responder
        self.calls: list[dict[str, Any]] = []

        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length).decode("utf-8")
                body = json.loads(raw)
                outer.calls.append({"path": self.path, "body": body})
                status_code, resp_obj = outer.responder(self.path, body)
                encoded = json.dumps(resp_obj, ensure_ascii=False).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *_args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self._server.server_port)

    @property
    def env(self) -> dict[str, str]:
        return {"TRI_ORCH_HOST": "127.0.0.1", "TRI_ORCH_PORT": str(self.port)}

    def __enter__(self) -> _MockRpcServer:
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=3)


def test_m9_manifests_entrypoints_and_spike_report_validity() -> None:
    pyproject = tomllib.loads((PLUGIN_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == "0.6.3"
    scripts = pyproject["project"]["scripts"]
    assert scripts["tri-orchestrator-hook"] == "orchestrator.hook_runner:main"
    assert scripts["tri-orchestrator-challenge"] == "orchestrator.challenge_runner:main"

    codex_manifest = json.loads(
        (PLUGIN_DIR / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    assert codex_manifest["name"] == "tri-orchestrator"
    assert codex_manifest["version"] == "0.6.3"
    assert codex_manifest["hooks"] == "./hooks/hooks.json"

    # M9b single-manifest convergence: root plugin/plugin.json must not exist so Codex loads .codex-plugin/plugin.json
    assert not (PLUGIN_DIR / "plugin.json").exists()

    hooks_path = PLUGIN_DIR / "hooks" / "hooks.json"
    assert hooks_path.is_file()
    hooks_cfg = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert set(hooks_cfg.keys()) == {"hooks"}
    assert set(hooks_cfg["hooks"].keys()) == SUPPORTED_HOOK_EVENTS

    for event_name, groups in hooks_cfg["hooks"].items():
        assert isinstance(groups, list) and groups, f"Empty matcher groups for {event_name}"
        for group in groups:
            handlers = group["hooks"]
            assert isinstance(handlers, list) and handlers
            for handler in handlers:
                assert handler["type"] == "command"
                assert "${CLAUDE_PLUGIN_ROOT}" in handler["command"]
                assert "${CLAUDE_PLUGIN_ROOT}" in handler["commandWindows"]
                assert 1 <= int(handler["timeout"]) <= 5

    spike_doc = REPO_ROOT / "docs" / "verification" / "m9-hooks-spike-0.6.0.md"
    assert spike_doc.is_file()
    spike_text = spike_doc.read_text(encoding="utf-8")
    for token in (
        "0.166.0-alpha.5.1",
        "SessionStart",
        "PostCompact",
        "UserPromptSubmit",
        "Stop",
        "stop_hook_active",
        "HookTrustStatus",
    ):
        assert token in spike_text


def test_daemon_offline_exits_zero_fast_and_never_spawns_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dead_port = _free_loopback_port()
    empty_data_dir = tmp_path / "untouched-data"
    env = {
        "TRI_ORCH_HOST": "127.0.0.1",
        "TRI_ORCHESTRATOR_PORT": str(dead_port),
        "TRI_ORCH_DATA_DIR": str(empty_data_dir),
    }

    spawn_attempts: list[Any] = []
    orig_popen = subprocess.Popen

    def _forbid_spawn(*args: Any, **kwargs: Any) -> Any:
        spawn_attempts.append((args, kwargs))
        raise AssertionError("hook_runner MUST NEVER spawn any subprocess or daemon!")

    monkeypatch.setattr(subprocess, "Popen", _forbid_spawn)

    sample_events = [
        {
            "hook_event_name": "SessionStart",
            "session_id": "sess-offline-1",
            "cwd": str(tmp_path),
            "source": "compact",
            "model": "gpt-5",
            "permission_mode": "default",
            "transcript_path": None,
        },
        {
            "hook_event_name": "PostCompact",
            "session_id": "sess-offline-1",
            "cwd": str(tmp_path),
            "trigger": "auto",
            "turn_id": "t1",
            "model": "gpt-5",
            "transcript_path": None,
        },
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "sess-offline-1",
            "cwd": str(tmp_path),
            "prompt": "Use $boost to implement feature",
            "turn_id": "t1",
            "model": "gpt-5",
            "permission_mode": "default",
            "transcript_path": None,
        },
        {
            "hook_event_name": "Stop",
            "session_id": "sess-offline-1",
            "cwd": str(tmp_path),
            "turn_id": "t1",
            "model": "gpt-5",
            "permission_mode": "default",
            "stop_hook_active": False,
            "last_assistant_message": "Done part 1",
            "transcript_path": None,
        },
    ]

    for payload in sample_events:
        t0 = time.monotonic()
        code, out = _invoke_hook_inproc(payload, env=env)
        elapsed = time.monotonic() - t0
        assert code == 0
        event_name = payload["hook_event_name"]
        if event_name == "UserPromptSubmit":
            # Cold-boot fix: UserPromptSubmit with $boost returns local session_id even when 18790 is offline
            assert out != "", "UserPromptSubmit with $boost must return session_id when daemon offline"
            parsed = json.loads(out)
            assert "sess-offline-1" in parsed["hookSpecificOutput"]["additionalContext"]
        elif event_name == "SessionStart" and str(payload.get("source", "")).lower() == "startup":
            # SessionStart(startup) returns local session_id even when daemon offline
            assert out != ""
            parsed = json.loads(out)
            assert "sess-offline-1" in parsed["hookSpecificOutput"]["additionalContext"]
        else:
            assert out == ""
        assert elapsed <= MAX_RPC_TIMEOUT_SEC
        assert spawn_attempts == []
        assert not empty_data_dir.exists()

    # Restore Popen and verify real subprocess CLI execution is also fast (`<= 1.5s`) and silent
    monkeypatch.setattr(subprocess, "Popen", orig_popen)
    sub_env = dict(os.environ, **env)
    sub_env["PYTHONPATH"] = str(PLUGIN_DIR / "src")
    t0 = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-m", "orchestrator.hook_runner"],
        input=json.dumps(sample_events[-1]),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=sub_env,
        timeout=5,
    )
    elapsed_sub = time.monotonic() - t0
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert proc.stderr == ""
    assert elapsed_sub <= 1.5
    assert not empty_data_dir.exists()


def test_stalled_server_respects_1_5s_timeout_ceiling(tmp_path: Path) -> None:
    assert clamp_timeout(99.0) == MAX_RPC_TIMEOUT_SEC
    assert clamp_timeout(None, {"TRI_ORCH_HOOK_TIMEOUT_SEC": "30"}) == MAX_RPC_TIMEOUT_SEC

    release = threading.Event()

    def _stall_accept(sock: socket.socket) -> None:
        try:
            conn, _ = sock.accept()
            with conn:
                release.wait(timeout=3.0)
        except OSError:
            return

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(2)
        stalled_port = int(listener.getsockname()[1])
        th = threading.Thread(target=_stall_accept, args=(listener,), daemon=True)
        th.start()
        try:
            t0 = time.monotonic()
            code, out = _invoke_hook_inproc(
                {
                    "hook_event_name": "Stop",
                    "session_id": "sess-stall",
                    "cwd": str(tmp_path),
                    "stop_hook_active": False,
                },
                env={
                    "TRI_ORCH_PORT": str(stalled_port),
                    "TRI_ORCH_HOOK_TIMEOUT_SEC": "0.35",
                },
                argv=["--timeout-sec", "0.35"],
            )
            elapsed = time.monotonic() - t0
            assert code == 0
            assert out == ""
            assert elapsed < 1.2
        finally:
            release.set()
            th.join(timeout=2)


def test_session_start_and_post_compact_resume_context_cap_and_wire_schemas(
    tmp_path: Path,
) -> None:
    oversized_resume = (
        "run_id=run-long packet_version=3 unfinished=[s1:dispatched, s2:candidate_review] "
        + ("detail-padding-" * 90)
    )
    assert len(oversized_resume) > 1200

    def _responder(_path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        assert body["tool"] == "session_hook_check"
        sid = body["arguments"]["session_id"]
        if sid != "sess-bound":
            return 200, {"ok": True, "tool": "session_hook_check", "result": {"bound": False, "resume_context": ""}}
        return 200, {
            "ok": True,
            "tool": "session_hook_check",
            "result": {
                "bound": True,
                "run_id": "run-long",
                "resume_context": oversized_resume,
                "should_block_stop": False,
                "stop_reason": "",
                "blocked_key": "",
            },
        }

    with _MockRpcServer(_responder) as srv:
        # 1. SessionStart(compact) -> hookSpecificOutput with <= 800 chars
        code, out = _invoke_hook_inproc(
            {
                "hook_event_name": "SessionStart",
                "session_id": "sess-bound",
                "cwd": str(tmp_path),
                "source": "compact",
            },
            env=srv.env,
        )
        assert code == 0
        parsed_ss = json.loads(out)
        assert set(parsed_ss.keys()) == {"hookSpecificOutput"}
        assert set(parsed_ss["hookSpecificOutput"].keys()) == {"hookEventName", "additionalContext"}
        assert parsed_ss["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        ctx = parsed_ss["hookSpecificOutput"]["additionalContext"]
        assert 0 < len(ctx) <= MAX_RESUME_CONTEXT_CHARS

        # 2. SessionStart(clear / fork) -> fast-path exit 0 without network call
        before_calls = len(srv.calls)
        for non_resume_src in ("clear", "fork"):
            c_nr, out_nr = _invoke_hook_inproc(
                {
                    "hook_event_name": "SessionStart",
                    "session_id": "sess-bound",
                    "cwd": str(tmp_path),
                    "source": non_resume_src,
                },
                env=srv.env,
            )
            assert c_nr == 0 and out_nr == ""
        assert len(srv.calls) == before_calls

        # 3. PostCompact -> systemMessage ONLY (no hookSpecificOutput per Codex wire schema) & <= 800 chars
        code_pc, out_pc = _invoke_hook_inproc(
            {
                "hook_event_name": "PostCompact",
                "session_id": "sess-bound",
                "cwd": str(tmp_path),
                "trigger": "auto",
                "turn_id": "turn-9",
            },
            env=srv.env,
        )
        assert code_pc == 0
        parsed_pc = json.loads(out_pc)
        assert set(parsed_pc.keys()) == {"systemMessage"}
        assert "hookSpecificOutput" not in parsed_pc
        assert 0 < len(parsed_pc["systemMessage"]) <= MAX_RESUME_CONTEXT_CHARS

        # 4. Unbound session -> silent exit 0
        code_unb, out_unb = _invoke_hook_inproc(
            {
                "hook_event_name": "PostCompact",
                "session_id": "sess-other",
                "cwd": str(tmp_path),
                "trigger": "manual",
                "turn_id": "turn-10",
            },
            env=srv.env,
        )
        assert code_unb == 0 and out_unb == ""


def test_user_prompt_submit_mode_gating_and_subagent_isolation(tmp_path: Path) -> None:
    assert detect_explicit_mode_invocation("Use $boost to build parser") == "boost"
    assert detect_explicit_mode_invocation("/teamwork split modules") == "teamwork"
    assert detect_explicit_mode_invocation("请用 $swarm 并发推进") == "swarm"
    assert detect_explicit_mode_invocation("boost performance of loop") is None
    assert detect_explicit_mode_invocation("$pipeline 批量重构模块") is None
    # New: CJK no-space
    assert detect_explicit_mode_invocation("$swarm开始补全") == "swarm"
    assert detect_explicit_mode_invocation("/boost修复这个问题") == "boost"
    assert detect_explicit_mode_invocation("$teamwork重构模块") == "teamwork"
    # New: CJK prefix without space
    assert detect_explicit_mode_invocation("请用$boost修复") == "boost"
    # New: Markdown skill mentions
    assert detect_explicit_mode_invocation("[$boost](file:///path/SKILL.md) 修复问题") == "boost"
    assert detect_explicit_mode_invocation("[skill:$teamwork](C:/path/SKILL.md)") == "teamwork"
    # Negative: URLs and paths should NOT match
    assert detect_explicit_mode_invocation("http://example.com/boost") is None
    assert detect_explicit_mode_invocation("/usr/bin/swarm_helper") is None

    def _responder(_path: str, _body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return 200, {
            "ok": True,
            "tool": "session_hook_check",
            "result": {"bound": False, "run_id": None, "resume_context": ""},
        }

    with _MockRpcServer(_responder) as srv:
        # Ordinary chat does not even hit RPC
        code1, out1 = _invoke_hook_inproc(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "sess-chat",
                "cwd": str(tmp_path),
                "prompt": "Explain how SQLite WAL works",
            },
            env=srv.env,
        )
        assert code1 == 0 and out1 == ""
        assert len(srv.calls) == 0

        # Subagent UserPromptSubmit (`agent_id` present) never hits RPC
        code2, out2 = _invoke_hook_inproc(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "sess-sub",
                "agent_id": "/root/coder_1",
                "cwd": str(tmp_path),
                "prompt": "Implement $boost slice",
            },
            env=srv.env,
        )
        assert code2 == 0 and out2 == ""
        assert len(srv.calls) == 0

        # Root explicit `$teamwork` prompt injects non-blocking session_id hint cleanly
        code3, out3 = _invoke_hook_inproc(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "sess-root-tw",
                "cwd": str(tmp_path),
                "prompt": "请使用 $teamwork 完成模块重构",
            },
            env=srv.env,
        )
        assert code3 == 0
        parsed = json.loads(out3)
        assert set(parsed.keys()) == {"hookSpecificOutput"}
        assert "decision" not in parsed
        assert parsed["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        assert "sess-root-tw" in parsed["hookSpecificOutput"]["additionalContext"]
        assert len(parsed["hookSpecificOutput"]["additionalContext"]) <= MAX_RESUME_CONTEXT_CHARS


def _ready_board_fixture() -> dict[str, Any]:
    return {
        "run_id": "run-ready-1",
        "run": {"id": "run-ready-1", "mode": "boost", "status": "active", "workflow_version": 5},
        "latest_seq": 12,
        "latest_task_packet": {"version": 2},
        "control_requests": [],
        "workers": [
            {"name": "coder-1", "role": "coder", "status": "waiting", "agent_id": "/root/coder_1"},
        ],
        "workflow": {
            "steps": [
                {"id": "step-1", "step_key": "impl", "state": "submitted"},
            ]
        },
        "native_actions": {
            "needs_reconciliation": [],
            "needs_clarification": [],
            "next_actions": [
                {
                    "action_id": "act-1",
                    "action_type": "spawn_agent",
                    "source_type": "inbox",
                    "source_id": "msg-verify-1",
                    "recipient": "verifier-1",
                    "step_id": "step-1",
                    "summary": "Candidate verification ready",
                }
            ],
            "next_action_groups": [],
        },
        "progress_status": {
            "classification": "ready",
            "blockers": [],
            "executable_candidates": [{"kind": "native_spawn", "recipient": "verifier-1"}],
        },
    }


@pytest.mark.parametrize("non_block_cls", sorted(NON_BLOCKABLE_PROGRESS_STATUSES))
def test_stop_guardrail_3_blocks_none_of_non_ready_classifications(non_block_cls: str) -> None:
    snap = _ready_board_fixture()
    snap["progress_status"] = {"classification": non_block_cls, "blockers": []}
    should_block, reason, _ = evaluate_stop_from_board(snap)
    assert should_block is False
    assert reason == ""


def test_stop_guardrails_1_2_3_comprehensive_matrix(tmp_path: Path) -> None:
    # 1. Pure board evaluator checks:
    base = _ready_board_fixture()
    ok, reason, key1 = evaluate_stop_from_board(base)
    assert ok is True
    assert "run-ready-1" in reason
    assert key1 == compute_stop_blocked_key(base)

    # Guardrail 2a: stop_hook_active=True -> pass through
    assert evaluate_stop_from_board(base, stop_hook_active=True) == (False, "", "")
    # Guardrail 2b: same blocked_key -> pass through (single-shot cap)
    assert evaluate_stop_from_board(base, last_stop_blocked_key=key1) == (False, "", key1)
    # Advancing latest_seq changes blocked_key -> eligible again
    advanced = _ready_board_fixture()
    advanced["latest_seq"] = 13
    ok_adv, _, key2 = evaluate_stop_from_board(advanced, last_stop_blocked_key=key1)
    assert ok_adv is True and key2 != key1

    # Guardrail 3a: pending control_requests -> pass through
    with_ctrl = _ready_board_fixture()
    with_ctrl["control_requests"] = [{"id": "ctrl-1", "action": "pause", "status": "pending"}]
    assert evaluate_stop_from_board(with_ctrl) == (False, "", "")

    # Guardrail 3b: worker stopped or killed -> pass through
    for w_state in ("stopped", "killed"):
        with_stopped = _ready_board_fixture()
        with_stopped["workers"] = [{"name": "coder-1", "role": "coder", "status": w_state}]
        assert evaluate_stop_from_board(with_stopped) == (False, "", "")

    # Guardrail 3c: needs_reconciliation / needs_clarification -> pass through
    with_recon = _ready_board_fixture()
    with_recon["native_actions"]["needs_reconciliation"] = [{"action_id": "a1"}]
    assert evaluate_stop_from_board(with_recon) == (False, "", "")

    # Resume context formatting check (`<= 800` chars)
    resume_str = format_resume_context_from_board(base)
    assert "run_id=run-ready-1" in resume_str
    assert "packet_version=2" in resume_str
    assert "impl:submitted" in resume_str
    assert len(resume_str) <= MAX_RESUME_CONTEXT_CHARS

    # 2. Wire-level fast-path checks (`stop_hook_active=True`, subagent `agent_id`, malformed JSON):
    def _fail_if_called(_path: str, _body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        raise AssertionError("Should not query daemon on local fast-path guardrails!")

    with _MockRpcServer(_fail_if_called) as srv:
        # Re-entrant Stop (`stop_hook_active=True`)
        c1, o1 = _invoke_hook_inproc(
            {
                "hook_event_name": "Stop",
                "session_id": "sess-1",
                "cwd": str(tmp_path),
                "stop_hook_active": True,
            },
            env=srv.env,
        )
        assert c1 == 0 and o1 == ""

        # Subagent turn stop (`agent_id` present or `SubagentStop`)
        c2, o2 = _invoke_hook_inproc(
            {
                "hook_event_name": "Stop",
                "session_id": "sess-1",
                "agent_id": "/root/coder_1",
                "cwd": str(tmp_path),
                "stop_hook_active": False,
            },
            env=srv.env,
        )
        assert c2 == 0 and o2 == ""

        c3, o3 = _invoke_hook_inproc("{malformed-json", env=srv.env)
        assert c3 == 0 and o3 == ""
        assert len(srv.calls) == 0


def test_live_daemon_session_bind_and_single_shot_stop_e2e(tmp_path: Path) -> None:
    """End-to-end integration against the real `create_app` daemon `/api/rpc` handlers."""
    data_dir = tmp_path / "runtime"
    proj_dir = tmp_path / "repo"
    proj_dir.mkdir()
    settings = Settings(host="127.0.0.1", port=_free_loopback_port(), data_dir=data_dir)
    app = create_app(settings, start_watchdog=False, manage_pid_file=False)

    import asyncio
    from orchestrator.hook_runner import call_daemon_rpc

    ready_evt = threading.Event()
    stop_evt = threading.Event()
    holder: dict[str, Any] = {}

    def _run_asgi_bridge() -> None:
        async def _runner() -> None:
            async with app.router.lifespan_context(app):
                dispatcher = app.state.dispatcher

                def _dispatch_http(path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
                    if path != "/api/rpc":
                        return 404, {"ok": False, "error": "not found"}
                    try:
                        tool = body["tool"]
                        args = dispatcher.validate_arguments(tool, body.get("arguments"))
                        res = dispatcher.dispatch(tool, args)
                        return 200, {"ok": True, "tool": tool, "result": res}
                    except Exception as exc:
                        return 400, {"ok": False, "error": str(exc)}

                with _MockRpcServer(_dispatch_http) as bridge:
                    holder["port"] = bridge.port
                    ready_evt.set()
                    while not stop_evt.is_set():
                        await asyncio.sleep(0.02)

        asyncio.run(_runner())

    bridge_thread = threading.Thread(target=_run_asgi_bridge, daemon=True)
    bridge_thread.start()
    assert ready_evt.wait(timeout=5.0)

    try:
        env = {"TRI_ORCH_HOST": "127.0.0.1", "TRI_ORCH_PORT": str(holder["port"])}
        rpc_url = resolve_rpc_url(env)
        assert rpc_url == f"http://127.0.0.1:{holder['port']}/api/rpc"

        import urllib.request

        def _rpc(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
            req = urllib.request.Request(
                rpc_url,
                data=json.dumps({"tool": tool, "arguments": arguments}).encode("utf-8"),
                headers={"Content-Type": "application/json", "Connection": "close"},
                method="POST",
            )
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10.0) as resp:
                decoded = json.loads(resp.read().decode("utf-8"))
            assert decoded.get("ok") is True, f"RPC {tool} failed: {decoded}"
            return decoded["result"]

        # 1. Create active workflow5 run bound to `sess-live-root`
        run = _rpc(
            "run_create",
            {
                "mode": "boost",
                "title": "M9 Live Hook E2E",
                "project_root": str(proj_dir),
                "session_id": "sess-live-root",
            },
        )
        run_id = run["id"]
        _rpc(
            "task_packet_save",
            {
                "run_id": run_id,
                "title": "Packet 1",
                "goal": "Ship M9 safely",
                "file_ownership": {"coder-1": ["src/mod.py"]},
                "acceptance_criteria": ["Tests pass"],
                "status": "frozen",
            },
        )

        # Also verify HTTP 400 error responses close sockets cleanly with 0 ResourceWarnings under `-W error`
        assert call_daemon_rpc(rpc_url, "unknown_nonexistent_tool", {}, timeout_sec=1.0) is None

        # Unbound ordinary chat session in the same repo MUST pass through silently (Guardrail 1)
        code_unb, out_unb = _invoke_hook_inproc(
            {
                "hook_event_name": "Stop",
                "session_id": "sess-ordinary-chat",
                "cwd": str(proj_dir),
                "stop_hook_active": False,
            },
            env=env,
        )
        assert code_unb == 0 and out_unb == ""

        # 2. Register pre-spawn worker + lazy verifier + step + pending inbox message -> creates ready `spawn_agent` next_action!
        _rpc(
            "worker_register",
            {
                "run_id": run_id,
                "name": "coder-1",
                "role": "coder",
                "task_name": "coder_1",
                "status": "waiting",
            },
        )
        _rpc(
            "worker_register",
            {
                "run_id": run_id,
                "name": "verifier-1",
                "role": "verifier",
                "status": "waiting",
            },
        )
        step = _rpc(
            "workflow_step_save",
            {
                "run_id": run_id,
                "step_key": "impl-1",
                "owner": "coder-1",
                "verifier": "verifier-1",
                "criteria": ["Tests pass"],
            },
        )
        _rpc(
            "workflow_step_advance",
            {
                "step_id": step["id"],
                "action": "dispatch",
                "expected_revision": step["revision"],
            },
        )
        _rpc(
            "inbox_send",
            {
                "run_id": run_id,
                "sender": "coordinator",
                "recipient": "coder-1",
                "key": "assign-impl-1",
                "kind": "task",
                "summary": "Start impl-1",
                "body_md": "Implement src/mod.py",
                "step_id": step["id"],
            },
        )

        # 3. Compact / Resume hooks emit <= 800 char resume_context
        c_ss, out_ss = _invoke_hook_inproc(
            {
                "hook_event_name": "SessionStart",
                "session_id": "sess-live-root",
                "cwd": str(proj_dir),
                "source": "compact",
            },
            env=env,
        )
        assert c_ss == 0
        ss_json = json.loads(out_ss)
        assert run_id in ss_json["hookSpecificOutput"]["additionalContext"]
        assert len(ss_json["hookSpecificOutput"]["additionalContext"]) <= MAX_RESUME_CONTEXT_CHARS

        # 4. First Stop (`stop_hook_active=False`) MUST block once with continuation reason
        c_stop1, out_stop1 = _invoke_hook_inproc(
            {
                "hook_event_name": "Stop",
                "session_id": "sess-live-root",
                "cwd": str(proj_dir),
                "stop_hook_active": False,
            },
            env=env,
        )
        assert c_stop1 == 0
        stop1_json = json.loads(out_stop1)
        assert set(stop1_json.keys()) == {"decision", "reason"}
        assert stop1_json["decision"] == "block"
        assert run_id in stop1_json["reason"]

        # 5. Second Stop WITHOUT state progression (`last_stop_blocked_key` matches) MUST pass through (Guardrail 2)!
        c_stop2, out_stop2 = _invoke_hook_inproc(
            {
                "hook_event_name": "Stop",
                "session_id": "sess-live-root",
                "cwd": str(proj_dir),
                "stop_hook_active": False,
            },
            env=env,
        )
        assert c_stop2 == 0 and out_stop2 == ""

        # 6. Advance board seq (`board_post`), verify ready state would be eligible again,
        #    THEN add a stopped worker and verify Guardrail 3 immediately suppresses Stop block!
        _rpc(
            "board_post",
            {"run_id": run_id, "sender": "coordinator", "summary": "Progress note"},
        )
        _rpc(
            "worker_status_update",
            {"run_id": run_id, "worker_name": "coder-1", "status": "stopped"},
        )
        c_stop3, out_stop3 = _invoke_hook_inproc(
            {
                "hook_event_name": "Stop",
                "session_id": "sess-live-root",
                "cwd": str(proj_dir),
                "stop_hook_active": False,
            },
            env=env,
        )
        assert c_stop3 == 0 and out_stop3 == ""

        # 7. On Windows, also execute the exact `commandWindows` shell snippet from `plugin/hooks/hooks.json`!
        if os.name == "nt":
            pwsh = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
            if pwsh:
                hooks_cfg = json.loads((PLUGIN_DIR / "hooks" / "hooks.json").read_text(encoding="utf-8"))
                cmd_win = hooks_cfg["hooks"]["SessionStart"][0]["hooks"][0]["commandWindows"].replace(
                    "${CLAUDE_PLUGIN_ROOT}", str(PLUGIN_DIR)
                )
                sub_env = dict(os.environ, **env, CLAUDE_PLUGIN_ROOT=str(PLUGIN_DIR))
                proc = subprocess.run(
                    [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", cmd_win],
                    input=json.dumps(
                        {
                            "hook_event_name": "SessionStart",
                            "session_id": "sess-live-root",
                            "cwd": str(proj_dir),
                            "source": "resume",
                        }
                    ),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    env=sub_env,
                    timeout=5,
                )
                assert proc.returncode == 0
                pwsh_out = json.loads(proc.stdout)
                assert run_id in pwsh_out["hookSpecificOutput"]["additionalContext"]
    finally:
        stop_evt.set()
        bridge_thread.join(timeout=5)


def test_upgrade_script_verifies_schema9_capabilities(tmp_path: Path) -> None:
    upgrade_path = REPO_ROOT / "scripts" / "upgrade.py"
    spec = importlib.util.spec_from_file_location("m9_upgrade_check", upgrade_path)
    assert spec is not None and spec.loader is not None
    upgrade_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(upgrade_mod)

    assert upgrade_mod.supported_schema(PLUGIN_DIR) == 10
    assert upgrade_mod.package_identity(PLUGIN_DIR) == {
        "name": "tri-orchestrator",
        "version": "0.6.3",
    }

    backup_dir = tmp_path / "backup"
    data_dir = tmp_path / "data"
    backup_dir.mkdir()
    data_dir.mkdir()

    base_caps = [
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
    ]
    new_060_caps = [
        "macro_rpc_v1",
        "compact_board_v1",
        "workflow5_direct_spawn_v1",
        "session_hooks_v1",
        "agent_model_tracking_v1",
    ]

    state = {"capabilities": list(base_caps)}

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            payload = {
                "status": "ok",
                "ok": True,
                "pid": os.getpid(),
                "software_version": "0.6.3",
                "schema_version": 10,
                "data_dir": str(data_dir.resolve()),
                "port": self.server.server_port,  # type: ignore[attr-defined]
                "capabilities": state["capabilities"],
            }
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_args: Any) -> None:
            return

    with ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler) as srv:
        (backup_dir / "prepared.json").write_text(
            json.dumps(
                {
                    "data_dir": str(data_dir.resolve()),
                    "port": srv.server_port,
                    "source_identity": {"name": "tri-orchestrator", "version": "0.6.3"},
                    "supported_schema": 10,
                }
            ),
            encoding="utf-8",
        )
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            # Missing 0.6.x capabilities must fail verification on schema 10
            with pytest.raises(upgrade_mod.UpgradeError, match="mismatch"):
                upgrade_mod.verify(backup_dir)

            state["capabilities"] = base_caps + new_060_caps[:-1]
            with pytest.raises(upgrade_mod.UpgradeError, match="mismatch"):
                upgrade_mod.verify(backup_dir)

            # With all required capabilities (16 total) present, verification succeeds
            state["capabilities"] = base_caps + new_060_caps
            verified = upgrade_mod.verify(backup_dir)
            assert verified["status"] == "backend_verified"
        finally:
            srv.shutdown()
            th.join(timeout=3)



def test_cold_boot_user_prompt_submit_returns_session_id_when_18790_offline(
    tmp_path: Path,
) -> None:
    """UserPromptSubmit with explicit $boost still returns session_id when 18790 is offline (cold-boot fix)."""
    dead_port = _free_loopback_port()
    env = {
        "TRI_ORCH_HOST": "127.0.0.1",
        "TRI_ORCH_PORT": str(dead_port),
    }
    code, out = _invoke_hook_inproc(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "sess-cold-1",
            "cwd": str(tmp_path),
            "prompt": "$boost fix bug",
        },
        env=env,
    )
    assert code == 0
    assert out != "", "Cold-boot UserPromptSubmit must return session_id even when 18790 is offline"
    parsed = json.loads(out)
    assert parsed["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "sess-cold-1" in parsed["hookSpecificOutput"]["additionalContext"]
    assert len(parsed["hookSpecificOutput"]["additionalContext"]) <= MAX_RESUME_CONTEXT_CHARS


def test_session_start_startup_returns_session_id_when_18790_offline(
    tmp_path: Path,
) -> None:
    """SessionStart(source='startup') returns root session_id even when 18790 is offline."""
    dead_port = _free_loopback_port()
    env = {
        "TRI_ORCH_HOST": "127.0.0.1",
        "TRI_ORCH_PORT": str(dead_port),
    }
    code, out = _invoke_hook_inproc(
        {
            "hook_event_name": "SessionStart",
            "session_id": "sess-startup-1",
            "cwd": str(tmp_path),
            "source": "startup",
        },
        env=env,
    )
    assert code == 0
    assert out != "", "SessionStart(startup) must return session_id even when 18790 is offline"
    parsed = json.loads(out)
    assert parsed["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "sess-startup-1" in parsed["hookSpecificOutput"]["additionalContext"]
    assert len(parsed["hookSpecificOutput"]["additionalContext"]) <= 220

    # Also verify online daemon returning bound=False still emits <= 220 char local session_id
    def _unbound_responder(_path: str, _body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return 200, {"ok": True, "tool": "session_hook_check", "result": {"bound": False, "resume_context": ""}}

    with _MockRpcServer(_unbound_responder) as srv:
        code_u, out_u = _invoke_hook_inproc(
            {
                "hook_event_name": "SessionStart",
                "session_id": "sess-startup-unbound",
                "cwd": str(tmp_path),
                "source": "startup",
            },
            env=srv.env,
        )
        assert code_u == 0
        parsed_u = json.loads(out_u)
        ctx_u = parsed_u["hookSpecificOutput"]["additionalContext"]
        assert "sess-startup-unbound" in ctx_u
        assert len(ctx_u) <= 220

    # clear and fork sources should still be silent
    for silent_source in ("clear", "fork"):
        code_s, out_s = _invoke_hook_inproc(
            {
                "hook_event_name": "SessionStart",
                "session_id": "sess-startup-1",
                "cwd": str(tmp_path),
                "source": silent_source,
            },
            env=env,
        )
        assert code_s == 0 and out_s == ""


def test_mode_prompt_re_cjk_and_skill_mentions() -> None:
    """_MODE_PROMPT_RE matches CJK no-space, prefix CJK, and markdown skill mentions (and ignores removed $pipeline)."""
    assert detect_explicit_mode_invocation("$pipeline 批量重构模块") is None
    assert detect_explicit_mode_invocation("/pipeline 执行DAG") is None

    # CJK no-space (trailing CJK)
    assert detect_explicit_mode_invocation("$swarm开始补全") == "swarm"
    assert detect_explicit_mode_invocation("/boost修复这个问题") == "boost"
    assert detect_explicit_mode_invocation("$teamwork重构模块") == "teamwork"

    # CJK prefix (leading CJK without space)
    assert detect_explicit_mode_invocation("请用$boost修复") == "boost"

    # Markdown skill mentions
    assert detect_explicit_mode_invocation("[$boost](file:///path/SKILL.md) 修复问题") == "boost"
    assert detect_explicit_mode_invocation("[skill:$teamwork](C:/path/SKILL.md)") == "teamwork"

    # Negative: URLs and paths should NOT match (swarm after / in a path context is preceded by alnum)
    assert detect_explicit_mode_invocation("http://example.com/boost") is None
    assert detect_explicit_mode_invocation("/usr/bin/swarm_helper") is None


def test_session_start_startup_merges_resume_context_when_bound(
    tmp_path: Path,
) -> None:
    """SessionStart(startup) merges resume_context from daemon when session is already bound."""
    def _responder(_path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        tool = body["tool"]
        if tool == "session_hook_check":
            return 200, {
                "ok": True,
                "tool": "session_hook_check",
                "result": {
                    "bound": True,
                    "run_id": "run-startup-bound",
                    "resume_context": "run_id=run-startup-bound mode=boost wf=v5 active resume state",
                    "should_block_stop": False,
                },
            }
        return 200, {"ok": True, "result": {}}

    with _MockRpcServer(_responder) as srv:
        code, out = _invoke_hook_inproc(
            {
                "hook_event_name": "SessionStart",
                "session_id": "sess-bound-startup",
                "cwd": str(tmp_path),
                "source": "startup",
            },
            env=srv.env,
        )
        assert code == 0
        parsed = json.loads(out)
        ctx = parsed["hookSpecificOutput"]["additionalContext"]
        # When bound, should contain BOTH session_id and the resume_context from daemon
        assert "sess-bound-startup" in ctx
        assert "run-startup-bound" in ctx
        assert len(ctx) <= MAX_RESUME_CONTEXT_CHARS


def test_live_daemon_workflow5_transition_stop_hook_blocks_once_e2e(tmp_path: Path) -> None:
    """Stop hook honors server-side workflow5 coordinator transition blocks (e.g. candidate_review)."""
    import asyncio
    from orchestrator.hook_runner import call_daemon_rpc

    data_dir = tmp_path / "runtime_wf5"
    proj_dir = tmp_path / "repo_wf5"
    proj_dir.mkdir()
    (proj_dir / "src").mkdir()
    (proj_dir / "src" / "mod.py").write_text("x = 1\n", encoding="utf-8")

    settings = Settings(host="127.0.0.1", port=_free_loopback_port(), data_dir=data_dir)
    app = create_app(settings, start_watchdog=False, manage_pid_file=False)

    ready_evt = threading.Event()
    stop_evt = threading.Event()
    holder: dict[str, Any] = {}

    def _run_asgi_bridge() -> None:
        async def _runner() -> None:
            async with app.router.lifespan_context(app):
                dispatcher = app.state.dispatcher

                def _dispatch_http(path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
                    if path != "/api/rpc":
                        return 404, {"ok": False, "error": "not found"}
                    try:
                        tool = body["tool"]
                        args = dispatcher.validate_arguments(tool, body.get("arguments"))
                        res = dispatcher.dispatch(tool, args)
                        return 200, {"ok": True, "tool": tool, "result": res}
                    except Exception as exc:
                        return 400, {"ok": False, "error": str(exc)}

                with _MockRpcServer(_dispatch_http) as bridge:
                    holder["port"] = bridge.port
                    ready_evt.set()
                    while not stop_evt.is_set():
                        await asyncio.sleep(0.02)

        asyncio.run(_runner())

    bridge_thread = threading.Thread(target=_run_asgi_bridge, daemon=True)
    bridge_thread.start()
    assert ready_evt.wait(timeout=5.0)

    try:
        env = {"TRI_ORCH_HOST": "127.0.0.1", "TRI_ORCH_PORT": str(holder["port"])}
        rpc_url = resolve_rpc_url(env)
        assert rpc_url is not None

        import urllib.request

        def _rpc(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
            req = urllib.request.Request(
                rpc_url,
                data=json.dumps({"tool": tool, "arguments": arguments}).encode("utf-8"),
                headers={"Content-Type": "application/json", "Connection": "close"},
                method="POST",
            )
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10.0) as resp:
                decoded = json.loads(resp.read().decode("utf-8"))
            assert decoded.get("ok") is True, f"RPC {tool} failed: {decoded}"
            return decoded["result"]

        run = _rpc(
            "run_create",
            {
                "mode": "boost",
                "title": "WF5 Stop Transition Gate",
                "project_root": str(proj_dir),
                "session_id": "sess-wf5-stop",
            },
        )
        run_id = run["id"]
        _rpc(
            "task_packet_save",
            {
                "run_id": run_id,
                "title": "Packet WF5",
                "goal": "Implement mod.py",
                "file_ownership": {"coder_1": ["src/mod.py"]},
                "acceptance_criteria": ["mod.py updated"],
            },
        )
        prep = _rpc(
            "workflow_prepare_dispatch",
            {
                "run_id": run_id,
                "create_workspace": True,
                "owner": "coder_1",
                "task_name": "coder_1",
                "verifier": "verifier_1",
                "step_key": "impl-mod",
                "instruction": "Edit src/mod.py",
            },
        )
        step_id = prep["step"]["id"]
        msg_id = prep["message_id"]

        _rpc(
            "native_action_record_call",
            {
                "run_id": run_id,
                "action_id": prep["action_id"],
                "claim_token": prep["claim_token"],
                "expected_revision": prep["expected_revision"],
                "outcome": "confirmed",
                "raw_response": {"task_name": "/root/coder_1"},
            },
        )
        _rpc(
            "workflow_author_turn",
            {
                "run_id": run_id,
                "step_id": step_id,
                "message_id": msg_id,
                "phase": "accept",
                "summary": "Accepted impl-mod",
            },
        )
        ws_path = Path(prep["workspace"]["path"])
        (ws_path / "src" / "mod.py").write_text("x = 2\n", encoding="utf-8")
        _rpc(
            "workflow_author_turn",
            {
                "run_id": run_id,
                "step_id": step_id,
                "message_id": msg_id,
                "phase": "submit",
                "summary": "Implemented mod.py",
                "post_board_note": True,
            },
        )

        # At candidate_review with all workers waiting, Stop hook MUST block once (even with normal_wait)
        c1, out1 = _invoke_hook_inproc(
            {
                "hook_event_name": "Stop",
                "session_id": "sess-wf5-stop",
                "cwd": str(proj_dir),
                "stop_hook_active": False,
            },
            env=env,
        )
        assert c1 == 0
        stop_json = json.loads(out1)
        assert stop_json["decision"] == "block"
        assert run_id in stop_json["reason"]

        # Second Stop without state change MUST pass through (single-shot cap)
        c2, out2 = _invoke_hook_inproc(
            {
                "hook_event_name": "Stop",
                "session_id": "sess-wf5-stop",
                "cwd": str(proj_dir),
                "stop_hook_active": False,
            },
            env=env,
        )
        assert c2 == 0 and out2 == ""
    finally:
        stop_evt.set()
        bridge_thread.join(timeout=5)
