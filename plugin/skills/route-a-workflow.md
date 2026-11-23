# Route A shared contract (`workflow5`, `0.6.11/schema10`)

Boost, Teamwork and Swarm all follow this file (new runs: `workflow5`, `0.6.11/schema10`; normal path only). Open a `reference/` file **only when its trigger happens**:

| Trigger | Read |
| :--- | :--- |
| Lost/timed-out response, `needs_reconciliation`, grouped or manual transfers, interrupted merge | [transfers-and-recovery](reference/transfers-and-recovery.md) |
| Verifier detail: collector commands, `env_keys`, `parallel_group`, challenger, `review_dimensions` | [verification](reference/verification.md) |
| Overlapping/sequential ownership, milestones, coordinator handoff | [admission-and-handoff](reference/admission-and-handoff.md) |
| Solo `coordinator_local` boundaries, revising an executed local step | [local-steps](reference/local-steps.md) |
| `board_status.workflow.workflow_version` is not 5 (stored `workflow0..4` run) | [legacy-resume](reference/legacy-resume.md) |
| `agent_type` unavailable, so the role contract must be inlined | [inline-roles](reference/inline-roles.md) |
| Human board posts, Web UI pause/stop controls, native state checks | [human-control](reference/human-control.md) |
| Choosing and recording a subagent's `reasoning_effort` | [reasoning-effort](reference/reasoning-effort.md) |
| Calling a primitive that is not in the tool list | [advanced-tools](reference/advanced-tools.md) |

## Hard rules (never weakened)

