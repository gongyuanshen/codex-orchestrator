---
name: teamwork
description: "Structured multi-agent engineering workflow ($teamwork) with optional [skip-interview], requirement clarification, architecture planning with mutually exclusive file ownership, isolated parallel coders, two-stage verification, same-thread rework, and safe sequential merge. Use for medium-to-large features or refactors needing strict spec freeze and file ownership separation."
metadata:
  short-description: "Spec freeze -> Task Packet -> Parallel isolation -> 2-stage review"
---

## M6/M7/M8 workflow5 primary path (software 0.6.1/schema9 default)

New runs default to `workflow5` (`0.6.1/schema9`); stored runs follow `board_status.workflow.workflow_version` without fabricated historical evidence:

- **Coordinator 2-step Macro Dispatch & 1-turn Direct Spawn**: Bind the root session on `run_create`; reuse that `session_id` if also passing it to `workflow_prepare_dispatch` (`run_create` and `board_status(compact=True)` also expose `cli_executables` for `tri-orchestrator-verify` and `tri-orchestrator-challenge`). For each author slice, call `workflow_prepare_dispatch` with deterministic `task_name` (`^[a-z0-9_-]{1,48}$`, pre-freezing `/root/<task_name>`, optional `create_workspace=True`) -> issue 1-turn direct `spawn_agent(task_name="<task_name>", message="<full contract + workspace + step_id + message_id>")` with zero waiting-only bootstrap empty turns -> call `native_action_record_call(run_id=..., action_id=..., claim_token=..., expected_revision=..., outcome="confirmed"|"failed", raw_response=...)` (server writes receipt JSON, computes 64-hex SHA-256 `evidence_digest`, and atomically binds `agent_id` + `original_bindings`).
- **`spawn_agent` Reconciliation Fence**: If `begin(action_type="spawn_agent")` times out, disconnects, or restarts into `needs_reconciliation`, NEVER blindly re-call `spawn_agent`; first call `list_agents(path_prefix="/root/<task_name>")` and pass observed state to `native_action_update(action="reconcile", ...)` (`outcome="confirmed"` if present; `outcome="not_called"|"rejected", cancel=True` ONLY if proven `not_found` or explicitly rejected).
- **Author & Verifier Macro Turns, `env_profile`, Lazy `verifier` & 1-Call Closeout**: Authors use `workflow_author_turn(run_id=..., step_id=..., message_id=..., phase="accept"|"submit", body_md=..., post_board_note=...)` (`accept` auto-sets `working` + heartbeat; `submit` auto-posts optional `#verify` details and resets `waiting`). Register `verifier` logically at step creation (`verifier_agent_id=""`, optional `verifier_task_name`), spawn it directly in 1 turn on first candidate review, permanently freeze `original_bindings`, and reuse that exact `verifier` via `followup_task` for rework and integrated checks. Verifiers call `workflow_verifier_turn(phase="prepare")` (atomically running `inbox_ack("accepted")` + `artifact_capture(..., env_profile="windows_minimal_py"|"posix_minimal_py")` and returning `verify_executable` / `verify_command_hint`), run real `tri-orchestrator-verify` in the host terminal, and call `workflow_verifier_turn(phase="record")` (atomically handling `verification_record` + `inbox_ack("completed")` + `waiting` + `#verify` note; manual callers must likewise run explicit `inbox_ack`). After all milestones and final integrated gates pass, coordinators finish in **1 MCP call** via `workflow_closeout_run(run_id=run_id)`.
- **Compact Polling (`<=30s`) & Parallel Direct MCP Calls**: Poll `board_status(run_id=run_id, compact=True)` around `wait_agent(timeout_ms=30000)` so Web UI pause/stop (`control_requests`) responds within `<=30s`. Issue multi-workspace read-only checks (`workspace_status`, `workspace_diff`, `workspace_merge_preview`) as parallel direct MCP tool calls in the same assistant turn before merge; invoke `collaboration.*` ONLY as top-level direct calls, and check `isError` on every write tool and abort immediately without automatic retries.

## M3/M4/M5 workflow4 (inherited baseline; superseded as default by workflow5)

