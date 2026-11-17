---
name: swarm
description: Concurrent multi-worker swarm coordination ($swarm) using tri-orchestrator SQLite #seq message board, domain channels (main/#backend/#ui/#verify), isolated .swarm-workspaces clones, anti-context-explosion short-pointer replies, incremental board_read(since_seq), and heartbeat stall recovery. Use for large multi-stream parallel work with peer handoffs and live dashboard observability.
metadata:
  short-description: Concurrent #seq board swarm with isolation & stall recovery
---

## M3/M4/M5 workflow4 (current contract)

New runs use workflow4 (software 0.5.0/schema7); stored workflow0/1/2/3 preserve their original semantics. Load the shared M3/M4/M5 sections before any dispatch/resume. board_status.plan_readiness and resume_context are read-only hints, not authorization. Freeze dependencies and mutually exclusive parallel ownership; sequential overlap needs predecessor final proof, exact handoff_digest ACK and actual native quiescence. Preserve original bindings, merge-chain repair proof, workspace and rework budget; final integrated evidence remains mandatory. A new coordinator receives a plan, not old native-session control.

Use only a read-only challenger on explicitly frozen high complexity. workflow_review maps each finding to an existing criterion; original verification_record requires review_dimensions (specification/code/stress/evidence) for high complexity, all four PASS before high-complexity acceptance (optional on normal scope), actual FAIL/repair and eventual resolved_finding_ids on real PASS. Original-verifier actual PASS may dismiss only unsubstantiated findings with dismissed_finding_ids, without inventing a FAIL/repair or consuming an attempt; substantiated FAIL still requires repaired later-attempt resolution. Genuinely new requirements need explicit packet revision; no new author or auditor hides failed work.

M5 uses native_action_update claim_group → begin_group → actual native call → record_group; reconcile_group handles uncertainty without replay. Each original message/source ID retains independent ACK/version; controls first, business messages only, no replacement identities or permanent polling. The shared contract defines exact API fields and frozen group membership. Model inheritance and host approval/permissions remain unchanged.

Execution is frozen with workflow_step_save.execution_kind: native is the default for delegated work, Teamwork and Swarm. Only workflow4 Boost Solo with no workspace may select coordinator_local before first dispatch (including coordinator-owned investigation reports). The actual original root remains an existing coder-role owner; first dispatch and every start_rework need its exact owner agent_id plus nonblank completed-local-phase evidence. Retain coordinator_local_boundaries, never substitute a fake host-idle observation or bypass original stop/control/ended/packet/binding/budget checks; no conversion after original bindings/admission. See the shared exact contract.

For an already-executed coordinator_local step, local_revision_boundary is a separate single-use revision receipt bound to current revision/packet/contract/attempt and project content_digest. The exact original owner supplies completed-phase evidence before either task_packet_save or workflow_step_save; each successful save consumes the proof, so packet save requires a fresh boundary before step save. Any intervening observation/revision/file edit invalidates it. Record one for each executed local step affected by a packet; after old criteria are removed, fresh boundary checks packet identity and permits explicit step criteria revision. This Boost-only protocol never relaxes native/dependency gates or original binding/budget. See shared exact fields and history.

## M2 workflow3 collector (historical, inherited by workflow4)

Stored workflow3 runs use this collector; existing workflow0/1/2 are unchanged, without fabricated historical evidence. The original verifier freezes verification_commands in artifact_capture (exact argv/cwd/timeout_sec/env_keys/criteria), then uses the host terminal: `tri-orchestrator-verify --manifest ABS --output-dir NEW_ABS` with returned manifest_path and a new external output directory. Pass real receipt_files to verification_record and still judge every criterion. Candidate and integrated captures/receipts remain separate; finished/exit0 alone is not PASS. env_keys is the exact child environment allowlist, not merely a required-key list. Values come from the verifier terminal; the daemon validates only key syntax/uniqueness and does not require secrets in its environment. The collector checks listed values exist; explicitly list needed SystemRoot/PATH/TEMP and every other required key. Unlisted variables are not inherited; values are never serialized. Keep environment values out of collector logs; command raw stdout/stderr retains its original source and may itself contain secrets. local_collector_v1 is local evidence, not host authentication; temporary changes restored between checkpoints remain undetectable. No command RPC, no new permissions/model, no fabricated success on incomplete execution. Preserve the original author/verifier, existing three-repair budget and M1 transfer protocol. The shared M2 section defines exact fields and gate checks and supersedes historical workflow2-only sequencing below for new runs.

