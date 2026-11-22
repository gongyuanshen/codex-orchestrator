---
name: boost
description: "Adaptive single-goal execution workflow ($boost) with Solo vs Delegation routing, verbatim requirement passthrough, mandatory verifier + coordinator spot-checks, and same-thread rework loops. Use when executing a focused feature, fix, or refactor directly or with targeted subagents; do not use when full multi-role interview/planning ($teamwork) or multi-channel swarm coordination ($swarm) is requested."
metadata:
  short-description: "Adaptive Solo/Delegation execution with forced verification"
---

# `$boost` — Adaptive Routing & Forced Verification Loop

Execute a focused goal with minimal coordination overhead while guaranteeing zero requirement drift, independent verification, and zero unaddressed caveats. Live state syncs to the `tri-orchestrator` dashboard (`http://127.0.0.1:18790`).

## Shared Route A Contract (Required)

Before running `workflow5` (`0.6.10/schema10` default) or resuming stored `workflow0..4` runs (which branch strictly by `board_status.workflow.workflow_version` without fabricated historical evidence; `workflow1..4` bootstrap children stay waiting-only), load [the shared workflow contract](../route-a-workflow.md):

- **Primary `workflow5` macro path**: Bind root `session_id` on `run_create` (and optionally `workflow_prepare_dispatch`; inspect `cli_executables`). For delegated authors, run `workflow_prepare_dispatch` (`task_name` `/root/<task_name>`, optional `create_workspace=True`) -> 1-turn direct `spawn_agent` (**zero waiting-only bootstrap empty turns**) -> `native_action_record_call`. If `begin` hits `needs_reconciliation`, call `list_agents(path_prefix="/root/<task_name>")` first and pass observed state to `reconcile` (never blindly re-spawn). Authors use `workflow_author_turn(phase="accept"|"submit")`; register `verifier` logically, spawn it directly on first candidate review, freeze `original_bindings`, and reuse it via `followup_task`. Verifiers use `workflow_verifier_turn(phase="prepare")` (`env_profile="windows_minimal_py"|"posix_minimal_py"`), run the returned `verify_argv_template` in the verifier terminal (plus optional `tri-orchestrator-challenge`), and call `workflow_verifier_turn(phase="record")` (handling Boost `challenge`, `verification_record`, `inbox_ack`, and `#verify` note). Finish after integrated gates pass in **1 MCP call** via `workflow_closeout_run`.
- **Admission, review & revision invariants**: Interactive modes default to `execution_kind="native"`; only Boost without a workspace may freeze `execution_kind="coordinator_local"` before first dispatch (`coordinator_local_boundaries`), and revising executed local steps requires single-use `local_revision_boundary` (`content_digest`) before `task_packet_save` and a fresh boundary before `workflow_step_save`. `plan_readiness` and `resume_context` are read-only hints (sequential overlaps still require predecessor integrated PASS + `handoff_digest` ACK + native quiescence); grouped transfers use `native_action_update` (`claim_group` -> `begin_group` -> `record_group` / `reconcile_group`). High-complexity steps map read-only challenger findings via `workflow_review`, require `review_dimensions` (`specification/code/stress/evidence`), resolve repairs via `resolved_finding_ids`, and allow original-verifier PASS `dismissed_finding_ids` only for unsubstantiated items. Preserve original author, original verifier, `worker_control_ack` separation, and at most 3 automatic rework rounds (`attempt` 1..3, then `needs_decision`).

## Execution Stages

Execute → Independent challenge → Original-author repair → Final re-verification.
Persist these stages on the same workflow step. Capture before checks and bind challenge to that candidate `artifact_id`; Solo repairs belong to the coordinator, Delegation repairs belong to the original author child, and the same original verifier checks repaired candidates and integrated delivery within 3 automatic rework rounds (`attempt` 1..3, then `needs_decision`).

## Non-Negotiable Codex Subagent Rules

