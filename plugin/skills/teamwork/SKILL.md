---
name: teamwork
description: Structured multi-agent engineering workflow ($teamwork) with optional [skip-interview], requirement clarification, xhigh architecture planning with mutually exclusive file ownership, isolated parallel coders, two-stage verification, same-thread rework, and safe sequential merge. Use for medium-to-large features or refactors needing strict spec freeze and file ownership separation.
metadata:
  short-description: Spec freeze -> Task Packet -> Parallel isolation -> 2-stage review
---

## Route A structured workflow (Required)

Before executing a new workflow_version=1 run, read [the shared workflow contract](../route-a-workflow.md). Use the backend inbox, step, artifact and verification gates throughout the workflow below, including the inline fallback roles. Following the first failed delivery, use at most 3 automatic rework rounds; then needs_decision stops further automatic work. A board PASS or completed message is not independent acceptance. Legacy version0 runs keep their recorded behavior.


# `$teamwork` — Structured Multi-Agent Engineering Pipeline

Run a disciplined end-to-end delivery flow backed by `tri-orchestrator` SQLite Task Packets, isolated `.swarm-workspaces` clones, and a two-stage verification gate.

## Non-Negotiable Codex Subagent Rules

- **Dynamic Model Inheritance**: NEVER pass a `model` parameter (or hardcoded/stale model preset name) to `spawn_agent`. Omit `model` so every child inherits your current Codex session model.
- **Role Resolution (`agent_type` with Zero-Script `default` Fallback)**: Prefer the specialized `agent_type` (`interviewer`, `planner`, `investigator`, `coder`, or `verifier`) when installed in `~/.codex/agents/*.toml` (which configures role `model_reasoning_effort` and `sandbox_mode`). If installed directly from GitHub via `codex plugin add` without running the role-installer script (so custom `agent_type` roles are absent or rejected), gracefully fall back to `agent_type: "default"` and inline that role's contract from **Inline Fallback Role Contracts** below directly into the child's prompt. Always pass the logical role (`interviewer`, `planner`, `investigator`, `coder`, `verifier`) to `worker_register`.
- **Clean Context**: Always pass `fork_turns: "none"` to `spawn_agent`.
- **Same-Thread Rework**: Resume the SAME `coder` thread with `followup_task` (V2) or `send_input` (V1) whenever Stage 1 or Stage 2 verification finds gaps. Never replace a coder to paper over defects.
- **Control-Aware Waiting**: Do useful local work while children run. When idle, use `wait_agent(timeout_ms=30000)` and read `board_status` before and after the wait to service pending human controls; never busy-poll. Preserve same-thread rework only for workers not paused or terminated by the user.

## Inline Fallback Role Contracts (when `~/.codex/agents/` is absent)

Include the matching contract verbatim in the child prompt whenever falling back to `agent_type: "default"`:

- **`interviewer` (Read-Only Requirement Clarifier)**: Strictly read-only — NEVER edit files or run mutating commands. Preserve verbatim constraints, ask ONE focused high-leverage question per round (2–4 options + recommended default), and once resolved output a Frozen Requirement & Acceptance Contract (verbatim goal, non-goals, verifiable acceptance criteria) and post to `board_post` (`channel="main"`, `msg_type="info"`). Never spawn subagents.
- **`planner` (Architecture & File Ownership Matrix)**: Do NOT edit application code or tests. Lock cross-module interfaces/schemas first, produce a **mutually exclusive file ownership matrix** (`worker_name -> [owned_paths]` with zero writer overlap + `read_only_shared_paths`), ordered waves/merge sequence, and verifiable acceptance criteria; persist via `task_packet_save` when `run_id` is given. Never spawn subagents.
- **`investigator` (Read-Only Explorer)**: Strictly read-only — NEVER edit files or run mutating git commands. Trace code paths empirically, cite exact `file:line` and observed command outputs, and report Root Cause, Evidence, Blast Radius, and Recommended Fix Surface. Never spawn subagents.
- **`coder` (Scoped Implementer & Tests)**: Work EXCLUSIVELY inside your assigned isolated workspace path and edit ONLY your owned files from `task_packet_get` (stop and report if unowned edits are needed). Call `worker_heartbeat`, write real tests, run tests AFTER your final edit, commit clean changes on your assigned workspace branch only for `git_clone`; for snapshot workspaces report the reviewed diff without a Git commit. Report changed files, actual commit SHA (Git only), and observed test results. Never spawn subagents.
- **`verifier` (Independent Two-Stage Gate)**: NEVER edit or commit source/test files. Treat coder reports as unverified claims; inspect real `workspace_diff` / `git diff`, `task_packet_get`, and run real verification commands. Execute **Stage 1 (Spec Compliance)** and **Stage 2 (Code Quality & Real Tests)**, post findings via `board_post(channel="#verify", msg_type="test")`, and output `VERDICT: PASS` or `VERDICT: FAIL` with `[P0]`/`[P1]`/`[P2]` `file:line` defects. Never spawn subagents.


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

