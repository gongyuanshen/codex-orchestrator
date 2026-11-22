---
name: teamwork
description: "Structured multi-agent engineering workflow ($teamwork) with optional [skip-interview], requirement clarification, architecture planning with mutually exclusive file ownership, isolated parallel coders, two-stage verification, same-thread rework, and safe sequential merge. Use for medium-to-large features or refactors needing strict spec freeze and file ownership separation."
metadata:
  short-description: "Spec freeze -> Task Packet -> Parallel isolation -> 2-stage review"
---

# `$teamwork` — Structured Multi-Agent Engineering Workflow

Run a disciplined end-to-end delivery flow backed by `tri-orchestrator` SQLite Task Packets, isolated `.swarm-workspaces` clones, and a two-stage verification gate. Live state syncs to `http://127.0.0.1:18790`.

## Shared Route A Contract (Required)

Before running `workflow5` (`0.6.8/schema10` default) or resuming stored `workflow0..4` runs (which branch strictly by `board_status.workflow.workflow_version` without fabricated historical evidence; `workflow1..4` bootstrap children stay waiting-only), load [the shared workflow contract](../route-a-workflow.md):

- **Primary `workflow5` macro path**: Bind root `session_id` on `run_create` (and optionally `workflow_prepare_dispatch`; inspect `cli_executables`). For each author slice, run `workflow_prepare_dispatch` (`task_name` `/root/<task_name>`, optional `create_workspace=True`) -> 1-turn direct `spawn_agent` (**zero waiting-only bootstrap empty turns**) -> `native_action_record_call`. If `begin` hits `needs_reconciliation`, call `list_agents(path_prefix="/root/<task_name>")` first and pass observed state to `reconcile` (never blindly re-spawn). Authors use `workflow_author_turn(phase="accept"|"submit")`; register `verifier` logically, spawn it directly on first candidate review, freeze `original_bindings`, and reuse it via `followup_task`. Verifiers use `workflow_verifier_turn(phase="prepare")` (`env_profile="windows_minimal_py"|"posix_minimal_py"`), run the returned `verify_argv_template` in the verifier terminal (plus optional `tri-orchestrator-challenge`), and call `workflow_verifier_turn(phase="record")` (`verification_record`, `inbox_ack`, `#verify` note). Finish after all milestones and integrated gates pass in **1 MCP call** via `workflow_closeout_run`.
- **Admission, review & milestone invariants**: Interactive Teamwork defaults to `execution_kind="native"` (`coordinator_local` is Boost-without-workspace only; executed local revisions require `local_revision_boundary`). `plan_readiness` and `resume_context` are read-only hints; enforce mutually exclusive parallel ownership, predecessor integrated PASS + `handoff_digest` ACK + native quiescence on sequential same-file milestones, and grouped transfers via `native_action_update` (`claim_group` -> `begin_group` -> `record_group` / `reconcile_group`). High-complexity steps map read-only challenger findings via `workflow_review`, require `review_dimensions` (`specification/code/stress/evidence`), resolve repairs via `resolved_finding_ids`, and allow original-verifier PASS `dismissed_finding_ids` only for unsubstantiated items. Preserve original author, original verifier, `worker_control_ack` separation, and at most 3 automatic rework rounds (`needs_decision` thereafter).

## Milestones & Coordinator Handoff

Use one `kind="milestone"` work step per milestone with explicit criteria/dependencies, retaining candidate and integrated verification on the milestone's existing attempt counter (never a nested retry loop). Confirm predecessor integrated PASS and confirmed handoff (`action="handoff"` with `recipient_agent_id` set to the intended recipient's native binding -> `action="ack_handoff"` with `handoff_digest`; single-root `$teamwork` sessions without a second coordinator may pass `handoff_required=False` at step creation) before dispatching dependent milestones; root keeps native child lifecycle ownership.

## Non-Negotiable Codex Subagent Rules