- **Native model selection**: On `spawn_agent`, omit `model` and let Codex role and session settings determine the model. Always omit both `model` and `reasoning_effort` with `fork_turns="all"`. Pass `reasoning_effort` only with `fork_turns="none"` and only if the host schema and model support the requested tier; choose it for the task or omit it.
- **Role resolution**: Only pass a custom `agent_type` when the live `spawn_agent` schema accepts it and the matching role is installed in the active Codex home's `agents/` directory (`CODEX_HOME` if set, otherwise `~/.codex`). If not, omit `agent_type` and inline the corresponding **Inline Fallback Role Contracts** entry. Always pass the logical role to `worker_register` / `workflow_prepare_dispatch`.
- **Nuanced `fork_turns` Policy**: In Codex V2, custom `agent_type` applies regardless of `fork_turns`. MUST keep `fork_turns: "none"` to keep child context minimal for independent `verifier` and high-complexity counterexample `challenger` (unbiased blind review). ALLOW `fork_turns: "all"` when the coordinator has already loaded key context and the subagent (`coder`, `swarm_worker`, or scoped branch `investigator`) does not need context isolation, reusing warm KV cache instead of cold rescanning.
- **Controlled `max_depth = 2` Read-Only `investigator` Helpers**: `interviewer`, `investigator`, and `challenger` are strictly forbidden from spawning subagents. Depth-1 `planner`, `coder`, `swarm_worker`, and `verifier` MAY spawn pure read-only `investigator` helpers within active concurrency budget ONLY for cross-module call-chain lookup (helpers must never edit files, never register as author/verifier on `18790`, and never submit verification receipts; caller remains solely accountable).
- **Same-Thread Rework**: Resume the SAME child thread using `followup_task`. Never spawn a fresh replacement coder to cover up defects.
- **Control-Aware Waiting (`<=30s`) & Parallel Direct MCP Calls**: Use `wait_agent(timeout_ms=30000)` max wait interval paired with `board_status(run_id=run_id, compact=True)` and `board_read` before and after waits so Web UI pause/stop (`control_requests`) responds within `<=30s`. Issue independent read-only checks (`workspace_status`, `workspace_diff`, `workspace_merge_preview`) as parallel direct MCP tool calls in the same assistant turn; invoke `collaboration.*` ONLY as top-level direct calls, and check `isError` on every write tool, forbidding automatic retries on write errors.

## Inline Fallback Role Contracts

Include the matching contract verbatim in `message` whenever omitting `agent_type`, along with the shared [Route A workflow](../route-a-workflow.md) rules (`workflow5` macro turns, `local_collector_v1` `env_keys` allowlist / `env_profile`, `execution_kind`, and `needs_reconciliation` discipline):

- **`investigator` (Read-Only Root Cause Analyst)**: Strictly read-only — NEVER edit, create, or delete files or run mutating git commands; trace execution paths, separate observed facts from hypotheses, cite exact `file:line` and observed command outputs, and report Root Cause, Evidence, Blast Radius, and Recommended Fix Surface. Strictly forbidden from spawning subagents (including when acting as challenger).
- **`coder` (Scoped Implementer & Tests)**: Operate EXCLUSIVELY inside your assigned isolated workspace path (if provided) and edit ONLY your assigned `file_ownership` files; write real tests, run tests AFTER your final edit, commit clean changes on the assigned workspace branch only for `git_clone`, and for snapshot workspaces report the reviewed diff without a Git commit; use `workflow_author_turn(phase="accept"|"submit")`. Depth-1 role: may spawn pure read-only `investigator` helpers within active concurrency budget for cross-module call-chain lookup ONLY (helpers never edit files, never register as author/verifier on `18790`, and never submit verification receipts; caller remains solely accountable).
- **`verifier` (Independent Two-Stage Gate)**: NEVER edit, patch, or commit source or test files; inspect real `git diff` / `workspace_diff`, `task_packet_get`, run `workflow_verifier_turn(phase="prepare", env_profile=...)`, execute `tri-orchestrator-verify` using the returned `verify_argv_template`, audit Stage 1 (Spec Compliance) and Stage 2 (Code Quality & Real Tests), record via `workflow_verifier_turn(phase="record")` (`challenge`, `verification_record` with `receipt_files` and `review_dimensions`, `inbox_ack`, `#verify` note), and output `VERDICT: PASS` or `VERDICT: FAIL` with `[P0]`/`[P1]`/`[P2]` `file:line` findings. Depth-1 role: may spawn pure read-only `investigator` helpers within active concurrency budget for cross-module call-chain lookup ONLY (helpers never edit files, never register as author/verifier on `18790`, and never submit verification receipts; caller remains solely accountable).

## Native Agent Binding, State & Human Control

