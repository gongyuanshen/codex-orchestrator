---
name: boost
description: "Adaptive single-goal execution workflow ($boost) with Solo vs Delegation routing, verbatim requirement passthrough, mandatory verifier + coordinator spot-checks, and same-thread rework loops. Use when executing a focused feature, fix, or refactor directly or with targeted subagents; do not use when full multi-role interview/planning ($teamwork) or multi-channel swarm coordination ($swarm) is requested."
metadata:
  short-description: "Adaptive Solo/Delegation execution with forced verification"
---

## M6/M7/M8 workflow5 primary path (software 0.6.2/schema10 default)

New runs default to `workflow5` (`0.6.2/schema10`); stored runs follow `board_status.workflow.workflow_version` without fabricated historical evidence:

- **Coordinator 2-step Macro Dispatch & 1-turn Direct Spawn**: Bind the root session on `run_create`; reuse that `session_id` if also passing it to `workflow_prepare_dispatch` (`run_create` and `board_status(compact=True)` also expose `cli_executables` for `tri-orchestrator-verify` and `tri-orchestrator-challenge`). For delegated authors, call `workflow_prepare_dispatch` with deterministic `task_name` (`^[a-z0-9_-]{1,48}$`, pre-freezing `/root/<task_name>`, optional `create_workspace=True`) -> issue 1-turn direct `spawn_agent(task_name="<task_name>", message="<full contract + workspace + step_id + message_id>")` with zero waiting-only bootstrap empty turns -> call `native_action_record_call(run_id=..., action_id=..., claim_token=..., expected_revision=..., outcome="confirmed"|"failed", raw_response=...)` (server writes receipt JSON, computes 64-hex SHA-256 `evidence_digest`, and atomically binds `agent_id` + `original_bindings`).
- **`spawn_agent` Reconciliation Fence**: If `begin(action_type="spawn_agent")` times out, disconnects, or restarts into `needs_reconciliation`, NEVER blindly re-call `spawn_agent`; first call `list_agents(path_prefix="/root/<task_name>")` and pass observed state to `native_action_update(action="reconcile", ...)` (`outcome="confirmed"` if present; `outcome="not_called"|"rejected", cancel=True` ONLY if proven `not_found` or explicitly rejected).
- **Author & Verifier Macro Turns, `env_profile`, Lazy `verifier` & 1-Call Closeout**: Authors use `workflow_author_turn(run_id=..., step_id=..., message_id=..., phase="accept"|"submit", body_md=..., post_board_note=...)` (`accept` auto-sets `working` + heartbeat; `submit` auto-posts optional `#verify` details and resets `waiting`). Register `verifier` logically at step creation (`verifier_agent_id=""`, optional `verifier_task_name`), spawn it directly in 1 turn on first candidate review, permanently freeze `original_bindings`, and reuse that exact `verifier` via `followup_task` for rework and integrated checks. Verifiers call `workflow_verifier_turn(phase="prepare")` (atomically running `inbox_ack("accepted")` + `artifact_capture(..., env_profile="windows_minimal_py"|"posix_minimal_py")` and returning `verify_executable` / `verify_command_hint`), run real `tri-orchestrator-verify` in the host terminal, and call `workflow_verifier_turn(phase="record")` (atomically handling Boost `challenge` + `verification_record` + `inbox_ack("completed")` + `waiting` + `#verify` note; manual callers must likewise run explicit `inbox_ack` and `challenge`). After all integrated gates pass, coordinators finish in **1 MCP call** via `workflow_closeout_run(run_id=run_id)`.
- **Compact Polling (`<=30s`) & Parallel Direct MCP Calls**: Poll `board_status(run_id=run_id, compact=True)` around `wait_agent(timeout_ms=30000)` so Web UI pause/stop (`control_requests`) responds within `<=30s`. Issue independent read-only workspace checks (`workspace_status`, `workspace_diff`, `workspace_merge_preview`) as parallel direct MCP tool calls in the same assistant turn; invoke `collaboration.*` ONLY as top-level direct calls, and check `isError` on every write tool and abort immediately without automatic retries.

