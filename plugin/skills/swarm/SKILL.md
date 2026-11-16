---
name: swarm
description: Concurrent multi-worker swarm coordination ($swarm) using tri-orchestrator SQLite #seq message board, domain channels (main/#backend/#ui/#verify), isolated .swarm-workspaces clones, anti-context-explosion short-pointer replies, incremental board_read(since_seq), and heartbeat stall recovery. Use for large multi-stream parallel work with peer handoffs and live dashboard observability.
metadata:
  short-description: Concurrent #seq board swarm with isolation & stall recovery
---

## Route A structured workflow (Required)

Before executing a new workflow_version=1 run, read [the shared workflow contract](../route-a-workflow.md). Use the backend inbox, step, artifact and verification gates throughout the workflow below, including the inline fallback roles. Following the first failed delivery, use at most 3 automatic rework rounds; then needs_decision stops further automatic work. A board PASS or completed message is not independent acceptance. Legacy version0 runs keep their recorded behavior.


# `$swarm` — `#seq` Message Board & Concurrent Isolation Swarm

Coordinate multiple parallel workers across isolated `.swarm-workspaces` clones using the SQLite WAL `#seq` message board, channel routing, anti-context-explosion short pointers, and watchdog stall recovery.

## Non-Negotiable Codex Subagent Rules

- **Dynamic Model Inheritance**: NEVER pass a `model` parameter (or hardcoded/stale model preset name) to `spawn_agent`. Omit `model` so every worker inherits your active Codex session model.
- **Role Resolution (`agent_type` with Zero-Script `default` Fallback)**: Prefer the specialized `agent_type` (`swarm_worker`, `verifier`, `planner`, `investigator`, etc.) when installed in `~/.codex/agents/<role>.toml`. If installed directly from GitHub via `codex plugin add` without running the role-installer script (so custom `agent_type` roles are absent or rejected), gracefully fall back to `agent_type: "default"` and inline that role's contract from **Inline Fallback Role Contracts** below directly into the child's prompt. Always pass the logical role (`swarm_worker`, `verifier`, `planner`, `investigator`) to `worker_register`.
- **Clean Context**: Always pass `fork_turns: "none"` to `spawn_agent`.
- **Same-Thread Wake & Rework**: Use `followup_task` (V2) or `send_input` (V1) to wake stalled workers, route new `#seq` handoffs, or return failed reviews to the same thread.
- **Control-Aware Waiting**: Do useful local work while children run. When idle, use `wait_agent(timeout_ms=30000)` and read `board_status` before and after the wait to service pending human controls; never busy-poll. Preserve same-thread rework only for workers not paused or terminated by the user.

## Inline Fallback Role Contracts (when `~/.codex/agents/` is absent)

Include the matching contract verbatim in the child prompt whenever falling back to `agent_type: "default"`:

- **`planner` (Architecture & File Ownership Matrix)**: Do NOT edit application code or tests. Lock cross-module interfaces/schemas first, produce a **mutually exclusive file ownership matrix** (`worker_name -> [owned_paths]` with zero writer overlap + `read_only_shared_paths`) and ordered merge sequence, and persist via `task_packet_save` when `run_id` is given. Never spawn subagents.
- **`investigator` (Read-Only Explorer)**: Strictly read-only — NEVER edit files or run mutating git commands. Trace code paths empirically, cite exact `file:line` and observed command outputs, and report Root Cause, Evidence, Blast Radius, and Recommended Fix Surface. Never spawn subagents.
- **`swarm_worker` (Isolated `#seq` Worker)**: Operate EXCLUSIVELY inside `.swarm-workspaces/<run_short>-<worker>` and your owned files in `task_packet_get` (never edit unowned files or main repo root; use `assigned_port`). Read unread `#seq` posts via `board_read(advance_cursor=True)`, call `worker_heartbeat` regularly, commit clean changes on the assigned workspace branch only for `git_clone`; for snapshot workspaces report the reviewed diff without a Git commit. Write ALL detailed diffs/files/actual commits (Git only)/test outputs to `board_post` (never dump prose/diffs back to coordinator), set `worker_status_update(status="waiting")`, and reply to coordinator with ONLY 1 line: `已完成：<任务>，详见 #<seq> (@verifier)` (or `阻塞：<原因>，详见 #<seq> (@coordinator)`). Never spawn subagents.
- **`verifier` (Independent Two-Stage Gate)**: NEVER edit or commit source/test files. Treat worker reports as unverified claims; inspect real `workspace_diff`, `task_packet_get`, referenced `#seq` posts, and run real verification commands. Audit **Stage 1 (Spec Compliance)** and **Stage 2 (Code Quality & Real Tests)**, post full findings via `board_post(channel="#verify", msg_type="test")`, and output `VERDICT: PASS` or `VERDICT: FAIL` with `[P0]`/`[P1]`/`[P2]` `file:line` findings. Never spawn subagents.


