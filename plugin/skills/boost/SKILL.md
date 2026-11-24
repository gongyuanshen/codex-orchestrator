---
name: boost
description: "Use $boost for a focused feature or fix requiring an independent verifier, coordinator spot-checks and same-thread rework, Solo or delegated."
metadata:
  short-description: "Adaptive Solo/Delegation execution with forced verification"
---

# `$boost` — single goal, forced independent verification

Use [Route A](../route-a-workflow.md) for workflow5. Execute → Independent challenge → Original-author repair → Final re-verification. Verifier challenges the candidate via `workflow_verifier_turn(phase="record")`; separate challenger only for high complexity.

### 1. Run & packet

Resume/create Boost; freeze verbatim goal, owner paths and criteria.

### 2. Route Complexity: Solo vs Delegation

Use a read-only investigator for substantive unknowns (actual callers, shared state, reproduction, independent work); known local fixes need no extra author. Post `route_decision` via `board_post(msg_type="info")`: packet version, grounds, ownership/dependency boundary.

- **Solo**: [local-steps](../reference/local-steps.md) gives exact arguments. `workflow_prepare_dispatch(dispatch_action="accept_local")` registers the owner and logical verifier, bundles `workflow_step_save(execution_kind="coordinator_local")`, dispatch with `agent_id=step["owner_agent_id"]`, and accept. Edit/test; `submit_local_for_verify` submits and prepares the review. Revisions: `local_revision_boundary → task_packet_save → fresh local_revision_boundary → workflow_step_save`.
- **Delegation**: `workflow_prepare_dispatch(dispatch_action="dispatch", create_workspace=True, ...)` → spawn the scoped coder → `native_action_record_call`.
- **Pure investigation**: Solo report contract. The `investigator` stays read-only; the report also uses `coordinator_local`. Capture only approved report scope for the independent `verifier`; no invented code edits, workspace, or merge. If no report path is approved, agree on a destination first.

Judge routing by actual defects, unplanned human rescue and empty waits, not subagent count.

### 3. Verify & spot-check

Use Route A step 5. Spot-check the diff and test output; post evidence pointers.

### 4. Same-Thread Rework Loop

- **Solo Path**: `start_rework` requires the original owner_agent_id and nonblank evidence in `coordinator_local_boundaries`. The coordinator fixes and tests locally; no coder thread exists. Submit and prepare a new ticket.
- **Delegation Path**: `workflow_prepare_dispatch(dispatch_action="start_rework")` resumes the exact original `coder` thread. Wake the same original `verifier` via `followup_task` after resubmission.

### 5. Merge, Cleanup & Close

Merge delegated work only; retain original author workspaces. Use the same verifier for `scope="integrated"`; `action="reverify"` needs a fresh capture. After all checks, `workflow_closeout_run` revalidates Solo reuse and performs guarded `workspace_cleanup`. Report leftovers.
