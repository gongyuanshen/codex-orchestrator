---
name: boost
description: Adaptive single-goal execution workflow ($boost) with Solo vs Delegation routing, verbatim requirement passthrough, mandatory verifier + coordinator spot-checks, and same-thread rework loops. Use when executing a focused feature, fix, or refactor directly or with targeted subagents; do not use when full multi-role interview/planning ($teamwork) or multi-channel swarm coordination ($swarm) is requested.
metadata:
  short-description: Adaptive Solo/Delegation execution with forced verification
---

## Route A structured workflow (Required)

Before executing a new workflow_version=1 or2 run, read [the shared workflow contract](../route-a-workflow.md). Use the backend inbox, step, artifact and verification gates throughout the workflow below, including the inline fallback roles. Following the first failed delivery, use at most 3 automatic rework rounds; then needs_decision stops further automatic work. A board PASS or completed message is not independent acceptance. Legacy version0 runs keep their recorded behavior.


# `$boost` — Adaptive Routing & Forced Verification Loop

Execute a focused goal with minimal coordination overhead while guaranteeing zero requirement drift, independent verification, and zero unaddressed caveats. Live state syncs to the `tri-orchestrator` dashboard (`http://127.0.0.1:18790`).


## Mode2 execution stages

Execute → Independent challenge → Original-author repair → Final re-verification.
Persist these stages on the same workflow step. Capture before checks and bind challenge to that candidate artifact_id; a new delivery needs a new independent challenge. Solo: the coordinator fixes. Delegation: the original author child fixes. The same original verifier checks the repaired candidate and the separate integrated delivery. Attempt0 is initial; only1..3 are automatic repairs, then needs_decision. Do not revise identities or task packets to reset that decision.

## Non-Negotiable Codex Subagent Rules

- **Dynamic Model Inheritance**: NEVER pass a `model` parameter (or hardcoded/stale model preset name) to `spawn_agent`. Omit `model` so every child inherits your current Codex session model.
- **Role Resolution (`agent_type` with Zero-Script `default` Fallback)**: Prefer the specialized `agent_type` (`investigator`, `coder`, or `verifier`) when installed in `~/.codex/agents/<role>.toml` (which configures role `model_reasoning_effort` and `sandbox_mode`). If installed directly from GitHub via `codex plugin add` without running the role-installer script (so custom `agent_type` roles are absent or rejected), gracefully fall back to `agent_type: "default"` and inline that role's contract from **Inline Fallback Role Contracts** below directly into the child's prompt. Always pass the logical role (`investigator`, `coder`, or `verifier`) to `worker_register`.
- **Clean Context**: Always pass `fork_turns: "none"` to `spawn_agent`.
- **Same-Thread Rework**: When fixes or missing tests are needed, resume the SAME child thread using `followup_task` (Codex V2) or `send_input` (Codex V1). Never spawn a fresh replacement coder to cover up defects or caveats.
- **Control-Aware Waiting**: Do useful local work while children run. When idle, use `wait_agent(timeout_ms=30000)` and read `board_status` and `board_read` before and after the wait to service pending human controls and messages; never busy-poll. Preserve same-thread rework only for workers not paused or terminated by the user.

## Inline Fallback Role Contracts (when `~/.codex/agents/` is absent)

Include the matching contract verbatim in the child prompt whenever falling back to `agent_type: "default"`:

- **`investigator` (Read-Only Root Cause Analyst)**: Strictly read-only — NEVER edit, create, or delete files or run mutating git commands. Trace execution paths, separate observed facts from hypotheses, cite exact `file:line` and observed command outputs, and report Root Cause, Evidence, Blast Radius, and Recommended Fix Surface. Never spawn subagents.
- **`coder` (Scoped Implementer & Tests)**: Operate EXCLUSIVELY inside your assigned isolated workspace path (if provided) and edit ONLY your assigned `file_ownership` files (stop and report if unowned edits are needed). Reuse existing helpers, write real tests covering edge/failure cases, run relevant tests AFTER your final edit, commit clean changes on the assigned workspace branch only for `git_clone`; for snapshot workspaces report the reviewed diff without a Git commit. Return changed files, actual commit SHA (Git only), and observed test results. Never spawn subagents.
- **`verifier` (Independent Two-Stage Gate)**: NEVER edit, patch, or commit source or test files. Treat coder reports as unverified claims; inspect real `git diff` / `workspace_diff`, `task_packet_get`, and run real verification commands. Audit **Stage 1 (Spec Compliance)** (`MET`/`GAP` + ownership adherence) and **Stage 2 (Code Quality & Real Tests)**, and output `VERDICT: PASS` or `VERDICT: FAIL` with `[P0]`/`[P1]`/`[P2]` `file:line` findings. Never spawn subagents.


