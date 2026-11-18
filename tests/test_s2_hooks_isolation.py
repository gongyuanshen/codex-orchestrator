import io
import json

import pytest

from orchestrator import hook_runner


def test_ordinary_turn_reports_normalized_bounded_preview_but_writes_no_stdout(monkeypatch):
    calls = []

    def check_turn(url, tool, arguments, *, timeout_sec):
        calls.append((url, tool, arguments, timeout_sec))
        return {"bound": True, "run_id": "run-bound", "resume_context": "Resume run-bound"}

    monkeypatch.setattr(hook_runner, "call_daemon_rpc", check_turn)
    payload = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": " session-1 ",
        "cwd": " G:/project ",
        "turn_id": "  turn-1  ",
        "prompt": "  Explain\t the\nnext step  " + "x" * 200,
    }
    stdout = io.StringIO()

    assert hook_runner.main(
        ["--timeout-sec", "10"],
        stdin=io.StringIO(json.dumps(payload)),
        stdout=stdout,
        env={"TRI_ORCH_PORT": "19795"},
    ) == 0
    assert stdout.getvalue() == ""
    ((url, tool, arguments, timeout_sec),) = calls
    assert url == "http://127.0.0.1:19795/api/rpc"
    assert tool == "session_hook_check"
    assert timeout_sec == 1.5
    assert arguments["session_id"] == "session-1"
    assert arguments["event_name"] == "UserPromptSubmit"
    assert arguments["cwd"] == "G:/project"
    assert arguments["turn_id"] == "turn-1"
    assert arguments["has_mode_invocation"] is False
    preview = arguments["prompt_preview"]
    assert preview.startswith("Explain the next step ")
    assert preview == " ".join(preview.split())
    assert 0 < len(preview) <= 160


@pytest.mark.parametrize("turn_id", [None, " \t "])
def test_ordinary_prompt_without_turn_id_does_not_call_rpc(monkeypatch, turn_id):
    def unexpected_call(*args, **kwargs):
        pytest.fail("A legacy ordinary prompt must not call the daemon")

    monkeypatch.setattr(hook_runner, "call_daemon_rpc", unexpected_call)
    result = hook_runner.build_hook_stdout_json(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-1",
            "turn_id": turn_id,
            "prompt": "Continue the explanation",
        }
    )
    assert result is None


@pytest.mark.parametrize(
    ("turn_id", "rpc_result"),
    [
        (" turn-2 ", None),
        (" turn-2 ", {"bound": True, "resume_context": "Resume run-bound: " + "x" * 1000}),
        (None, {"bound": False}),
    ],
    ids=["offline", "bound", "legacy-explicit"],
)
def test_mode_prompt_reports_turn_metadata_and_injects_bounded_context(monkeypatch, turn_id, rpc_result):
    calls = []

    def check_turn(_url, tool, arguments, *, timeout_sec):
        calls.append((tool, arguments))
        return rpc_result

    monkeypatch.setattr(hook_runner, "call_daemon_rpc", check_turn)
    result = hook_runner.build_hook_stdout_json(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-1",
            "cwd": "G:/project",
            "turn_id": turn_id,
            "prompt": " /swarm\t finish\n the work ",
        },
        env={},
    )

    assert result.keys() == {"hookSpecificOutput"}
    hook_output = result["hookSpecificOutput"]
    assert hook_output["hookEventName"] == "UserPromptSubmit"
    context = hook_output["additionalContext"]
    assert context.startswith("[tri-orchestrator] Active Codex root session_id=session-1")
    assert len(context) <= hook_runner.MAX_RESUME_CONTEXT_CHARS
    if turn_id:
        assert "turn_id=turn-2" in context
    else:
        assert "turn_id=" not in context
    if rpc_result and rpc_result.get("bound"):
        assert "Resume run-bound:" in context
    ((tool, arguments),) = calls
    assert tool == "session_hook_check"
    assert arguments["turn_id"] == ("turn-2" if turn_id else "")
    assert arguments["prompt_preview"] == "/swarm finish the work"
    assert arguments["has_mode_invocation"] is True