- In `workflow5`, `workflow_prepare_dispatch(task_name=...)` registers `status="waiting"` (`pending_agent_path="/root/<task_name>"`) and `native_action_record_call` atomically binds `agent_id` and sets `status="working"` after `spawn_agent`. On manual/stored paths, call `worker_register` pre-spawn with `status="waiting"`, then re-call with `agent_id` and report `status="working"` only after dispatch. Never invent IDs or overwrite bindings.
- Inspect native `list_agents` / `wait_agent` for real host state (`working/waiting/stopped/killed`); never revive killed workers or auto-resume paused ones without explicit user instruction.
- Before dispatching/reworking, before and after `wait_agent(timeout_ms=30000)`, and before closure, inspect `board_status(run_id=run_id, compact=True)` and handle every pending `control_requests` entry within `<=30s`: interrupt the snapshotted `agent_id`, confirm idle/stopped state, and call `worker_control_ack(request_id=..., outcome="applied"|"failed", result=...)` (uncertain interruptions stay `needs_reconciliation`; do not bypass with `worker_status_update` or raw SQL).
- **Human Message Consumption**: At control checkpoints — before dispatching/reworking, before and after waits, and before run closure — read `batch = board_read(run_id=run_id, reader_name="coordinator", advance_cursor=False)` without channel or mention filters (durable cursor). Handle every `channel="human"` or `msg_type="human"` post before further dispatch or closure, preserving verbatim text and `#seq` (the board does not automatically deliver text: Solo handles locally; Delegation routes via `send_message` when running or `followup_task` when idle). Record received `#seq`, planned action, and separate completion evidence via `board_post` (advancing the cursor is not completion; not exactly-once delivery). Only after recording the batch dispositions, call `board_read(run_id=run_id, reader_name="coordinator", since_seq=batch["since_seq"], limit=batch["count"], advance_cursor=True)`. Update the task packet first if guidance changes scope.

## Workspace Mode & Dependency Contract

- Pass returned `mode`, `path`, `branch_name`, and `workspace_id` to writers and verifiers.
- For `mode="git_clone"` only: commit owned changes on the assigned branch, report actual commit SHA, and require a clean Git worktree before verification and merge.
- For `mode="snapshot"`: `branch_name=None` is normal and `dirty=true` before merge is expected. Do not require a Git commit or a clean Git worktree; verify `workspace_diff`, ownership, and post-edit tests (`workspace_merge_preview` / `workspace_merge(workspace_id, strategy="merge")` run a three-way snapshot comparison/apply).
- Use `dep_mode="none"`, `dep_mode="copy"`, or `dep_mode="fresh_venv_uv"` when dependency writes need isolation; `dep_mode="link_ro_cache"` provides shared writable dependencies and is not read-only or write-isolated.
- **Storage Transparency & Closeout Disclosure**: On non-Git `snapshot` roots, watch snapshot size (`TRI_ORCH_MAX_SNAPSHOT_MB` / `.triignore` exclusions) before dispatching isolated workspaces; at completion, execute `workflow_closeout_run` / `workspace_cleanup` and state in the final user report whether isolated workspaces were cleaned and whether any uncleaned/dirty leftovers remain.

## Workflow

### 1. Register Run & Anchor Verbatim Requirements

1. Check active runs (`run_list(status="active", mode="boost")`) or create a new `workflow5` run using the current root session (`session_id=$env:CODEX_THREAD_ID`, or hook-reported root ID):
   - `run_create(mode="boost", title="<concise task title>", project_root="<real_workspace_or_repo_root>", session_id=<root_session_id>)` (pass `execution_root` separately if needed; never register a slice as `project_name`).
2. Freeze verbatim user requirements and checks via `task_packet_save(run_id=run_id, title="<task title>", goal="<VERBATIM user requirements>", file_ownership={"coordinator_or_coder": ["<target paths>"]}, acceptance_criteria=["<check 1>", "..."])` (or `task_packet_get` on resume).

### 2. Route Complexity: Solo vs Delegation

Choose the smallest execution shape from evidence, not a file-count score. Investigate only substantive unknowns via read-only `investigator` to establish actual callers, shared state, reproduction, and genuinely independent work; known local fixes add no extra author. Record `route_decision` via `board_post(run_id=run_id, sender="coordinator", sender_role="coordinator", channel="main", msg_type="info", summary="route_decision", body_md=...)` with packet version, Solo/delegated grounds, and ownership/dependency boundary.