## Native Agent Binding, State and Human Control (Required)

- After each successful `spawn_agent`, call `worker_register` again with the same run/name/role and `agent_id` set to the exact returned native target (canonical task path or agent ID), then report `status="working"` only after actual dispatch. Pre-spawn registration uses `status="waiting"`. Never invent IDs, overwrite another agent binding, or report registered-but-not-spawned members as working. If spawning fails, record the error and stop that worker.
- `working/waiting/stopped/killed` describe the workflow, not OS process telemetry. Inspect native `list_agents` / `wait_agent` (V1 equivalent where available): a completed turn awaiting review becomes `waiting`; closure becomes `stopped`. Record observed errors in `current_task` and on the board; a heartbeat alone does not prove a live model connection. After `followup_task`, report `working` only once it is actually dispatched. Never revive a killed worker or resume a paused one without a new explicit user request.
- Before dispatching/reworking, before and after waits, and before run closure, read `board_status(run_id)` and handle every pending `control_requests` entry. During active coordination, wait at most 30 seconds before checking again. Long synchronous tools can delay handling; do not claim instantaneous control or mark pending requests executed.
- Each request snapshots its `agent_id`. Use that exact bound target with native `interrupt_agent` (V1 interrupt equivalent), then inspect the native result. For requested `stopped`, pause and do not automatically resume. For requested `killed`, interrupt and remove the worker from further scheduling for this run; this is workflow termination, NOT killing the Codex application or an OS process.
- Only after native interruption succeeds and confirms the target is no longer running, call `worker_control_ack(request_id=..., outcome="applied", result="actual native tool result and observed state")`. On unavailable tools, unknown target or failed interruption, call the same tool with `outcome="failed"` and the actual error; do not change worker state to simulate success. A control arriving after the worker completed can be acknowledged only with a fresh native observation that it is idle; preserve that evidence.
- Read-only `board_status` returns pending/applied/failed requests without consuming them. Failed requests remain visible; pending survives daemon restart. A repeated identical acknowledgement is safe, but never run the native action again for an already-resolved request. While pending, do not bypass the acknowledgement with `worker_status_update`.
- MCP transport errors are not model network errors. A request timeout can mean a write already committed: inspect the board/run/workspace before deciding whether another action is needed. Never silently bypass MCP with direct SQLite or Python service calls; report the original failure and recover the supported connection instead.

## Workspace Mode & Dependency Contract

- Pass the returned `mode`, `path`, `branch_name`, and `workspace_id` to writers and verifiers; apply the same mode rules to inline and installed roles.
- For `mode="git_clone"` only: commit owned changes on the assigned branch, report the actual commit SHA, and require a clean Git worktree before verification and merge.
- For `mode="snapshot"`: `branch_name=None` is normal and `dirty=true` before merge is expected when files differ from the baseline. Do not require a Git commit or a clean Git worktree, initialize Git, or invent a commit SHA. Verify the actual `workspace_diff`, file ownership, and post-edit test output.
- Both modes use `workspace_merge_preview` then `workspace_merge(workspace_id, strategy="merge")`; for snapshot mode these perform a three-way snapshot comparison/apply, not a Git merge. After successful merge and final integrated tests, `workspace_cleanup` checks the mode-specific merge proof and unchanged workspace; do not bypass rejection or discard new post-merge edits.
- Keep `dep_mode="none"` when dependencies are not needed. Use `dep_mode="copy"` for separate copied dependency files, or `dep_mode="fresh_venv_uv"` for a new `.venv` (install required packages there; it does not provision `node_modules`).
- `dep_mode="link_ro_cache"` provides shared writable dependencies through links/hardlinks (or copies if linking is unavailable). Despite its name, it is not read-only or write-isolated: writes may change the source project's dependencies. Do not use this mode when dependency writes need isolation.

