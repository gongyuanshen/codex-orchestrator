"""Unit and isolated end-to-end tests for M11 AppServerStdioClient & tri-orchestrator-pipeline."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import textwrap
import threading
import time
from typing import Any

import httpx
import pytest
import uvicorn

from orchestrator.appserver_client import (
    AppServerError,
    AppServerStdioClient,
    BUILTIN_VERIFIER_OUTPUT_SCHEMA,
    format_appserver_agent_id,
    normalize_thread_id,
    resolve_appserver_argv,
)
from orchestrator.config import Settings
from orchestrator.daemon import create_app
from orchestrator.mcp_shim import _is_pid_alive
from orchestrator.pipeline_runner import (
    PipelineError,
    PipelineRunner,
    PipelineSpecError,
    _canonical_json_bytes,
    _read_tail,
    _resolve_commands_for_root,
    _resolve_workspace_dep_mode,
    _sanitize_pytest_cache_argv,
    main as pipeline_main,
    validate_pipeline_spec,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


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
            creationflags=flags,
        )

    _git("init")
    _git("config", "user.name", "m11-test")
    _git("config", "user.email", "m11-test@localhost")
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

    def _serve() -> None:
        server.run()

    thread = threading.Thread(target=_serve, name="ephemeral-m11-daemon", daemon=True)
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
        raise RuntimeError(f"Ephemeral test daemon failed to start at {settings.base_url}")

    try:
        yield settings
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)


def _write_mock_appserver_script(script_path: Path) -> Path:
    """Write a deterministic stdio JSON-RPC mock ``codex app-server`` driven by ``MOCK_APPSERVER_STATE``."""
    code = textwrap.dedent(
        """\
        from __future__ import annotations
        import json
        import os
        from pathlib import Path
        import subprocess
        import sys
        import threading
        import time

        STATE_FILE = Path(os.environ["MOCK_APPSERVER_STATE"])
        LOG_LOCK = threading.Lock()
        WRITE_LOCK = threading.Lock()
        PENDING_REQS: dict[str, tuple[float, threading.Event, dict]] = {}
        ACTIVE_SLEEPER_EVENTS: dict[str, threading.Event] = {}

        def load_cfg() -> dict:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))

        def append_event(entry: dict) -> None:
            with LOG_LOCK:
                log_path = STATE_FILE.with_suffix(".events.jsonl")
                with log_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\\n")

        def send_json(payload: dict) -> None:
            raw = json.dumps(payload, ensure_ascii=False) + "\\n"
            with WRITE_LOCK:
                sys.stdout.write(raw)
                sys.stdout.flush()

        def call_client_request(req_id: str, method: str, params: dict) -> tuple[dict, float]:
            ev = threading.Event()
            t0 = time.perf_counter()
            PENDING_REQS[req_id] = (t0, ev, {})
            send_json({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
            if not ev.wait(timeout=5.0):
                raise RuntimeError(f"Client did not respond to {method} within 5s")
            t_start, _, holder = PENDING_REQS.pop(req_id)
            elapsed_ms = (time.perf_counter() - t_start) * 1000.0
            return holder["msg"], elapsed_ms

        def handle_turn_worker(req_id, params: dict) -> None:
            cfg = load_cfg()
            scenario = cfg["scenario"]
            tid = params["threadId"]
            cwd = Path(params["cwd"])
            prompt = params["input"][0]["text"]
            is_verifier = "outputSchema" in params and params.get("sandboxPolicy", {}).get("type") == "readOnly"

            turn_idx = int(time.time() * 1000000) % 100000000
            turn_id = f"turn_{tid}_{turn_idx}"
            stop_ev = threading.Event()
            ACTIVE_SLEEPER_EVENTS[turn_id] = stop_ev

            append_event({
                "type": "turn_start",
                "threadId": tid,
                "turnId": turn_id,
                "role": "verifier" if is_verifier else "author",
                "cwd": str(cwd),
                "approvalPolicy": params.get("approvalPolicy"),
                "sandboxPolicy": params.get("sandboxPolicy"),
                "effort": params.get("effort"),
                "has_output_schema": "outputSchema" in params,
                "outputSchema_keys": sorted(params["outputSchema"].keys()) if "outputSchema" in params else [],
                "prompt": prompt,
            })

            send_json({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"turn": {"id": turn_id, "items": [], "status": "inProgress", "error": None}},
            })
            send_json({
                "jsonrpc": "2.0",
                "method": "turn/started",
                "params": {"threadId": tid, "turn": {"id": turn_id, "items": [], "status": "inProgress", "error": None}},
            })

            try:
                if scenario == "interception_suite" and not is_verifier:
                    checks = [
                        ("srv_cmd_1", "item/commandExecution/requestApproval", {"threadId": tid, "turnId": turn_id, "itemId": "it1", "command": "rm -rf /"}),
                        ("srv_file_1", "item/fileChange/requestApproval", {"threadId": tid, "turnId": turn_id, "itemId": "it2"}),
                        ("srv_perm_1", "item/permissions/requestApproval", {"threadId": tid, "turnId": turn_id, "itemId": "it3", "permissions": {"network": True}}),
                        ("srv_mcp_1", "mcpServer/elicitation/request", {"threadId": tid, "turnId": turn_id, "serverName": "ext", "mode": "form", "message": "enter secret"}),
                    ]
                    if cfg.get("include_legacy_approvals"):
                        checks.extend([
                            ("srv_legacy_exec", "execCommandApproval", {"conversationId": tid, "callId": "c1", "command": ["rm", "-rf"], "cwd": str(cwd)}),
                            ("srv_legacy_patch", "applyPatchApproval", {"conversationId": tid, "callId": "c2", "fileChanges": {}}),
                        ])
                    if cfg.get("include_user_input", True):
                        checks.append(("srv_input_1", "item/tool/requestUserInput", {"threadId": tid, "turnId": turn_id, "itemId": "it4", "questions": [{"id": "q1", "header": "Auth", "question": "Which key?"}]}))
                    latencies = {}
                    responses = {}
                    for s_id, s_method, s_params in checks:
                        resp_msg, ms = call_client_request(s_id, s_method, s_params)
                        latencies[s_method] = round(ms, 3)
                        responses[s_method] = resp_msg.get("result")
                    append_event({"type": "interception_results", "latencies_ms": latencies, "responses": responses})
                    final_txt = json.dumps({"latencies_ms": latencies, "responses": responses})
                    send_json({"jsonrpc": "2.0", "method": "item/agentMessage/delta", "params": {"threadId": tid, "turnId": turn_id, "itemId": "msg1", "delta": final_txt}})
                    send_json({"jsonrpc": "2.0", "method": "item/completed", "params": {"threadId": tid, "turnId": turn_id, "completedAtMs": 1, "item": {"id": "msg1", "type": "agentMessage", "text": final_txt}}})
                    send_json({"jsonrpc": "2.0", "method": "turn/completed", "params": {"threadId": tid, "turn": {"id": turn_id, "items": [], "status": "completed", "error": None}}})
                    return

                if scenario == "hang_for_stop" and not is_verifier:
                    flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
                    grandchild = subprocess.Popen(
                        [sys.executable, "-c", "import time; time.sleep(60)"],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=flags,
                    )
                    if "grandchild_pid_file" in cfg:
                        pid_file = Path(cfg["grandchild_pid_file"])
                        pid_file.write_text(str(grandchild.pid), encoding="utf-8")
                    if "grandchild_pids_dir" in cfg:
                        pids_dir = Path(cfg["grandchild_pids_dir"])
                        pids_dir.mkdir(parents=True, exist_ok=True)
                        (pids_dir / f"{tid}.pid").write_text(str(grandchild.pid), encoding="utf-8")
                    send_json({"jsonrpc": "2.0", "method": "item/agentMessage/delta", "params": {"threadId": tid, "turnId": turn_id, "itemId": "msg_hang", "delta": "working..."}})
                    interrupted = stop_ev.wait(timeout=30.0)
                    status_str = "interrupted" if interrupted else "completed"
                    send_json({"jsonrpc": "2.0", "method": "turn/completed", "params": {"threadId": tid, "turn": {"id": turn_id, "items": [], "status": status_str, "error": None}}})
                    return

                if scenario == "api_turn_failures" and not is_verifier:
                    counter_file = STATE_FILE.with_suffix(".api_fail_counts.json")
                    with LOG_LOCK:
                        counts = json.loads(counter_file.read_text("utf-8")) if counter_file.exists() else {}
                        turn_num = counts.get(tid, 0) + 1
                        counts[tid] = turn_num
                        counter_file.write_text(json.dumps(counts), encoding="utf-8")
                    fail_mode = cfg.get("fail_mode", "401")
                    if fail_mode == "401":
                        err_obj = {"message": "HTTP 401 Unauthorized: invalid_api_key provided"}
                        send_json({"jsonrpc": "2.0", "method": "error", "params": {"threadId": tid, "turnId": turn_id, "error": err_obj}})
                        send_json({"jsonrpc": "2.0", "method": "turn/completed", "params": {"threadId": tid, "turn": {"id": turn_id, "items": [], "status": "failed", "error": err_obj}}})
                        return
                    if fail_mode == "transient_429_twice" and turn_num <= 2:
                        err_obj = {"message": f"HTTP 429 Too Many Requests (transient try {turn_num})"}
                        send_json({"jsonrpc": "2.0", "method": "error", "params": {"threadId": tid, "turnId": turn_id, "error": err_obj}})
                        send_json({"jsonrpc": "2.0", "method": "turn/completed", "params": {"threadId": tid, "turn": {"id": turn_id, "items": [], "status": "failed", "error": err_obj}}})
                        return
                    (cwd / "mod1.py").write_text("VALUE_1 = 10\\n", encoding="utf-8")
                    msg = f"Succeeded after {turn_num} turn invocation(s)"
                    send_json({"jsonrpc": "2.0", "method": "item/completed", "params": {"threadId": tid, "turnId": turn_id, "completedAtMs": 1, "item": {"id": "amsg", "type": "agentMessage", "text": msg}}})
                    send_json({"jsonrpc": "2.0", "method": "turn/completed", "params": {"threadId": tid, "turn": {"id": turn_id, "items": [], "status": "completed", "error": None}}})
                    return

                if is_verifier:
                    # Inspect prompt to extract criteria and Collector status
                    criteria = []
                    in_crit = False
                    all_passed = "Hard Command Collector All Passed: True" in prompt
                    for line in prompt.splitlines():
                        if line.startswith("Acceptance Criteria"):
                            in_crit = True
                            continue
                        if in_crit:
                            if line.startswith("- "):
                                criteria.append(line[2:].strip())
                            elif line.strip() == "":
                                in_crit = False
                    verdict = "PASS" if all_passed else "FAIL"
                    dim_val = "PASS" if verdict == "PASS" else "FAIL"
                    out_obj = {
                        "verdict": verdict,
                        "summary": "Verified cleanly against receipt and diff." if verdict == "PASS" else "Verification failed due to test command failure.",
                        "review_dimensions": {
                            "specification": dim_val,
                            "code": dim_val,
                            "stress": dim_val,
                            "evidence": dim_val,
                        },
                        "criteria_results": [{"criterion": c, "status": "PASS" if verdict == "PASS" else "FAIL"} for c in criteria],
                        "findings": [] if verdict == "PASS" else ["Pytest assertion failed in candidate workspace"],
                    }
                    out_txt = json.dumps(out_obj)
                    send_json({"jsonrpc": "2.0", "method": "item/completed", "params": {"threadId": tid, "turnId": turn_id, "completedAtMs": 1, "item": {"id": "vmsg", "type": "agentMessage", "text": out_txt}}})
                    send_json({"jsonrpc": "2.0", "method": "turn/completed", "params": {"threadId": tid, "turn": {"id": turn_id, "items": [], "status": "completed", "error": None}}})
                    return

                # Author turns for DAG scenarios
                counter_file = STATE_FILE.with_suffix(".turn_counts.json")
                with LOG_LOCK:
                    counts = json.loads(counter_file.read_text("utf-8")) if counter_file.exists() else {}
                    turn_num = counts.get(tid, 0) + 1
                    counts[tid] = turn_num
                    counter_file.write_text(json.dumps(counts), encoding="utf-8")

                if cfg.get("parallel_sleep_sec"):
                    time.sleep(float(cfg["parallel_sleep_sec"]))

                if scenario == "dag_rework_and_parallel":
                    if "Pipeline Step: step_a " in prompt:
                        target = cwd / "pkg_a" / "math_a.py"
                        target.parent.mkdir(parents=True, exist_ok=True)
                        if turn_num == 1:
                            # Intentional bug on Turn 1 -> real pytest fails!
                            target.write_text("def add_a(x: int, y: int) -> int:\\n    return x - y\\n", encoding="utf-8")
                            msg = "Implemented initial draft of add_a"
                        else:
                            assert "Rework Required" in prompt, "Missing rework header in Turn 2 prompt"
                            target.write_text("def add_a(x: int, y: int) -> int:\\n    return x + y\\n", encoding="utf-8")
                            msg = "Fixed add_a bug on rework turn"
                    elif "Pipeline Step: step_b " in prompt:
                        target = cwd / "pkg_b" / "math_b.py"
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text("def mul_b(x: int, y: int) -> int:\\n    return x * y\\n", encoding="utf-8")
                        msg = "Implemented mul_b"
                    elif "Pipeline Step: step_c " in prompt:
                        target = cwd / "pkg_c" / "combo.py"
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(
                            "from pkg_a.math_a import add_a\\nfrom pkg_b.math_b import mul_b\\n\\n"
                            "def combine(a: int, b: int, c: int) -> int:\\n    return mul_b(add_a(a, b), c)\\n",
                            encoding="utf-8",
                        )
                        msg = "Implemented combine using merged pkg_a and pkg_b"
                    else:
                        msg = "No-op"

                elif scenario == "always_fail_4_times":
                    target = cwd / "broken.py"
                    target.write_text(f"def compute() -> int:\\n    return -1  # turn {turn_num}\\n", encoding="utf-8")
                    msg = f"Attempt {turn_num} still broken"

                elif scenario == "resume_flow":
                    if "Pipeline Step: step_1 " in prompt:
                        (cwd / "mod1.py").write_text("VALUE_1 = 10\\n", encoding="utf-8")
                        msg = "step_1 done"
                    elif "Pipeline Step: step_2 " in prompt:
                        if cfg.get("pause_step_2_once"):
                            call_client_request("u_pause", "item/tool/requestUserInput", {"threadId": tid, "turnId": turn_id, "questions": [{"id": "q_resume", "header": "Confirm", "question": "Proceed?"}]})
                            msg = "paused step_2"
                        else:
                            (cwd / "mod2.py").write_text("from mod1 import VALUE_1\\nVALUE_2 = VALUE_1 + 25\\n", encoding="utf-8")
                            msg = "step_2 done after resume"
                    else:
                        msg = "resume turn done"
                else:
                    msg = "generic author completion"

                send_json({"jsonrpc": "2.0", "method": "item/agentMessage/delta", "params": {"threadId": tid, "turnId": turn_id, "itemId": "amsg", "delta": msg}})
                send_json({"jsonrpc": "2.0", "method": "item/completed", "params": {"threadId": tid, "turnId": turn_id, "completedAtMs": 1, "item": {"id": "amsg", "type": "agentMessage", "text": msg}}})
                send_json({"jsonrpc": "2.0", "method": "turn/completed", "params": {"threadId": tid, "turn": {"id": turn_id, "items": [], "status": "completed", "error": None}}})
            finally:
                ACTIVE_SLEEPER_EVENTS.pop(turn_id, None)

        def main() -> int:
            thread_seq = 0
            initialized = False
            for raw_line in sys.stdin:
                line = raw_line.strip()
                if not line:
                    continue
                msg = json.loads(line)
                if "id" in msg and "method" not in msg:
                    req_key = str(msg["id"])
                    if req_key in PENDING_REQS:
                        _, ev, holder = PENDING_REQS[req_key]
                        holder["msg"] = msg
                        ev.set()
                    continue

                method = msg.get("method")
                req_id = msg.get("id")
                params = msg.get("params") or {}

                if method == "initialize":
                    append_event({"type": "initialize", "params": params})
                    send_json({"jsonrpc": "2.0", "id": req_id, "result": {"userAgent": "mock-codex/0.6.0"}})
                    continue
                if method == "initialized":
                    initialized = True
                    append_event({"type": "initialized"})
                    continue
                if not initialized:
                    send_json({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32002, "message": "Not initialized"}})
                    continue

                if method == "thread/start":
                    cfg = load_cfg()
                    thread_seq += 1
                    role_prefix = "ver" if params.get("sandbox") == "read-only" else "auth"
                    tid = f"thr_{role_prefix}_{os.getpid()}_{thread_seq}"
                    append_event({"type": "thread_start", "threadId": tid, "params": params})
                    forced_sandbox = (
                        "readOnly"
                        if (role_prefix == "ver" or cfg.get("coerce_author_readonly"))
                        else "workspaceWrite"
                    )
                    send_json({
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "thread": {"id": tid, "status": {"type": "idle"}},
                            "cwd": params.get("cwd"),
                            "approvalPolicy": params.get("approvalPolicy"),
                            "sandbox": {"type": forced_sandbox, "networkAccess": False},
                            "model": params.get("model") or "gpt-5",
                            "modelProvider": "openai",
                            "approvalsReviewer": "user",
                        },
                    })
                    continue

                if method == "thread/read":
                    cfg = load_cfg()
                    tid = params["threadId"]
                    if tid in set(cfg.get("unloaded_zero_turn_tids", [])):
                        append_event({"type": "thread_read_error", "threadId": tid})
                        send_json({
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {"code": -32600, "message": f"thread not loaded: {tid}"},
                        })
                        continue
                    forced_status = cfg.get("thread_read_status", "idle")
                    append_event({"type": "thread_read", "threadId": tid, "params": params, "status": forced_status})
                    send_json({
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {"thread": {"id": tid, "status": {"type": forced_status}}},
                    })
                    continue

                if method == "thread/resume":
                    tid = params["threadId"]
                    append_event({"type": "thread_resume", "threadId": tid, "params": params})
                    send_json({
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {"thread": {"id": tid, "status": {"type": "idle"}}, "cwd": params.get("cwd")},
                    })
                    continue

                if method == "turn/interrupt":
                    tid = params["threadId"]
                    turn_id = params["turnId"]
                    append_event({"type": "turn_interrupt", "threadId": tid, "turnId": turn_id})
                    ev = ACTIVE_SLEEPER_EVENTS.get(turn_id)
                    if ev is not None:
                        ev.set()
                    send_json({"jsonrpc": "2.0", "id": req_id, "result": {}})
                    continue

                if method == "turn/start":
                    t = threading.Thread(target=handle_turn_worker, args=(req_id, params), daemon=True)
                    t.start()
                    continue

                send_json({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown {method}"}})
            return 0

        if __name__ == "__main__":
            raise SystemExit(main())
        """
    )
    script_path.write_text(code, encoding="utf-8")
    return script_path


def _read_mock_events(state_file: Path) -> list[dict[str, Any]]:
    log_path = state_file.with_suffix(".events.jsonl")
    if not log_path.is_file():
        return []
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ---------------------------------------------------------------------------
# 1. Unit Tests: Spec Validation, Helpers, & CLI --help
# ---------------------------------------------------------------------------


def test_pipeline_spec_validation_rejects_invalid_bounds_and_cycles(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()

    def _base_spec(**overrides: Any) -> dict[str, Any]:
        spec: dict[str, Any] = {
            "project_root": str(proj),
            "mode": "swarm",
            "title": "Test Spec",
            "max_workers": 2,
            "steps": [
                {
                    "step_key": "s1",
                    "title": "Step 1",
                    "instruction": "Do s1",
                    "file_ownership": ["src/a.py"],
                    "acceptance_criteria": ["c1"],
                    "commands": [
                        {
                            "id": "cmd1",
                            "argv": [sys.executable, "-c", "print('ok')"],
                            "cwd": ".",
                            "timeout_sec": 10,
                            "criteria": ["c1"],
                        }
                    ],
                }
            ],
        }
        spec.update(overrides)
        return spec

    valid = validate_pipeline_spec(_base_spec())
    assert valid["max_workers"] == 2
    expected_default_profile = "windows_minimal_py" if os.name == "nt" else "posix_minimal_py"
    assert valid["env_profile"] == expected_default_profile
    assert valid["steps"][0]["owner"] == "author-s1"
    assert valid["steps"][0]["verifier"] == "verifier-s1"

    for bad_workers in (0, -1, 5, True):
        with pytest.raises(PipelineSpecError, match="max_workers"):
            validate_pipeline_spec(_base_spec(max_workers=bad_workers))

    # Cycle rejection
    cyclic_steps = [
        {
            "step_key": "a",
            "instruction": "do a",
            "file_ownership": ["a.py"],
            "acceptance_criteria": ["ca"],
            "dependencies": ["b"],
            "commands": [{"id": "ca", "argv": ["python", "-V"], "criteria": ["ca"]}],
        },
        {
            "step_key": "b",
            "instruction": "do b",
            "file_ownership": ["b.py"],
            "acceptance_criteria": ["cb"],
            "dependencies": ["a"],
            "commands": [{"id": "cb", "argv": ["python", "-V"], "criteria": ["cb"]}],
        },
    ]
    with pytest.raises(PipelineSpecError, match="cycle"):
        validate_pipeline_spec(_base_spec(steps=cyclic_steps))

    # Path traversal in file_ownership
    bad_owner_step = copy_step = dict(_base_spec()["steps"][0])
    copy_step["file_ownership"] = ["../escape.py"]
    with pytest.raises(PipelineSpecError, match="relative path"):
        validate_pipeline_spec(_base_spec(steps=[bad_owner_step]))

    # Author == Verifier rejection
    same_actor_step = dict(_base_spec()["steps"][0])
    same_actor_step["owner"] = "same-worker"
    same_actor_step["verifier"] = "same-worker"
    with pytest.raises(PipelineSpecError, match="distinct workers"):
        validate_pipeline_spec(_base_spec(steps=[same_actor_step]))

    assert normalize_thread_id("appserver://thr_123") == "thr_123"
    assert format_appserver_agent_id("thr_123") == "appserver://thr_123"
    assert resolve_appserver_argv(argv=["custom-bin", "arg1"]) == ["custom-bin", "arg1"]


def test_cli_entrypoint_help_and_missing_args() -> None:
    scripts_dir = Path(sys.executable).parent
    exe_name = "tri-orchestrator-pipeline.exe" if os.name == "nt" else "tri-orchestrator-pipeline"
    cli_bin = scripts_dir / exe_name
    cmd = [str(cli_bin), "--help"] if cli_bin.is_file() else [sys.executable, "-m", "orchestrator.pipeline_runner", "--help"]
    flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        creationflags=flags,
        timeout=15,
    )
    assert proc.returncode == 0
    assert "--spec" in proc.stdout
    assert "--resume-run" in proc.stdout
    assert "--output" in proc.stdout


# ---------------------------------------------------------------------------
# 2. Case 1: <100ms Deterministic Interception & requestUserInput Pause
# ---------------------------------------------------------------------------


def test_appserver_client_deterministic_interception_under_100ms(tmp_path: Path) -> None:
    mock_script = _write_mock_appserver_script(tmp_path / "mock_appserver.py")
    state_file = tmp_path / "state_intercept.json"
    state_file.write_text(
        json.dumps({"scenario": "interception_suite", "include_user_input": True}),
        encoding="utf-8",
    )
    work_dir = tmp_path / "work"
    work_dir.mkdir()

    async def _exercise() -> None:
        env = {"MOCK_APPSERVER_STATE": str(state_file)}
        async with AppServerStdioClient(codex_bin=mock_script, env=env, cwd=work_dir) as client:
            assert client.pid is not None
            child_pid = client.pid

            author = await client.start_author_thread(work_dir, effort="medium", allow_network=False)
            verifier = await client.start_verifier_thread(work_dir, effort="high")
            assert author.agent_id.startswith("appserver://thr_auth_")
            assert verifier.agent_id.startswith("appserver://thr_ver_")
            assert author.thread_id != verifier.thread_id

            turn_res = await client.run_turn(
                author.thread_id,
                "Trigger all server request interceptions",
                cwd=work_dir,
            )
            assert turn_res.status == "completed"
            assert turn_res.needs_clarification is True
            assert len(turn_res.clarification_requests) == 1
            assert len(turn_res.interceptions) == 5

            for item in turn_res.interceptions:
                assert item["latency_ms"] < 100.0, f"Interception exceeded 100ms: {item}"

            server_measured = json.loads(turn_res.final_message)
            for method, ms in server_measured["latencies_ms"].items():
                assert ms < 100.0, f"Server-side roundtrip for {method} exceeded 100ms: {ms}ms"
            assert server_measured["responses"]["item/commandExecution/requestApproval"] == {"decision": "decline"}
            assert server_measured["responses"]["item/fileChange/requestApproval"] == {"decision": "decline"}
            assert server_measured["responses"]["item/permissions/requestApproval"] == {"permissions": {}}
            assert server_measured["responses"]["mcpServer/elicitation/request"] == {
                "action": "decline",
                "content": None,
            }
            assert server_measured["responses"]["item/tool/requestUserInput"] == {"answers": {}}

            # Verify verifier turn sends exact readOnly sandboxPolicy & outputSchema
            v_turn = await client.run_turn(
                verifier.thread_id,
                "Acceptance Criteria:\n- c1\n\nHard Command Collector All Passed: True\n",
                cwd=work_dir,
                role="verifier",
            )
            assert v_turn.structured_output is not None
            assert v_turn.structured_output["verdict"] == "PASS"

        # Client context exited -> child process reaped cleanly via Win32 Job Object
        deadline = time.monotonic() + 3.0
        while _is_pid_alive(child_pid) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not _is_pid_alive(child_pid)

    asyncio.run(_exercise())

    events = _read_mock_events(state_file)
    init_ev = next(e for e in events if e["type"] == "initialize")
    assert init_ev["params"]["clientInfo"] == {
        "name": "tri-orchestrator-pipeline",
        "version": "0.6.0",
    }
    assert init_ev["params"]["capabilities"] == {"experimentalApi": True}
    assert any(e["type"] == "initialized" for e in events)

    turn_events = [e for e in events if e["type"] == "turn_start"]
    auth_turn_ev = next(e for e in turn_events if e["role"] == "author")
    assert auth_turn_ev["approvalPolicy"] == "never"
    assert auth_turn_ev["sandboxPolicy"] == {
        "type": "workspaceWrite",
        "writableRoots": [str(work_dir.resolve())],
        "networkAccess": False,
    }
    ver_turn_ev = next(e for e in turn_events if e["role"] == "verifier")
    assert ver_turn_ev["approvalPolicy"] == "never"
    assert ver_turn_ev["effort"] == "high"
    assert ver_turn_ev["sandboxPolicy"] == {
        "type": "readOnly",
        "networkAccess": False,
    }
    assert ver_turn_ev["has_output_schema"] is True
    assert "$schema" not in ver_turn_ev["outputSchema_keys"]
    assert "title" not in ver_turn_ev["outputSchema_keys"]
    for th_ev in (e for e in events if e["type"] == "thread_start"):
        assert "effort" not in th_ev["params"]
        assert "model_reasoning_effort" in th_ev["params"]["config"]
    assert BUILTIN_VERIFIER_OUTPUT_SCHEMA["required"] == [
        "verdict",
        "summary",
        "review_dimensions",
        "criteria_results",
        "findings",
    ]


def test_pipeline_pauses_cleanly_on_request_user_input_clarification(tmp_path: Path) -> None:
    mock_script = _write_mock_appserver_script(tmp_path / "mock_appserver.py")
    state_file = tmp_path / "state_clarify.json"
    state_file.write_text(
        json.dumps({"scenario": "interception_suite", "include_user_input": True}),
        encoding="utf-8",
    )
    repo = _init_temp_git_repo(
        tmp_path / "repo_clarify",
        {"app.py": "X = 1\n"},
    )
    summary_out = tmp_path / "clarify_summary.json"

    with _ephemeral_daemon(tmp_path) as settings:
        spec = {
            "project_root": str(repo),
            "mode": "swarm",
            "title": "Clarification Pause Run",
            "max_workers": 2,
            "steps": [
                {
                    "step_key": "step_ask",
                    "instruction": "Ask question via requestUserInput",
                    "file_ownership": ["app.py"],
                    "acceptance_criteria": ["app_ok"],
                    "commands": [
                        {
                            "id": "chk_app",
                            "argv": [sys.executable, "-c", "import app; assert app.X == 1"],
                            "cwd": ".",
                            "timeout_sec": 10,
                            "criteria": ["app_ok"],
                        }
                    ],
                },
                {
                    "step_key": "step_after",
                    "instruction": "Should never run while upstream needs clarification",
                    "dependencies": ["step_ask"],
                    "file_ownership": ["after.py"],
                    "acceptance_criteria": ["after_ok"],
                    "commands": [
                        {
                            "id": "chk_after",
                            "argv": [sys.executable, "-c", "print('ok')"],
                            "cwd": ".",
                            "timeout_sec": 10,
                            "criteria": ["after_ok"],
                        }
                    ],
                },
            ],
        }
        runner = PipelineRunner(
            settings=settings,
            explicit_daemon_url=True,
            codex_bin=mock_script,
            appserver_env={"MOCK_APPSERVER_STATE": str(state_file)},
            output_path=summary_out,
        )
        rc, summary = asyncio.run(runner.execute(spec=spec))
        assert rc != 0
        assert summary["status"] == "needs_clarification"
        assert summary["halted_reason"] == "needs_clarification"
        assert summary["interceptions"]["total"] == 5
        by_key = {s["step_key"]: s for s in summary["steps"]}
        assert by_key["step_after"]["state"] == "planned"
        assert by_key["step_after"]["turns"] == 0


# ---------------------------------------------------------------------------
# 3. Case 2: Parallel DAG + Turn 1 Real Pytest Failure -> Same-Thread Rework
#    on Turn 2 -> Candidate PASS -> Merge -> Integrated PASS -> Completed
# ---------------------------------------------------------------------------


def test_parallel_dag_auto_rework_same_thread_and_full_completion(tmp_path: Path) -> None:
    mock_script = _write_mock_appserver_script(tmp_path / "mock_appserver.py")
    state_file = tmp_path / "state_dag.json"
    state_file.write_text(
        json.dumps({"scenario": "dag_rework_and_parallel", "parallel_sleep_sec": 0.25}),
        encoding="utf-8",
    )

    repo = _init_temp_git_repo(
        tmp_path / "repo_dag",
        {
            "pkg_a/__init__.py": "",
            "pkg_a/math_a.py": "def add_a(x: int, y: int) -> int:\n    return 0\n",
            "tests/test_a.py": "from pkg_a.math_a import add_a\n\ndef test_add_a() -> None:\n    assert add_a(7, 5) == 12\n",
            "pkg_b/__init__.py": "",
            "pkg_b/math_b.py": "def mul_b(x: int, y: int) -> int:\n    return 0\n",
            "tests/test_b.py": "from pkg_b.math_b import mul_b\n\ndef test_mul_b() -> None:\n    assert mul_b(3, 4) == 12\n",
            "pkg_c/__init__.py": "",
            "pkg_c/combo.py": "def combine(a: int, b: int, c: int) -> int:\n    return 0\n",
            "tests/test_c.py": "from pkg_c.combo import combine\n\ndef test_combine() -> None:\n    assert combine(2, 3, 4) == 20\n",
        },
    )
    spec_path = tmp_path / "dag_spec.json"
    summary_out = tmp_path / "dag_summary.json"

    with _ephemeral_daemon(tmp_path) as settings:
        spec = {
            "project_root": str(repo),
            "mode": "boost",
            "title": "M11 Parallel Rework & Boost High-Complexity Pipeline",
            "objective": "Implement pkg_a, pkg_b in parallel and combine in pkg_c",
            "max_workers": 2,
            "steps": [
                {
                    "step_key": "step_a",
                    "title": "Implement add_a",
                    "instruction": "Implement add_a(x, y) returning x + y",
                    "file_ownership": ["pkg_a/math_a.py"],
                    "acceptance_criteria": ["add_a returns exact sum"],
                    "complexity": "high",
                    "commands": [
                        {
                            "id": "pytest_a",
                            "argv": [sys.executable, "-m", "pytest", "-q", "tests/test_a.py"],
                            "cwd": ".",
                            "timeout_sec": 30,
                            "criteria": ["add_a returns exact sum"],
                        }
                    ],
                },
                {
                    "step_key": "step_b",
                    "title": "Implement mul_b",
                    "instruction": "Implement mul_b(x, y) returning x * y",
                    "file_ownership": ["pkg_b/math_b.py"],
                    "acceptance_criteria": ["mul_b returns exact product"],
                    "complexity": "normal",
                    "commands": [
                        {
                            "id": "pytest_b",
                            "argv": [sys.executable, "-m", "pytest", "-q", "tests/test_b.py"],
                            "cwd": ".",
                            "timeout_sec": 30,
                            "criteria": ["mul_b returns exact product"],
                        }
                    ],
                },
                {
                    "step_key": "step_c",
                    "title": "Implement combine",
                    "instruction": "Implement combine(a, b, c) returning mul_b(add_a(a, b), c)",
                    "dependencies": ["step_a", "step_b"],
                    "file_ownership": ["pkg_c/combo.py"],
                    "acceptance_criteria": ["combine integrates pkg_a and pkg_b"],
                    "complexity": "high",
                    "commands": [
                        {
                            "id": "pytest_c",
                            "argv": [sys.executable, "-m", "pytest", "-q", "tests/test_c.py"],
                            "cwd": ".",
                            "timeout_sec": 30,
                            "criteria": ["combine integrates pkg_a and pkg_b"],
                        }
                    ],
                },
            ],
        }
        spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")

        old_env = os.environ.get("MOCK_APPSERVER_STATE")
        os.environ["MOCK_APPSERVER_STATE"] = str(state_file)
        try:
            rc = pipeline_main(
                [
                    "--spec",
                    str(spec_path),
                    "--output",
                    str(summary_out),
                    "--daemon-url",
                    settings.base_url,
                    "--data-dir",
                    str(settings.data_dir),
                    "--codex-bin",
                    str(mock_script),
                ]
            )
        finally:
            if old_env is None:
                os.environ.pop("MOCK_APPSERVER_STATE", None)
            else:
                os.environ["MOCK_APPSERVER_STATE"] = old_env

        assert rc == 0
        assert summary_out.is_file()
        disk_summary = json.loads(summary_out.read_text(encoding="utf-8"))
        expected_sha = hashlib.sha256(
            _canonical_json_bytes({k: v for k, v in disk_summary.items() if k != "sha256"})
        ).hexdigest()
        assert disk_summary["sha256"] == expected_sha
        assert disk_summary["status"] == "completed"
        assert disk_summary["peak_concurrency"] == 2

        steps_by_key = {s["step_key"]: s for s in disk_summary["steps"]}
        # step_a failed Turn 1 (real pytest exit 1) -> reworked on SAME author_thread_id on Turn 2 -> PASS
        assert steps_by_key["step_a"]["reworks"] == 1
        assert steps_by_key["step_a"]["attempts"] == 2
        assert steps_by_key["step_a"]["state"] == "done"
        assert steps_by_key["step_b"]["reworks"] == 0
        assert steps_by_key["step_b"]["state"] == "done"
        assert steps_by_key["step_c"]["reworks"] == 0
        assert steps_by_key["step_c"]["state"] == "done"

        for s_info in steps_by_key.values():
            assert s_info["author_thread_id"] != s_info["verifier_thread_id"]
            for r_path in s_info["receipt_paths"]:
                assert Path(r_path).is_file()

        # Verify daemon persisted state: run completed, all workspaces cleaned, honest appserver:// bindings
        with httpx.Client(base_url=settings.base_url, trust_env=False) as http:
            snap = http.get(f"/api/runs/{disk_summary['run_id']}/snapshot").json()
        assert snap["run"]["status"] == "completed"
        for st in snap["workflow"]["steps"]:
            assert st["state"] == "done"
            assert st["execution_kind"] == "app_server_pipeline"
            assert st["owner_agent_id"].startswith("appserver://thr_auth_")
            assert st["verifier_agent_id"].startswith("appserver://thr_ver_")
        for ws in snap["workspaces"]:
            assert ws["status"] == "cleaned"
            assert not Path(ws["path"]).exists()

    # Verify Turn 1 and Turn 2 for step_a used the EXACT SAME author_thread_id
    events = _read_mock_events(state_file)
    step_a_author_turns = [
        e
        for e in events
        if e["type"] == "turn_start"
        and e["role"] == "author"
        and "Pipeline Step: step_a " in e["prompt"]
    ]
    assert len(step_a_author_turns) == 2
    assert step_a_author_turns[0]["threadId"] == step_a_author_turns[1]["threadId"]
    assert "Rework Required (Attempt 1 of 3)" in step_a_author_turns[1]["prompt"]
    assert "exit_code=1" in step_a_author_turns[1]["prompt"]


# ---------------------------------------------------------------------------
# 4. Case 3: 4 Consecutive Failures (Initial + 3 Reworks) -> Halts at needs_decision
# ---------------------------------------------------------------------------


def test_rework_cap_halts_at_needs_decision_on_fourth_failure(tmp_path: Path) -> None:
    mock_script = _write_mock_appserver_script(tmp_path / "mock_appserver.py")
    state_file = tmp_path / "state_cap.json"
    state_file.write_text(json.dumps({"scenario": "always_fail_4_times"}), encoding="utf-8")

    repo = _init_temp_git_repo(
        tmp_path / "repo_cap",
        {
            "broken.py": "def compute() -> int:\n    return 0\n",
            "test_broken.py": "from broken import compute\n\ndef test_compute() -> None:\n    assert compute() == 42\n",
            "downstream.py": "OK = True\n",
        },
    )
    summary_out = tmp_path / "cap_summary.json"

    with _ephemeral_daemon(tmp_path) as settings:
        spec = {
            "project_root": str(repo),
            "mode": "swarm",
            "title": "Rework Budget Exhaustion Test",
            "max_workers": 2,
            "steps": [
                {
                    "step_key": "step_fail",
                    "instruction": "Try to fix compute()",
                    "file_ownership": ["broken.py"],
                    "acceptance_criteria": ["compute returns 42"],
                    "commands": [
                        {
                            "id": "pytest_broken",
                            "argv": [sys.executable, "-m", "pytest", "-q", "test_broken.py"],
                            "cwd": ".",
                            "timeout_sec": 20,
                            "criteria": ["compute returns 42"],
                        }
                    ],
                },
                {
                    "step_key": "step_downstream",
                    "instruction": "Must never execute when upstream halts at needs_decision",
                    "dependencies": ["step_fail"],
                    "file_ownership": ["downstream.py"],
                    "acceptance_criteria": ["downstream untouched"],
                    "commands": [
                        {
                            "id": "chk_down",
                            "argv": [sys.executable, "-c", "import downstream; assert downstream.OK"],
                            "cwd": ".",
                            "timeout_sec": 10,
                            "criteria": ["downstream untouched"],
                        }
                    ],
                },
            ],
        }
        runner = PipelineRunner(
            settings=settings,
            explicit_daemon_url=True,
            codex_bin=mock_script,
            appserver_env={"MOCK_APPSERVER_STATE": str(state_file)},
            output_path=summary_out,
        )
        rc, summary = asyncio.run(runner.execute(spec=spec))
        assert rc != 0
        assert summary["status"] == "needs_decision"
        assert summary["halted_reason"] == "needs_decision"

        by_key = {s["step_key"]: s for s in summary["steps"]}
        assert by_key["step_fail"]["state"] == "needs_decision"
        assert by_key["step_fail"]["reworks"] == 3
        assert by_key["step_fail"]["attempts"] == 4
        assert by_key["step_downstream"]["state"] == "planned"
        assert by_key["step_downstream"]["attempts"] == 0

        with httpx.Client(base_url=settings.base_url, trust_env=False) as http:
            snap = http.get(f"/api/runs/{summary['run_id']}/snapshot").json()
        assert snap["run"]["status"] == "active"
        db_steps = {s["step_key"]: s for s in snap["workflow"]["steps"]}
        assert db_steps["step_fail"]["state"] == "needs_decision"
        assert db_steps["step_fail"]["attempt"] == 3
        assert db_steps["step_downstream"]["state"] == "planned"

    # Exactly 4 author turns (initial + 3 reworks) on the same author thread; NEVER a 5th turn!
    events = _read_mock_events(state_file)
    author_turns = [e for e in events if e["type"] == "turn_start" and e["role"] == "author"]
    assert len(author_turns) == 4
    assert len({e["threadId"] for e in author_turns}) == 1


# ---------------------------------------------------------------------------
# 5. Case 4: Live Web worker_control(stop) -> <=2s turn/interrupt & Job Reap
# ---------------------------------------------------------------------------


def test_live_worker_control_stop_interrupts_under_2s_and_reaps_job_tree(tmp_path: Path) -> None:
    mock_script = _write_mock_appserver_script(tmp_path / "mock_appserver.py")
    grandchild_pid_file = tmp_path / "grandchild.pid"
    state_file = tmp_path / "state_stop.json"
    state_file.write_text(
        json.dumps(
            {
                "scenario": "hang_for_stop",
                "grandchild_pid_file": str(grandchild_pid_file),
            }
        ),
        encoding="utf-8",
    )

    repo = _init_temp_git_repo(tmp_path / "repo_stop", {"mod.py": "A = 1\n"})
    summary_out = tmp_path / "stop_summary.json"

    with _ephemeral_daemon(tmp_path) as settings:
        spec = {
            "project_root": str(repo),
            "mode": "swarm",
            "title": "Live Stop Interrupt Test",
            "max_workers": 1,
            "steps": [
                {
                    "step_key": "step_long",
                    "instruction": "Long running task to interrupt from Web API",
                    "file_ownership": ["mod.py"],
                    "acceptance_criteria": ["mod valid"],
                    "commands": [
                        {
                            "id": "chk_mod",
                            "argv": [sys.executable, "-c", "import mod; assert mod.A == 1"],
                            "cwd": ".",
                            "timeout_sec": 10,
                            "criteria": ["mod valid"],
                        }
                    ],
                }
            ],
        }

        async def _run_and_stop() -> tuple[int, dict[str, Any], float, int]:
            runner = PipelineRunner(
                settings=settings,
                explicit_daemon_url=True,
                codex_bin=mock_script,
                appserver_env={"MOCK_APPSERVER_STATE": str(state_file)},
                poll_interval_sec=0.25,
                output_path=summary_out,
            )
            exec_task = asyncio.create_task(runner.execute(spec=spec))

            # Wait until grandchild sleeper is spawned by the active turn
            deadline = time.monotonic() + 10.0
            while not grandchild_pid_file.is_file() and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            assert grandchild_pid_file.is_file(), "Mock turn did not spawn grandchild process in time"
            g_pid = int(grandchild_pid_file.read_text(encoding="utf-8").strip())
            assert _is_pid_alive(g_pid)

            async with httpx.AsyncClient(base_url=settings.base_url, trust_env=False) as http:
                runs = (await http.get("/api/runs")).json()["runs"]
                assert len(runs) == 1
                run_id = runs[0]["id"]
                snap = (await http.get(f"/api/runs/{run_id}/snapshot")).json()
                author_worker = next(w for w in snap["workers"] if w["name"] == "author-step_long")

                t_stop = time.monotonic()
                ctl_resp = await http.post(
                    f"/api/workers/{author_worker['id']}/status",
                    json={"status": "killed"},
                )
                assert ctl_resp.status_code == 202

            rc_val, sum_val = await asyncio.wait_for(exec_task, timeout=5.0)
            stop_latency = time.monotonic() - t_stop
            return rc_val, sum_val, stop_latency, g_pid

        rc, summary, stop_latency, grandchild_pid = asyncio.run(_run_and_stop())
        assert rc != 0
        assert stop_latency <= 2.0, f"Stop interrupt took {stop_latency:.3f}s (> 2.0s)"
        assert summary["status"] == "interrupted_by_control"
        assert len(summary["acknowledged_controls"]) == 1

        # Verify zero orphan processes (grandchild sleeper killed by Win32 Job Object / process group)
        reap_deadline = time.monotonic() + 2.0
        while _is_pid_alive(grandchild_pid) and time.monotonic() < reap_deadline:
            time.sleep(0.02)
        assert not _is_pid_alive(grandchild_pid), f"Orphan grandchild PID {grandchild_pid} survived!"

        with httpx.Client(base_url=settings.base_url, trust_env=False) as http:
            snap = http.get(f"/api/runs/{summary['run_id']}/snapshot").json()
        ctls = snap["control_requests"]
        assert len(ctls) == 1
        assert ctls[0]["status"] == "applied"
        step_row = snap["workflow"]["steps"][0]
        obs_map = step_row["checkpoint"].get("native_observations", {})
        assert obs_map[step_row["owner_agent_id"]]["state"] == "interrupted"


# ---------------------------------------------------------------------------
# 6. Case 5: --resume-run Recovery (thread/read + thread/resume, skips done,
#    and systemError fail-closed into needs_decision)
# ---------------------------------------------------------------------------


def test_resume_run_skips_done_steps_and_finishes_remaining_dag(tmp_path: Path) -> None:
    mock_script = _write_mock_appserver_script(tmp_path / "mock_appserver.py")
    state_file = tmp_path / "state_resume.json"
    state_file.write_text(json.dumps({"scenario": "resume_flow"}), encoding="utf-8")

    repo = _init_temp_git_repo(
        tmp_path / "repo_resume",
        {
            "mod1.py": "VALUE_1 = 0\n",
            "test_mod1.py": "import mod1\n\ndef test_1() -> None:\n    assert mod1.VALUE_1 == 10\n",
            "mod2.py": "VALUE_2 = 0\n",
            "test_mod2.py": "import mod2\n\ndef test_2() -> None:\n    assert mod2.VALUE_2 == 35\n",
        },
    )

    with _ephemeral_daemon(tmp_path) as settings:
        # Phase 1: Run a 2-step spec where step_2 pauses for clarification on run 1, leaving step_1 == 'done'
        # and step_2 bound to 'appserver://thr_auth_...' in 'implementing'!
        state_file.write_text(
            json.dumps({"scenario": "resume_flow", "pause_step_2_once": True}),
            encoding="utf-8",
        )
        spec = {
            "project_root": str(repo),
            "mode": "teamwork",
            "title": "Two-Step Resume Pipeline",
            "max_workers": 1,
            "steps": [
                {
                    "step_key": "step_1",
                    "instruction": "Set VALUE_1 = 10 in mod1.py",
                    "file_ownership": ["mod1.py"],
                    "acceptance_criteria": ["mod1 VALUE_1 is 10"],
                    "commands": [
                        {
                            "id": "pytest_1",
                            "argv": [sys.executable, "-m", "pytest", "-q", "test_mod1.py"],
                            "cwd": ".",
                            "timeout_sec": 20,
                            "criteria": ["mod1 VALUE_1 is 10"],
                        }
                    ],
                },
                {
                    "step_key": "step_2",
                    "instruction": "Set VALUE_2 = VALUE_1 + 25 in mod2.py",
                    "dependencies": ["step_1"],
                    "file_ownership": ["mod2.py"],
                    "acceptance_criteria": ["mod2 VALUE_2 is 35"],
                    "commands": [
                        {
                            "id": "pytest_2",
                            "argv": [sys.executable, "-m", "pytest", "-q", "test_mod2.py"],
                            "cwd": ".",
                            "timeout_sec": 20,
                            "criteria": ["mod2 VALUE_2 is 35"],
                        }
                    ],
                },
            ],
        }

        sum1_path = tmp_path / "run1_summary.json"
        runner1 = PipelineRunner(
            settings=settings,
            explicit_daemon_url=True,
            codex_bin=mock_script,
            appserver_env={"MOCK_APPSERVER_STATE": str(state_file)},
            output_path=sum1_path,
        )
        rc1, sum1 = asyncio.run(runner1.execute(spec=spec))
        assert rc1 != 0
        assert sum1["status"] == "needs_clarification"
        run_id = sum1["run_id"]
        s1_by_key = {s["step_key"]: s for s in sum1["steps"]}
        assert s1_by_key["step_1"]["state"] == "done"
        assert s1_by_key["step_2"]["state"] == "implementing"
        original_step2_tid = s1_by_key["step_2"]["author_thread_id"]
        assert original_step2_tid

        # Phase 2a: Verify fail-closed when thread/read reports systemError (no silent rebinding!)
        state_file.write_text(
            json.dumps(
                {
                    "scenario": "resume_flow",
                    "pause_step_2_once": False,
                    "thread_read_status": "systemError",
                }
            ),
            encoding="utf-8",
        )
        sum_err_path = tmp_path / "resume_err_summary.json"
        runner_err = PipelineRunner(
            settings=settings,
            explicit_daemon_url=True,
            codex_bin=mock_script,
            appserver_env={"MOCK_APPSERVER_STATE": str(state_file)},
            output_path=sum_err_path,
        )
        rc_err, sum_err = asyncio.run(runner_err.execute(resume_run_id=run_id))
        assert rc_err != 0
        assert sum_err["status"] == "needs_decision"

        # Reset observation back to idle & healthy thread status to test clean standalone --resume-run CLI
        with httpx.Client(base_url=settings.base_url, trust_env=False) as http:
            snap_mid = http.get(f"/api/runs/{run_id}/snapshot").json()
            st2_mid = next(s for s in snap_mid["workflow"]["steps"] if s["step_key"] == "step_2")
            http.post(
                "/api/rpc",
                json={
                    "tool": "workflow_step_advance",
                    "arguments": {
                        "step_id": st2_mid["id"],
                        "action": "observe",
                        "expected_revision": st2_mid["revision"],
                        "observation": {
                            "agent_id": st2_mid["owner_agent_id"],
                            "state": "idle",
                            "observed_at": time.time() + 0.05,
                            "evidence": "Thread restored before resume",
                        },
                    },
                },
            )

        # Clear mock event log before Phase 2b so we can assert exact resume RPC calls
        state_file.with_suffix(".events.jsonl").unlink(missing_ok=True)
        state_file.write_text(
            json.dumps(
                {
                    "scenario": "resume_flow",
                    "pause_step_2_once": False,
                    "thread_read_status": "idle",
                }
            ),
            encoding="utf-8",
        )

        # Phase 2b: Resume via CLI WITHOUT --spec (reads frozen metadata.pipeline_spec)
        sum2_path = tmp_path / "run2_summary.json"
        old_env = os.environ.get("MOCK_APPSERVER_STATE")
        os.environ["MOCK_APPSERVER_STATE"] = str(state_file)
        try:
            rc2 = pipeline_main(
                [
                    "--resume-run",
                    run_id,
                    "--output",
                    str(sum2_path),
                    "--daemon-url",
                    settings.base_url,
                    "--data-dir",
                    str(settings.data_dir),
                    "--codex-bin",
                    str(mock_script),
                ]
            )
        finally:
            if old_env is None:
                os.environ.pop("MOCK_APPSERVER_STATE", None)
            else:
                os.environ["MOCK_APPSERVER_STATE"] = old_env

        assert rc2 == 0
        sum2 = json.loads(sum2_path.read_text(encoding="utf-8"))
        assert sum2["resumed"] is True
        assert sum2["status"] == "completed"
        s2_by_key = {s["step_key"]: s for s in sum2["steps"]}
        assert s2_by_key["step_1"]["state"] == "done"
        assert s2_by_key["step_2"]["state"] == "done"
        assert s2_by_key["step_2"]["author_thread_id"] == original_step2_tid

    resume_events = _read_mock_events(state_file)
    # step_1 author was NEVER re-executed during resume!
    step1_author_turns = [
        e
        for e in resume_events
        if e["type"] == "turn_start"
        and e["role"] == "author"
        and "Pipeline Step: step_1 " in e["prompt"]
    ]
    assert step1_author_turns == []

    # Bound step_2 thread was verified via thread/read and resumed via thread/resume
    read_tids = [e["threadId"] for e in resume_events if e["type"] == "thread_read"]
    resume_calls = [e for e in resume_events if e["type"] == "thread_resume"]
    assert original_step2_tid in read_tids
    resumed_map = {e["threadId"]: e["params"] for e in resume_calls}
    assert original_step2_tid in resumed_map
    assert resumed_map[original_step2_tid]["excludeTurns"] is True
    assert "persistExtendedHistory" not in resumed_map[original_step2_tid]
    assert "config" not in resumed_map[original_step2_tid]


def test_cli_subprocess_binary_end_to_end_execution(tmp_path: Path) -> None:
    mock_script = _write_mock_appserver_script(tmp_path / "mock_appserver.py")
    state_file = tmp_path / "state_bin.json"
    state_file.write_text(json.dumps({"scenario": "resume_flow"}), encoding="utf-8")

    repo = _init_temp_git_repo(
        tmp_path / "repo_bin",
        {
            "mod1.py": "VALUE_1 = 0\n",
            "test_mod1.py": "import mod1\n\ndef test_1() -> None:\n    assert mod1.VALUE_1 == 10\n",
        },
    )
    spec_file = tmp_path / "bin_spec.json"
    out_file = tmp_path / "bin_summary.json"

    with _ephemeral_daemon(tmp_path) as settings:
        spec_file.write_text(
            json.dumps(
                {
                    "project_root": str(repo),
                    "mode": "swarm",
                    "title": "Installed CLI Binary E2E Check",
                    "max_workers": 1,
                    "steps": [
                        {
                            "step_key": "step_1",
                            "instruction": "Set VALUE_1 = 10 in mod1.py",
                            "file_ownership": ["mod1.py"],
                            "acceptance_criteria": ["VALUE_1 equals 10"],
                            "commands": [
                                {
                                    "id": "chk_1",
                                    "argv": [sys.executable, "-m", "pytest", "-q", "test_mod1.py"],
                                    "cwd": ".",
                                    "timeout_sec": 20,
                                    "criteria": ["VALUE_1 equals 10"],
                                }
                            ],
                        }
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        scripts_dir = Path(sys.executable).parent
        exe_name = (
            "tri-orchestrator-pipeline.exe"
            if os.name == "nt"
            else "tri-orchestrator-pipeline"
        )
        cli_bin = scripts_dir / exe_name
        argv = (
            [str(cli_bin)]
            if cli_bin.is_file()
            else [sys.executable, "-m", "orchestrator.pipeline_runner"]
        )
        argv.extend(
            [
                "--spec",
                str(spec_file),
                "--output",
                str(out_file),
                "--daemon-url",
                settings.base_url,
                "--data-dir",
                str(settings.data_dir),
                "--codex-bin",
                str(mock_script),
            ]
        )

        env = os.environ.copy()
        env["MOCK_APPSERVER_STATE"] = str(state_file)
        env["TRI_ORCH_DATA_DIR"] = str(settings.data_dir)
        env["TRI_ORCH_PORT"] = str(settings.port)

        flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            creationflags=flags,
            timeout=60,
        )
        assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
        stdout_json = json.loads(proc.stdout.strip().splitlines()[-1])
        assert stdout_json["status"] == "completed"
        assert stdout_json["exit_code"] == 0
        assert out_file.is_file()
        summary = json.loads(out_file.read_text(encoding="utf-8"))
        assert summary["status"] == "completed"
        assert summary["execution_kind"] == "app_server_pipeline"
        assert len(summary["receipt_paths"]) >= 2
        assert (repo / "mod1.py").read_text(encoding="utf-8") == "VALUE_1 = 10\n"


# ---------------------------------------------------------------------------
# 7. Phase 2 Audit Remediation Regressions (Schema, Isolation, Multi-Worker
#    Interrupts, Turn Failed Budget Preservation, 0-Turn Resume, Snapshot
#    Cache Exclusion, Git Subdirectory cwd, Final-Wave Integration, GB18030)
# ---------------------------------------------------------------------------


def test_author_readonly_coercion_legacy_approvals_and_stream_isolation(tmp_path: Path) -> None:
    mock_script = _write_mock_appserver_script(tmp_path / "mock_appserver.py")
    state_file = tmp_path / "state_schema.json"
    work_dir = tmp_path / "work_schema"
    work_dir.mkdir()

    # 1) Author workspace-write silently coerced to readOnly -> raises AppServerError immediately
    state_file.write_text(
        json.dumps({"scenario": "interception_suite", "coerce_author_readonly": True}),
        encoding="utf-8",
    )

    async def _check_coercion() -> None:
        async with AppServerStdioClient(
            codex_bin=mock_script,
            env={"MOCK_APPSERVER_STATE": str(state_file)},
            cwd=work_dir,
        ) as client:
            with pytest.raises(AppServerError, match="coerced sandbox"):
                await client.start_author_thread(work_dir)

    asyncio.run(_check_coercion())

    # 2) Legacy execCommandApproval / applyPatchApproval ReviewDecision + _match_turn_state isolation
    state_file.write_text(
        json.dumps(
            {
                "scenario": "interception_suite",
                "include_legacy_approvals": True,
                "include_user_input": False,
            }
        ),
        encoding="utf-8",
    )

    async def _check_legacy_and_isolation() -> None:
        async with AppServerStdioClient(
            codex_bin=mock_script,
            env={"MOCK_APPSERVER_STATE": str(state_file)},
            cwd=work_dir,
        ) as client:
            author = await client.start_author_thread(work_dir)
            turn_res = await client.run_turn(author.thread_id, "Test legacy approvals", cwd=work_dir)
            measured = json.loads(turn_res.final_message)
            expected_legacy = {
                "decision": {
                    "denied": {
                        "rejection": "Declined by deterministic pipeline sandbox policy"
                    }
                }
            }
            assert measured["responses"]["execCommandApproval"] == expected_legacy
            assert measured["responses"]["applyPatchApproval"] == expected_legacy
            assert measured["responses"]["item/commandExecution/requestApproval"] == {"decision": "decline"}

            # Direct unit check on _match_turn_state: trailing event from thr_A must NEVER hit sole active thr_B
            from orchestrator.appserver_client import _ActiveTurnState

            state_b = _ActiveTurnState(thread_id="thr_B")
            state_b.turn_id = "turn_B_1"
            client._active_turns_by_thread["thr_B"] = state_b
            try:
                assert client._match_turn_state({"threadId": "thr_A", "turn": {"id": "turn_A_99"}}) is None
                assert client._match_turn_state({"conversationId": "thr_A"}) is None
                assert client._match_turn_state({"turnId": "turn_A_99"}) is None
                # Delivering a trailing turn/completed for thr_A leaves thr_B untouched
                client._handle_notification(
                    "turn/completed",
                    {"threadId": "thr_A", "turn": {"id": "turn_A_99", "status": "interrupted"}},
                )
                assert not state_b.done_event.is_set()
                assert state_b.turn_id == "turn_B_1"
                assert state_b.status == "inProgress"
            finally:
                client._active_turns_by_thread.pop("thr_B", None)

    asyncio.run(_check_legacy_and_isolation())


def test_multi_worker_concurrent_control_interrupt_under_2s(tmp_path: Path) -> None:
    mock_script = _write_mock_appserver_script(tmp_path / "mock_appserver.py")
    pids_dir = tmp_path / "multi_pids"
    state_file = tmp_path / "state_multi_stop.json"
    state_file.write_text(
        json.dumps({"scenario": "hang_for_stop", "grandchild_pids_dir": str(pids_dir)}),
        encoding="utf-8",
    )
    repo = _init_temp_git_repo(
        tmp_path / "repo_multi_stop",
        {"a.py": "A = 1\n", "b.py": "B = 2\n"},
    )

    with _ephemeral_daemon(tmp_path) as settings:
        spec = {
            "project_root": str(repo),
            "mode": "swarm",
            "title": "Concurrent Multi-Worker Stop SLA",
            "max_workers": 2,
            "steps": [
                {
                    "step_key": "s_left",
                    "instruction": "Hang left worker",
                    "file_ownership": ["a.py"],
                    "acceptance_criteria": ["ca"],
                    "commands": [{"id": "ca", "argv": [sys.executable, "-c", "print(1)"], "criteria": ["ca"]}],
                },
                {
                    "step_key": "s_right",
                    "instruction": "Hang right worker",
                    "file_ownership": ["b.py"],
                    "acceptance_criteria": ["cb"],
                    "commands": [{"id": "cb", "argv": [sys.executable, "-c", "print(2)"], "criteria": ["cb"]}],
                },
            ],
        }

        async def _run() -> tuple[int, dict[str, Any], float]:
            runner = PipelineRunner(
                settings=settings,
                explicit_daemon_url=True,
                codex_bin=mock_script,
                appserver_env={"MOCK_APPSERVER_STATE": str(state_file)},
                poll_interval_sec=0.15,
                output_path=tmp_path / "multi_stop_sum.json",
            )
            task = asyncio.create_task(runner.execute(spec=spec))
            deadline = time.monotonic() + 10.0
            while (not pids_dir.is_dir() or len(list(pids_dir.glob("*.pid"))) < 2) and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            assert len(list(pids_dir.glob("*.pid"))) == 2, "Both parallel turns must be active simultaneously"

            async with httpx.AsyncClient(base_url=settings.base_url, trust_env=False) as http:
                run_id = (await http.get("/api/runs")).json()["runs"][0]["id"]
                snap = (await http.get(f"/api/runs/{run_id}/snapshot")).json()
                w_left = next(w for w in snap["workers"] if w["name"] == "author-s_left")
                t0 = time.monotonic()
                resp = await http.post(f"/api/workers/{w_left['id']}/status", json={"status": "stopped"})
                assert resp.status_code == 202

            rc_val, sum_val = await asyncio.wait_for(task, timeout=3.0)
            return rc_val, sum_val, time.monotonic() - t0

        rc, summary, latency = asyncio.run(_run())
        assert rc != 0
        assert latency <= 2.0, f"Multi-worker interrupt took {latency:.3f}s (> 2.0s SLA)"
        assert summary["status"] == "interrupted_by_control"
        assert summary["peak_concurrency"] == 2
        events = _read_mock_events(state_file)
        interrupted_tids = {e["threadId"] for e in events if e["type"] == "turn_interrupt"}
        assert len(interrupted_tids) == 2, f"Expected both active threads interrupted, got {interrupted_tids}"


def test_turn_failed_retries_transient_429_and_preserves_attempt_budget_on_401(tmp_path: Path) -> None:
    mock_script = _write_mock_appserver_script(tmp_path / "mock_appserver.py")
    state_file = tmp_path / "state_api_fail.json"
    repo = _init_temp_git_repo(
        tmp_path / "repo_api_fail",
        {
            "mod1.py": "VALUE_1 = 0\n",
            "test_mod1.py": "import mod1\n\ndef test_1() -> None:\n    assert mod1.VALUE_1 == 10\n",
        },
    )

    with _ephemeral_daemon(tmp_path) as settings:
        spec = {
            "project_root": str(repo),
            "mode": "swarm",
            "title": "API Turn Failure Handling",
            "max_workers": 1,
            "steps": [
                {
                    "step_key": "step_api",
                    "instruction": "Set VALUE_1 = 10",
                    "file_ownership": ["mod1.py"],
                    "acceptance_criteria": ["v1 is 10"],
                    "commands": [
                        {
                            "id": "t1",
                            "argv": [sys.executable, "-m", "pytest", "-q", "test_mod1.py"],
                            "criteria": ["v1 is 10"],
                        }
                    ],
                }
            ],
        }

        # Case A: Permanent 401 aborts immediately WITHOUT burning attempt budget (attempt stays 0, state implementing)
        state_file.write_text(
            json.dumps({"scenario": "api_turn_failures", "fail_mode": "401"}),
            encoding="utf-8",
        )
        runner_401 = PipelineRunner(
            settings=settings,
            explicit_daemon_url=True,
            codex_bin=mock_script,
            appserver_env={"MOCK_APPSERVER_STATE": str(state_file)},
            output_path=tmp_path / "sum_401.json",
        )
        rc_401, sum_401 = asyncio.run(runner_401.execute(spec=spec))
        assert rc_401 != 0
        assert sum_401["status"] == "turn_failed"
        assert "401 Unauthorized" in (sum_401["error"] or "")
        assert sum_401["steps"][0]["reworks"] == 0
        assert sum_401["steps"][0]["state"] == "implementing"

        # Case B: Restore API (with 2 transient 429s before success) and --resume-run the exact same run!
        state_file.with_suffix(".api_fail_counts.json").unlink(missing_ok=True)
        state_file.write_text(
            json.dumps({"scenario": "api_turn_failures", "fail_mode": "transient_429_twice"}),
            encoding="utf-8",
        )
        runner_resumed = PipelineRunner(
            settings=settings,
            explicit_daemon_url=True,
            codex_bin=mock_script,
            appserver_env={"MOCK_APPSERVER_STATE": str(state_file)},
            output_path=tmp_path / "sum_resumed_ok.json",
        )
        rc_ok, sum_ok = asyncio.run(runner_resumed.execute(resume_run_id=sum_401["run_id"]))
        assert rc_ok == 0
        assert sum_ok["status"] == "completed"
        assert sum_ok["steps"][0]["reworks"] == 0
        assert sum_ok["steps"][0]["state"] == "done"


def test_zero_turn_rollout_resume_and_reconciliation_guard(tmp_path: Path) -> None:
    mock_script = _write_mock_appserver_script(tmp_path / "mock_appserver.py")
    state_file = tmp_path / "state_0turn.json"
    state_file.write_text(json.dumps({"scenario": "resume_flow"}), encoding="utf-8")
    repo = _init_temp_git_repo(
        tmp_path / "repo_0turn",
        {
            "mod1.py": "VALUE_1 = 0\n",
            "test_mod1.py": "import mod1\n\ndef test_1() -> None:\n    assert mod1.VALUE_1 == 10\n",
        },
    )

    with _ephemeral_daemon(tmp_path) as settings:
        spec = {
            "project_root": str(repo),
            "mode": "swarm",
            "title": "Zero Turn Crash Recovery",
            "max_workers": 1,
            "steps": [
                {
                    "step_key": "step_1",
                    "instruction": "Set VALUE_1 = 10",
                    "file_ownership": ["mod1.py"],
                    "acceptance_criteria": ["v1 == 10"],
                    "commands": [
                        {
                            "id": "t1",
                            "argv": [sys.executable, "-m", "pytest", "-q", "test_mod1.py"],
                            "criteria": ["v1 == 10"],
                        }
                    ],
                }
            ],
        }

        async def _setup_zero_turn_run() -> tuple[str, str]:
            runner = PipelineRunner(
                settings=settings,
                explicit_daemon_url=True,
                codex_bin=mock_script,
                appserver_env={"MOCK_APPSERVER_STATE": str(state_file)},
            )
            validated = validate_pipeline_spec(spec)
            await runner._ensure_daemon_and_clients(repo)
            try:
                run_id, step_map = await runner._create_fresh_run(validated)
                step_id = step_map["step_1"]
                ws = await runner._rpc.call(
                    "workspace_create",
                    {"run_id": run_id, "worker_name": "author-step_1", "copy_env": True, "dep_mode": "none"},
                )
                zero_tid = "thr_auth_unmaterialized_0"
                prep = await runner._rpc.call(
                    "workflow_prepare_dispatch",
                    {
                        "run_id": run_id,
                        "dispatch_action": "dispatch",
                        "step_id": step_id,
                        "workspace_id": ws["id"],
                        "owner": "author-step_1",
                        "verifier": "verifier-step_1",
                        "agent_id": format_appserver_agent_id(zero_tid),
                        "worker_status": "working",
                        "execution_kind": "app_server_pipeline",
                        "title": "Step 1",
                        "instruction": "Set VALUE_1 = 10",
                    },
                )
                # Leave native_action in 'invoking' first to verify needs_reconciliation_count guard when expired,
                # OR confirm it to test 0-turn -32600 rehydration!
                await runner._rpc.call(
                    "native_action_record_call",
                    {
                        "run_id": run_id,
                        "action_id": prep["action_id"],
                        "claim_token": prep["claim_token"],
                        "expected_revision": prep["expected_revision"],
                        "outcome": "confirmed",
                        "agent_id": format_appserver_agent_id(zero_tid),
                        "raw_response": {"thread_id": zero_tid},
                    },
                )
                return run_id, zero_tid
            finally:
                await runner._close_clients()

        run_id, zero_tid = asyncio.run(_setup_zero_turn_run())

        # Verify needs_reconciliation_count typo fix directly via mock snapshot
        runner_fenced = PipelineRunner(
            settings=settings,
            explicit_daemon_url=True,
            codex_bin=mock_script,
            appserver_env={"MOCK_APPSERVER_STATE": str(state_file)},
        )
        orig_get_snap = None

        async def _check_reconcile_guard() -> None:
            nonlocal orig_get_snap
            from orchestrator.pipeline_runner import _DaemonRpcClient

            orig_get_snap = _DaemonRpcClient.get_run_snapshot

            async def _fake_snap(self_rpc: Any, rid: str) -> dict[str, Any]:
                real = await orig_get_snap(self_rpc, rid)
                real["native_actions"] = {"needs_reconciliation_count": 1}
                return real

            _DaemonRpcClient.get_run_snapshot = _fake_snap  # type: ignore[method-assign]
            try:
                with pytest.raises(PipelineError, match="requiring reconciliation"):
                    await runner_fenced._prepare_resume_run(run_id, None)
            finally:
                _DaemonRpcClient.get_run_snapshot = orig_get_snap  # type: ignore[method-assign]
                await runner_fenced._close_clients()

        asyncio.run(_check_reconcile_guard())

        # Now simulate -32600 thread not loaded on 0-turn zero_tid -> rehydrates cleanly and completes!
        state_file.write_text(
            json.dumps({"scenario": "resume_flow", "unloaded_zero_turn_tids": [zero_tid]}),
            encoding="utf-8",
        )
        runner_resume = PipelineRunner(
            settings=settings,
            explicit_daemon_url=True,
            codex_bin=mock_script,
            appserver_env={"MOCK_APPSERVER_STATE": str(state_file)},
            output_path=tmp_path / "sum_0turn_ok.json",
        )
        rc, summary = asyncio.run(runner_resume.execute(resume_run_id=run_id))
        assert rc == 0, f"0-turn resume failed: {summary}"
        assert summary["status"] == "completed"


def test_snapshot_cache_exclusion_staging_cleanup_spec_hygiene_and_gb18030(tmp_path: Path) -> None:
    # 1) _sanitize_pytest_cache_argv handles pytest, -m pytest, and uv run pytest
    assert _sanitize_pytest_cache_argv(["uv", "run", "pytest", "-q"]) == [
        "uv",
        "run",
        "pytest",
        "-p",
        "no:cacheprovider",
        "-q",
    ]
    assert _sanitize_pytest_cache_argv(["uv", "run", "--frozen", "py.test", "tests"]) == [
        "uv",
        "run",
        "--frozen",
        "py.test",
        "-p",
        "no:cacheprovider",
        "tests",
    ]

    # 2) GB18030 / CP936 fallback decoding in _read_tail preserves Chinese characters without U+FFFD
    gbk_log = tmp_path / "gbk_stderr.log"
    gbk_log.write_bytes("AssertionError: 断言失败，实际输出不匹配\r\n".encode("gb18030"))
    decoded_tail = _read_tail(str(gbk_log))
    assert "断言失败，实际输出不匹配" in decoded_tail
    assert "\ufffd" not in decoded_tail

    # 3) Spec validation: shared verifier across concurrent steps, cwd '..', dep_mode
    proj = tmp_path / "non_git_proj"
    proj.mkdir()
    (proj / ".venv").mkdir()
    assert _resolve_workspace_dep_mode("auto", proj) == "link_ro_cache"
    assert _resolve_workspace_dep_mode("junction", proj) == "link_ro_cache"
    assert _resolve_workspace_dep_mode("none", proj) == "none"

    with pytest.raises(PipelineSpecError, match="cannot share verifier"):
        validate_pipeline_spec(
            {
                "project_root": str(proj),
                "mode": "swarm",
                "title": "Shared Verifier Collision",
                "steps": [
                    {
                        "step_key": "s1",
                        "instruction": "a",
                        "verifier": "v-shared",
                        "file_ownership": ["a.py"],
                        "acceptance_criteria": ["c1"],
                        "commands": [{"id": "c1", "argv": ["python", "-V"], "criteria": ["c1"]}],
                    },
                    {
                        "step_key": "s2",
                        "instruction": "b",
                        "verifier": "v-shared",
                        "file_ownership": ["b.py"],
                        "acceptance_criteria": ["c2"],
                        "commands": [{"id": "c2", "argv": ["python", "-V"], "criteria": ["c2"]}],
                    },
                ],
            }
        )

    with pytest.raises(PipelineSpecError, match="cwd"):
        validate_pipeline_spec(
            {
                "project_root": str(proj),
                "mode": "swarm",
                "title": "Traversal CWD",
                "steps": [
                    {
                        "step_key": "s1",
                        "instruction": "a",
                        "file_ownership": ["a.py"],
                        "acceptance_criteria": ["c1"],
                        "commands": [
                            {"id": "c1", "argv": ["python", "-V"], "cwd": "../outside", "criteria": ["c1"]}
                        ],
                    }
                ],
            }
        )

    # 4) Git monorepo subdirectory offset in _resolve_commands_for_root
    monorepo = _init_temp_git_repo(tmp_path / "monorepo", {"services/api/app.py": "X = 1\n"})
    sub_proj = monorepo / "services" / "api"
    fake_ws_clone = tmp_path / "ws_clone"
    (fake_ws_clone / ".git").mkdir(parents=True)
    (fake_ws_clone / "services" / "api").mkdir(parents=True)
    resolved_ws_cmds = _resolve_commands_for_root(
        [{"id": "sub_cmd", "argv": ["pytest"], "cwd": ".", "timeout_sec": 10, "criteria": ["c1"]}],
        target_root=fake_ws_clone,
        project_root=sub_proj,
    )
    assert Path(resolved_ws_cmds[0]["cwd"]) == (fake_ws_clone / "services" / "api").resolve()

    # 5) Non-git snapshot pipeline with .pytest_cache/.ruff_cache/.mypy_cache + final-wave integration_commands
    snap_proj = tmp_path / "snapshot_proj"
    snap_proj.mkdir()
    (snap_proj / "mod1.py").write_text("VALUE_1 = 0\n", encoding="utf-8")
    (snap_proj / "mod2.py").write_text("VALUE_2 = 0\n", encoding="utf-8")
    (snap_proj / ".pytest_cache").mkdir()
    (snap_proj / ".pytest_cache" / "README.md").write_text("pytest cache\n", encoding="utf-8")

    mock_script = _write_mock_appserver_script(tmp_path / "mock_appserver.py")
    state_file = tmp_path / "state_snap.json"
    state_file.write_text(json.dumps({"scenario": "resume_flow"}), encoding="utf-8")

    with _ephemeral_daemon(tmp_path) as settings:
        spec = {
            "project_root": str(snap_proj),
            "mode": "swarm",
            "title": "Snapshot Cache Exclusion & Final-Wave Integration",
            "dep_mode": "none",
            "max_workers": 1,
            "steps": [
                {
                    "step_key": "step_1",
                    "instruction": "Set VALUE_1 = 10 in mod1.py",
                    "file_ownership": ["mod1.py"],
                    "acceptance_criteria": ["m1 ok"],
                    "commands": [
                        {
                            "id": "c1",
                            "argv": [
                                sys.executable,
                                "-c",
                                # Simulate author/tool writing .pytest_cache & .ruff_cache in workspace
                                "import pathlib, mod1; "
                                "pathlib.Path('.pytest_cache').mkdir(exist_ok=True); "
                                "pathlib.Path('.pytest_cache/v').write_text('1'); "
                                "pathlib.Path('.ruff_cache').mkdir(exist_ok=True); "
                                "pathlib.Path('.ruff_cache/r').write_text('1'); "
                                "assert mod1.VALUE_1 == 10",
                            ],
                            "criteria": ["m1 ok"],
                        }
                    ],
                },
                {
                    "step_key": "step_2",
                    "instruction": "Set VALUE_2 = VALUE_1 + 25 in mod2.py",
                    "dependencies": ["step_1"],
                    "file_ownership": ["mod2.py"],
                    "acceptance_criteria": ["m2 ok"],
                    "commands": [
                        {
                            "id": "c2",
                            "argv": [sys.executable, "-c", "import mod2; assert mod2.VALUE_2 == 35"],
                            "criteria": ["m2 ok"],
                        }
                    ],
                },
            ],
            # Cross-step integration command referencing step_2 output: would FAIL if run on Wave 1!
            "integration_commands": [
                {
                    "id": "cross_step_int",
                    "argv": [
                        sys.executable,
                        "-c",
                        "import mod1, mod2; assert mod1.VALUE_1 == 10 and mod2.VALUE_2 == 35",
                    ],
                    "criteria": ["m2 ok"],
                }
            ],
        }
        runner = PipelineRunner(
            settings=settings,
            explicit_daemon_url=True,
            codex_bin=mock_script,
            appserver_env={"MOCK_APPSERVER_STATE": str(state_file)},
            output_path=tmp_path / "snap_sum.json",
        )
        rc, summary = asyncio.run(runner.execute(spec=spec))
        assert rc == 0, f"Snapshot pipeline failed: {summary}"
        assert summary["status"] == "completed"
        # Verify merge-staging temp dirs were cleaned up after snapshot merges
        staging_root = settings.data_dir / "merge-staging"
        assert not staging_root.exists() or list(staging_root.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows PATHEXT and quoted/empty PATH resolution")
def test_verify_process_windows_quoted_empty_path_and_dotted_basename(tmp_path: Path) -> None:
    import shutil
    from orchestrator.verify_process import ProcessFailure, run_owned

    # Create a dotted executable name (`py.test.exe`) inside a directory passed as quoted PATH
    sys_whoami = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "whoami.exe"
    tools_dir = tmp_path / "custom tools"
    tools_dir.mkdir()
    dotted_exe = tools_dir / "py.test.exe"
    shutil.copy2(sys_whoami, dotted_exe)

    # Also place a decoy executable in untrusted_cwd to prove empty PATH segments ("") NEVER resolve against cwd!
    untrusted_cwd = tmp_path / "untrusted_cwd"
    untrusted_cwd.mkdir()
    decoy_exe = untrusted_cwd / "only_in_cwd.exe"
    shutil.copy2(sys_whoami, decoy_exe)

    env = dict(os.environ)
    env["PATH"] = f';;"{tools_dir}";;'
    env["PATHEXT"] = ".COM;.EXE;.BAT;.CMD"

    with (tmp_path / "out").open("wb") as out, (tmp_path / "err").open("wb") as err:
        code = run_owned(["py.test"], str(untrusted_cwd), env, 5, out, err)
        assert code == 0
        with pytest.raises(ProcessFailure, match="executable not found"):
            run_owned(["only_in_cwd"], str(untrusted_cwd), env, 5, out, err)
    assert len((tmp_path / "out").read_bytes().strip()) > 0

