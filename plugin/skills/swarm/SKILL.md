---
name: swarm
description: "Concurrent multi-worker swarm coordination ($swarm) using tri-orchestrator SQLite #seq message board, domain channels (main/#backend/#ui/#verify), isolated .swarm-workspaces clones, anti-context-explosion short-pointer replies, incremental board_read(since_seq), and heartbeat stall recovery. Use for large multi-stream interactive parallel work with peer handoffs and live dashboard observability."
metadata:
  short-description: "Concurrent #seq board swarm with isolation & stall recovery"
---

## M6/M7/M8 workflow5 primary path (software 0.6.2/schema10 default)

New runs default to `workflow5` (`0.6.2/schema10`); stored runs follow `board_status.workflow.workflow_version` without fabricated historical evidence:

- **Coordinator 2-step Macro Dispatch & 1-turn Direct Spawn**: Bind the root session on `run_create`; reuse that `session_id` if also passing it to `workflow_prepare_dispatch` (`run_create` and `board_status(compact=True)` also expose `cli_executables` for `tri-orchestrator-verify` and `tri-orchestrator-challenge`). For each concurrent swarm slice, call `workflow_prepare_dispatch` with deterministic `task_name` (`^[a-z0-9_-]{1,48}$`, pre-freezing `/root/<task_name>`, optional `create_workspace=True`) -> issue 1-turn direct `spawn_agent(task_name="<task_name>", message="<full contract + workspace + step_id + message_id + #channel>")` with zero waiting-only bootstrap empty turns -> call `native_action_record_call(run_id=..., action_id=..., claim_token=..., expected_revision=..., outcome="confirmed"|"failed", raw_response=...)` (server writes receipt JSON, computes 64-hex SHA-256 `evidence_digest`, and atomically binds `agent_id` + `original_bindings`).
- **`spawn_agent` Reconciliation Fence**: If `begin(action_type="spawn_agent")` times out, disconnects, or restarts into `needs_reconciliation`, NEVER blindly re-call `spawn_agent`; first call `list_agents(path_prefix="/root/<task_name>")` and pass observed state to `native_action_update(action="reconcile", ...)` (`outcome="confirmed"` if present; `outcome="not_called"|"rejected", cancel=True` ONLY if proven `not_found` or explicitly rejected).
- **Author & Verifier Macro Turns, `env_profile`, Lazy `verifier` & 1-Call Closeout**: Swarm workers use `workflow_author_turn(run_id=..., step_id=..., message_id=..., phase="accept"|"submit", body_md=..., post_board_note=True)` (`accept` auto-sets `working` + heartbeat; `submit` atomically posts detailed `#verify` notes and resets `waiting` without duplicate manual `board_post`). Register `verifier` logically at step creation (`verifier_agent_id=""`, optional `verifier_task_name`), spawn it directly in 1 turn on first candidate `#verify` handoff, permanently freeze `original_bindings`, and reuse that exact `verifier` via `followup_task` for all subsequent slices, rework, and integrated gates. Verifiers call `workflow_verifier_turn(phase="prepare")` (atomically running `inbox_ack("accepted")` + `artifact_capture(..., env_profile="windows_minimal_py"|"posix_minimal_py")` and returning `verify_executable` / `verify_command_hint`), run real `tri-orchestrator-verify` in the host terminal, and call `workflow_verifier_turn(phase="record")` (atomically handling `verification_record` + `inbox_ack("completed")` + `waiting` + `#verify` note; manual callers must likewise run explicit `inbox_ack`). After all integrated gates pass, coordinators finish in **1 MCP call** via `workflow_closeout_run(run_id=run_id)`.
- **Compact Polling (`<=30s`) & Parallel Direct MCP Calls**: Poll `board_status(run_id=run_id, reader_name="coordinator", compact=True)` around `wait_agent(timeout_ms=30000)` so Web UI pause/stop (`control_requests`) responds within `<=30s`. Issue multi-workspace read-only checks (`workspace_status`, `workspace_diff`, `workspace_merge_preview`) as parallel direct MCP tool calls in the same assistant turn before sequential merge; invoke `collaboration.*` ONLY as top-level direct calls, and check `isError` on every write tool and abort immediately without automatic retries.