## Native Agent Binding, State and Human Control (Required)

- After each successful `spawn_agent`, call `worker_register` again with the same run/name/role and `agent_id` set to the exact returned native target (canonical task path or agent ID), then report `status="working"` only after actual dispatch. Pre-spawn registration uses `status="waiting"`. Never invent IDs, overwrite another agent binding, or report registered-but-not-spawned members as working. If spawning fails, record the error and stop that worker.
- `working/waiting/stopped/killed` describe the workflow, not OS process telemetry. Inspect native `list_agents` / `wait_agent` (V1 equivalent where available): a completed turn awaiting review becomes `waiting`; closure becomes `stopped`. Record observed errors in `current_task` and on the board; a heartbeat alone does not prove a live model connection. After `followup_task`, report `working` only once it is actually dispatched. Never revive a killed worker or resume a paused one without a new explicit user request.
- Before dispatching/reworking, before and after waits, and before run closure, read `board_status(run_id)` and handle every pending `control_requests` entry. During active coordination, wait at most 30 seconds before checking again. Long synchronous tools can delay handling; do not claim instantaneous control or mark pending requests executed.
- Each request snapshots its `agent_id`. Use that exact bound target with native `interrupt_agent` (V1 interrupt equivalent), then inspect the native result. For requested `stopped`, pause and do not automatically resume. For requested `killed`, interrupt and remove the worker from further scheduling for this run; this is workflow termination, NOT killing the Codex application or an OS process.
- Only after native interruption succeeds and confirms the target is no longer running, call `worker_control_ack(request_id=..., outcome="applied", result="actual native tool result and observed state")`. On unavailable tools, unknown target or failed interruption, call the same tool with `outcome="failed"` and the actual error; do not change worker state to simulate success. A control arriving after the worker completed can be acknowledged only with a fresh native observation that it is idle; preserve that evidence.
- Read-only `board_status` returns pending/applied/failed requests without consuming them. Failed requests remain visible; pending survives daemon restart. A repeated identical acknowledgement is safe, but never run the native action again for an already-resolved request. While pending, do not bypass the acknowledgement with `worker_status_update`.
- MCP transport errors are not model network errors. A request timeout can mean a write already committed: inspect the board/run/workspace before deciding whether another action is needed. Never silently bypass MCP with direct SQLite or Python service calls; report the original failure and recover the supported connection instead.

## Human Message Consumption (Required)

- At the same checkpoints as controls — before dispatching/reworking, before and after waits, and before run closure — read `batch = board_read(run_id=run_id, reader_name="coordinator", advance_cursor=False)` without channel or mention filters. This uses the durable cursor on resume; do not reset it with `since_seq=0`. Process each batch before advancing; read another batch when the count reaches the requested/default limit.
- Handle every `channel="human"` or `msg_type="human"` post before further dispatch or closure. Preserve its verbatim text and `#seq`. The board does not automatically deliver text to native agents: Solo guidance is handled by the coordinator; delegated guidance goes to the bound original responsible worker via `send_message` when running or `followup_task` / `send_input` when idle. Check paused/killed state first; resume a paused worker only on an explicit user request, and never reschedule a killed worker in this run.
- Use `board_post` to record the source `#seq`, received text, and planned action; record the actual native dispatch result separately from completion evidence. Receiving, forwarding, or advancing the cursor is not completion. If routing fails or a target is unclear, record the actual error or clarification needed and leave the action unresolved; never report it done. Resume unfinished actions from their board receipts, not merely the unread count.
- Only after recording the batch's dispositions, advance that same nonempty batch with `board_read(run_id=run_id, reader_name="coordinator", since_seq=batch["since_seq"], limit=batch["count"], advance_cursor=True)` so later arrivals are not skipped. If interrupted before advancement, inspect existing `#seq` receipts and native state before repeating an action; this is not exactly-once delivery. Unresolved actions remain in receipts for recovery even after their read cursor advances.
- If guidance changes scope, update the authoritative task packet before more implementation and pass the revised requirements to the same writer and verifier. Close only after each received message has a supported disposition and all accepted work has real verification evidence.

