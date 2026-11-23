# Solo `coordinator_local` steps (read on demand)

Read this before the first Solo Boost edit, before Solo rework, and before revising an already-executed local step.

## Execution kind

Freeze `execution_kind="native"` (default for interactive modes) or `execution_kind="coordinator_local"` via `workflow_step_save` before first dispatch. `coordinator_local` is allowed only for Boost without a workspace (`workflow4`/`workflow5`, immutable after `original_bindings`), recording `coordinator_local_boundaries` with completed synchronous local edit/verification phase evidence—never claim root host idle while running, and honor all `stopped`/`ended`/packet/binding/budget checks.

Solo sequence: `worker_register(run_id, name=<coordinator coder name>, role="coder", status="waiting")` for the root as owner and a logical `verifier` → `workflow_step_save(execution_kind="coordinator_local")` with no workspace → `workflow_step_advance(action="dispatch", agent_id=step["owner_agent_id"], evidence=<completed phase evidence>, expected_revision=step["revision"])` before editing. For Solo rework, call `action="start_rework"` with `agent_id=step["owner_agent_id"]` and nonblank evidence in `coordinator_local_boundaries` before editing.

## Revising an executed local step

Revise executed local steps only via single-use `action="local_revision_boundary"` bound to `revision/packet/contract/attempt` and project content `content_digest` (`local_revision_boundaries` history); any intervening observation or filesystem edit invalidates it. `task_packet_save` consumes the boundary, requiring a fresh `local_revision_boundary` before `workflow_step_save` for each executed local step (including after removing an old criterion).

Order: `local_revision_boundary → task_packet_save → fresh local_revision_boundary → workflow_step_save`.