## M3/M4/M5 workflow4 (inherited baseline; superseded as default by workflow5)

M3/M4/M5 baseline (`software 0.5.0/schema7`, where New runs use workflow4) is inherited by `workflow5`, while stored `workflow0/1/2/3` preserve legacy semantics via `board_status.workflow.workflow_version`:
- Read-only `plan_readiness` and `resume_context` are hints, not authorization; enforce mutually exclusive parallel ownership, predecessor integrated PASS + `handoff_digest` ACK + native quiescence on sequential overlap, and grouped transfers (`native_action_update` `claim_group` -> `begin_group` -> `record_group` / `reconcile_group` or `native_action_record_call(group_id=..., members=...)`).
- High-complexity steps map read-only challenger findings via `workflow_review` (or via host `tri-orchestrator-challenge`), require `review_dimensions` (`specification/code/stress/evidence`) on `verification_record`, resolve repairs via `resolved_finding_ids`, and allow original-verifier PASS `dismissed_finding_ids` only for unsubstantiated findings without consuming an attempt.
- Interactive Swarm defaults to `execution_kind="native"` (while Boost Solo with no workspace uses `coordinator_local`); revising executed local steps requires single-use `local_revision_boundary` (`content_digest`), consumed by `task_packet_save` and requiring a fresh proof before `workflow_step_save`.

## M2 workflow3 collector (inherited by workflow4/workflow5)

Stored `workflow3`..`workflow5` runs require the original verifier to freeze `verification_commands` in `artifact_capture` (`env_profile` or `env_keys` as the exact child environment allowlist from the verifier terminal—daemon validates only key syntax/uniqueness, list `SystemRoot/PATH/TEMP`, never serialize values), run `tri-orchestrator-verify --manifest ABS --output-dir NEW_ABS` in the host terminal, and pass real `receipt_files` to `verification_record` (`local_collector_v1`, separate candidate/integrated gates).

## M1 transfer protocol

All native transfers use the shared M1 ledger (`inbox_send` -> `board_status` -> `native_action_update` `claim` -> `begin` -> native call -> `record`, encapsulated by `workflow_prepare_dispatch` + `native_action_record_call` in `workflow5`; stored `workflow1..4` bootstrap children remain waiting-only; uncertain calls `reconcile` without auto-retry). Preserve original author, original verifier, and at most 3 automatic rework rounds; inbox ACK, business PASS, and control ACK (`worker_control_ack`) remain separate.

## Route A structured workflow (Required)

Before running `workflow5` or resuming `workflow_version=1/2/3/4/5`, read [the shared workflow contract](../route-a-workflow.md) and branch legacy behavior strictly by `board_status.workflow.workflow_version`. Following the first failed delivery, use at most 3 automatic rework rounds; then `needs_decision` stops further automatic work.


# `$swarm` — `#seq` Message Board & Concurrent Isolation Swarm

Coordinate multiple parallel workers across isolated `.swarm-workspaces` clones using the SQLite WAL `#seq` message board, channel routing, anti-context-explosion short pointers, and watchdog stall recovery.


## M5 grouped transfer and global stall contract

For `workflow4` and `workflow5`, inspect `board_status.next_action_groups` (`compact=True`). Handle controls first; no notice-only wakeups. Claim a shared recipient/binding/action_type batch via `native_action_update(action="claim_group", run_id=..., recipient=..., action_type=..., members=[{source_type,source_id,source_digest},...])` (`group_id` + individual ledger rows), call `action="begin_group"` with full `action_id`/`expected_revision`/`claim_token`, invoke one native wake, and record via `native_action_record_call(group_id=..., members=...)` or `action="record_group"`. Passing complete group membership is mandatory; one expired member fences all into `native_action_groups` (`frozen_member_ids`), resolved via `action="reconcile_group"` without replay.