## Workspace Mode & Dependency Contract

- Pass the returned `mode`, `path`, `branch_name`, and `workspace_id` to writers and verifiers; apply the same mode rules to inline and installed roles.
- For `mode="git_clone"` only: commit owned changes on the assigned branch, report the actual commit SHA, and require a clean Git worktree before verification and merge.
- For `mode="snapshot"`: `branch_name=None` is normal and `dirty=true` before merge is expected when files differ from the baseline. Do not require a Git commit or a clean Git worktree, initialize Git, or invent a commit SHA. Verify the actual `workspace_diff`, file ownership, and post-edit test output.
- Both modes use `workspace_merge_preview` then `workspace_merge(workspace_id, strategy="merge")`; for snapshot mode these perform a three-way snapshot comparison/apply, not a Git merge. After successful merge and final integrated tests, `workspace_cleanup` checks the mode-specific merge proof and unchanged workspace; do not bypass rejection or discard new post-merge edits.
- Keep `dep_mode="none"` when dependencies are not needed. Use `dep_mode="copy"` for separate copied dependency files, or `dep_mode="fresh_venv_uv"` for a new `.venv` (install required packages there; it does not provision `node_modules`).
- `dep_mode="link_ro_cache"` provides shared writable dependencies through links/hardlinks (or copies if linking is unavailable). Despite its name, it is not read-only or write-isolated: writes may change the source project's dependencies. Do not use this mode when dependency writes need isolation.

## Workflow

### 1. Register Run & Anchor Verbatim Requirements

1. Check active runs if resuming (`run_list(status="active", mode="boost")`), or create a new run:
   - `run_create(mode="boost", title="<concise task title>", project_root="<abs_repo_path>")`
2. Persist the **verbatim** user request and concrete checks immediately so context compaction cannot lose edge cases:
   - `task_packet_save(run_id=run_id, title="<task title>", goal="<VERBATIM user requirements and constraints>", file_ownership={"coordinator_or_coder": ["<target paths>"]}, acceptance_criteria=["<concrete check 1>", "..."])`
   - Reference `task_packet_get(run_id=run_id)` whenever resuming or checking drift.

### 2. Route Complexity: Solo vs Delegation

Choose the smallest execution shape that handles the scope safely. First spawn a bootstrap-only independent verifier, register its actual native binding and keep it waiting; this prebound verifier is reused throughout. Register the actual root as Solo owner, or bootstrap the delegated author before saving its step. After workflow_step_save and workflow_step_advance(action="dispatch") admit the step, begin Solo edits or use followup_task on the waiting original author. Bootstrap prompts prohibit implementation before admission.

- **Solo Path** (1–2 tightly coupled files, clear root cause, single domain):
  - Only after that admission, implement in the repo, run post-edit tests, and still run **Step 3** (`verifier` + spot-check) for non-trivial changes before finishing.
- **Delegation Path** (unclear root cause, broad codebase search, or 2+ independent workstreams):
  - **Root-cause / unknown behavior**: Register `investigator` via `worker_register(run_id=run_id, name="investigator-1", role="investigator", status="waiting", current_task="...")`, then `spawn_agent(agent_type="investigator", fork_turns="none", ...)` with the verbatim problem statement.
  - **Implementation**: Register each coder via `worker_register(run_id=run_id, name="coder-1", role="coder", status="waiting", current_task="...")`.
    - When running 2+ concurrent coders (or isolating risky edits), create an isolated clone/snapshot per writer first:
      `workspace_create(run_id=run_id, worker_name="coder-1", project_root="<abs_repo_path>", copy_env=True, dep_mode="none")`
    - Bootstrap `spawn_agent(agent_type="coder", fork_turns="none", ...)` in waiting-only mode; register its returned native binding, save/admit its step, then pass the following actual implementation brief via followup_task:
      1. The **verbatim** user requirements and acceptance criteria (do not paraphrase away constraints or invent unrequested features);
      2. Exact owned files (`file_ownership`) and isolated workspace `mode`, `workspace_id`, `branch_name`, `path` + `assigned_port` (if created), including the Workspace Mode & Dependency Contract;
      3. Instruction to call `worker_heartbeat(run_id=run_id, worker_name="coder-1", status="working")` during long tasks and run real tests after the final edit.
  - Wait via `wait_agent` (up to 30 seconds), with `board_status` and the durable-cursor `board_read` before and after each wait as specified above.

