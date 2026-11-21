"""Lightweight local lifecycle hook runner (`tri-orchestrator-hook`) for Codex plugins.

Design & Safety Red-Lines (M9 / Section 3.4.2):
1. Pure Python stdlib implementation for sub-100ms cold startup.
2. Strict `<= 1.5s` HTTP timeout against `/api/rpc` ONLY (bypassing system proxies on loopback).
   If the daemon is unavailable, emit only local startup or explicit mode context;
   NEVER auto-spawn the daemon or touch SQLite files directly!
3. All stdout payloads conform 100% to Codex plugin hook wire JSON schemas
   (`additionalProperties: false`):
   - `SessionStart`: `startup` injects concise local `session_id` (`<= 220 chars`) or bound `resume_context`; `compact`/`resume` injects `hookSpecificOutput` (`hookEventName="SessionStart"`, `additionalContext <= 800 chars`)
   - `PostCompact`: `{"systemMessage": "<resume_context <= 800 chars>"}`
   - `UserPromptSubmit`: `hookSpecificOutput` (`hookEventName="UserPromptSubmit"`, `additionalContext <= 800 chars`) ONLY on explicit `$boost`/`/boost`/`$teamwork`/`/teamwork`/`$swarm`/`/swarm` root prompts (including cold-boot before `18790` starts)
   - `Stop`: `{"decision": "block", "reason": "..."}` ONLY when all 3 hard safety guardrails pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any, BinaryIO, TextIO

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18790
MAX_RPC_TIMEOUT_SEC = 1.5
DEFAULT_RPC_TIMEOUT_SEC = 1.2
MAX_RESUME_CONTEXT_CHARS = 800
MAX_PROMPT_PREVIEW_CHARS = 160
MAX_STOP_REASON_CHARS = 500

SUPPORTED_HOOK_EVENTS = frozenset({
    "SessionStart",
    "PostCompact",
    "UserPromptSubmit",
    "Stop",
})

NON_RESUME_SESSION_START_SOURCES = frozenset({
    "clear",
    "fork",
})

NON_BLOCKABLE_PROGRESS_STATUSES = frozenset({
    "completed",
    "inactive",
    "user_decision",
    "needs_reconciliation",
    "needs_clarification",
    "no_progress",
    "normal_wait",
    "dependency_wait",
    "host_unknown",
})

# Server-side `session_hook_check` evaluates workflow5 coordinator transitions
# (`candidate_review`, `ready_to_merge`, `rework`, `integrated_review`, closeout)
# where `watchdog.classify_run` reports `classification="normal_wait"`. Client-side
# defense-in-depth still vetoes terminal, user-gated, or stalled classifications,
# while honoring `should_block_stop=True` on `"ready"` and `"normal_wait"`.
_RPC_STOP_HARD_VETO_STATUSES = frozenset({
    "completed",
    "inactive",
    "user_decision",
    "needs_reconciliation",
    "needs_clarification",
    "no_progress",
    "dependency_wait",
    "host_unknown",
})

MAX_STARTUP_SESSION_CONTEXT_CHARS = 220

_MODE_PROMPT_RE = re.compile(
    r"(?:^|[\s(\[{\"'「『【:：]|[^\x00-\x7F])([$/](?:boost|teamwork|swarm))(?=$|[^A-Za-z0-9_-])",
    re.IGNORECASE,
)


def resolve_rpc_url(env: Mapping[str, str] | None = None) -> str | None:
    """Resolve the daemon `/api/rpc` URL from environment without creating directories or locks."""
    environ = os.environ if env is None else env

    explicit_base = environ.get("TRI_ORCH_BASE_URL", "").strip()
    if explicit_base:
        return explicit_base.rstrip("/") + "/api/rpc"

    host = environ.get("TRI_ORCH_HOST", "").strip() or DEFAULT_HOST
    raw_port = (
        environ.get("TRI_ORCH_PORT", "").strip()
        or environ.get("TRI_ORCHESTRATOR_PORT", "").strip()
    )
    if raw_port:
        try:
            port = int(raw_port)
        except ValueError:
            return None
        if not (1 <= port <= 65535):
            return None
    else:
        port = DEFAULT_PORT

    return f"http://{host}:{port}/api/rpc"


def clamp_timeout(timeout_sec: float | None = None, env: Mapping[str, str] | None = None) -> float:
    """Enforce the hard `<= 1.5s` RPC timeout ceiling."""
    if timeout_sec is None:
        environ = os.environ if env is None else env
        raw = environ.get("TRI_ORCH_HOOK_TIMEOUT_SEC", "").strip()
        if raw:
            try:
                timeout_sec = float(raw)
            except ValueError:
                timeout_sec = DEFAULT_RPC_TIMEOUT_SEC
        else:
            timeout_sec = DEFAULT_RPC_TIMEOUT_SEC

    if timeout_sec <= 0:
        return 0.1
    return min(float(timeout_sec), MAX_RPC_TIMEOUT_SEC)


def detect_explicit_mode_invocation(prompt: str) -> str | None:
    """Return normalized mode name ('boost' | 'teamwork' | 'swarm') if explicitly invoked in prompt."""
    if not isinstance(prompt, str) or not prompt.strip():
        return None
    match = _MODE_PROMPT_RE.search(prompt)
    if not match:
        return None
    return match.group(1)[1:].lower()


def truncate_chars(text: str, max_chars: int = MAX_RESUME_CONTEXT_CHARS) -> str:
    """Deterministically cap text to `<= max_chars` characters."""
    cleaned = " ".join(str(text).split()) if "\n" not in str(text) else str(text).strip()
    if max_chars <= 0:
        return ""
    if len(cleaned) <= max_chars:
        return cleaned
    if max_chars <= 3:
        return cleaned[:max_chars]
    return cleaned[: max_chars - 3].rstrip() + "..."


def call_daemon_rpc(
    rpc_url: str,
    tool: str,
    arguments: dict[str, Any],
    *,
    timeout_sec: float = DEFAULT_RPC_TIMEOUT_SEC,
) -> dict[str, Any] | None:
    """POST a single tool call to `/api/rpc` with proxy bypass and strict `<= 1.5s` timeout.

    Returns `result` dict on HTTP 200 `{ok: True, result: {...}}`, or `None` on ANY failure.
    Never spawns processes or retries.
    """
    bounded_timeout = min(max(0.05, float(timeout_sec)), MAX_RPC_TIMEOUT_SEC)
    body_bytes = json.dumps(
        {"tool": tool, "arguments": arguments},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    request = urllib.request.Request(
        rpc_url,
        data=body_bytes,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "Connection": "close",
        },
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=bounded_timeout) as response:
            if getattr(response, "status", 200) != 200:
                return None
            raw = response.read(262_144)
    except urllib.error.HTTPError as exc:
        try:
            exc.close()
        except Exception:
            pass
        return None
    except Exception:
        return None

    try:
        decoded = json.loads(raw.decode("utf-8"))
    except Exception:
        return None

    if not isinstance(decoded, dict) or decoded.get("ok") is not True:
        return None
    result = decoded.get("result")
    return result if isinstance(result, dict) else None


def compute_workflow_summary_digest(workflow_summary: Any) -> str:
    """Compute a stable short hex digest over workflow summary state."""
    canonical = json.dumps(
        workflow_summary if workflow_summary is not None else {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[:16]


def compute_stop_blocked_key(status: Mapping[str, Any]) -> str:
    """Compute `(run_id, latest_seq, workflow_summary_digest)` single-shot stop key."""
    run = status.get("run") if isinstance(status.get("run"), Mapping) else {}
    run_id = str(status.get("run_id") or run.get("id") or "")
    latest_seq = int(status.get("latest_seq") or 0)
    wf_digest = compute_workflow_summary_digest(status.get("workflow"))
    return f"{run_id}:{latest_seq}:{wf_digest}"


def _extract_ready_coordinator_actions(status: Mapping[str, Any]) -> list[str]:
    """Extract concise summaries of ready non-control coordinator actions from `board_status`."""
    native = status.get("native_actions")
    if not isinstance(native, Mapping):
        native = status

    summaries: list[str] = []

    raw_actions = native.get("next_actions")
    if isinstance(raw_actions, list):
        for item in raw_actions:
            if not isinstance(item, Mapping):
                continue
            if item.get("source_type") == "control":
                continue
            action_type = str(item.get("action_type") or item.get("kind") or "dispatch")
            recipient = str(item.get("recipient") or "")
            step_id = str(item.get("step_id") or "")
            summary = str(item.get("summary") or "").strip()
            label = f"{action_type}->{recipient}" if recipient else action_type
            if step_id:
                label = f"{label}({step_id})"
            if summary:
                label = f"{label}: {truncate_chars(summary, 60)}"
            summaries.append(label)

    raw_groups = native.get("next_action_groups")
    if isinstance(raw_groups, list):
        for group in raw_groups:
            if not isinstance(group, Mapping):
                continue
            if group.get("source_type") == "control":
                continue
            recipient = str(group.get("recipient") or "worker")
            members = group.get("members")
            count = len(members) if isinstance(members, list) else 1
            summaries.append(f"followup_task->{recipient}({count})")

    # Also check workflow steps that have completed verification and await coordinator merge/closeout,
    # or submitted steps awaiting verifier dispatch when no blocking items exist.
    wf = status.get("workflow")
    if isinstance(wf, Mapping) and isinstance(wf.get("steps"), list):
        for step in wf["steps"]:
            if not isinstance(step, Mapping):
                continue
            state = str(step.get("state") or "")
            step_key = str(step.get("step_key") or step.get("id") or "step")
            if state in {"ready_to_merge", "submitted", "handoff"}:
                marker = f"step:{step_key}[{state}]"
                if marker not in summaries:
                    summaries.append(marker)

    return summaries


def format_resume_context_from_board(
    status: Mapping[str, Any],
    *,
    max_chars: int = MAX_RESUME_CONTEXT_CHARS,
) -> str:
    """Format a `<= 800` char `resume_context` pointer from `board_status` payload."""
    run = status.get("run") if isinstance(status.get("run"), Mapping) else {}
    run_id = str(status.get("run_id") or run.get("id") or "").strip()
    if not run_id or str(run.get("status") or "") != "active":
        return ""

    mode = str(run.get("mode") or "unknown")
    wf_ver = run.get("workflow_version")
    packet = status.get("latest_task_packet")
    packet_ver: Any = None
    if isinstance(packet, Mapping):
        packet_ver = packet.get("version") or packet.get("packet_version") or packet.get("id")
    resume_obj = status.get("resume_context")
    if packet_ver is None and isinstance(resume_obj, Mapping):
        packet_ver = resume_obj.get("frozen_packet_id") or "none"
    if packet_ver is None:
        packet_ver = "none"

    unfinished: list[str] = []
    wf = status.get("workflow")
    if isinstance(wf, Mapping) and isinstance(wf.get("steps"), list):
        for s in wf["steps"]:
            if isinstance(s, Mapping) and s.get("state") != "done":
                key = str(s.get("step_key") or s.get("id") or "?")
                unfinished.append(f"{key}:{s.get('state')}")
    elif isinstance(resume_obj, Mapping) and isinstance(resume_obj.get("milestones"), list):
        for m in resume_obj["milestones"]:
            if isinstance(m, Mapping) and m.get("state") != "done":
                key = str(m.get("step_key") or m.get("step_id") or "?")
                unfinished.append(f"{key}:{m.get('state')}")

    actions = _extract_ready_coordinator_actions(status)
    steps_part = ", ".join(unfinished[:6]) if unfinished else "none"
    if len(unfinished) > 6:
        steps_part += f" (+{len(unfinished) - 6} more)"
    actions_part = "; ".join(actions[:4]) if actions else "none (check board_status(compact=True))"

    text = (
        f"[tri-orchestrator resume] run_id={run_id} mode={mode} wf=v{wf_ver} "
        f"packet_version={packet_ver} seq={status.get('latest_seq', 0)} | "
        f"unfinished_steps=[{steps_part}] | next_actions=[{actions_part}]. "
        f"Query board_status(run_id=\"{run_id}\", compact=True) before acting."
    )
    return truncate_chars(text, max_chars=max_chars)


def evaluate_stop_from_board(
    status: Mapping[str, Any],
    *,
    stop_hook_active: bool = False,
    last_stop_blocked_key: str | None = None,
) -> tuple[bool, str, str]:
    """Evaluate all 3 Section 3.4.2 Stop guardrails against a `board_status` snapshot.

    Returns `(should_block, reason, blocked_key)`.
    """
    # Guardrail 2a: Re-entry single-shot cap
    if stop_hook_active:
        return (False, "", "")

    run = status.get("run") if isinstance(status.get("run"), Mapping) else {}
    run_id = str(status.get("run_id") or run.get("id") or "").strip()
    if not run_id or str(run.get("status") or "") != "active":
        return (False, "", "")

    # Guardrail 3a: Pending user control requests take absolute priority
    controls = status.get("control_requests")
    if isinstance(controls, list):
        for ctrl in controls:
            if isinstance(ctrl, Mapping) and str(ctrl.get("status") or "pending") == "pending":
                return (False, "", "")

    # Guardrail 3b: Any stopped or killed worker forbids automatic continuation
    workers = status.get("workers")
    if isinstance(workers, list):
        for worker in workers:
            if isinstance(worker, Mapping) and str(worker.get("status") or "") in {"stopped", "killed"}:
                return (False, "", "")

    # Guardrail 3c: Native reconciliation or clarification pending
    native = status.get("native_actions")
    if not isinstance(native, Mapping):
        native = status
    if native.get("needs_reconciliation") or native.get("needs_reconciliation_count"):
        return (False, "", "")
    if native.get("needs_clarification") or native.get("needs_clarification_count"):
        return (False, "", "")

    # Guardrail 3d: Progress classification check
    prog = status.get("progress_status")
    if isinstance(prog, Mapping):
        classification = str(prog.get("classification") or "")
        if prog.get("blockers"):
            return (False, "", "")
    else:
        classification = str(prog or "")

    if classification in NON_BLOCKABLE_PROGRESS_STATUSES:
        return (False, "", "")
    if classification and classification != "ready":
        return (False, "", "")

    ready_actions = _extract_ready_coordinator_actions(status)
    if not ready_actions:
        return (False, "", "")

    blocked_key = compute_stop_blocked_key(status)
    # Guardrail 2b: State unchanged since previous single-shot block
    if last_stop_blocked_key and blocked_key == last_stop_blocked_key:
        return (False, "", blocked_key)

    preview = "; ".join(ready_actions[:2])
    reason = truncate_chars(
        f"Run {run_id} 仍有 {len(ready_actions)} 项无阻塞待办动作（如 {preview}），"
        f"请按契约调用 board_status(run_id=\"{run_id}\", compact=True) 继续推进或明确说明阻塞原因。",
        max_chars=MAX_STOP_REASON_CHARS,
    )
    return (True, reason, blocked_key)


def evaluate_rpc_hook_response_for_stop(
    rpc_result: Mapping[str, Any],
    *,
    stop_hook_active: bool = False,
) -> tuple[bool, str]:
    """Apply client-side defense-in-depth over `session_hook_check` Stop response."""
    if stop_hook_active:
        return (False, "")
    if not rpc_result.get("bound") or not rpc_result.get("run_id"):
        return (False, "")

    run_status = rpc_result.get("run_status")
    if run_status is not None and str(run_status) != "active":
        return (False, "")

    if rpc_result.get("has_pending_controls") or (
        isinstance(rpc_result.get("pending_controls_count"), int)
        and int(rpc_result["pending_controls_count"]) > 0
    ):
        return (False, "")

    if rpc_result.get("has_stopped_worker") or rpc_result.get("has_stopped_or_killed_worker"):
        return (False, "")

    prog_cls = rpc_result.get("progress_classification") or rpc_result.get("progress_status")
    if isinstance(prog_cls, Mapping):
        prog_cls = prog_cls.get("classification")
    if prog_cls is not None:
        cls_str = str(prog_cls)
        if cls_str in _RPC_STOP_HARD_VETO_STATUSES or (
            cls_str and cls_str not in {"ready", "normal_wait"}
        ):
            return (False, "")

    blocked_key = str(rpc_result.get("blocked_key") or "")
    prev_key = str(rpc_result.get("previous_blocked_key") or "")
    if blocked_key and prev_key and blocked_key == prev_key:
        return (False, "")

    if rpc_result.get("should_block_stop") is not True:
        return (False, "")

    run_id = str(rpc_result["run_id"])
    raw_reason = str(rpc_result.get("stop_reason") or "").strip()
    if not raw_reason:
        raw_reason = (
            f"Run {run_id} 仍有无阻塞待办动作，请调用 board_status(run_id=\"{run_id}\", compact=True) "
            "继续推进或明确说明阻塞原因。"
        )
    return (True, truncate_chars(raw_reason, max_chars=MAX_STOP_REASON_CHARS))


def build_hook_stdout_json(
    payload: Mapping[str, Any],
    *,
    env: Mapping[str, str] | None = None,
    timeout_sec: float | None = None,
) -> dict[str, Any] | None:
    """Process a parsed Codex hook stdin JSON dict and return stdout JSON dict (or `None` for silent pass)."""
    # Guardrail 1 fast-path: ignore any subagent hook payload (`agent_id` present or Subagent* events)
    agent_id = payload.get("agent_id")
    if isinstance(agent_id, str) and agent_id.strip():
        return None

    event_name = str(payload.get("hook_event_name") or "").strip()
    if event_name not in SUPPORTED_HOOK_EVENTS:
        return None

    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        return None

    cwd = str(payload.get("cwd") or "").strip()
    stop_hook_active = bool(payload.get("stop_hook_active"))
    model = str(payload.get("model") or "").strip()
    turn_id = str(payload.get("turn_id") or "").strip()

    # Event-specific local fast-paths (zero network I/O when unneeded)
    if event_name == "Stop" and stop_hook_active:
        return None

    if event_name == "SessionStart":
        source = str(payload.get("source") or "").strip().lower()
        if source in NON_RESUME_SESSION_START_SOURCES:
            return None

    if event_name == "UserPromptSubmit":
        prompt = str(payload.get("prompt") or "")
        invoked_mode = detect_explicit_mode_invocation(prompt)
        if invoked_mode is None and not turn_id:
            return None

        rpc_url = resolve_rpc_url(env)
        turn_result = None
        if rpc_url:
            rpc_args: dict[str, Any] = {
                "session_id": session_id,
                "event_name": event_name,
                "cwd": cwd,
                "turn_id": turn_id,
                "prompt_preview": truncate_chars(" ".join(prompt.split()), MAX_PROMPT_PREVIEW_CHARS),
                "has_mode_invocation": invoked_mode is not None,
            }
            if model:
                rpc_args["model"] = model
            turn_result = call_daemon_rpc(
                rpc_url,
                "session_hook_check",
                rpc_args,
                timeout_sec=clamp_timeout(timeout_sec, env),
            )
        if invoked_mode is None:
            return None

        context = f"[tri-orchestrator] Active Codex root session_id={session_id}"
        if turn_id:
            context += f" turn_id={turn_id}"
        context += f" for ${invoked_mode}"
        if turn_result and turn_result.get("bound") and turn_result.get("resume_context"):
            context += f" | {turn_result['resume_context']}"
        else:
            context += f' (pass session_id="{session_id}" to run_create / workflow_prepare_dispatch).'
        return {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": truncate_chars(context, max_chars=MAX_RESUME_CONTEXT_CHARS),
            }
        }

    rpc_url = resolve_rpc_url(env)
    if not rpc_url:
        # Startup still carries the local session_id if the RPC URL is invalid.
        if event_name == "SessionStart":
            source = str(payload.get("source") or "").strip().lower()
            if source == "startup":
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": truncate_chars(
                            f"[tri-orchestrator] Active Codex root session_id={session_id} "
                            f"(pass session_id=\"{session_id}\" to run_create, session_bind, or workflow_prepare_dispatch).",
                            max_chars=MAX_STARTUP_SESSION_CONTEXT_CHARS,
                        ),
                    }
                }
        return None

    deadline_timeout = clamp_timeout(timeout_sec, env)
    started = time.monotonic()

    main_rpc_args: dict[str, Any] = {
        "session_id": session_id,
        "event_name": event_name,
        "cwd": cwd,
        "stop_hook_active": stop_hook_active,
        "mark_stop_blocked": bool(event_name == "Stop" and not stop_hook_active),
    }
    if turn_id:
        main_rpc_args["turn_id"] = turn_id
    if model:
        main_rpc_args["model"] = model

    rpc_result = call_daemon_rpc(
        rpc_url,
        "session_hook_check",
        main_rpc_args,
        timeout_sec=deadline_timeout,
    )

    if event_name == "SessionStart":
        source = str(payload.get("source") or "").strip().lower()
        if source == "startup":
            # SessionStart(startup): Always inject session_id locally.
            # Merge resume_context from daemon if already bound, otherwise use local fallback.
            if rpc_result is not None and rpc_result.get("bound"):
                resume_text = str(rpc_result.get("resume_context") or "").strip()
                if not resume_text and rpc_result.get("run_id"):
                    remaining = deadline_timeout - (time.monotonic() - started)
                    if remaining > 0.1:
                        board = call_daemon_rpc(
                            rpc_url,
                            "board_status",
                            {"run_id": str(rpc_result["run_id"]), "compact": True},
                            timeout_sec=remaining,
                        )
                        if board is not None:
                            resume_text = format_resume_context_from_board(board)
                if resume_text:
                    merged_resume = (
                        resume_text
                        if session_id in resume_text
                        else f"[tri-orchestrator session_id={session_id}] {resume_text}"
                    )
                    return {
                        "hookSpecificOutput": {
                            "hookEventName": "SessionStart",
                            "additionalContext": truncate_chars(
                                merged_resume, max_chars=MAX_RESUME_CONTEXT_CHARS
                            ),
                        }
                    }
            # Fallback: inject local session_id even when daemon is offline or session unbound
            return {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": truncate_chars(
                        f"[tri-orchestrator] Active Codex root session_id={session_id} "
                        f"(pass session_id=\"{session_id}\" to run_create, session_bind, or workflow_prepare_dispatch).",
                        max_chars=MAX_STARTUP_SESSION_CONTEXT_CHARS,
                    ),
                }
            }

    if rpc_result is None:
        return None

    if event_name in {"SessionStart", "PostCompact"}:
        # For non-startup SessionStart (compact/resume) and PostCompact, require bound session
        if not rpc_result.get("bound"):
            return None
        resume_text = str(rpc_result.get("resume_context") or "").strip()
        if not resume_text and rpc_result.get("run_id"):
            remaining = deadline_timeout - (time.monotonic() - started)
            if remaining > 0.1:
                board = call_daemon_rpc(
                    rpc_url,
                    "board_status",
                    {"run_id": str(rpc_result["run_id"]), "compact": True},
                    timeout_sec=remaining,
                )
                if board is not None:
                    resume_text = format_resume_context_from_board(board)
        if not resume_text:
            return None
        capped = truncate_chars(resume_text, max_chars=MAX_RESUME_CONTEXT_CHARS)
        if event_name == "SessionStart":
            return {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": capped,
                }
            }
        # PostCompact schema (`post-compact.command.output`) forbids `hookSpecificOutput` (`additionalProperties: false`)
        return {"systemMessage": capped}

    if event_name == "Stop":
        should_block, reason = evaluate_rpc_hook_response_for_stop(
            rpc_result,
            stop_hook_active=stop_hook_active,
        )
        if not should_block or not reason:
            return None
        return {
            "decision": "block",
            "reason": reason,
        }

    return None


def _read_stdin_utf8(stdin: TextIO | BinaryIO | None = None) -> str:
    stream = sys.stdin if stdin is None else stdin
    buf = getattr(stream, "buffer", None)
    if buf is not None:
        raw_bytes = buf.read()
        return raw_bytes.decode("utf-8", errors="replace")
    content = stream.read()
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    return str(content)


def _write_stdout_utf8(text: str, stdout: TextIO | BinaryIO | None = None) -> None:
    stream = sys.stdout if stdout is None else stdout
    buf = getattr(stream, "buffer", None)
    if buf is not None:
        buf.write(text.encode("utf-8"))
        buf.flush()
        return
    stream.write(text)
    stream.flush()


def main(
    argv: list[str] | None = None,
    *,
    stdin: TextIO | BinaryIO | None = None,
    stdout: TextIO | BinaryIO | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    """CLI entrypoint for `tri-orchestrator-hook`."""
    parser = argparse.ArgumentParser(
        prog="tri-orchestrator-hook",
        description="Lightweight read-mostly Codex lifecycle hook adapter for tri-orchestrator.",
    )
    parser.add_argument(
        "--timeout-sec",
        type=float,
        default=None,
        help="Optional RPC timeout override in seconds (hard-clamped to <= 1.5s).",
    )
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 0

    try:
        raw_stdin = _read_stdin_utf8(stdin)
        if not raw_stdin or not raw_stdin.strip():
            return 0
        parsed = json.loads(raw_stdin)
        if not isinstance(parsed, dict):
            return 0
        output = build_hook_stdout_json(
            parsed,
            env=env,
            timeout_sec=args.timeout_sec,
        )
        if output is not None:
            _write_stdout_utf8(json.dumps(output, ensure_ascii=False), stdout=stdout)
    except Exception:
        # Never block or crash the host Codex turn on hook errors.
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