Keep one in-flight business wake per recipient, per-message `inbox_ack`, new arrivals outside frozen membership, and one inbox_wait per worker turn (`default 20000ms / max 25000ms`). Classifier states (`ready/completed/inactive/normal_wait/dependency_wait/user_decision/needs_reconciliation/host_unknown/no_progress` plus planned `potential_candidates`) distinguish normal waiting, dependency not ready, user decision, `needs_reconciliation`, host state unknown, and waiting for coordinator from an unfinished task with no executable progress and an actionable blocker.

## Mode2 linked execution and bounded receive

Every task/rework/review/result uses `inbox_send(..., step_id=step["id"])` (or `workflow_prepare_dispatch`) bound to the step's owner/verifier and attempt. Make one `inbox_wait` per worker turn (`default 20000ms`, `max 25000ms`), and check real `list_agents`/`wait_agent` state before waking idle children—a heartbeat timeout is not evidence of death.

## Non-Negotiable Codex Subagent Rules

- **Dynamic Model & Reasoning Effort Inheritance**: NEVER pass a hardcoded `model` or `reasoning_effort` parameter to `spawn_agent` (unless the user explicitly asks for an override). Omit both `model` and `reasoning_effort` so 100% of workers inherit your active Codex session model and reasoning effort tier.
- **Role Resolution & Zero-Script `agent_type` Omission**: Prefer specialized `agent_type` (`swarm_worker`, `verifier`, `planner`, `investigator`) when installed in `~/.codex/agents/<role>.toml` (which also omits `model` and `model_reasoning_effort` to inherit session defaults). When `install.ps1` / `install.sh` has not installed custom agent TOMLs, **omit `agent_type` entirely** (do NOT pass a fallback `"default"` role name, because `codex.exe` omits `agent_type` from `spawn_agent`'s `additionalProperties: false` schema when `~/.codex/agents/` is empty), inline the matching contract from **Inline Fallback Role Contracts**, and omit `model` and `reasoning_effort` so the subagent inherits your session model and reasoning effort tier.
  Always pass the logical role (`swarm_worker`, `verifier`, `planner`, `investigator`) to `worker_register` / `workflow_prepare_dispatch`.
- **Nuanced `fork_turns` Policy**:
  - In Codex V2, custom `agent_type` applies regardless of `fork_turns`. MUST keep `fork_turns: "none"` to keep child context minimal for independent `verifier` and high-complexity counterexample `challenger` (unbiased blind review).
  - ALLOW `fork_turns: "all"` when the coordinator has already loaded key context and the subagent (`swarm_worker`, `coder`, or scoped branch `investigator`) does not need context isolation, reusing warm KV cache instead of cold rescanning.
- **Controlled `max_depth = 2` Read-Only `investigator` Helpers**:
  - `interviewer`, `investigator`, and `challenger` are strictly forbidden from spawning subagents.
  - Depth-1 `planner`, `coder`, `swarm_worker`, and `verifier` MAY spawn pure read-only `investigator` helpers within active concurrency budget ONLY for cross-module call-chain lookup (helpers must never edit files, never register as author/verifier on `18790`, and never submit verification receipts; caller remains solely accountable).
- **Same-Thread Wake & Rework**: Use `followup_task` (V2) or `send_input` (V1) to wake stalled workers, route new `#seq` handoffs, or return failed reviews to the same thread.
- **Control-Aware Waiting (`<=30s`) & Parallel Direct MCP Calls**: Use `wait_agent(timeout_ms=30000)` max wait interval paired with `board_status(run_id=run_id, reader_name="coordinator", compact=True)` before and after waits so Web UI pause/stop (`control_requests`) responds within `<=30s`. Issue multi-workspace read-only checks (`workspace_status`, `workspace_diff`, `workspace_merge_preview`) as parallel direct MCP tool calls in the same assistant turn; keep `collaboration.*` as top-level direct calls and check `isError` on every write tool, forbidding automatic retries on write errors.

## Inline Fallback Role Contracts (when `~/.codex/agents/` is absent)

Include the matching contract verbatim in the child prompt whenever omitting `agent_type` (because `~/.codex/agents/` is absent):

- **`planner` (Architecture & File Ownership Matrix)**: Do NOT edit application code or tests; lock cross-module interfaces/schemas first, produce a mutually exclusive file ownership matrix (`worker_name -> [owned_paths]` with zero concurrent writer overlap + `read_only_shared_paths`) and ordered merge sequence, persisting via `task_packet_save`. Depth-1 role: may spawn pure read-only `investigator` helpers within active concurrency budget for cross-module call-chain lookup ONLY (helpers never edit files, never register as author/verifier on `18790`, and never submit verification receipts; caller remains solely accountable). Apply the shared M1 transfer protocol, M2 workflow3 collector contract (`env_keys allowlist`), and shared M3/M4/M5 workflow4/workflow5 contract (`execution_kind` and single-use `local_revision_boundary`).
- **`investigator` (Read-Only Explorer)**: Strictly read-only — NEVER edit files or run mutating git commands; trace code paths empirically, cite exact `file:line` and observed command outputs, and report Root Cause, Evidence, Blast Radius, and Recommended Fix Surface. Strictly forbidden from spawning subagents (including when acting as challenger). Apply the shared M1 transfer protocol, M2 workflow3 collector contract (`env_keys allowlist`), and shared M3/M4/M5 workflow4/workflow5 contract (`execution_kind` and single-use `local_revision_boundary`).
- **`swarm_worker` (Isolated `#seq` Worker)**: Operate EXCLUSIVELY inside `.swarm-workspaces/<run_short>-<worker>` and your owned files in `task_packet_get`; read unread `#seq` posts via `board_read(advance_cursor=True)`, commit clean changes on the assigned workspace branch only for `git_clone`, and for snapshot workspaces report the reviewed diff without a Git commit; accept via `workflow_author_turn(phase="accept")` (auto-setting `working` + heartbeat; call `worker_heartbeat` on long phases) and submit via `workflow_author_turn(phase="submit", body_md=..., post_board_note=True)` (atomically posting the detailed `#verify` board note and resetting status to `waiting`—use standalone `board_post` + `worker_status_update(status="waiting")` only for blockers or non-step notices), and reply to coordinator with ONLY 1 line: `已完成：<任务>，详见 #<seq> (@verifier)` (or `阻塞：<原因>，详见 #<seq> (@coordinator)`). Depth-1 role: may spawn pure read-only `investigator` helpers within active concurrency budget for cross-module call-chain lookup ONLY (helpers never edit files, never register as author/verifier on `18790`, and never submit verification receipts; caller remains solely accountable). Apply the shared M1 transfer protocol, M2 workflow3 collector contract (`env_keys allowlist`), and shared M3/M4/M5 workflow4/workflow5 contract (`execution_kind` and single-use `local_revision_boundary`).
- **`verifier` (Independent Two-Stage Gate)**: NEVER edit or commit source/test files; inspect real `workspace_diff`, `task_packet_get`, referenced `#seq` posts, run `workflow_verifier_turn(phase="prepare")` (atomically running `inbox_ack("accepted")` + `artifact_capture(env_profile=...)` and returning `verify_executable` / `verify_command_hint`), execute real verification commands via `tri-orchestrator-verify`, audit Stage 1 (Spec Compliance) and Stage 2 (Code Quality & Real Tests), record via `workflow_verifier_turn(phase="record")` (`verification_record` + `inbox_ack("completed")` + `board_post(channel="#verify", msg_type="test")`), and output `VERDICT: PASS` or `VERDICT: FAIL` with `[P0]`/`[P1]`/`[P2]` `file:line` findings. Depth-1 role: may spawn pure read-only `investigator` helpers within active concurrency budget for cross-module call-chain lookup ONLY (helpers never edit files, never register as author/verifier on `18790`, and never submit verification receipts; caller remains solely accountable). Apply the shared M1 transfer protocol, M2 workflow3 collector contract (`env_keys allowlist`), and shared M3/M4/M5 workflow4/workflow5 contract (`execution_kind` and single-use `local_revision_boundary`).


## Native Agent Binding, State and Human Control (Required)

- In `workflow5`, `workflow_prepare_dispatch(task_name=...)` registers `status="waiting"` (`pending_agent_path="/root/<task_name>"`) and `native_action_record_call` atomically binds `agent_id` and sets `status="working"` after `spawn_agent`. On manual/stored paths, call `worker_register` pre-spawn with `status="waiting"`, then re-call with returned `agent_id` and report `status="working"` only after actual dispatch.
- Inspect native `list_agents` / `wait_agent` for real host state (`working/waiting/stopped/killed`); never revive killed workers or auto-resume paused ones without explicit user instruction.
- Before dispatching/reworking, before and after `wait_agent(timeout_ms=30000)`, and before closure, read `board_status(run_id=run_id, reader_name="coordinator", compact=True)` and handle every pending `control_requests` entry within `<=30s`.
- Interrupt the exact snapshotted `agent_id` with `interrupt_agent`, confirm idle/stopped state, and call `worker_control_ack(request_id=..., outcome="applied"|"failed", result=...)`; uncertain interruptions stay `needs_reconciliation`. Do not bypass MCP or pending acknowledgements with `worker_status_update`.

## Workspace Mode & Dependency Contract

- Pass returned `mode`, `path`, `branch_name`, and `workspace_id` to writers and verifiers.
- For `mode="git_clone"` only: commit owned changes on the assigned branch, report actual commit SHA, and require a clean Git worktree before verification and merge.
- For `mode="snapshot"`: `branch_name=None` is normal and `dirty=true` before merge is expected. Do not require a Git commit or a clean Git worktree; verify `workspace_diff`, ownership, and post-edit tests (`workspace_merge_preview` / `workspace_merge(workspace_id, strategy="merge")` run a three-way snapshot comparison/apply).
- Use `dep_mode="none"`, `dep_mode="copy"`, or `dep_mode="fresh_venv_uv"` when dependency writes need isolation; `dep_mode="link_ro_cache"` provides shared writable dependencies and is not read-only or write-isolated.
- **Storage Transparency & Closeout Disclosure**: On non-Git `snapshot` roots, watch snapshot size (`TRI_ORCH_MAX_SNAPSHOT_MB` / `.triignore` exclusions) before dispatching isolated workspaces; at completion, execute `workflow_closeout_run` / `workspace_cleanup` and state in the final user report whether isolated workspaces were cleaned and whether any uncleaned/dirty leftovers remain.

## Workflow

### 1. Initialize Swarm Run, Channels & Task Packet

1. Check active runs (`run_list(status="active", mode="swarm")`) or create a `workflow5` run. Always use the current root session (`session_id=$env:CODEX_THREAD_ID`; use the hook-reported root ID if needed).
   - `run_create(mode="swarm", title="<swarm mission>", project_root="<real_workspace_or_repo_root>", session_id=<root_session_id>)`
   - Pass a staging/slice subdirectory as `execution_root` when used; never use it as the root or as a new `project_name`.
2. Optionally dispatch `planner` (`fork_turns="none"`, omitting `model` and `reasoning_effort` to inherit session defaults) for non-trivial decomposition, freeze `task_packet_save`, and initialize channels via `board_post` (`main`, `#backend`, `#ui`, `#verify`; inspect via `board_channel_list`).

### 2. Provision Isolated Workspaces & Register Workers

1. **Primary `workflow5` 1-turn Direct Dispatch**: Register logical `verifier` (`verifier_agent_id=""`), then for each concurrent author (`backend-1`, `ui-1`, ...) call `workflow_prepare_dispatch(run_id=run_id, create_workspace=True, task_name="backend_1", owner="backend-1", verifier="verifier", dispatch_action="dispatch", ...)` -> top-level 1-turn `spawn_agent(task_name="backend_1", ...)` (`fork_turns="all"` when context is warm, or `fork_turns="none"`, omit `model` and `reasoning_effort`) -> `native_action_record_call(...)`. Workers accept/submit via `workflow_author_turn(phase="accept"|"submit")`. (Stored `workflow1..4` fallback via `board_status.workflow.workflow_version`: spawn a bootstrap-only prebound verifier, run `workspace_create` + `worker_register(run_id=run_id, name="backend-1", role="swarm_worker", status="waiting", workspace_id=ws["id"], stall_threshold_sec=300)`, call `workflow_step_advance(action="dispatch")`, and deliver briefs via `followup_task`.)

### 3. Anti-Context-Explosion Discipline (CRITICAL)

- Workers NEVER dump diffs, full logs, or long prose into their reply to the coordinator.
- Step-bound workers publish detailed reports directly via `workflow_author_turn(run_id=run_id, step_id=..., message_id=..., phase="submit", summary="...", body_md="...", post_board_note=True)` (or standalone `board_post(run_id=run_id, sender="<worker_name>", sender_role="swarm_worker", channel="#backend", msg_type="diff"|"test"|"handoff", summary="...", body_md="...", mentions=["verifier"])` for peer handoffs/blockers—never calling both on the same step submission) and reply to the coordinator with **ONLY a 1-line `#seq` pointer**: `已完成：<任务>，详见 #14 (@verifier)` (or `阻塞：<原因>，详见 #15 (@coordinator)`).

### 4. Incremental `#seq` Loop, Heartbeat & Stall Recovery

1. Between `wait_agent(timeout_ms=30000)` stretches, inspect `board_status(run_id=run_id, reader_name="coordinator", compact=True)` and incremental `board_read(run_id=run_id, since_seq=last_seq, reader_name="coordinator", advance_cursor=True)` (handling `channel="human"` / `msg_type="human"` operator guidance and watchdog alerts).
2. **Stall & Silent-Exit Recovery**:
   - Inspect `workspace_status(workspace_id)`, `workspace_diff(workspace_id)`, and `board_read`.
   - Obtain an actual native observation via `list_agents` / `wait_agent` (heartbeat expiry is not death) and honor pending controls/stopped status.
   - Wake an observed idle/ended original worker on the SAME thread via `followup_task` / `send_input` (or `send_message` if running); never replace its identity or reset its rework budget.

### 5. Cross-Verification, Sequential Merge & Cleanup

1. On first `#verify` handoff in `workflow5`, spawn `verifier` directly in 1 turn via `workflow_prepare_dispatch(..., dispatch_action="request_verify", task_name="verifier_1")` -> `spawn_agent(fork_turns="none", ...)` -> `native_action_record_call`, permanently freezing `original_bindings`; reuse that exact `verifier` via `followup_task` for all subsequent handoffs, rework, and integrated gates. `verifier` executes `workflow_verifier_turn(phase="prepare")` -> real `tri-orchestrator-verify` via `verify_executable` / `cli_executables` (plus optional `tri-orchestrator-challenge`) -> `workflow_verifier_turn(phase="record")` (`inbox_ack` + `verification_record` + `#verify` note).
2. On `VERDICT: FAIL`, route rework to the owning `swarm_worker` thread via `followup_task` / `send_input` until `VERDICT: PASS`.
3. Issue read-only pre-merge checks (`workspace_status`, `workspace_diff`, `workspace_merge_preview`) across verified workspaces as parallel direct MCP tool calls, then merge sequentially via `workspace_merge(workspace_id, strategy="merge")`.
4. Capture and verify `scope="integrated"` via `workflow_verifier_turn(phase="prepare"|"record", scope="integrated", env_profile=...)` (or `artifact_capture(env_profile=...)` + `verification_record`), then finish in **1 MCP call** via `workflow_closeout_run(run_id=run_id)` (atomically running `complete`, `workspace_cleanup(workspace_id)` on merged unchanged workspaces, marking workers `stopped`, and calling `run_update(run_id=run_id, status="completed")`).


### Large MCP results (explicit file delivery)

- Check `isError` before JSON parsing; for `delivery: "file"`, inspect `result_file` (`bytes`/`sha256`) locally via bounded reads without repeating write RPCs or dumping raw JSON into chat.