### 3. Mandatory Independent Verification & Coordinator Spot-Check

Never accept a worker's self-report or your own Solo edit without independent checks:

1. If workspaces were used, inspect `workspace_status(workspace_id)` and `workspace_diff(workspace_id)` to verify the mode-specific requirements above and zero out-of-scope edits; snapshot changes are expected to be dirty before merge.
2. Resume the same prebound `verifier-1` via `followup_task`; do not spawn a new verifier or change the step binding at this stage.
   - Pass verbatim acceptance criteria (`task_packet_get`), workspace mode(s) and path(s) or changed files, the Workspace Mode & Dependency Contract, and commands to verify.
3. **Coordinator Spot-Check**: Independently inspect the real `git diff` (or `workspace_diff`) and verify observed post-edit test output (`exit code == 0`, no skipped assertions).
4. Record verification evidence on the board:
   - `board_post(run_id=run_id, sender="coordinator", sender_role="verifier", channel="main", msg_type="test", summary="...", body_md="...")`

### 4. Same-Thread Rework Loop (Zero Tolerance for Caveats)

- If `verifier` returns `VERDICT: FAIL`, OR if ANY acceptance criterion, edge case, or untested path/caveat remains open:
  - Do **NOT** spawn a replacement coder or close with caveats.
  - **Solo Path**: The coordinator fixes its own implementation against the verbatim `file:line` defects and missing tests, then runs post-edit tests; no coder thread exists, so do not invent or spawn one for rework.
  - **Delegation Path**: Resume the exact original `coder` thread via `followup_task` (V2) or `send_input` (V1) with the verbatim `file:line` defect list and missing tests. Preserve its workspace and file ownership.
  - In both paths, resume the same original `verifier` via `followup_task` / `send_input` after the author's fixes and tests. Repeat verification and coordinator spot-checks within the at most 3 automatic rework rounds until `VERDICT: PASS` with zero open P0/P1 findings or untested acceptance paths, respecting human controls before each dispatch.

### 5. Merge, Cleanup & Close

1. For each isolated workspace created in this run:
   - Run `workspace_merge_preview(workspace_id)` -> confirm `has_conflicts == false`.
   - Run `workspace_merge(workspace_id, strategy="merge")` -> confirm `merged == true` (if conflict occurs, resolve cleanly before continuing).
   - Run final verification tests on the merged main repository.
   - Clean up ONLY successfully merged workspaces unchanged since that merge (mode-specific checks above) with `workspace_cleanup(workspace_id)`.
2. Mark active workers finished (`worker_status_update(run_id=run_id, worker_name=..., status="stopped")`) and close the run:
   - `run_update(run_id=run_id, status="completed")`


### Large MCP results (explicit file delivery)

- Check the tool-level `isError` before parsing text as JSON. A transport error may be plain text; preserve it instead of masking it with a JSON.parse exception.
- Small results retain the existing schema. A successful result with `delivery: "file"` is a delivery receipt, NOT the original result object and NOT a truncated diff. Open the absolute `result_file`, verify its `bytes` / `sha256` metadata as needed, and read the complete original JSON through bounded local file reads. Inspect selected fields/files/diff ranges; do not print the entire large JSON into the conversation.
- Do not repeat the RPC to obtain inline output: writes have already executed. The complete returned data is retained locally. Do not claim a diff was reviewed merely because the file receipt arrived.
- A file-delivery failure is an explicit tool error. Preserve that error and check side effects before retrying; toggling/restarting the plugin is not a substitute for diagnosing a failed call.