- **Native model selection**: On `spawn_agent`, omit `model` and let Codex role and session settings determine the model. Always omit both `model` and `reasoning_effort` with `fork_turns="all"`. Pass `reasoning_effort` only with `fork_turns="none"` and only if the host schema and model support the requested tier; choose it for the task or omit it.
- **Role resolution**: Only pass a custom `agent_type` when the live `spawn_agent` schema accepts it and the matching role is installed in the active Codex home's `agents/` directory (`CODEX_HOME` if set, otherwise `~/.codex`). If not, omit `agent_type` and inline the corresponding **Inline Fallback Role Contracts** entry. Always pass the logical role to `worker_register` / `workflow_prepare_dispatch`.
- **Nuanced `fork_turns` Policy**: In Codex V2, custom `agent_type` applies regardless of `fork_turns`. MUST keep `fork_turns: "none"` to keep child context minimal for independent `verifier` and high-complexity counterexample `challenger` (unbiased blind review). ALLOW `fork_turns: "all"` when the coordinator has already loaded key context and the subagent (`coder`, `swarm_worker`, or scoped branch `investigator`) does not need context isolation, reusing warm KV cache instead of cold rescanning.
- **Controlled `max_depth = 2` Read-Only `investigator` Helpers**: `interviewer`, `investigator`, and `challenger` are strictly forbidden from spawning subagents. Depth-1 `planner`, `coder`, `swarm_worker`, and `verifier` MAY spawn pure read-only `investigator` helpers within active concurrency budget ONLY for cross-module call-chain lookup (helpers must never edit files, never register as author/verifier on `18790`, and never submit verification receipts; caller remains solely accountable).
- **Same-Thread Rework**: Resume the SAME `coder` thread with `followup_task` whenever Stage 1 or Stage 2 verification finds gaps. Never replace a coder to paper over defects.
- **Control-Aware Waiting (`<=30s`) & Parallel Direct MCP Calls**: Use `wait_agent(timeout_ms=30000)` max wait interval paired with `board_status(run_id=run_id, compact=True)` before and after waits so Web UI pause/stop (`control_requests`) responds within `<=30s`. Issue multi-workspace read-only checks (`workspace_status`, `workspace_diff`, `workspace_merge_preview`) as parallel direct MCP tool calls in the same assistant turn; invoke `collaboration.*` ONLY as top-level direct calls, and check `isError` on every write tool, forbidding automatic retries on write errors.

## Inline Fallback Role Contracts

Include the matching contract verbatim in `message` whenever omitting `agent_type`, along with the shared [Route A workflow](../route-a-workflow.md) rules (`workflow5` macro turns, `local_collector_v1` `env_keys` allowlist / `env_profile`, `execution_kind`, and `needs_reconciliation` discipline):

- **`interviewer` (Read-Only Requirement Clarifier)**: Strictly read-only — NEVER edit files or run mutating commands; preserve verbatim constraints, ask ONE focused high-leverage question per round (2–4 options + recommended default), output a Frozen Requirement & Acceptance Contract, and post via `board_post(channel="main", msg_type="info")`. Strictly forbidden from spawning subagents (including when acting as challenger).
- **`planner` (Architecture & File Ownership Matrix)**: Do NOT edit application code or tests; lock cross-module interfaces/schemas first, produce a mutually exclusive file ownership matrix (`worker_name -> [owned_paths]` with zero writer overlap + `read_only_shared_paths`), ordered waves/merge sequence, and verifiable acceptance criteria, persisting via `task_packet_save`. Depth-1 role: may spawn pure read-only `investigator` helpers within active concurrency budget for cross-module call-chain lookup ONLY (helpers never edit files, never register as author/verifier on `18790`, and never submit verification receipts; caller remains solely accountable).
- **`investigator` (Read-Only Explorer)**: Strictly read-only — NEVER edit files or run mutating git commands; trace code paths empirically, cite exact `file:line` and observed command outputs, and report Root Cause, Evidence, Blast Radius, and Recommended Fix Surface. Strictly forbidden from spawning subagents (including when acting as challenger).
- **`coder` (Scoped Implementer & Tests)**: Work EXCLUSIVELY inside your assigned isolated workspace path and edit ONLY your owned files from `task_packet_get`; call `worker_heartbeat` on long phases, write real tests, run tests AFTER your final edit, commit clean changes on your assigned workspace branch only for `git_clone`, and for snapshot workspaces report the reviewed diff without a Git commit; use `workflow_author_turn(phase="accept"|"submit")`. Depth-1 role: may spawn pure read-only `investigator` helpers within active concurrency budget for cross-module call-chain lookup ONLY (helpers never edit files, never register as author/verifier on `18790`, and never submit verification receipts; caller remains solely accountable).
- **`verifier` (Independent Two-Stage Gate)**: NEVER edit or commit source/test files; inspect real `workspace_diff` / `git diff`, `task_packet_get`, run `workflow_verifier_turn(phase="prepare", env_profile=...)`, execute Stage 1 (Spec Compliance) and Stage 2 (Code Quality & Real Tests via `tri-orchestrator-verify` using the returned `verify_argv_template`), record via `workflow_verifier_turn(phase="record")` (`verification_record` with `receipt_files` and `review_dimensions`, `inbox_ack`, `board_post(channel="#verify", msg_type="test")`), and output `VERDICT: PASS` or `VERDICT: FAIL` with `[P0]`/`[P1]`/`[P2]` `file:line` defects. Depth-1 role: may spawn pure read-only `investigator` helpers within active concurrency budget for cross-module call-chain lookup ONLY (helpers never edit files, never register as author/verifier on `18790`, and never submit verification receipts; caller remains solely accountable).