### Step 0: Parse Flags & Initialize Run

1. Check if the user prompt includes `[skip-interview]` (or supplies an already-frozen written spec).
2. Check active runs (`run_list(status="active", mode="teamwork")`) if resuming, otherwise initialize:
   - `run_create(mode="teamwork", title="<concise project task>", project_root="<abs_repo_path>", metadata={"skip_interview": <bool>})`

### Step 1: Requirement Clarification (`interviewer`, unless `[skip-interview]`)

- **If `[skip-interview]` is NOT set** and any requirement, interface, or edge case is ambiguous:
  1. Register `interviewer` via `worker_register(run_id=run_id, name="interviewer", role="interviewer", status="waiting", current_task="Clarify requirements")`.
  2. Dispatch `spawn_agent(agent_type="interviewer", fork_turns="none", ...)` (omit `model`).
  3. Present focused, one-question-per-round clarification (with concrete options + recommendation) to the user until ambiguities are resolved and acceptance criteria are frozen.
  4. Record the frozen contract on the board via `board_post(run_id=run_id, sender="interviewer", sender_role="interviewer", channel="main", msg_type="info", summary="Frozen requirements", body_md="...")` and set `worker_status_update(run_id=run_id, worker_name="interviewer", status="waiting")`.
- **If `[skip-interview]` IS set**: Skip `interviewer` and carry the user's verbatim requirements straight into Step 2.

### Step 2: Architecture & Mutually Exclusive File Ownership (`planner`)

1. Register `planner` via `worker_register(run_id=run_id, name="planner", role="planner", status="waiting", current_task="Decompose architecture & file ownership")`.
   - If deep codebase tracing is needed first, optionally dispatch `investigator` (`agent_type="investigator"`, `fork_turns="none"`).
2. Dispatch `spawn_agent(agent_type="planner", fork_turns="none", ...)` (omit `model`; `planner.toml` configures `model_reasoning_effort = "xhigh"`).
3. Require the planner to produce:
   - Locked cross-module interfaces and schemas;
   - A **mutually exclusive file ownership matrix** (`worker_name -> [owned_files]`, zero overlap between concurrent coders, plus read-only shared files);
   - Verifiable acceptance criteria and exact test commands;
   - Sequential merge order.
4. Persist the authoritative packet via `task_packet_save(run_id=run_id, title=..., goal=<verbatim goal + locked constraints>, file_ownership=<matrix>, acceptance_criteria=<criteria list>)` and verify via `task_packet_get(run_id=run_id)`.
5. Mark planner done: `worker_status_update(run_id=run_id, worker_name="planner", status="waiting")`.

### Step 3: Isolated Workspaces & Parallel Implementation (`coder`)

1. For every parallel writer (`coder-1`, `coder-2`, ...) in `file_ownership`:
   - Create an isolated clone/snapshot:
     `workspace_create(run_id=run_id, worker_name="coder-1", project_root="<abs_repo_path>", copy_env=True, dep_mode="none")`
     (For dependency writes needing isolation, choose `dep_mode="copy"` or `dep_mode="fresh_venv_uv"` according to the Workspace Mode & Dependency Contract; `link_ro_cache` is shared writable storage.)
   - Register the worker linked to its workspace:
     `worker_register(run_id=run_id, name="coder-1", role="coder", status="waiting", workspace_id=ws["id"], current_task="<slice summary>")`
