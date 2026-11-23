---
name: boost
description: "Adaptive single-goal execution workflow ($boost) with Solo vs Delegation routing, verbatim requirement passthrough, mandatory verifier + coordinator spot-checks, and same-thread rework loops. Use when executing a focused feature, fix, or refactor directly or with targeted subagents; do not use when full multi-role interview/planning ($teamwork) or multi-channel swarm coordination ($swarm) is requested."
metadata:
  short-description: "Adaptive Solo/Delegation execution with forced verification"
---

# `$boost` — single goal, forced independent verification

First load [the shared Route A contract](../route-a-workflow.md) (hard rules, subagent rules, `workflow5` normal path, polling, workspaces); this file adds only Boost specifics. Dashboard: `http://127.0.0.1:18790`.

Stages on one step: Execute → Independent challenge → Original-author repair → Final re-verification. Capture before checks and bind the challenge to the candidate `artifact_id`; at most 3 automatic rework rounds, then `needs_decision`. Light path: on `complexity="normal"` the challenge is the verifier's own `workflow_verifier_turn(phase="record")`; a separate challenger or `tri-orchestrator-challenge` run is for `complexity="high"` only.

### 1. Run & packet

`run_list(status="active", mode="boost")`, else `run_create(mode="boost", ...)`. Freeze the VERBATIM requirement: `task_packet_save(goal=..., file_ownership={"coordinator_or_coder": [...]}, acceptance_criteria=[...])`.

### 2. Route Complexity: Solo vs Delegation

Choose the smallest shape from evidence, not a file-count score. Investigate only substantive unknowns via read-only `investigator` (actual callers, shared state, reproduction, genuinely independent work); known local fixes add no extra author. Post `route_decision` via `board_post(channel="main", msg_type="info", summary="route_decision", body_md=<packet version, Solo/delegated grounds, ownership/dependency boundary>)`.

- **Solo (`coordinator_local`, no workspace)**: `worker_register(run_id=run_id, name="coordinator", role="coder", status="waiting")` as owner plus a logical verifier, freeze `workflow_step_save(execution_kind="coordinator_local")`, then `workflow_step_advance(action="dispatch", agent_id=step["owner_agent_id"], evidence=..., expected_revision=step["revision"])` before editing. Boundaries: [local-steps](../reference/local-steps.md); revising executed work: `local_revision_boundary → task_packet_save → fresh local_revision_boundary → workflow_step_save`.
- **Delegation**: `workflow_prepare_dispatch(create_workspace=True, task_name="coder_1", owner="coder-1", verifier="verifier-1", dispatch_action="dispatch", ...)` → `spawn_agent` (`fork_turns="all"` when context is warm) → `native_action_record_call`.
- **Pure investigation**: Solo report contract — `investigator` stays read-only; the coordinator-owned report also uses `coordinator_local`, saving only the approved report scope for independent `verifier` candidate and integrated checks, with no invented code edits, workspace, or merge.

Judge routing by actual defects, unplanned human rescue and empty waits, not subagent count.

### 3. Verify & spot-check

Route A step 5 with `verifier_task_name="verifier_1"`, `fork_turns="none"`. Then spot-check the real `workspace_diff`/`git diff` and test output yourself; post pointers via `board_post`.

### 4. Same-Thread Rework Loop

On `VERDICT: FAIL` or any open gap:
- **Solo Path**: call `action="start_rework"` with `agent_id=step["owner_agent_id"]` and nonblank evidence in `coordinator_local_boundaries`; the coordinator fixes its own implementation and re-runs tests — no coder thread exists, so never spawn one.
- **Delegation Path**: `workflow_prepare_dispatch(dispatch_action="start_rework")`, resume the exact original `coder` thread via `followup_task` in its preserved workspace.
- Re-verify with the same original `verifier` via `followup_task` until `VERDICT: PASS`.

### 5. Merge, Cleanup & Close

1. Candidate PASS → `workspace_merge_preview` → `workspace_merge(workspace_id, strategy="merge")`, retaining original author workspaces.
2. Integrated gate by the same verifier: `workflow_verifier_turn(phase="prepare"|"record", scope="integrated")`; reopen touched scope with `action="reverify"`. Solo: `action="begin_integrated_review"` reuses the current candidate PASS, so there is no second verifier round unless files changed after it.
3. After all integrated gates pass, `workflow_closeout_run(run_id)` completes steps, runs guarded `workspace_cleanup` and stops workers; report any leftovers.