## M1 transfer protocol

Introduced in software 0.4.0/schema6 and inherited by current software 0.5.0/schema7, all native business/review/rework/human/control transfers below use the shared M1 ledger protocol, including inline roles. For admitted business work: inbox_send first, board_status projection, native_action_update claim, begin, actual native call, record; uncertain results reconcile, never automatically retry. Controls reference the original control_request_id and human guidance references the original seq directly; do not create synthetic inbox sources for either. All bootstrap children remain waiting-only. Follow the shared contract for source_digest/token/revision/evidence fields, freshness and pending-control checks. The daemon does not invoke native tools; bounded queries never mutate state. Preserve the original author, original verifier and maximum 3 automatic rework rounds. Inbox ACK, business PASS and control ACK remain separate; a confirmed transfer is not business completion, and control requests still require worker_control_ack. Historical workflow0/1/2 semantics remain unchanged; workflow3 collector gates are defined by the M2 section above. Every later followup_task/send_input/send_message/interrupt_agent instruction is subject to this sequence, including verifier wakeups, repair dispatch and human guidance. Preserve original source IDs and human seq/text; unresolved recipients require clarification, not broadcast. No auto-resume of stopped members and no exactly-once host guarantee.

## Route A structured workflow (Required)

Before executing a new workflow4 run or resuming stored workflow_version=1/2/3/4, read [the shared workflow contract](../route-a-workflow.md). Use the backend inbox, step, artifact and verification gates throughout the workflow below, including the inline fallback roles. Following the first failed delivery, use at most 3 automatic rework rounds; then needs_decision stops further automatic work. A board PASS or completed message is not independent acceptance. Legacy version0 runs keep their recorded behavior.


# `$swarm` — `#seq` Message Board & Concurrent Isolation Swarm

Coordinate multiple parallel workers across isolated `.swarm-workspaces` clones using the SQLite WAL `#seq` message board, channel routing, anti-context-explosion short pointers, and watchdog stall recovery.


## M5 grouped transfer and global stall contract

For workflow4 only, inspect board_status.next_action_groups over the existing M1 projection. Handle controls first; no notice-only wakeups. A group shares run_id, recipient, bound agent_id and action_type; each member retains source_type, source_id and source_digest. Call native_action_update(action="claim_group", run_id=..., recipient=..., action_type=..., members=[{source_type,source_id,source_digest},...]). Its group_id and members are individual ledger rows, not a single business receipt.

Call action="begin_group" with group_id and every member's action_id, expected_revision and claim_token, then make one actual native followup_task/send_message against the original target prompting it to read those existing IDs. Record with action="record_group" using the returned member revisions/tokens and real outcome/evidence_ref/evidence_digest/result. Recovery reads native_action_groups (group_id plus full per-action rows); needs_reconciliation members also carry group_id/frozen_member_ids. The complete group membership is mandatory at begin/record/reconcile; one expired member fences all. Frozen membership survives restart; use action="reconcile_group" with group_id, exact member action_id/expected_revision and real host evidence, without old tokens. Outcome/cancel rules remain M1: unknown never replays or cancels; not_called/rejected alone permit retry after revalidation. Failure, control races or changed binding/version stops invocation and requires explicit reconciliation, not partial silent success.

Keep one in-flight business wake per recipient/binding. Respect available native slots and admitted ownership; do not create replacement agents to hide waiting/failure. Each inbox_ack remains per-message with its own version/read/completion evidence. New arrivals remain new projected work outside frozen membership, even while a group is consumed; cursor advancement never proves forwarding or ACK. Retain one inbox_wait per author turn, default20000ms/max25000ms, with no permanent polling.