## Workflow

### 1. Initialize Swarm Run, Channels & Task Packet

1. Check for an active swarm run to resume (`run_list(status="active", mode="swarm")`), or create a new one:
   - `run_create(mode="swarm", title="<swarm mission>", project_root="<abs_repo_path>")`
2. If decomposition is non-trivial, dispatch `planner` (`spawn_agent(agent_type="planner", fork_turns="none", ...)`) to produce locked cross-module interfaces and a mutually exclusive `file_ownership` matrix.
3. Persist the shared Task Packet:
   - `task_packet_save(run_id=run_id, title=..., goal=<verbatim requirements>, file_ownership=<exclusive map>, acceptance_criteria=<criteria list>)`
4. Initialize coordination channels via `board_post` (always `main`, plus domain channels as needed such as `#backend`, `#ui`, `#verify`):
   - `board_post(run_id=run_id, sender="coordinator", sender_role="planner", channel="main", msg_type="info", summary="Swarm initialized", body_md="<locked interfaces & channel routing>")`
   - Inspect active channels anytime via `board_channel_list(run_id=run_id)`.

### 2. Provision Isolated Workspaces & Register Workers

For each worker node (e.g., `backend-1`, `ui-1`, `verify-1`):

1. For concurrent writers (`swarm_worker`), provision an isolated workspace first:
   - `workspace_create(run_id=run_id, worker_name="backend-1", project_root="<abs_repo_path>", copy_env=True, dep_mode="none")`
   - Record the returned `mode`, `workspace_id`, `path` (`.swarm-workspaces/<run_short>-<worker>`), `branch_name`, and `assigned_port` (`18800..18999`).
2. Register the worker in SQLite so the Web dashboard and Watchdog track it:
   - `worker_register(run_id=run_id, name="backend-1", role="swarm_worker", status="waiting", workspace_id=ws["id"], current_task="<slice>", stall_threshold_sec=300)`
3. Dispatch `spawn_agent(agent_type="swarm_worker", fork_turns="none", ...)` (omit `model`). Each brief MUST specify:
   - `run_id`, `worker_name`, `workspace_id`, returned `mode`, isolated `path`, `branch_name` (None for snapshot), `assigned_port`, and target channel (`#backend`, `#ui`, etc.);
   - Verbatim slice requirements, `task_packet_get(run_id=run_id)` reference, and strict owned files;
   - Heartbeat obligation (`worker_heartbeat`), the Workspace Mode & Dependency Contract, and **Anti-Context-Explosion** return rule below.

### 3. Anti-Context-Explosion Discipline (CRITICAL)

- **Workers NEVER dump diffs, full logs, or long prose into their reply to the coordinator.**
- Workers MUST write all detailed reports (changed files, commit SHAs for Git only, unified diff summaries, executed test commands, full outputs, caveats) to SQLite via:
  `board_post(run_id=run_id, sender="<worker_name>", sender_role="swarm_worker", channel="#backend", msg_type="diff"|"test"|"handoff", summary="...", body_md="...", mentions=["verifier"])`
  (`board_post` returns a compact `{seq, run_id, channel, mentions, summary}` pointer without echoing `body_md`.)
- Workers MUST reply to the coordinator with **ONLY a 1-line `#seq` pointer**:
  `已完成：<任务>，详见 #14 (@verifier)`
  (or `阻塞：<原因>，详见 #15 (@coordinator)` when blocked).

### 4. Incremental `#seq` Loop, Heartbeat & Stall Recovery

