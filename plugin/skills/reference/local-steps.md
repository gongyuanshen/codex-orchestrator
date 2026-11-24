# Solo `coordinator_local` steps (read on demand)

Read this before the first Solo Boost edit, before Solo rework, and before revising an already-executed local step.

## Execution kind

Freeze `execution_kind="native"` (default for interactive modes) or `execution_kind="coordinator_local"` via `workflow_step_save` before first dispatch. `coordinator_local` is allowed only for Boost without a workspace (`workflow4`/`workflow5`, immutable after `original_bindings`), recording `coordinator_local_boundaries` with completed synchronous local edit/verification phase evidence—never claim root host idle while running, and honor all `stopped`/`ended`/packet/binding/budget checks.

**Workflow5 normal path** (after run and packet creation):
1. `workflow_prepare_dispatch(dispatch_action="accept_local", run_id, step_key, owner, verifier, options={"agent_id": actual_root_id, "advance_evidence": completed_phase_evidence})`. This registers missing identities and advances dispatch + accept atomically, without creating an author ticket. Edit and test only after it returns implementing. If the response is lost, inspect board_status by step_key, never replay.
2. `workflow_prepare_dispatch(dispatch_action="submit_local_for_verify", run_id, step_id, message_key, verifier_task_name, options={"expected_step_revision": revision, "advance_agent_id": actual_root_id})`. Follow its native verifier hint; record the host call and complete independent verification. After rework, use the same bound verifier (omit verifier_task_name).
3. Spot-check the diff and evidence. `workflow_closeout_run(run_id)` accepts a still-current candidate. If it reports stale evidence, request_verify on the same step for fresh integrated receipts; never repeat the candidate PASS.

Manual Solo workflow5 sequence (pass run_id, step_id and the latest expected_revision where required):
1. Register the actual root identity: `worker_register(run_id, name="coordinator", role="coder", agent_id=<actual root identity>, status="waiting")`; register a distinct lazy `worker_register(run_id, name="verifier", role="verifier", status="waiting")`. The packet must give this owner its exact deliverable paths.
2. Save the no-workspace step with owner, verifier and `execution_kind="coordinator_local"`. Advance `dispatch` with the recorded owner_agent_id and completed-phase evidence, then `accept` **before editing**.
3. Edit and test; advance `submit`, then `workflow_prepare_dispatch(dispatch_action="request_verify", verifier_task_name="verifier_1", ...)`. Bind the actual verifier via the returned native call protocol. On FAIL, advance `start_rework` with the original owner_agent_id and nonblank evidence; repair, test, submit and prepare a new review ticket before waking the same verifier.
4. Candidate PASS → `begin_integrated_review`. Reuse is allowed only for a current candidate. If stale, request a real integrated review and fresh receipts. Never merge a workspace-less step.

## Revising an executed local step

Revise executed local steps only via single-use `action="local_revision_boundary"` bound to `revision/packet/contract/attempt` and project content `content_digest` (`local_revision_boundaries` history); any intervening observation or filesystem edit invalidates it. `task_packet_save` consumes the boundary, requiring a fresh `local_revision_boundary` before `workflow_step_save` for each executed local step (including after removing an old criterion).

Order: `local_revision_boundary → task_packet_save → fresh local_revision_boundary → workflow_step_save`.
