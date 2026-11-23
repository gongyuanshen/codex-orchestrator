---
name: teamwork
description: "Structured multi-agent engineering workflow ($teamwork) with optional [skip-interview], requirement clarification, architecture planning with mutually exclusive file ownership, isolated parallel coders, two-stage verification, same-thread rework, and safe sequential merge. Use for medium-to-large features or refactors needing strict spec freeze and file ownership separation."
metadata:
  short-description: "Spec freeze -> Task Packet -> Parallel isolation -> 2-stage review"
---

# `$teamwork` — spec freeze, ownership plan, parallel coders, two-stage review

First load [the shared Route A contract](../route-a-workflow.md) (hard rules, subagent rules, `workflow5` normal path, polling, workspaces). This file adds only what is Teamwork-specific. Dashboard: `http://127.0.0.1:18790`.

Milestones: one `step_kind="milestone"` step per milestone (pass it in `options`) with explicit criteria/dependencies; candidate and integrated verification share the milestone's existing attempt counter (never a nested retry loop). Before a dependent milestone, confirm predecessor integrated PASS and a handoff acknowledged by the intended recipient ([admission-and-handoff](../reference/admission-and-handoff.md)); single-root sessions may pass `handoff_required=False`.

### Step 0: Flags & run

Parse `[skip-interview]`; `run_list(status="active", mode="teamwork")`, else `run_create(mode="teamwork", ..., metadata={"skip_interview": <bool>})` per Route A.

### Step 1: Clarify requirements (inline, unless `[skip-interview]`)

If requirements are ambiguous, clarify them yourself in the main conversation: ask ONE high-leverage question per round with 2–4 options and a recommended default, keep the user's words verbatim, stop when the acceptance criteria are checkable, and post the frozen contract via `board_post(channel="main", msg_type="info")`. Spawn the `interviewer` role only when the user explicitly asks for an independent clarifier.

### Step 2: Ownership plan (`planner`)

For non-trivial decomposition spawn `planner` (`fork_turns="none"`; tier per Route A). Persist locked interfaces, mutually exclusive `file_ownership`, verifiable acceptance criteria and merge order via `task_packet_save`; confirm with `task_packet_get(run_id=run_id)`.

### Step 3: Parallel implementation (`coder`)

Register the logical `verifier` (or per-slice `verifier_1`, `verifier_2`, ...), then per admitted writer: `workflow_prepare_dispatch(create_workspace=True, task_name="coder_1", owner="coder-1", verifier="verifier", dispatch_action="dispatch", ...)` → `spawn_agent` → `native_action_record_call`. Poll per Route A.

### Step 4: Two-stage verification (`verifier`)

Route A step 5 (Stage 1 spec compliance, Stage 2 code quality and real tests; `tri-orchestrator-challenge` on `complexity="high"`). Later milestones and rework reuse the same frozen `verifier` via `followup_task`.

### Step 5: Same-thread rework

On `VERDICT: FAIL`: `workflow_prepare_dispatch(dispatch_action="start_rework")`, resume the responsible `coder` via `followup_task`, re-audit with the same `verifier`.

### Step 6: Merge, integrate & hand off

For each admitted milestone, complete candidate verification and integration before admitting dependents:
1. Candidate PASS → `workspace_merge_preview` → `workspace_merge(workspace_id, strategy="merge")` in order.
2. Integrated gate `workflow_verifier_turn(phase="prepare"|"record", scope="integrated")`, then `workflow_step_advance(action="complete")`.
3. `action="handoff"` → the receiving coordinator confirms `action="ack_handoff"` with the exact `handoff_digest`.
4. Only then admit the next dependent milestone, retaining all original author workspaces.

After all milestones and final integrated gates pass (reopen touched scope with `action="reverify"`), call `workflow_closeout_run(run_id)`, which runs guarded `workspace_cleanup`, and report leftovers.