Use the existing classifier categories ready/completed/inactive/normal_wait/dependency_wait/user_decision/needs_reconciliation/host_unknown/no_progress. Planned potential_candidates still need existing admission; they do not prove executable work. Distinguish normal waiting, dependency not ready, user decision, needs_reconciliation, host state unknown and actual no-progress. Heartbeat expiry requests observation, never proves death. Persist an actionable global alert only for unfinished task + no executable progress + actionable blocker. Root inactivity means waiting for coordinator, not automatic plugin wakeup or zero-token permanent sleep. Treat events as hints; recover remaining IDs from the database after event loss, pause or restart. Respect stopped members and pending controls. Measure actual wakeups/empty waits against M0 before claiming improvement.

## Mode2 linked execution and bounded receive

Every task/rework/review/result uses inbox_send(..., step_id=step["id"]) with the correct bound owner/verifier recipient and current attempt. Admit with workflow_step_advance before native implementation dispatch; record the exact inbox id and native forwarding evidence. Author receipt, execution accept/submit, independent candidate review, rework and integrated acceptance stay on that same step. Cancel or reconcile stale delivery rather than silently forwarding it to another attempt or agent.

Make one inbox_wait per worker turn (default20000ms, maximum25000ms), then return control on timeout. The root checks real list_agents/wait_agent state before followup_task for an ended/idle original child. A heartbeat timeout is not evidence of death. Honor pending controls and stopped/killed status before any wakeup. Keep overview-first UI and full logs on the existing folded board.

## Non-Negotiable Codex Subagent Rules

- **Dynamic Model Inheritance**: NEVER pass a `model` parameter (or hardcoded/stale model preset name) to `spawn_agent`. Omit `model` so every worker inherits your active Codex session model.
- **Role Resolution (`agent_type` with Zero-Script `default` Fallback)**: Prefer the specialized `agent_type` (`swarm_worker`, `verifier`, `planner`, `investigator`, etc.) when installed in `~/.codex/agents/<role>.toml`. If installed directly from GitHub via `codex plugin add` without running the role-installer script (so custom `agent_type` roles are absent or rejected), gracefully fall back to `agent_type: "default"` and inline that role's contract from **Inline Fallback Role Contracts** below directly into the child's prompt. Always pass the logical role (`swarm_worker`, `verifier`, `planner`, `investigator`) to `worker_register`.
- **Clean Context**: Always pass `fork_turns: "none"` to `spawn_agent`.
- **Same-Thread Wake & Rework**: Use `followup_task` (V2) or `send_input` (V1) to wake stalled workers, route new `#seq` handoffs, or return failed reviews to the same thread.
- **Control-Aware Waiting**: Do useful local work while children run. When idle, use `wait_agent(timeout_ms=30000)` and read `board_status` before and after the wait to service pending human controls; never busy-poll. Preserve same-thread rework only for workers not paused or terminated by the user.

## Inline Fallback Role Contracts (when `~/.codex/agents/` is absent)

Include the matching contract verbatim in the child prompt whenever falling back to `agent_type: "default"`:

- **`planner` (Architecture & File Ownership Matrix)**: Do NOT edit application code or tests. Lock cross-module interfaces/schemas first, produce a **mutually exclusive file ownership matrix** (`worker_name -> [owned_paths]` with zero concurrent writer overlap + `read_only_shared_paths`) and ordered merge sequence, and persist via `task_packet_save` when `run_id` is given. Never spawn subagents. Apply the shared M1 transfer protocol; bootstrap is waiting-only, not implementation. Apply the shared M2 workflow3 collector contract; only the original verifier records real receipts. Use the shared env_keys allowlist contract: verifier-terminal values only; explicitly list required system keys, never inherit the full environment. Apply the shared M3/M4/M5 workflow4 contract; challenger findings go to the original author and original verifier without resetting the three-repair budget. Preserve frozen execution_kind: coordinator_local is Boost-only root local work, never fabricated native idle; other work retains native boundaries. Apply shared local_revision_boundary single-use version/content proof for each local packet/step revision; never reuse consumed evidence.
- **`investigator` (Read-Only Explorer)**: Strictly read-only — NEVER edit files or run mutating git commands. Trace code paths empirically, cite exact `file:line` and observed command outputs, and report Root Cause, Evidence, Blast Radius, and Recommended Fix Surface. Never spawn subagents. Apply the shared M1 transfer protocol; bootstrap is waiting-only, not implementation. Apply the shared M2 workflow3 collector contract; only the original verifier records real receipts. Use the shared env_keys allowlist contract: verifier-terminal values only; explicitly list required system keys, never inherit the full environment. Apply the shared M3/M4/M5 workflow4 contract; challenger findings go to the original author and original verifier without resetting the three-repair budget. Preserve frozen execution_kind: coordinator_local is Boost-only root local work, never fabricated native idle; other work retains native boundaries. Apply shared local_revision_boundary single-use version/content proof for each local packet/step revision; never reuse consumed evidence.
- **`swarm_worker` (Isolated `#seq` Worker)**: Operate EXCLUSIVELY inside `.swarm-workspaces/<run_short>-<worker>` and your owned files in `task_packet_get` (never edit unowned files or main repo root; use `assigned_port`). Read unread `#seq` posts via `board_read(advance_cursor=True)`, call `worker_heartbeat` regularly, commit clean changes on the assigned workspace branch only for `git_clone`; for snapshot workspaces report the reviewed diff without a Git commit. Write ALL detailed diffs/files/actual commits (Git only)/test outputs to `board_post` (never dump prose/diffs back to coordinator), set `worker_status_update(status="waiting")`, and reply to coordinator with ONLY 1 line: `已完成：<任务>，详见 #<seq> (@verifier)` (or `阻塞：<原因>，详见 #<seq> (@coordinator)`). Never spawn subagents. Apply the shared M1 transfer protocol; bootstrap is waiting-only, not implementation. Apply the shared M2 workflow3 collector contract; only the original verifier records real receipts. Use the shared env_keys allowlist contract: verifier-terminal values only; explicitly list required system keys, never inherit the full environment. Apply the shared M3/M4/M5 workflow4 contract; challenger findings go to the original author and original verifier without resetting the three-repair budget. Preserve frozen execution_kind: coordinator_local is Boost-only root local work, never fabricated native idle; other work retains native boundaries. Apply shared local_revision_boundary single-use version/content proof for each local packet/step revision; never reuse consumed evidence.
- **`verifier` (Independent Two-Stage Gate)**: NEVER edit or commit source/test files. Treat worker reports as unverified claims; inspect real `workspace_diff`, `task_packet_get`, referenced `#seq` posts, and run real verification commands. Audit **Stage 1 (Spec Compliance)** and **Stage 2 (Code Quality & Real Tests)**, post full findings via `board_post(channel="#verify", msg_type="test")`, and output `VERDICT: PASS` or `VERDICT: FAIL` with `[P0]`/`[P1]`/`[P2]` `file:line` findings. Never spawn subagents. Apply the shared M1 transfer protocol; bootstrap is waiting-only, not implementation. Apply the shared M2 workflow3 collector contract; only the original verifier records real receipts. Use the shared env_keys allowlist contract: verifier-terminal values only; explicitly list required system keys, never inherit the full environment. Apply the shared M3/M4/M5 workflow4 contract; challenger findings go to the original author and original verifier without resetting the three-repair budget. Preserve frozen execution_kind: coordinator_local is Boost-only root local work, never fabricated native idle; other work retains native boundaries. Apply shared local_revision_boundary single-use version/content proof for each local packet/step revision; never reuse consumed evidence.


## Native Agent Binding, State and Human Control (Required)

