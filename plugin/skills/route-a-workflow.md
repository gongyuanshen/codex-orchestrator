# Route A · workflow5 · 0.6.12/schema10

Read references on demand:

| Trigger | Contract |
|---|---|
| Timeout, interrupted merge, grouped/manual transfer | [recovery](reference/transfers-and-recovery.md) |
| Collector commands, evidence, challenge | [verification](reference/verification.md) |
| Dependencies, overlap, handoff | [admission](reference/admission-and-handoff.md) |
| Solo or local revision | [local steps](reference/local-steps.md) |
| Stored workflow0..4 | [legacy](reference/legacy-resume.md) |
| Missing role | [inline roles](reference/inline-roles.md) |
| Human board post or pause/stop | [controls](reference/human-control.md) |
| Choose/record reasoning effort | [effort](reference/reasoning-effort.md) |
| Named tool absent from core | [advanced tools](reference/advanced-tools.md): `tool_advanced(tool=..., arguments=...)` |

## Hard rules

- PASS requires independent per-criterion checks and real `tri-orchestrator-verify` SHA-256 receipts. A receipt is not independent acceptance; board notes, SQL and worker status cannot waive a gate.
- Distinct native identities: freeze `original_bindings`; same original author and original verifier for up to 3 automatic rework rounds, then `needs_decision`. Concurrent `file_ownership` must not overlap.
- Check `isError`; no automatic retries on writes. Reconcile unknown outcomes first. Never delete dirty/unmerged work. Discard is explicit and limited to provably unchanged abandoned workspaces of finished runs. Never automatically resume stopped/killed workers.

## Subagent rules

- On spawn, omit `model`; use Codex role and session settings. Always omit both `model` and `reasoning_effort` with `fork_turns="all"`. Pass `reasoning_effort` only with `fork_turns="none"` when host schema and model support the requested tier. Use `medium` for bounded reads, `high` for critical/cross-module analysis, `ultra` only if needed. Record plan and observation.
- Check the live `spawn_agent` schema and active `CODEX_HOME` (default `~/.codex`) agents directory. If absent, omit `agent_type` and inline the linked role contract; keep the logical role.
- A custom `agent_type` applies regardless of `fork_turns`. Use `fork_turns: "none"` for verifier/challenger unbiased blind review; `fork_turns: "all"` allows warm KV cache for coders and scoped investigators.
- `max_depth = 2`: interviewer, investigator and challenger never spawn. Depth-1 planner/coder/swarm_worker/verifier may delegate only read-only cross-module lookup to investigators; helpers never edit, register on 18790 or submit receipts.

## Normal path

1. Resume `run_list(status="active")` + `task_packet_get`, or `run_create(mode, title, project_root, session_id=$env:CODEX_THREAD_ID)` with the actual root ID (hook-reported if unset). Use `execution_root` for a slice, never project_name. Read returned source_root and cli_executables.
2. `task_packet_save`: verbatim goal, owner paths, unique criteria.
3. Delegated: choose a unique message_key BEFORE `workflow_prepare_dispatch(run_id, step_key, owner, verifier, verifier_task_name, task_name, criteria, message_key, session_id, create_workspace=True)`. Pass uncommon options inside `options`. Follow its `spawn_agent` hint with workspace, step and message IDs (zero waiting-only bootstrap empty turns); immediately `native_action_record_call(run_id, action_id, claim_token, expected_revision, outcome, raw_response, agent_id)`. For timeout/needs_reconciliation inspect `inbox_get` by key and `list_agents(path_prefix="/root/<task_name>")`, never replay.
4. Worker: `workflow_author_turn(phase="accept"|"submit")`; submit after real tests with summary/body and post_board_note. At most one inbox_wait per turn (default 20000ms / max 25000ms).
5. `workflow_prepare_dispatch(dispatch_action="request_verify", step_id, task_name)` → spawn independent verifier on first review, later `followup_task` → record native call. Pass packet criteria and workspace_diff summary. Verifier: `workflow_verifier_turn(phase="prepare", scope, verification_commands, env_profile)` → execute returned verify_argv_template in its terminal → `workflow_verifier_turn(phase="record", verdict, receipt_files, ...)`.
6. FAIL: start_rework for original author; repair, test, submit, prepare a new review ticket.
7. Preview then merge verified workspaces sequentially; same verifier checks the integrated tree. Without a workspace, current candidate PASS can be reused; stale content requires real integrated review. Explicit reverify always needs fresh evidence.
8. `workflow_closeout_run(run_id)` validates gates, completes steps, cleans merged workspaces and stops workers in one call. Report any dirty/unmerged leftovers and unresolved native actions, including on archive/failure.

## Polling, board & MCP discipline

- `board_status(run_id, compact=True)` before/after `wait_agent(timeout_ms=30000)`; process control_requests first (<=30s). Expired heartbeat is not proof of death; observe the native host.
- Use parallel direct MCP tool calls for independent `workspace_status`, `workspace_diff`, `workspace_merge_preview`; `collaboration.*` only as top-level direct calls. Put detail on the board; return a one-line #seq pointer. On wake_route=coordinator_relay, relay to bound @mentions.
- On delivery: "file", verify isError, bytes and sha256; read result_file in bounded chunks. Do not repeat the write.

## Workspaces

Pass mode, path, branch_name and workspace_id to author and verifier. For `mode="git_clone"` commit owned changes on the assigned branch, report SHA and require a clean tree. Linked worktree is opt-in.
For `mode="snapshot"`, `branch_name=None` is normal and `dirty=true` before merge is expected. Do not require a Git commit or a clean Git worktree. Verify workspace_diff and ownership; merge uses a three-way snapshot. Honor .triignore and TRI_ORCH_MAX_SNAPSHOT_MB (500).
Use `dep_mode="none"`, `dep_mode="copy"` or `dep_mode="fresh_venv_uv"` for isolated dependencies; `dep_mode="link_ro_cache"` means shared writable dependencies, not read-only or write-isolated.