## Native Agent Binding, State & Human Control

- In `workflow5`, `workflow_prepare_dispatch(task_name=...)` registers `status="waiting"` (`pending_agent_path="/root/<task_name>"`) and `native_action_record_call` atomically binds `agent_id` and sets `status="working"` after `spawn_agent`. On manual/stored paths, call `worker_register` pre-spawn with `status="waiting"`, then re-call with `agent_id` and report `status="working"` only after dispatch.
- Inspect native `list_agents` / `wait_agent` for real host state (`working/waiting/stopped/killed`); never revive killed workers or auto-resume paused ones without explicit user instruction.
- Before dispatching/reworking, before and after `wait_agent(timeout_ms=30000)`, and before closure, read `board_status(run_id=run_id, compact=True)` and handle every pending `control_requests` entry within `<=30s`: interrupt the snapshotted `agent_id`, confirm idle/stopped state, and call `worker_control_ack(request_id=..., outcome="applied"|"failed", result=...)` (uncertain interruptions stay `needs_reconciliation`; do not bypass with `worker_status_update`).

## Workspace Mode & Dependency Contract

- Pass returned `mode`, `path`, `branch_name`, and `workspace_id` to writers and verifiers.
- For `mode="git_clone"` only: commit owned changes on the assigned branch, report actual commit SHA, and require a clean Git worktree before verification and merge.
- For `mode="snapshot"`: `branch_name=None` is normal and `dirty=true` before merge is expected. Do not require a Git commit or a clean Git worktree; verify `workspace_diff`, ownership, and post-edit tests (`workspace_merge_preview` / `workspace_merge(workspace_id, strategy="merge")` run a three-way snapshot comparison/apply).
- Use `dep_mode="none"`, `dep_mode="copy"`, or `dep_mode="fresh_venv_uv"` when dependency writes need isolation; `dep_mode="link_ro_cache"` provides shared writable dependencies and is not read-only or write-isolated.
- **Storage Transparency & Closeout Disclosure**: On non-Git `snapshot` roots, watch snapshot size (`TRI_ORCH_MAX_SNAPSHOT_MB` / `.triignore` exclusions) before dispatching isolated workspaces; at completion, execute `workflow_closeout_run` / `workspace_cleanup` and state in the final user report whether isolated workspaces were cleaned and whether any uncleaned/dirty leftovers remain.

## Workflow

### Step 0: Parse Flags & Initialize Run

1. Check `[skip-interview]` and active runs (`run_list(status="active", mode="teamwork")`), or initialize a `workflow5` run using the current root session (`session_id=$env:CODEX_THREAD_ID`, or hook-reported root ID):
   - `run_create(mode="teamwork", title="<concise project task>", project_root="<real_workspace_or_repo_root>", session_id=<root_session_id>, metadata={"skip_interview": <bool>})` (pass `execution_root` separately if needed; never register a slice as `project_name`).

### Step 1: Requirement Clarification (`interviewer`, unless `[skip-interview]`)

- If `[skip-interview]` is NOT set and requirements are ambiguous: register `worker_register(run_id=run_id, name="interviewer", role="interviewer", status="waiting", current_task="Clarify requirements")`, dispatch `spawn_agent(agent_type="interviewer", fork_turns="none", ...)` (or omit `agent_type` when the role is unavailable in the active host; omit `model`, choose `reasoning_effort` by task difficulty), clarify one question per round, and post the frozen contract via `board_post`.

### Step 2: Architecture & Mutually Exclusive File Ownership (`planner`)

1. Register `worker_register(run_id=run_id, name="planner", role="planner", status="waiting", current_task="Decompose architecture & file ownership")` and dispatch `spawn_agent(agent_type="planner", fork_turns="none", ...)` (or omit `agent_type` when the role is unavailable in the active host; omit `model`, choose `reasoning_effort` by task difficulty).
2. Persist locked interfaces, mutually exclusive `file_ownership`, verifiable acceptance criteria, and sequential merge order via `task_packet_save` and verify via `task_packet_get(run_id=run_id)`.

