---
name: teamwork
description: Structured multi-agent engineering workflow ($teamwork) with optional [skip-interview], requirement clarification, xhigh architecture planning with mutually exclusive file ownership, isolated parallel coders, two-stage verification, same-thread rework, and safe sequential merge. Use for medium-to-large features or refactors needing strict spec freeze and file ownership separation.
metadata:
  short-description: Spec freeze -> Task Packet -> Parallel isolation -> 2-stage review
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


# `$teamwork` — Structured Multi-Agent Engineering Pipeline

Run a disciplined end-to-end delivery flow backed by `tri-orchestrator` SQLite Task Packets, isolated `.swarm-workspaces` clones, and a two-stage verification gate.


## Mode2 milestones and coordinator handoff

Use one kind="milestone" work step per milestone with explicit criteria/dependencies. Every milestone retains candidate and integrated verification, exact artifacts and unresolved checkpoint items; use the milestone's existing attempt counter, never a nested retry loop. Only dependency-admitted milestones may start implementation; future milestones remain planned. Confirm predecessor integrated PASS and intended recipient handoff before the next milestone's native dispatch.

After integrated PASS, send the plan/checkpoint with action="handoff" and recipient_agent_id set to the intended recipient's actual native binding. That recipient reads it and issues action="ack_handoff" with evidence. A sent message is not confirmed handoff. The new project coordinator receives planning/checkpoints only: the root keeps native dispatch, waits, interruptions and child ownership.

## Non-Negotiable Codex Subagent Rules

- **Dynamic Model Inheritance**: NEVER pass a `model` parameter (or hardcoded/stale model preset name) to `spawn_agent`. Omit `model` so every child inherits your current Codex session model.
- **Role Resolution (`agent_type` with Zero-Script `default` Fallback)**: Prefer the specialized `agent_type` (`interviewer`, `planner`, `investigator`, `coder`, or `verifier`) when installed in `~/.codex/agents/*.toml` (which configures role `model_reasoning_effort` and `sandbox_mode`). If installed directly from GitHub via `codex plugin add` without running the role-installer script (so custom `agent_type` roles are absent or rejected), gracefully fall back to `agent_type: "default"` and inline that role's contract from **Inline Fallback Role Contracts** below directly into the child's prompt. Always pass the logical role (`interviewer`, `planner`, `investigator`, `coder`, `verifier`) to `worker_register`.
- **Clean Context**: Always pass `fork_turns: "none"` to `spawn_agent`.
- **Same-Thread Rework**: Resume the SAME `coder` thread with `followup_task` (V2) or `send_input` (V1) whenever Stage 1 or Stage 2 verification finds gaps. Never replace a coder to paper over defects.
- **Control-Aware Waiting**: Do useful local work while children run. When idle, use `wait_agent(timeout_ms=30000)` and read `board_status` before and after the wait to service pending human controls; never busy-poll. Preserve same-thread rework only for workers not paused or terminated by the user.

## Inline Fallback Role Contracts (when `~/.codex/agents/` is absent)

Include the matching contract verbatim in the child prompt whenever falling back to `agent_type: "default"`:

- **`interviewer` (Read-Only Requirement Clarifier)**: Strictly read-only — NEVER edit files or run mutating commands. Preserve verbatim constraints, ask ONE focused high-leverage question per round (2–4 options + recommended default), and once resolved output a Frozen Requirement & Acceptance Contract (verbatim goal, non-goals, verifiable acceptance criteria) and post to `board_post` (`channel="main"`, `msg_type="info"`). Never spawn subagents. Apply the shared M1 transfer protocol; bootstrap is waiting-only, not implementation. Apply the shared M2 workflow3 collector contract; only the original verifier records real receipts. Use the shared env_keys allowlist contract: verifier-terminal values only; explicitly list required system keys, never inherit the full environment. Apply the shared M3/M4/M5 workflow4 contract; challenger findings go to the original author and original verifier without resetting the three-repair budget. Preserve frozen execution_kind: coordinator_local is Boost-only root local work, never fabricated native idle; other work retains native boundaries. Apply shared local_revision_boundary single-use version/content proof for each local packet/step revision; never reuse consumed evidence.
- **`planner` (Architecture & File Ownership Matrix)**: Do NOT edit application code or tests. Lock cross-module interfaces/schemas first, produce a **mutually exclusive file ownership matrix** (`worker_name -> [owned_paths]` with zero writer overlap + `read_only_shared_paths`), ordered waves/merge sequence, and verifiable acceptance criteria; persist via `task_packet_save` when `run_id` is given. Never spawn subagents. Apply the shared M1 transfer protocol; bootstrap is waiting-only, not implementation. Apply the shared M2 workflow3 collector contract; only the original verifier records real receipts. Use the shared env_keys allowlist contract: verifier-terminal values only; explicitly list required system keys, never inherit the full environment. Apply the shared M3/M4/M5 workflow4 contract; challenger findings go to the original author and original verifier without resetting the three-repair budget. Preserve frozen execution_kind: coordinator_local is Boost-only root local work, never fabricated native idle; other work retains native boundaries. Apply shared local_revision_boundary single-use version/content proof for each local packet/step revision; never reuse consumed evidence.
- **`investigator` (Read-Only Explorer)**: Strictly read-only — NEVER edit files or run mutating git commands. Trace code paths empirically, cite exact `file:line` and observed command outputs, and report Root Cause, Evidence, Blast Radius, and Recommended Fix Surface. Never spawn subagents. Apply the shared M1 transfer protocol; bootstrap is waiting-only, not implementation. Apply the shared M2 workflow3 collector contract; only the original verifier records real receipts. Use the shared env_keys allowlist contract: verifier-terminal values only; explicitly list required system keys, never inherit the full environment. Apply the shared M3/M4/M5 workflow4 contract; challenger findings go to the original author and original verifier without resetting the three-repair budget. Preserve frozen execution_kind: coordinator_local is Boost-only root local work, never fabricated native idle; other work retains native boundaries. Apply shared local_revision_boundary single-use version/content proof for each local packet/step revision; never reuse consumed evidence.
- **`coder` (Scoped Implementer & Tests)**: Work EXCLUSIVELY inside your assigned isolated workspace path and edit ONLY your owned files from `task_packet_get` (stop and report if unowned edits are needed). Call `worker_heartbeat`, write real tests, run tests AFTER your final edit, commit clean changes on your assigned workspace branch only for `git_clone`; for snapshot workspaces report the reviewed diff without a Git commit. Report changed files, actual commit SHA (Git only), and observed test results. Never spawn subagents. Apply the shared M1 transfer protocol; bootstrap is waiting-only, not implementation. Apply the shared M2 workflow3 collector contract; only the original verifier records real receipts. Use the shared env_keys allowlist contract: verifier-terminal values only; explicitly list required system keys, never inherit the full environment. Apply the shared M3/M4/M5 workflow4 contract; challenger findings go to the original author and original verifier without resetting the three-repair budget. Preserve frozen execution_kind: coordinator_local is Boost-only root local work, never fabricated native idle; other work retains native boundaries. Apply shared local_revision_boundary single-use version/content proof for each local packet/step revision; never reuse consumed evidence.
- **`verifier` (Independent Two-Stage Gate)**: NEVER edit or commit source/test files. Treat coder reports as unverified claims; inspect real `workspace_diff` / `git diff`, `task_packet_get`, and run real verification commands. Execute **Stage 1 (Spec Compliance)** and **Stage 2 (Code Quality & Real Tests)**, post findings via `board_post(channel="#verify", msg_type="test")`, and output `VERDICT: PASS` or `VERDICT: FAIL` with `[P0]`/`[P1]`/`[P2]` `file:line` defects. Never spawn subagents. Apply the shared M1 transfer protocol; bootstrap is waiting-only, not implementation. Apply the shared M2 workflow3 collector contract; only the original verifier records real receipts. Use the shared env_keys allowlist contract: verifier-terminal values only; explicitly list required system keys, never inherit the full environment. Apply the shared M3/M4/M5 workflow4 contract; challenger findings go to the original author and original verifier without resetting the three-repair budget. Preserve frozen execution_kind: coordinator_local is Boost-only root local work, never fabricated native idle; other work retains native boundaries. Apply shared local_revision_boundary single-use version/content proof for each local packet/step revision; never reuse consumed evidence.


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

Before any writer implements, spawn a bootstrap-only verifier, register its exact native binding and keep it waiting. Reuse this prebound verifier for candidate, rework and integrated checks.