2. Dispatch all independent `coder` subagents in parallel in one turn (`spawn_agent(agent_type="coder", fork_turns="none", ...)` — omit `model`). Each coder prompt MUST include:
   - `run_id`, `worker_name`, `workspace_id`, returned `mode`, isolated `path`, `branch_name` (None for snapshot), and `assigned_port`;
   - Verbatim task requirements, locked interfaces, and acceptance criteria from `task_packet_get`;
   - Its exclusive owned file list and explicit prohibition against editing unowned files or touching the main repo outside `path`;
   - Instruction to call `worker_heartbeat(run_id=run_id, worker_name=..., status="working")` during work; include the Workspace Mode & Dependency Contract, requiring commits only in `git_clone` and reviewed diffs/tests without commits in snapshot mode.
3. Wait with `wait_agent` (at most `30000` ms) and inspect `board_status(run_id=run_id)` / `board_read(run_id=run_id)` between stretches.

### Step 4: Two-Stage Independent Verification (`verifier`)

1. After coders finish, check `workspace_status(workspace_id)` and `workspace_diff(workspace_id)` for each workspace to verify the mode-specific requirements above and zero ownership violations; a snapshot diff is not an uncommitted Git failure.
2. Register `verifier` via `worker_register(run_id=run_id, name="verifier", role="verifier", status="waiting", current_task="Stage 1 Spec + Stage 2 Quality & Tests")` and dispatch `spawn_agent(agent_type="verifier", fork_turns="none", ...)` (omit `model`).
3. Pass each workspace mode and the Workspace Mode & Dependency Contract to `verifier`, which MUST execute both stages without editing any code:
   - **Stage 1 — Spec Compliance**: Verify every acceptance criterion in `task_packet_get` (`MET` / `GAP`), Missing / Extra / Misunderstood scope, and file ownership adherence.
   - **Stage 2 — Code Quality & Real Tests**: Inspect diffs for defects, concurrency/Windows issues, error handling, and test rigor, then run real test/lint/build commands in each workspace.
4. Post Stage 1 & 2 findings to the board (`board_post(run_id=run_id, sender="verifier", sender_role="verifier", channel="#verify", msg_type="test", ...)`).

### Step 5: Same-Thread Rework Loop

- If Stage 1 or Stage 2 fails, or ANY acceptance criterion / edge case / test remains unverified:
  - Resume the responsible `coder` thread via `followup_task` (V2) or `send_input` (V1) with the exact `file:line` defect list.
  - Refresh worker status (`worker_heartbeat` / `worker_status_update(..., status="working")`).
  - Once fixed and re-tested, resume `verifier` (`followup_task` / `send_input`) to re-audit within the at most 3 automatic rework rounds until `VERDICT: PASS`.

### Step 6: Safe Sequential Merge, Final Verification & Cleanup

Follow the planner's dependency/merge order one workspace at a time:

1. Run `workspace_merge_preview(workspace_id)` -> verify `has_conflicts == false`.
   - If conflicts exist, do NOT force merge; resolve via the owning coder in its workspace and re-verify first.
2. Run `workspace_merge(workspace_id, strategy="merge")` -> confirm `merged == true` (`status == "merged"`).
3. After all workspaces are merged into the main repository, run the full verification suite on the main repository (and have `verifier` or coordinator confirm all acceptance criteria pass together).
4. Clean up ONLY successfully merged workspaces unchanged since that merge (mode-specific checks above) with `workspace_cleanup(workspace_id)` (never bypass the unmerged/dirty guard).
5. Update worker states (`worker_status_update(..., status="stopped")`) and mark the run done (`run_update(run_id=run_id, status="completed")`).


### Large MCP results (explicit file delivery)

- Check the tool-level `isError` before parsing text as JSON. A transport error may be plain text; preserve it instead of masking it with a JSON.parse exception.
- Small results retain the existing schema. A successful result with `delivery: "file"` is a delivery receipt, NOT the original result object and NOT a truncated diff. Open the absolute `result_file`, verify its `bytes` / `sha256` metadata as needed, and read the complete original JSON through bounded local file reads. Inspect selected fields/files/diff ranges; do not print the entire large JSON into the conversation.
- Do not repeat the RPC to obtain inline output: writes have already executed. The complete returned data is retained locally. Do not claim a diff was reviewed merely because the file receipt arrived.
- A file-delivery failure is an explicit tool error. Preserve that error and check side effects before retrying; toggling/restarting the plugin is not a substitute for diagnosing a failed call.