## M3/M4/M5 workflow4 (inherited baseline; superseded as default by workflow5)

M3/M4/M5 baseline (`software 0.5.0/schema7`, where New runs use workflow4) is inherited by `workflow5`, while stored `workflow0/1/2/3` preserve legacy semantics via `board_status.workflow.workflow_version`:
- Read-only `plan_readiness` and `resume_context` are hints, not authorization; enforce mutually exclusive parallel ownership, predecessor integrated PASS + `handoff_digest` ACK + native quiescence on sequential overlap, and grouped transfers (`native_action_update` `claim_group` -> `begin_group` -> `record_group` / `reconcile_group` or `native_action_record_call`).
- High-complexity steps map read-only challenger findings via `workflow_review` (or via host `tri-orchestrator-challenge`), require `review_dimensions` (`specification/code/stress/evidence`) on `verification_record`, resolve repairs via `resolved_finding_ids`, and allow original-verifier PASS `dismissed_finding_ids` only for unsubstantiated findings without consuming an attempt.
- Interactive modes use `execution_kind="native"` by default, while Boost Solo with no workspace freezes `coordinator_local` before first dispatch (`coordinator_local_boundaries`). Revising executed local steps requires single-use `local_revision_boundary` (`content_digest`); `task_packet_save` consumes it, requiring a fresh proof before `workflow_step_save`.

## M2 workflow3 collector (inherited by workflow4/workflow5)

Stored `workflow3`..`workflow5` runs require the original verifier to freeze `verification_commands` in `artifact_capture` (`env_profile` or `env_keys` as the exact child environment allowlist from the verifier terminal—daemon validates only key syntax/uniqueness, list `SystemRoot/PATH/TEMP`, never serialize values), run `tri-orchestrator-verify --manifest ABS --output-dir NEW_ABS` in the host terminal, and pass real `receipt_files` to `verification_record` (`local_collector_v1`, separate candidate/integrated gates).

## M1 transfer protocol

All native transfers use the shared M1 ledger (`inbox_send` -> `board_status` -> `native_action_update` `claim` -> `begin` -> native call -> `record`, encapsulated by `workflow_prepare_dispatch` + `native_action_record_call` in `workflow5`; stored `workflow1..4` bootstrap children remain waiting-only; uncertain calls `reconcile` without auto-retry). Preserve original author, original verifier, and at most 3 automatic rework rounds; inbox ACK, business PASS, and control ACK (`worker_control_ack`) remain separate.

## Route A structured workflow (Required)

Before running `workflow5` or resuming `workflow_version=1/2/3/4/5`, read [the shared workflow contract](../route-a-workflow.md) and branch legacy behavior strictly by `board_status.workflow.workflow_version`. Following the first failed delivery, use at most 3 automatic rework rounds; then `needs_decision` stops further automatic work.


# `$boost` — Adaptive Routing & Forced Verification Loop

Execute a focused goal with minimal coordination overhead while guaranteeing zero requirement drift, independent verification, and zero unaddressed caveats. Live state syncs to the `tri-orchestrator` dashboard (`http://127.0.0.1:18790`).


## Mode2 execution stages

Execute → Independent challenge → Original-author repair → Final re-verification.
Persist these stages on the same workflow step. Capture before checks and bind challenge to that candidate `artifact_id`; Solo repairs belong to the coordinator, Delegation repairs belong to the original author child, and the same original verifier checks repaired candidates and integrated delivery within 3 automatic rework rounds (`attempt` 1..3, then `needs_decision`).

## Non-Negotiable Codex Subagent Rules