1. PASS needs a real `tri-orchestrator-verify` SHA-256 receipt (`receipt_files`). A receipt is not independent acceptance and a board note is not a PASS. Never use raw SQL, metadata, worker status, or a board PASS to disable gates.
2. Author and verifier must be a different native agent. `original_bindings` freeze both: rework goes to the original author and the same original verifier via `followup_task`, at most 3 automatic rework rounds, then `needs_decision`.
3. Concurrent steps need mutually exclusive `file_ownership`; admission rejects overlaps.
4. Check `isError` on every write tool; automatic retries on write tools are forbidden. An unknown outcome is reconciled first (never re-dispatch, re-spawn, or replay it).
5. Never delete unmerged or dirty workspaces (`workspace_discard` only removes a finished run's never-merged workspace that is identical to its baseline). Never revive killed workers or auto-resume paused ones without explicit user instruction.

## Subagent rules

- **Native model selection**: On `spawn_agent`, omit `model` and let Codex role and session settings determine the model. Always omit both `model` and `reasoning_effort` with `fork_turns="all"`. Pass `reasoning_effort` only with `fork_turns="none"` and only if the host schema and model support the requested tier. Pick the tier per task: `medium` for bounded read-only checks, `high` for cross-module investigation or a critical review, `ultra` only when the task needs more than `high`; record plan and observation per [reasoning-effort](reference/reasoning-effort.md).
- **Role resolution**: Only pass a custom `agent_type` when the live `spawn_agent` schema accepts it and the role is installed in the active Codex home's `agents/` directory (`CODEX_HOME` if set, otherwise `~/.codex`). If not, omit `agent_type` and inline the role from [inline-roles](reference/inline-roles.md). The logical role recorded in the orchestrator stays the same.
- **`fork_turns`**: In Codex V2, custom `agent_type` applies regardless of `fork_turns`. Keep `fork_turns: "none"` for `verifier` and `challenger` (unbiased blind review); allow `fork_turns: "all"` for `coder`/`swarm_worker`/scoped `investigator` when coordinator context is already warm (warm KV cache, no cold rescan).
- **`max_depth = 2`**: `interviewer`, `investigator` and `challenger` never spawn subagents. Depth-1 `planner`, `coder`, `swarm_worker` and `verifier` may spawn read-only `investigator` helpers for cross-module call-chain lookup only; helpers never edit files, register on `18790`, or submit receipts.
- **Same-thread rework**: resume the same child with `followup_task`; never spawn a replacement author to cover defects.

## Normal path

1. **Run**: `run_create(mode, title, project_root=<real root>, session_id=$env:CODEX_THREAD_ID)` (hook-reported root ID if unset; never a worker ID). Pass a slice as `execution_root`, never as `project_name`. Read `source_root` (paths are relative to it) and `cli_executables`. Resume with `run_list(status="active")` + `task_packet_get`.
2. **Packet**: `task_packet_save(goal=<verbatim requirement>, file_ownership, acceptance_criteria=[unique checks])`.
3. **Author dispatch**: pick a unique `message_key` first and keep it even if the response is lost. `workflow_prepare_dispatch(run_id, step_key, owner, verifier, verifier_task_name, task_name="<task_name>", dispatch_action="dispatch", criteria, message_key, session_id, create_workspace=True when isolating)` → one direct `spawn_agent(task_name="<task_name>", message=<role + workspace + step_id + message_id>)` (zero waiting-only bootstrap empty turns) → immediately `native_action_record_call(run_id, action_id, claim_token, expected_revision, outcome="confirmed"|"failed", raw_response, agent_id)`, which binds `/root/<task_name>`, freezes `original_bindings` and marks the worker `working`. Rare fields (`dep_mode`, `git_backend`, `step_kind`, `handoff_required`, ...) go in `options={...}`. If the call times out or `begin` reports `needs_reconciliation`: stop and follow [transfers-and-recovery](reference/transfers-and-recovery.md) (`inbox_get` by `message_key`, then `list_agents(path_prefix="/root/<task_name>")`).
4. **Author turns**: `workflow_author_turn(phase="accept")` on receipt; after real post-edit tests, `workflow_author_turn(phase="submit", summary, body_md, post_board_note=True)`. At most one `inbox_wait` per turn (default 20000ms, max 25000ms).
5. **Verify**: register the verifier logically and spawn it on first submit: `workflow_prepare_dispatch(dispatch_action="request_verify", recipient=<verifier>, task_name=<verifier_task_name>)` → `spawn_agent(fork_turns="none")` → `native_action_record_call`; later rounds reuse it via `followup_task`. Put the `workspace_diff` summary and the packet criteria in the verifier's message so it does not cold-rescan (the review stays blind). Verifier: `workflow_verifier_turn(phase="prepare", scope="candidate"|"integrated", verification_commands, env_profile="windows_minimal_py"|"posix_minimal_py")` → run the returned `verify_argv_template` in the verifier terminal (plus `tri-orchestrator-challenge` from `cli_executables` on `complexity="high"`) → `workflow_verifier_turn(phase="record", verdict, receipt_files, ...)`.
6. **FAIL**: `workflow_prepare_dispatch(dispatch_action="start_rework")` → original author via `followup_task` → the same verifier re-checks.
7. **Merge**: after candidate PASS, `workspace_merge_preview` → `workspace_merge(workspace_id, strategy="merge")` in order, then the integrated gate (`scope="integrated"`) by the same verifier. Without a workspace, `workflow_step_advance(action="begin_integrated_review")` reuses the still-current candidate PASS as the integrated PASS (same tree, basis frozen in the checkpoint); go to step 8, and only if `complete` reports the candidate is no longer current run the integrated gate. Reopen scope touched by a later merge with `action="reverify"`.
8. **Close**: after all integrated gates pass, `workflow_closeout_run(run_id)` completes steps, runs the guarded workspace cleanup (never unmerged or dirty), stops workers and completes the run in **1 MCP call**. Tell the user whether isolated workspaces were cleaned and list any unmerged/dirty leftovers. Archiving or failing a run returns `residuals` (leftover workspaces, unresolved native actions) to report.

## Polling, board & MCP discipline

- Coordinator waits use `wait_agent(timeout_ms=30000)` at most, with `board_status(run_id, compact=True)` before and after, so Web UI pause/stop (`control_requests`) is handled within `<=30s`. Handle controls first ([human-control](reference/human-control.md)). Heartbeat expiry is not death: check real `list_agents`/`wait_agent` state.
- Issue independent read-only checks (`workspace_status`, `workspace_diff`, `workspace_merge_preview`) as parallel direct MCP tool calls in one turn. Invoke `collaboration.*` tools only as top-level direct calls.
- Subagents put detail on the board (`body_md`) and reply with a 1-line `#seq` pointer; fetch bodies on demand (`board_read`, `inbox_get`). A post with @mentions returns `wake_route`; on `coordinator_relay` you wake them.
- For `delivery: "file"` results, check `isError`, then read `result_file` (`bytes`/`sha256`) with bounded reads; never repeat the write.

## Workspaces

- Pass returned `mode`, `path`, `branch_name` and `workspace_id` to writers and verifiers.
- `mode="git_clone"`: commit owned changes on the assigned branch, report the commit SHA, require a clean worktree before verify and merge. Clone is the default Git backend; `git_backend="worktree"` is opt-in.
- `mode="snapshot"` (non-Git roots): `branch_name=None` is normal and `dirty=true` before merge is expected. Do not require a Git commit or a clean Git worktree; verify `workspace_diff`, ownership and post-edit tests; `workspace_merge` applies a three-way snapshot merge. Mind `TRI_ORCH_MAX_SNAPSHOT_MB` (500) and `.triignore`.
- `dep_mode="none"`, `dep_mode="copy"` or `dep_mode="fresh_venv_uv"` isolate dependency writes; `dep_mode="link_ro_cache"` provides shared writable dependencies and is not read-only or write-isolated.