M3/M4/M5 baseline (`software 0.5.0/schema7`, where New runs use workflow4) is inherited by `workflow5`, while stored `workflow0/1/2/3` preserve legacy semantics via `board_status.workflow.workflow_version`:
- Read-only `plan_readiness` and `resume_context` are hints, not authorization; enforce mutually exclusive parallel ownership, predecessor integrated PASS + `handoff_digest` ACK + native quiescence on sequential overlap, and grouped transfers (`native_action_update` `claim_group` -> `begin_group` -> `record_group` / `reconcile_group` or `native_action_record_call`).
- High-complexity steps map read-only challenger findings via `workflow_review` (or via host `tri-orchestrator-challenge`), require `review_dimensions` (`specification/code/stress/evidence`) on `verification_record`, resolve repairs via `resolved_finding_ids`, and allow original-verifier PASS `dismissed_finding_ids` only for unsubstantiated findings without consuming an attempt.
- Interactive Teamwork defaults to `execution_kind="native"` (while Boost Solo with no workspace uses `coordinator_local`); revising executed local steps requires single-use `local_revision_boundary` (`content_digest`), consumed by `task_packet_save` and requiring a fresh proof before `workflow_step_save`.

## M2 workflow3 collector (inherited by workflow4/workflow5)

Stored `workflow3`..`workflow5` runs require the original verifier to freeze `verification_commands` in `artifact_capture` (`env_profile` or `env_keys` as the exact child environment allowlist from the verifier terminal—daemon validates only key syntax/uniqueness, list `SystemRoot/PATH/TEMP`, never serialize values), run `tri-orchestrator-verify --manifest ABS --output-dir NEW_ABS` in the host terminal, and pass real `receipt_files` to `verification_record` (`local_collector_v1`, separate candidate/integrated gates).

## M1 transfer protocol

All native transfers use the shared M1 ledger (`inbox_send` -> `board_status` -> `native_action_update` `claim` -> `begin` -> native call -> `record`, encapsulated by `workflow_prepare_dispatch` + `native_action_record_call` in `workflow5`; stored `workflow1..4` bootstrap children remain waiting-only; uncertain calls `reconcile` without auto-retry). Preserve original author, original verifier, and at most 3 automatic rework rounds; inbox ACK, business PASS, and control ACK (`worker_control_ack`) remain separate.

## Route A structured workflow (Required)

Before running `workflow5` or resuming `workflow_version=1/2/3/4/5`, read [the shared workflow contract](../route-a-workflow.md) and branch legacy behavior strictly by `board_status.workflow.workflow_version`. Following the first failed delivery, use at most 3 automatic rework rounds; then `needs_decision` stops further automatic work.


# `$teamwork` — Structured Multi-Agent Engineering Pipeline

Run a disciplined end-to-end delivery flow backed by `tri-orchestrator` SQLite Task Packets, isolated `.swarm-workspaces` clones, and a two-stage verification gate.


## Mode2 milestones and coordinator handoff

Use one `kind="milestone"` work step per milestone with explicit criteria/dependencies, retaining candidate and integrated verification on the milestone's existing attempt counter (never a nested retry loop). Confirm predecessor integrated PASS and confirmed handoff (`action="handoff"` with `recipient_agent_id` set to the intended recipient's native binding -> `action="ack_handoff"` with `handoff_digest`; single-root `$teamwork` sessions without a second coordinator may pass `handoff_required=False` at step creation) before dispatching dependent milestones; root keeps native child lifecycle ownership.

## Non-Negotiable Codex Subagent Rules