1. For every parallel writer (`coder-1`, `coder-2`, ...) in `file_ownership`:
   - Create an isolated clone/snapshot:
     `workspace_create(run_id=run_id, worker_name="coder-1", project_root="<abs_repo_path>", copy_env=True, dep_mode="none")`
     (For dependency writes needing isolation, choose `dep_mode="copy"` or `dep_mode="fresh_venv_uv"` according to the Workspace Mode & Dependency Contract; `link_ro_cache` is shared writable storage.)
   - Register the worker linked to its workspace:
     `worker_register(run_id=run_id, name="coder-1", role="coder", status="waiting", workspace_id=ws["id"], current_task="<slice summary>")`
2. Bootstrap independent `coder` subagents in parallel in waiting-only mode (`spawn_agent(agent_type="coder", fork_turns="none", ...)` — omit `model`); their initial prompt forbids implementation. Register actual bindings, save milestone steps/dependencies, and call workflow_step_advance(action="dispatch") only for currently admitted milestones. Then persist inbox_send for the admitted step and use the M1 transfer protocol before delivering actual implementation briefs via followup_task to those original children only. Each brief MUST include:
   - `run_id`, `worker_name`, `workspace_id`, returned `mode`, isolated `path`, `branch_name` (None for snapshot), and `assigned_port`;
   - Verbatim task requirements, locked interfaces, and acceptance criteria from `task_packet_get`;
   - Its exclusive owned file list and explicit prohibition against editing unowned files or touching the main repo outside `path`;
   - Instruction to call `worker_heartbeat(run_id=run_id, worker_name=..., status="working")` during work; include the Workspace Mode & Dependency Contract, requiring commits only in `git_clone` and reviewed diffs/tests without commits in snapshot mode.
3. Wait with `wait_agent` (at most `30000` ms) and inspect `board_status(run_id=run_id)` / `board_read(run_id=run_id)` between stretches.

### Step 4: Two-Stage Independent Verification (`verifier`)

1. After coders finish, check `workspace_status(workspace_id)` and `workspace_diff(workspace_id)` for each workspace to verify the mode-specific requirements above and zero ownership violations; a snapshot diff is not an uncommitted Git failure.
2. Resume the same prebound `verifier` via `followup_task` with Stage1 specification and Stage2 quality/tests; do not replace its binding or create a fresh verifier.
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

For each admitted milestone, repeat implementation, candidate verification and integration before admitting its dependents:

1. Require the current candidate artifact's independent PASS, then workspace_merge_preview(workspace_id) and workspace_merge(workspace_id, strategy="merge") in the planner's sequential order. Conflicts go to the original author and same verifier; never force merge.
2. artifact_capture(step_id=step["id"], scope="integrated") before running the actual main-directory tests for this milestone. Record verification_record with every assigned criterion and retained evidence, then workflow_step_advance(action="complete"). Candidate PASS never substitutes for this integrated PASS.
3. Send the milestone's plan/checkpoint using action="handoff" and its intended recipient_agent_id. On workflow4 include next_responsibility, forbidden_actions and decision_seqs, retain the returned immutable pack/digest, and have the receiving coordinator read that exact pack then call action="ack_handoff" with its exact handoff_digest and actual receipt evidence. Root still owns the existing native children.
4. Only then admit the next dependent milestone through workflow_step_advance(action="dispatch"). Its author also rechecks admission at accept; future milestones must not implement early. Keep each milestone's existing attempt counter and unresolved evidence.
5. Retain every milestone's original author workspace until all milestones and final integrated re-verification pass; a later failure must still return to its original author.

After all milestones, run the full main-directory integration suite. If later merges changed a prior approved scope, reopen that step with action="reverify" and record a fresh integrated gate before closure. Only after those final gates pass, workspace_cleanup each currently integrated-verified, successfully merged workspace. Finally stop finished worker records and run_update(status="completed") only after all steps, controls and business inbox work are disposed.


### Large MCP results (explicit file delivery)

- Check the tool-level `isError` before parsing text as JSON. A transport error may be plain text; preserve it instead of masking it with a JSON.parse exception.
- Small results retain the existing schema. A successful result with `delivery: "file"` is a delivery receipt, NOT the original result object and NOT a truncated diff. Open the absolute `result_file`, verify its `bytes` / `sha256` metadata as needed, and read the complete original JSON through bounded local file reads. Inspect selected fields/files/diff ranges; do not print the entire large JSON into the conversation.
- Do not repeat the RPC to obtain inline output: writes have already executed. The complete returned data is retained locally. Do not claim a diff was reviewed merely because the file receipt arrived.
- A file-delivery failure is an explicit tool error. Preserve that error and check side effects before retrying; toggling/restarting the plugin is not a substitute for diagnosing a failed call.