- **Dynamic Model & Reasoning Effort Inheritance**: NEVER pass a hardcoded `model` or `reasoning_effort` parameter to `spawn_agent` (unless the user explicitly asks for an override). Omit both `model` and `reasoning_effort` so 100% of subagents inherit your active Codex session model and reasoning effort tier.
- **Role Resolution & Zero-Script `agent_type` Omission**: Prefer specialized `agent_type` (`investigator`, `coder`, `verifier`) when installed in `~/.codex/agents/<role>.toml` (which also omits `model` and `model_reasoning_effort` to inherit session defaults). When `install.ps1` / `install.sh` has not installed custom agent TOMLs, **omit `agent_type` entirely** (do NOT pass a fallback `"default"` role name, because `codex.exe` omits `agent_type` from `spawn_agent`'s `additionalProperties: false` schema when `~/.codex/agents/` is empty), inline the matching contract from **Inline Fallback Role Contracts**, and omit `model` and `reasoning_effort` so the subagent inherits your session model and reasoning effort tier.
  Always pass the logical role (`investigator`, `coder`, `verifier`) to `worker_register` / `workflow_prepare_dispatch`.
- **Nuanced `fork_turns` Policy**:
  - In Codex V2, custom `agent_type` applies regardless of `fork_turns`. MUST keep `fork_turns: "none"` to keep child context minimal for independent `verifier` and high-complexity counterexample `challenger` (unbiased blind review).
  - ALLOW `fork_turns: "all"` when the coordinator has already loaded key context and the subagent (`coder`, `swarm_worker`, or scoped branch `investigator`) does not need context isolation, reusing warm KV cache instead of cold rescanning.
- **Controlled `max_depth = 2` Read-Only `investigator` Helpers**:
  - `interviewer`, `investigator`, and `challenger` are strictly forbidden from spawning subagents.
  - Depth-1 `planner`, `coder`, `swarm_worker`, and `verifier` MAY spawn pure read-only `investigator` helpers within active concurrency budget ONLY for cross-module call-chain lookup (helpers must never edit files, never register as author/verifier on `18790`, and never submit verification receipts; caller remains solely accountable).
- **Same-Thread Rework**: Resume the SAME child thread using `followup_task` (Codex V2) or `send_input` (Codex V1). Never spawn a fresh replacement coder to cover up defects.
- **Control-Aware Waiting (`<=30s`) & Parallel Direct MCP Calls**: Use `wait_agent(timeout_ms=30000)` max wait interval paired with `board_status(run_id=run_id, compact=True)` and `board_read` before and after waits so Web UI pause/stop (`control_requests`) responds within `<=30s`. Issue independent read-only checks (`workspace_status`, `workspace_diff`, `workspace_merge_preview`) as parallel direct MCP tool calls in the same assistant turn; keep `collaboration.*` as top-level direct calls and check `isError` on every write tool, forbidding automatic retries on write errors.

## Inline Fallback Role Contracts (when `~/.codex/agents/` is absent)

Include the matching contract verbatim in the child prompt whenever omitting `agent_type` (because `~/.codex/agents/` is absent):

- **`investigator` (Read-Only Root Cause Analyst)**: Strictly read-only — NEVER edit, create, or delete files or run mutating git commands; trace execution paths, separate observed facts from hypotheses, cite exact `file:line` and observed command outputs, and report Root Cause, Evidence, Blast Radius, and Recommended Fix Surface. Strictly forbidden from spawning subagents (including when acting as challenger). Apply the shared M1 transfer protocol (`workflow5` 1-turn direct spawn or stored `workflow1..4` waiting-only bootstrap), the M2 workflow3 collector contract with explicit `env_keys allowlist` (`env_profile` or explicit `SystemRoot/PATH/TEMP`, verifier-terminal values only), and the shared M3/M4/M5 workflow4/workflow5 contract (`execution_kind`, `coordinator_local` Boost-only local work, single-use `local_revision_boundary`, and challenger findings routed without resetting the three-repair budget).
- **`coder` (Scoped Implementer & Tests)**: Operate EXCLUSIVELY inside your assigned isolated workspace path (if provided) and edit ONLY your assigned `file_ownership` files; write real tests, run tests AFTER your final edit, commit clean changes on the assigned workspace branch only for `git_clone`, and for snapshot workspaces report the reviewed diff without a Git commit. Depth-1 role: may spawn pure read-only `investigator` helpers within active concurrency budget for cross-module call-chain lookup ONLY (helpers never edit files, never register as author/verifier on `18790`, and never submit verification receipts; caller remains solely accountable). Use `workflow_author_turn(phase="accept"|"submit")` and apply the shared M1 transfer protocol, M2 workflow3 collector contract (`env_keys allowlist`), and shared M3/M4/M5 workflow4/workflow5 contract (`execution_kind` and single-use `local_revision_boundary`).
- **`verifier` (Independent Two-Stage Gate)**: NEVER edit, patch, or commit source or test files; inspect real `git diff` / `workspace_diff`, `task_packet_get`, run `workflow_verifier_turn(phase="prepare")` (atomically running `inbox_ack("accepted")` + `artifact_capture(env_profile=...)` and returning `verify_executable` / `verify_command_hint`), execute real verification commands via `tri-orchestrator-verify`, audit Stage 1 (Spec Compliance) and Stage 2 (Code Quality & Real Tests), record via `workflow_verifier_turn(phase="record")` (handling Boost `challenge`, `verification_record`, `inbox_ack("completed")`, and `#verify` note), and output `VERDICT: PASS` or `VERDICT: FAIL` with `[P0]`/`[P1]`/`[P2]` `file:line` findings. Depth-1 role: may spawn pure read-only `investigator` helpers within active concurrency budget for cross-module call-chain lookup ONLY (helpers never edit files, never register as author/verifier on `18790`, and never submit verification receipts; caller remains solely accountable). Apply the shared M1 transfer protocol, M2 workflow3 collector contract (`env_keys allowlist`), and shared M3/M4/M5 workflow4/workflow5 contract (`execution_kind` and single-use `local_revision_boundary`).


## Native Agent Binding, State and Human Control (Required)

- In `workflow5`, `workflow_prepare_dispatch(task_name=...)` registers `status="waiting"` (`pending_agent_path="/root/<task_name>"`) and `native_action_record_call` atomically binds `agent_id` and sets `status="working"` after `spawn_agent`. On manual/stored paths, call `worker_register` pre-spawn with `status="waiting"`, then re-call with returned `agent_id` and report `status="working"` only after actual dispatch. Never invent IDs or overwrite bindings.
- Inspect native `list_agents` / `wait_agent` for real host state (`working/waiting/stopped/killed`); never revive killed workers or auto-resume paused ones without explicit user instruction.
- Before dispatching/reworking, before and after `wait_agent(timeout_ms=30000)`, and before closure, read `board_status(run_id=run_id, compact=True)` and handle every pending `control_requests` entry within `<=30s`.
- Interrupt the exact snapshotted `agent_id` with `interrupt_agent`, confirm idle/stopped state, and call `worker_control_ack(request_id=..., outcome="applied"|"failed", result=...)`; uncertain interruptions stay `needs_reconciliation`. Do not bypass pending acknowledgements with `worker_status_update` or direct SQLite calls.

## Human Message Consumption (Required)

- At control checkpoints — before dispatching/reworking, before and after waits, and before run closure — read `batch = board_read(run_id=run_id, reader_name="coordinator", advance_cursor=False)` without channel or mention filters, preserving the durable cursor.
- Handle every `channel="human"` or `msg_type="human"` post before further dispatch or closure, preserving verbatim text and `#seq`. The board does not automatically deliver text to native agents: Solo handles locally; Delegation routes to the bound worker via `send_message` (running) or `followup_task` / `send_input` (idle), respecting paused/killed state.
- Record received `#seq`, planned action, and separate completion evidence via `board_post` (advancing the cursor is not completion; this is not exactly-once delivery). Only after recording the batch dispositions, call `board_read(run_id=run_id, reader_name="coordinator", since_seq=batch["since_seq"], limit=batch["count"], advance_cursor=True)`. Update the task packet first if guidance changes scope.

## Workspace Mode & Dependency Contract

- Pass returned `mode`, `path`, `branch_name`, and `workspace_id` to writers and verifiers.
- For `mode="git_clone"` only: commit owned changes on the assigned branch, report actual commit SHA, and require a clean Git worktree before verification and merge.
- For `mode="snapshot"`: `branch_name=None` is normal and `dirty=true` before merge is expected. Do not require a Git commit or a clean Git worktree; verify `workspace_diff`, ownership, and post-edit tests (`workspace_merge_preview` / `workspace_merge(workspace_id, strategy="merge")` run a three-way snapshot comparison/apply).
- Use `dep_mode="none"`, `dep_mode="copy"`, or `dep_mode="fresh_venv_uv"` when dependency writes need isolation; `dep_mode="link_ro_cache"` provides shared writable dependencies and is not read-only or write-isolated.
- **Storage Transparency & Closeout Disclosure**: On non-Git `snapshot` roots, watch snapshot size (`TRI_ORCH_MAX_SNAPSHOT_MB` / `.triignore` exclusions) before dispatching isolated workspaces; at completion, execute `workflow_closeout_run` / `workspace_cleanup` and state in the final user report whether isolated workspaces were cleaned and whether any uncleaned/dirty leftovers remain.

## Workflow

### 1. Register Run & Anchor Verbatim Requirements

1. Check active runs (`run_list(status="active", mode="boost")`) or create a new `workflow5` run. Always use the current root session (`session_id=$env:CODEX_THREAD_ID`; use the hook-reported root ID if needed).
   - `run_create(mode="boost", title="<concise task title>", project_root="<real_workspace_or_repo_root>", session_id=<root_session_id>)`
   - Pass a staging/slice subdirectory as `execution_root` when used; never use it as the root or as a new `project_name`.
2. Freeze verbatim user requirements and checks via `task_packet_save(run_id=run_id, title="<task title>", goal="<VERBATIM user requirements>", file_ownership={"coordinator_or_coder": ["<target paths>"]}, acceptance_criteria=["<check 1>", "..."])`, referencing `task_packet_get(run_id=run_id)` on resume.

### 2. Route Complexity: Solo vs Delegation

Choose the smallest execution shape from evidence, not a file-count score. Investigate only substantive unknowns via read-only `investigator` to establish actual callers, shared state, reproduction, and genuinely independent work; Known local fixes add no extra author. Record `route_decision` via `board_post(run_id=run_id, sender="coordinator", sender_role="coordinator", channel="main", msg_type="info", summary="route_decision", body_md=...)` with packet version, Solo/delegated grounds, and ownership/dependency boundary.

- **Solo Path (`coordinator_local`)**: Register root as coder-role owner (`worker_register(..., status="waiting")`) and logical `verifier`, freeze `workflow_step_save(execution_kind="coordinator_local")` with no workspace, then admit via `workflow_step_advance(action="dispatch", agent_id=step["owner_agent_id"], evidence=completed_phase_evidence, expected_revision=step["revision"])` before Solo edits.
- **Delegation Path (`workflow5` 1-turn direct primary)**: Use `workflow_prepare_dispatch(run_id=run_id, create_workspace=True, task_name="coder_1", owner="coder-1", verifier="verifier-1", dispatch_action="dispatch", ...)` -> 1-turn direct `spawn_agent(task_name="coder_1", ...)` (`fork_turns="all"` when context is warm, or `fork_turns="none"`, omit `model` and `reasoning_effort`) -> `native_action_record_call(...)`. Register `verifier-1` logically and spawn it directly on first candidate review (`fork_turns="none"`), then reuse that frozen `verifier-1` via `followup_task`. (Stored `workflow1..4` fallback via `board_status.workflow.workflow_version`: spawn a bootstrap-only prebound verifier and waiting-only `worker_register(run_id=run_id, name="coder-1", role="coder", status="waiting")`, call `workflow_step_advance(action="dispatch")`, then wake via `followup_task`.)
- **Pure investigation — Solo report contract**: investigator stays read-only; the coordinator-owned report also uses coordinator_local, saving only the approved report scope for independent verifier candidate and integrated checks, with no invented code edits, workspace or merge.

For local revisions after execution, enforce `local_revision_boundary → task_packet_save → fresh local_revision_boundary → workflow_step_save`. For difficult scope, use the M4 read-only challenger (`fork_turns="none"`) and benchmark against M0 using actual defects, unplanned human rescue, and empty waits (static instruction checks do not prove routing quality).

### 3. Mandatory Independent Verification & Coordinator Spot-Check

1. Issue independent read-only workspace checks (`workspace_status`, `workspace_diff`, `workspace_merge_preview`) as parallel direct MCP tool calls when workspaces are used.
2. On first candidate review in `workflow5`, instantiate `verifier-1` in 1 turn via `workflow_prepare_dispatch(..., dispatch_action="request_verify", task_name="verifier_1")` -> `spawn_agent(fork_turns="none", ...)` -> `native_action_record_call` (or resume the already-bound `verifier-1` via `followup_task`), instructing it to run `workflow_verifier_turn(phase="prepare", scope="candidate", env_profile="windows_minimal_py"|"posix_minimal_py", ...)` -> real `tri-orchestrator-verify` via `verify_executable` / `cli_executables` (and `tri-orchestrator-challenge` when adversarial counterexample probing is needed) -> `workflow_verifier_turn(phase="record", ...)` (atomically handling `inbox_ack`, Boost `challenge`, `verification_record`, and `#verify` note).
3. Perform the Coordinator Spot-Check on real `git diff` / `workspace_diff` and post-edit test outputs, recording verification pointers via `board_post`.

### 4. Same-Thread Rework Loop (Zero Tolerance for Caveats)

For Solo `coordinator_local`, call `action="start_rework"` with `agent_id=step["owner_agent_id"]` and nonblank evidence in `coordinator_local_boundaries` before editing. On `VERDICT: FAIL` or any open gap:
- **Solo Path**: The coordinator fixes its own implementation and re-runs tests; no coder thread exists, so never spawn one for rework.
- **Delegation Path**: Dispatch rework (`workflow_prepare_dispatch(..., dispatch_action="start_rework")`) and resume the exact original `coder` thread via `followup_task` / `send_input` in its preserved workspace.
- Resume the same original `verifier` via `followup_task` / `send_input` within at most 3 automatic rework rounds until `VERDICT: PASS`.

### 5. Merge, Cleanup & Close

1. Require candidate PASS, inspect `workspace_merge_preview(workspace_id)` (issuing independent read-only MCP calls in parallel across multiple workspaces), and run `workspace_merge(workspace_id, strategy="merge")` in order while retaining original author workspaces.
2. After all merges (or Solo edits), run `workflow_verifier_turn(step_id=step["id"], phase="prepare", scope="integrated", env_profile=...)` (or `artifact_capture(step_id=step["id"], scope="integrated", env_profile=...)`), verify main-directory tests with the same original verifier, and record integrated PASS via `workflow_verifier_turn(phase="record", scope="integrated", ...)` (reopen invalidated scope via `action="reverify"`).
3. Only after all integrated gates pass, call `workflow_closeout_run(run_id=run_id)` to atomically advance steps via `workflow_step_advance(action="complete")`, run `workspace_cleanup`, mark workers `stopped`, and call `run_update(status="completed")` in **1 MCP call**.


### Large MCP results (explicit file delivery)

- Check `isError` before JSON parsing; for `delivery: "file"`, inspect `result_file` (`bytes`/`sha256`) locally via bounded reads without repeating write RPCs or dumping raw JSON into chat.