- **Dynamic Model & Reasoning Effort Inheritance**: NEVER pass a hardcoded `model` or `reasoning_effort` parameter to `spawn_agent` (unless the user explicitly asks for an override). Omit both `model` and `reasoning_effort` so 100% of subagents inherit your active Codex session model and reasoning effort tier.
- **Role Resolution & Zero-Script `agent_type` Omission**: Prefer specialized `agent_type` (`interviewer`, `planner`, `investigator`, `coder`, `verifier`) when installed in `~/.codex/agents/*.toml` (which also omits `model` and `model_reasoning_effort` to inherit session defaults). When `install.ps1` / `install.sh` has not installed custom agent TOMLs, **omit `agent_type` entirely** (do NOT pass a fallback `"default"` role name, because `codex.exe` omits `agent_type` from `spawn_agent`'s `additionalProperties: false` schema when `~/.codex/agents/` is empty), inline the matching contract from **Inline Fallback Role Contracts**, and omit `model` and `reasoning_effort` so the subagent inherits your session model and reasoning effort tier.
  Always pass the logical role (`interviewer`, `planner`, `investigator`, `coder`, `verifier`) to `worker_register` / `workflow_prepare_dispatch`.
- **Nuanced `fork_turns` Policy**:
  - In Codex V2, custom `agent_type` applies regardless of `fork_turns`. MUST keep `fork_turns: "none"` to keep child context minimal for independent `verifier` and high-complexity counterexample `challenger` (unbiased blind review).
  - ALLOW `fork_turns: "all"` when the coordinator has already loaded key context and the subagent (`coder`, `swarm_worker`, or scoped branch `investigator`) does not need context isolation, reusing warm KV cache instead of cold rescanning.
- **Controlled `max_depth = 2` Read-Only `investigator` Helpers**:
  - `interviewer`, `investigator`, and `challenger` are strictly forbidden from spawning subagents.
  - Depth-1 `planner`, `coder`, `swarm_worker`, and `verifier` MAY spawn pure read-only `investigator` helpers within active concurrency budget ONLY for cross-module call-chain lookup (helpers must never edit files, never register as author/verifier on `18790`, and never submit verification receipts; caller remains solely accountable).
- **Same-Thread Rework**: Resume the SAME `coder` thread with `followup_task` (V2) or `send_input` (V1) whenever Stage 1 or Stage 2 verification finds gaps. Never replace a coder to paper over defects.
- **Control-Aware Waiting (`<=30s`) & Parallel Direct MCP Calls**: Use `wait_agent(timeout_ms=30000)` max wait interval paired with `board_status(run_id=run_id, compact=True)` before and after waits so Web UI pause/stop (`control_requests`) responds within `<=30s`. Issue multi-workspace read-only checks (`workspace_status`, `workspace_diff`, `workspace_merge_preview`) as parallel direct MCP tool calls in the same assistant turn; keep `collaboration.*` as top-level direct calls and check `isError` on every write tool, forbidding automatic retries on write errors.

## Inline Fallback Role Contracts (when `~/.codex/agents/` is absent)

Include the matching contract verbatim in the child prompt whenever omitting `agent_type` (because `~/.codex/agents/` is absent):

- **`interviewer` (Read-Only Requirement Clarifier)**: Strictly read-only — NEVER edit files or run mutating commands; preserve verbatim constraints, ask ONE focused high-leverage question per round (2–4 options + recommended default), output a Frozen Requirement & Acceptance Contract, and post via `board_post(channel="main", msg_type="info")`. Strictly forbidden from spawning subagents (including when acting as challenger). Apply the shared M1 transfer protocol, M2 workflow3 collector contract (`env_keys allowlist`), and shared M3/M4/M5 workflow4/workflow5 contract (`execution_kind`, Boost-only `coordinator_local`, and single-use `local_revision_boundary`).
- **`planner` (Architecture & File Ownership Matrix)**: Do NOT edit application code or tests; lock cross-module interfaces/schemas first, produce a mutually exclusive file ownership matrix (`worker_name -> [owned_paths]` with zero writer overlap + `read_only_shared_paths`), ordered waves/merge sequence, and verifiable acceptance criteria, persisting via `task_packet_save`. Depth-1 role: may spawn pure read-only `investigator` helpers within active concurrency budget for cross-module call-chain lookup ONLY (helpers never edit files, never register as author/verifier on `18790`, and never submit verification receipts; caller remains solely accountable). Apply the shared M1 transfer protocol, M2 workflow3 collector contract (`env_keys allowlist`), and shared M3/M4/M5 workflow4/workflow5 contract (`execution_kind` and single-use `local_revision_boundary`).
- **`investigator` (Read-Only Explorer)**: Strictly read-only — NEVER edit files or run mutating git commands; trace code paths empirically, cite exact `file:line` and observed command outputs, and report Root Cause, Evidence, Blast Radius, and Recommended Fix Surface. Strictly forbidden from spawning subagents (including when acting as challenger). Apply the shared M1 transfer protocol, M2 workflow3 collector contract (`env_keys allowlist`), and shared M3/M4/M5 workflow4/workflow5 contract (`execution_kind` and single-use `local_revision_boundary`).
- **`coder` (Scoped Implementer & Tests)**: Work EXCLUSIVELY inside your assigned isolated workspace path and edit ONLY your owned files from `task_packet_get`; call `worker_heartbeat`, write real tests, run tests AFTER your final edit, commit clean changes on your assigned workspace branch only for `git_clone`, and for snapshot workspaces report the reviewed diff without a Git commit. Depth-1 role: may spawn pure read-only `investigator` helpers within active concurrency budget for cross-module call-chain lookup ONLY (helpers never edit files, never register as author/verifier on `18790`, and never submit verification receipts; caller remains solely accountable). Use `workflow_author_turn(phase="accept"|"submit")` and apply the shared M1 transfer protocol, M2 workflow3 collector contract (`env_keys allowlist`), and shared M3/M4/M5 workflow4/workflow5 contract (`execution_kind` and single-use `local_revision_boundary`).
- **`verifier` (Independent Two-Stage Gate)**: NEVER edit or commit source/test files; inspect real `workspace_diff` / `git diff`, `task_packet_get`, run `workflow_verifier_turn(phase="prepare")` (atomically running `inbox_ack("accepted")` + `artifact_capture(env_profile=...)` and returning `verify_executable` / `verify_command_hint`), execute Stage 1 (Spec Compliance) and Stage 2 (Code Quality & Real Tests via `tri-orchestrator-verify`), record via `workflow_verifier_turn(phase="record")` (`verification_record` + `inbox_ack("completed")` + `board_post(channel="#verify", msg_type="test")`), and output `VERDICT: PASS` or `VERDICT: FAIL` with `[P0]`/`[P1]`/`[P2]` `file:line` defects. Depth-1 role: may spawn pure read-only `investigator` helpers within active concurrency budget for cross-module call-chain lookup ONLY (helpers never edit files, never register as author/verifier on `18790`, and never submit verification receipts; caller remains solely accountable). Apply the shared M1 transfer protocol, M2 workflow3 collector contract (`env_keys allowlist`), and shared M3/M4/M5 workflow4/workflow5 contract (`execution_kind` and single-use `local_revision_boundary`).


## Native Agent Binding, State and Human Control (Required)

- In `workflow5`, `workflow_prepare_dispatch(task_name=...)` registers `status="waiting"` (`pending_agent_path="/root/<task_name>"`) and `native_action_record_call` atomically binds `agent_id` and sets `status="working"` after `spawn_agent`. On manual/stored paths, call `worker_register` pre-spawn with `status="waiting"`, then re-call with returned `agent_id` and report `status="working"` only after actual dispatch.
- Inspect native `list_agents` / `wait_agent` for real host state (`working/waiting/stopped/killed`); never revive killed workers or auto-resume paused ones without explicit user instruction.
- Before dispatching/reworking, before and after `wait_agent(timeout_ms=30000)`, and before closure, read `board_status(run_id=run_id, compact=True)` and handle every pending `control_requests` entry within `<=30s`.
- Interrupt the exact snapshotted `agent_id` with `interrupt_agent`, confirm idle/stopped state, and call `worker_control_ack(request_id=..., outcome="applied"|"failed", result=...)`; uncertain interruptions stay `needs_reconciliation`. Do not bypass MCP or pending acknowledgements with `worker_status_update`.

## Workspace Mode & Dependency Contract

- Pass returned `mode`, `path`, `branch_name`, and `workspace_id` to writers and verifiers.
- For `mode="git_clone"` only: commit owned changes on the assigned branch, report actual commit SHA, and require a clean Git worktree before verification and merge.
- For `mode="snapshot"`: `branch_name=None` is normal and `dirty=true` before merge is expected. Do not require a Git commit or a clean Git worktree; verify `workspace_diff`, ownership, and post-edit tests (`workspace_merge_preview` / `workspace_merge(workspace_id, strategy="merge")` run a three-way snapshot comparison/apply).
- Use `dep_mode="none"`, `dep_mode="copy"`, or `dep_mode="fresh_venv_uv"` when dependency writes need isolation; `dep_mode="link_ro_cache"` provides shared writable dependencies and is not read-only or write-isolated.

## Workflow

### Step 0: Parse Flags & Initialize Run

1. Check `[skip-interview]` and active runs (`run_list(status="active", mode="teamwork")`), or initialize a `workflow5` run. Always use the current root session (`session_id=$env:CODEX_THREAD_ID`; use the hook-reported root ID if needed).
   - `run_create(mode="teamwork", title="<concise project task>", project_root="<real_workspace_or_repo_root>", session_id=<root_session_id>, metadata={"skip_interview": <bool>})`
   - Pass a staging/slice subdirectory as `execution_root` when used; never use it as the root or as a new `project_name`.

### Step 1: Requirement Clarification (`interviewer`, unless `[skip-interview]`)

- If `[skip-interview]` is NOT set and requirements are ambiguous: register `worker_register(run_id=run_id, name="interviewer", role="interviewer", status="waiting", current_task="Clarify requirements")`, dispatch `spawn_agent(agent_type="interviewer", fork_turns="none", ...)` (or omit `agent_type` when `~/.codex/agents/` is absent, omitting `model` and `reasoning_effort` to inherit session defaults), clarify one question per round, and post the frozen contract via `board_post`.

### Step 2: Architecture & Mutually Exclusive File Ownership (`planner`)

1. Register `worker_register(run_id=run_id, name="planner", role="planner", status="waiting", current_task="Decompose architecture & file ownership")` and dispatch `spawn_agent(agent_type="planner", fork_turns="none", ...)` (or omit `agent_type` when `~/.codex/agents/` is absent, omitting `model` and `reasoning_effort` to inherit session defaults).
2. Persist locked interfaces, mutually exclusive `file_ownership`, verifiable acceptance criteria, and sequential merge order via `task_packet_save` and verify via `task_packet_get(run_id=run_id)`.

### Step 3: Isolated Workspaces & Parallel Implementation (`coder`)

1. **Primary `workflow5` 1-turn Direct Dispatch**: Register logical `verifier` (`verifier_agent_id=""`), then for each admitted parallel writer (`coder-1`, `coder-2`, ...) call `workflow_prepare_dispatch(run_id=run_id, create_workspace=True, task_name="coder_1", owner="coder-1", verifier="verifier", dispatch_action="dispatch", ...)` -> top-level 1-turn `spawn_agent(task_name="coder_1", ...)` (`fork_turns="all"` when context is warm, or `fork_turns="none"`, omit `model` and `reasoning_effort`) -> `native_action_record_call(...)`. Coders accept/submit via `workflow_author_turn(phase="accept"|"submit")`. (Stored `workflow1..4` fallback via `board_status.workflow.workflow_version`: spawn a bootstrap-only prebound verifier, run `workspace_create` + `worker_register(run_id=run_id, name="coder-1", role="coder", status="waiting", workspace_id=ws["id"])`, call `workflow_step_advance(action="dispatch")`, and deliver briefs via `followup_task`.)
2. Poll `board_status(run_id=run_id, compact=True)` / `board_read` around `wait_agent(timeout_ms=30000)`.

### Step 4: Two-Stage Independent Verification (`verifier`)

1. Issue read-only inspection (`workspace_status`, `workspace_diff`, `workspace_merge_preview`) across completed workspaces via parallel direct MCP tool calls.
2. On first candidate review in `workflow5`, spawn `verifier` directly in 1 turn via `workflow_prepare_dispatch(..., dispatch_action="request_verify", task_name="verifier_1")` -> `spawn_agent(fork_turns="none", ...)` -> `native_action_record_call`, permanently freezing `original_bindings`; on subsequent milestones/rework, reuse that exact `verifier` via `followup_task`.
3. `verifier` runs `workflow_verifier_turn(phase="prepare", scope="candidate", env_profile="windows_minimal_py"|"posix_minimal_py", ...)` (atomically running `inbox_ack("accepted")` + `artifact_capture` and returning `verify_executable` / `cli_executables`), executes Stage 1 (Spec Compliance) and Stage 2 (Code Quality & Real Tests via `tri-orchestrator-verify`, plus optional `tri-orchestrator-challenge` on high-complexity steps), and records via `workflow_verifier_turn(phase="record", ...)` (`verification_record` + `inbox_ack("completed")` + `board_post(channel="#verify", msg_type="test")`).

### Step 5: Same-Thread Rework Loop

- On `VERDICT: FAIL`, call `workflow_prepare_dispatch(..., dispatch_action="start_rework")`, resume the responsible `coder` via `followup_task` / `send_input`, and re-audit with the same frozen `verifier` via `followup_task` within at most 3 automatic rework rounds.

### Step 6: Safe Sequential Merge, Final Verification & Cleanup

For each admitted milestone, complete candidate verification and integration before admitting dependents:
1. Confirm candidate PASS, run `workspace_merge_preview(workspace_id)` (via parallel direct read-only MCP calls when checking multiple workspaces) and `workspace_merge(workspace_id, strategy="merge")` sequentially.
2. Run `workflow_verifier_turn(step_id=step["id"], phase="prepare", scope="integrated", env_profile=...)` (or `artifact_capture(step_id=step["id"], scope="integrated", env_profile=...)`), verify main-directory tests, record `verification_record` via `workflow_verifier_turn(phase="record", scope="integrated", ...)`, and call `workflow_step_advance(action="complete")`.
3. Issue `action="handoff"` (`next_responsibility`, `forbidden_actions`, `decision_seqs`) and have the receiving coordinator confirm `action="ack_handoff"` with exact `handoff_digest`.
4. Only then admit the next dependent milestone (`workflow_prepare_dispatch` / `workflow_step_advance(action="dispatch")`), retaining all original author workspaces.

After all milestones and final integrated gates pass (reopening affected scopes with `action="reverify"` if needed), call `workflow_closeout_run(run_id=run_id)` to atomically finish step `complete`, `workspace_cleanup`, worker `stopped`, and `run_update(status="completed")` in **1 MCP call**.


### Large MCP results (explicit file delivery)

- Check `isError` before JSON parsing; for `delivery: "file"`, inspect `result_file` (`bytes`/`sha256`) locally via bounded reads without repeating write RPCs or dumping raw JSON into chat.