### Step 3: Isolated Workspaces & Parallel Implementation (`coder`)

1. **Primary `workflow5` 1-turn Direct Dispatch**: Register logical `verifier` (or per-slice `verifier_1`, `verifier_2`, ... with `verifier_agent_id=""`), then for each admitted parallel writer (`coder-1`, `coder-2`, ...) call `workflow_prepare_dispatch(run_id=run_id, create_workspace=True, task_name="coder_1", owner="coder-1", verifier="verifier", dispatch_action="dispatch", ...)` -> top-level 1-turn `spawn_agent(task_name="coder_1", ...)` (`fork_turns="all"` when context is warm, or `fork_turns="none"`; follow the native model selection rule above) -> `native_action_record_call(...)`. Coders accept/submit via `workflow_author_turn(phase="accept"|"submit")`. *(Stored `workflow1..4` fallback via `board_status.workflow.workflow_version`: prebound verifier + `workspace_create` + `worker_register(run_id=run_id, name="coder-1", role="coder", status="waiting", workspace_id=ws["id"])` + `workflow_step_advance(action="dispatch")` + `followup_task`.)*
2. Poll `board_status(run_id=run_id, compact=True)` / `board_read` around `wait_agent(timeout_ms=30000)`.

### Step 4: Two-Stage Independent Verification (`verifier`)

1. Issue read-only inspection (`workspace_status`, `workspace_diff`, `workspace_merge_preview`) across completed workspaces via parallel direct MCP tool calls.
2. On first candidate review in `workflow5`, spawn `verifier` directly in 1 turn via `workflow_prepare_dispatch(..., dispatch_action="request_verify", task_name="verifier_1")` -> `spawn_agent(fork_turns="none", ...)` -> `native_action_record_call`, permanently freezing `original_bindings`; on subsequent milestones/rework, reuse that exact `verifier` via `followup_task`.
3. `verifier` runs `workflow_verifier_turn(phase="prepare", scope="candidate", env_profile="windows_minimal_py"|"posix_minimal_py", ...)`, executes Stage 1 (Spec Compliance) and Stage 2 (Code Quality & Real Tests via `tri-orchestrator-verify` using the returned `verify_argv_template`, plus optional `tri-orchestrator-challenge` on high-complexity steps), and records via `workflow_verifier_turn(phase="record", ...)` (`verification_record` + `inbox_ack("completed")` + `board_post(channel="#verify", msg_type="test")`).

### Step 5: Same-Thread Rework Loop

- On `VERDICT: FAIL`, call `workflow_prepare_dispatch(..., dispatch_action="start_rework")`, resume the responsible `coder` via `followup_task`, and re-audit with the same frozen `verifier` via `followup_task` within at most 3 automatic rework rounds.

### Step 6: Safe Sequential Merge, Final Verification & Cleanup

For each admitted milestone, complete candidate verification and integration before admitting dependents:
1. Confirm candidate PASS, run `workspace_merge_preview(workspace_id)` (parallel direct read-only MCP calls across workspaces) and `workspace_merge(workspace_id, strategy="merge")` sequentially.
2. Run `workflow_verifier_turn(step_id=step["id"], phase="prepare", scope="integrated", env_profile=...)` (or `artifact_capture(..., scope="integrated", env_profile=...)`), verify main-directory tests, record `verification_record` via `workflow_verifier_turn(phase="record", scope="integrated", ...)`, and call `workflow_step_advance(action="complete")`.
3. Issue `action="handoff"` (`next_responsibility`, `forbidden_actions`, `decision_seqs`) and have the receiving coordinator confirm `action="ack_handoff"` with exact `handoff_digest`.
4. Only then admit the next dependent milestone (`workflow_prepare_dispatch` / `workflow_step_advance(action="dispatch")`), retaining all original author workspaces.

After all milestones and final integrated gates pass (reopening affected scopes with `action="reverify"` if needed), call `workflow_closeout_run(run_id=run_id)` to atomically finish step `complete`, `workspace_cleanup`, worker `stopped`, and `run_update(status="completed")` in **1 MCP call**.

### Large MCP results (explicit file delivery)

- Check `isError` before JSON parsing; for `delivery: "file"`, inspect `result_file` (`bytes`/`sha256`) locally via bounded reads without repeating write RPCs or dumping raw JSON into chat.