- After each successful `spawn_agent`, call `worker_register` again with the same run/name/role and `agent_id` set to the exact returned native target (canonical task path or agent ID), then report `status="working"` only after actual dispatch. Pre-spawn registration uses `status="waiting"`. Never invent IDs, overwrite another agent binding, or report registered-but-not-spawned members as working. If spawning fails, record the error and stop that worker.
- `working/waiting/stopped/killed` describe the workflow, not OS process telemetry. Inspect native `list_agents` / `wait_agent` (V1 equivalent where available): a completed turn awaiting review becomes `waiting`; closure becomes `stopped`. Record observed errors in `current_task` and on the board; a heartbeat alone does not prove a live model connection. After `followup_task`, report `working` only once it is actually dispatched. Never revive a killed worker or resume a paused one without a new explicit user request.
- Before dispatching/reworking, before and after waits, and before run closure, read `board_status(run_id)` and handle every pending `control_requests` entry. During active coordination, wait at most 30 seconds before checking again. Long synchronous tools can delay handling; do not claim instantaneous control or mark pending requests executed.
- Each request snapshots its `agent_id`. Use that exact bound target with native `interrupt_agent` (V1 interrupt equivalent), then inspect the native result. For requested `stopped`, pause and do not automatically resume. For requested `killed`, interrupt and remove the worker from further scheduling for this run; this is workflow termination, NOT killing the Codex application or an OS process.
- Only after native interruption succeeds and confirms the target is no longer running, call `worker_control_ack(request_id=..., outcome="applied", result="actual native tool result and observed state")`. On known non-invocation or explicit host rejection, record the real failure and call the same tool with `outcome="failed"` and the actual error; unknown interruption results stay needs_reconciliation without claiming applied/failed; do not change worker state to simulate success. A control arriving after the worker completed can be acknowledged only with a fresh native observation that it is idle; preserve that evidence.
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

First spawn a bootstrap-only verifier, register its actual native binding and keep it waiting. This prebound verifier remains the same for rework and integration. For each author node (e.g., `backend-1`, `ui-1`):

1. For concurrent writers (`swarm_worker`), provision an isolated workspace first:
   - `workspace_create(run_id=run_id, worker_name="backend-1", project_root="<abs_repo_path>", copy_env=True, dep_mode="none")`
   - Record the returned `mode`, `workspace_id`, `path` (`.swarm-workspaces/<run_short>-<worker>`), `branch_name`, and `assigned_port` (`18800..18999`).
2. Register the worker in SQLite so the Web dashboard and Watchdog track it:
   - `worker_register(run_id=run_id, name="backend-1", role="swarm_worker", status="waiting", workspace_id=ws["id"], current_task="<slice>", stall_threshold_sec=300)`
3. Bootstrap `spawn_agent(agent_type="swarm_worker", fork_turns="none", ...)` (omit `model`) with a waiting-only prompt that prohibits implementation. Register the exact returned binding, workflow_step_save the task, then workflow_step_advance(action="dispatch") before inbox_send for that step. Only after admission persist inbox_send, then use the M1 transfer protocol to deliver the actual implementation brief via followup_task. Each brief MUST specify:
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
     2. Obtain an actual native observation using `list_agents` / `wait_agent`; heartbeat expiry alone is not a dead-agent verdict. Check pending controls and stopped/killed status before scheduling.
     3. Only for an observed idle/ended original worker without user stop, wake the SAME thread via `followup_task` (V2) or `send_input` (V1) and record that actual dispatch. A running worker receives a native message, not a second execution. If unavailable, retain its workspace/checkpoint as needs-decision; do not replace its identity or retry budget automatically.

### 5. Cross-Verification, Sequential Merge & Cleanup

1. When workers post `#verify` handoffs (`已完成：...，详见 #<seq> (@verifier)`), resume the same prebound `verify-1` via `followup_task`, passing `run_id`, `step_id`, `workspace_id`, returned workspace `mode`, the Workspace Mode & Dependency Contract, `task_packet_get` reference, and relevant `#seq` IDs (read via `board_read`). Do not replace its original native binding.
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