Track your coordinator read pointer `last_seq = 0` (or rely on `reader_name="coordinator"`, `advance_cursor=True`):

1. Between `wait_agent` stretches (at most `30000` ms), check global state:
   - `board_status(run_id=run_id, reader_name="coordinator")` -> inspect `workers` (`waiting | working | stopped | killed`), `heartbeat_age_sec`, `is_stalled`, `stalled_workers`, and `unread_by_channel`.
   - `board_read(run_id=run_id, since_seq=last_seq, reader_name="coordinator", advance_cursor=True)` -> fetch only new messages (`#seq > last_seq`), including:
     - Worker `diff` / `test` / `handoff` pointers;
     - Watchdog stall alerts (`sender="watchdog"`, `msg_type="alert"`);
     - Human operator messages injected from the Web dashboard (`channel="human"` or `msg_type="human"` — treat these as live high-priority operator guidance!).
   - Update `last_seq` to the returned `last_seq`.
2. **Stall & Silent-Exit Recovery**:
   - Whenever `stalled_workers` is non-empty, a watchdog `alert` arrives, or a child exits without posting `#seq`:
     1. Inspect its isolated workspace via `workspace_status(workspace_id)` (dirty state, commits ahead) and `workspace_diff(workspace_id)` + latest board posts (`board_read`).
     2. Wake/nudge the SAME worker thread via `followup_task` (V2) or `send_input` (V1) instructing it to call `worker_heartbeat` and finish or post its `#seq` state.
     3. If the thread is dead or stays unresponsive, mark it `worker_status_update(run_id=run_id, worker_name=..., status="stopped")` (or `"killed"` once terminal—note both preserve `.swarm-workspaces` files safely on disk!) and spawn a replacement worker anchored to the preserved workspace or a fresh clone.

### 5. Cross-Verification, Sequential Merge & Cleanup

1. When workers post `#verify` handoffs (`已完成：...，详见 #<seq> (@verifier)`), register/wake `verifier`:
   - `worker_register(run_id=run_id, name="verify-1", role="verifier", status="waiting", current_task="Audit #<seq> and workspace")`
   - `spawn_agent(agent_type="verifier", fork_turns="none", ...)` (omit `model`), passing `run_id`, `workspace_id`, returned workspace `mode`, the Workspace Mode & Dependency Contract, `task_packet_get` reference, and relevant `#seq` IDs (read via `board_read`).
2. On `VERDICT: FAIL`, route the `#verify` defect `#seq` back to the owning `swarm_worker` thread via `followup_task` / `send_input` until `verifier` posts `VERDICT: PASS`.
3. Merge verified workspaces sequentially in dependency order:
   - `workspace_merge_preview(workspace_id)` -> confirm `has_conflicts == false`
   - `workspace_merge(workspace_id, strategy="merge")` -> confirm `merged == true`
4. Run full end-to-end integration tests on the merged main repository.
5. Clean up ONLY successfully merged workspaces unchanged since that merge (mode-specific checks above) with `workspace_cleanup(workspace_id)` (never attempt unmerged/dirty deletion).
6. Finalize worker states (`worker_status_update(..., status="stopped")`), post final summary via `board_post(channel="main", ...)`, and close the run:
   - `run_update(run_id=run_id, status="completed")`


### Large MCP results (explicit file delivery)

- Check the tool-level `isError` before parsing text as JSON. A transport error may be plain text; preserve it instead of masking it with a JSON.parse exception.
- Small results retain the existing schema. A successful result with `delivery: "file"` is a delivery receipt, NOT the original result object and NOT a truncated diff. Open the absolute `result_file`, verify its `bytes` / `sha256` metadata as needed, and read the complete original JSON through bounded local file reads. Inspect selected fields/files/diff ranges; do not print the entire large JSON into the conversation.
- Do not repeat the RPC to obtain inline output: writes have already executed. The complete returned data is retained locally. Do not claim a diff was reviewed merely because the file receipt arrived.
- A file-delivery failure is an explicit tool error. Preserve that error and check side effects before retrying; toggling/restarting the plugin is not a substitute for diagnosing a failed call.