- **Solo Path (`coordinator_local`)**: Register root as coder-role owner (`worker_register(..., status="waiting")`) and logical `verifier`, freeze `workflow_step_save(execution_kind="coordinator_local")` with no workspace, then admit via `workflow_step_advance(action="dispatch", agent_id=step["owner_agent_id"], evidence=completed_phase_evidence, expected_revision=step["revision"])` before Solo edits.
- **Delegation Path (`workflow5` 1-turn direct primary)**: Use `workflow_prepare_dispatch(run_id=run_id, create_workspace=True, task_name="coder_1", owner="coder-1", verifier="verifier-1", dispatch_action="dispatch", ...)` -> 1-turn direct `spawn_agent(task_name="coder_1", ...)` (`fork_turns="all"` when context is warm, or `fork_turns="none"`; follow the native model selection rule above) -> `native_action_record_call(...)`. Register `verifier-1` logically and spawn it directly on first candidate review (`fork_turns="none"`), then reuse that frozen `verifier-1` via `followup_task`. *(Stored `workflow1..4` fallback via `board_status.workflow.workflow_version`: prebound verifier + `worker_register(run_id=run_id, name="coder-1", role="coder", status="waiting")` + `workflow_step_advance(action="dispatch")` + `followup_task`.)*
- **Pure investigation — Solo report contract**: `investigator` stays read-only; the coordinator-owned report also uses `coordinator_local`, saving only the approved report scope for independent `verifier` candidate and integrated checks, with no invented code edits, workspace, or merge.

For local revisions after execution, enforce `local_revision_boundary → task_packet_save → fresh local_revision_boundary → workflow_step_save`. For difficult scope, use a clean-context read-only challenger (`fork_turns="none"`), judging routing efficiency by actual defects, unplanned human rescue, and empty waits rather than subagent count.

### 3. Mandatory Independent Verification & Coordinator Spot-Check

1. Issue independent read-only workspace checks (`workspace_status`, `workspace_diff`, `workspace_merge_preview`) as parallel direct MCP tool calls when workspaces are used.
2. On first candidate review in `workflow5`, instantiate `verifier-1` in 1 turn via `workflow_prepare_dispatch(..., dispatch_action="request_verify", task_name="verifier_1")` -> `spawn_agent(fork_turns="none", ...)` -> `native_action_record_call` (or reuse frozen `verifier-1` via `followup_task`), instructing it to run `workflow_verifier_turn(phase="prepare", scope="candidate", env_profile="windows_minimal_py"|"posix_minimal_py", ...)` -> real `tri-orchestrator-verify` using the returned `verify_argv_template` (plus optional `tri-orchestrator-challenge`) -> `workflow_verifier_turn(phase="record", ...)`.
3. Perform the Coordinator Spot-Check on real `git diff` / `workspace_diff` and post-edit test outputs, recording verification pointers via `board_post`.

### 4. Same-Thread Rework Loop (Zero Tolerance for Caveats)

For Solo `coordinator_local`, call `action="start_rework"` with `agent_id=step["owner_agent_id"]` and nonblank evidence in `coordinator_local_boundaries` before editing. On `VERDICT: FAIL` or any open gap:
- **Solo Path**: The coordinator fixes its own implementation and re-runs tests; no coder thread exists, so never spawn one for rework.
- **Delegation Path**: Dispatch rework (`workflow_prepare_dispatch(..., dispatch_action="start_rework")`) and resume the exact original `coder` thread via `followup_task` in its preserved workspace.
- Resume the same original `verifier` via `followup_task` within at most 3 automatic rework rounds until `VERDICT: PASS`.

### 5. Merge, Cleanup & Close

1. Require candidate PASS, inspect `workspace_merge_preview(workspace_id)` (parallel direct MCP calls across multiple workspaces), and run `workspace_merge(workspace_id, strategy="merge")` in order while retaining original author workspaces.
2. After all merges (or Solo edits), run `workflow_verifier_turn(step_id=step["id"], phase="prepare", scope="integrated", env_profile=...)` (or `artifact_capture(..., scope="integrated", env_profile=...)`), verify main-directory tests with the same original verifier, and record integrated PASS via `workflow_verifier_turn(phase="record", scope="integrated", ...)` (reopen invalidated scope via `action="reverify"`).
3. Only after all integrated gates pass, call `workflow_closeout_run(run_id=run_id)` to atomically complete steps (`workflow_step_advance(action="complete")`), run `workspace_cleanup`, mark workers `stopped`, and call `run_update(status="completed")` in **1 MCP call**.

### Large MCP results (explicit file delivery)

- Check `isError` before JSON parsing; for `delivery: "file"`, inspect `result_file` (`bytes`/`sha256`) locally via bounded reads without repeating write RPCs or dumping raw JSON into chat.
