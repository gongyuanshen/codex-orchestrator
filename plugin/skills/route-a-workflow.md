# Route A structured workflow (workflow_version=1)

Load this shared contract before executing Boost, Teamwork or Swarm. Existing migrated version0 runs retain their historical contract and are explicitly labeled legacy. Never use raw SQL, metadata, worker status or a board PASS to disable new-run gates. A receipt is not independent acceptance.

## Coordinator: frozen work and native dispatch

1. Freeze the verbatim requirement and nonempty unique string acceptance criteria with task_packet_save. Spawn actual author and verifier agents; register their exact native agent_id values. They must be a different native agent, not merely different names. Solo uses the actual coordinator as the author and a separate native verifier.
2. Create a work item with workflow_step_save(run_id,step_key,owner,verifier,workspace_id when used,dependencies,criteria). Criteria must cover the frozen packet across the mandatory work items. Do not weaken criteria to bypass a failure. Keep the returned step id/revision; every workflow_step_advance requires expected_revision from a fresh stored step.
3. Call workflow_step_advance(action="dispatch") only after native dispatch is real. Use inbox_send with a stable key for this work item/attempt and its step_id, to the registered author. Do not send a changed payload with an old key. Record message id and actual native dispatch evidence. Posting alone does not wake or interrupt anyone.
4. An idle/ended child is resumed only through the actual native followup_task/send_input. A working child can receive native send_message at the host's supported boundary. Honor pending controls first; never resume paused/killed workers automatically. Use action="observe" only after an actual native observation, passing observation={agent_id,state,observed_at,evidence}; heartbeat is not host telemetry.

## Author: bounded receive, execution and submission

- One inbox_wait call defaults to20000ms, maximum25000ms. Return control on timeout; no permanent polling loop. The coordinator sends from another agent context, never behind the consumer wait in one desktop-agent tool batch.
- Inspect isError and full result before acting. Verify original recipient_agent_id and step_id. Accepted items marked requires_reconciliation must be checked against existing effects before any further action.
- Use inbox_ack(status="accepted") with real read evidence, then action="accept" for the initially dispatched step. Execute only the assigned files. After actual tests and final edits, action="submit". The message's completed receipt means this delivery's requested work was reported, not independent acceptance of the business task.
- On rework, the coordinator returns the exact rejection to the original author and original verifier. action="start_rework" moves rework to implementing; do not call initial accept again. Re-run tests, then submit again. Preserve workspace, native identity and frozen test requirements.
- A paused pending message may be explicitly cancelled with nonempty disposition evidence; cancellation never claims execution or acknowledges a native control request. For uncertain accepted work, reconcile first; do not fabricate completed.

## Verifier: version-bound candidate and integrated gates

1. Read the actual packet, step and diff; before running independent verification commands call artifact_capture(step_id,scope="candidate"). Workspace candidates use the full approved workspace (no partial paths); Solo can provide explicit source/test paths. Keep test logs outside that captured source scope.
2. Execute actual commands; inspect every frozen criterion. Boost independent challenge evidence uses action="challenge", evidence and the exact verifier agent_id. Record actual counterexamples/findings, not a promised verdict.
3. Call verification_record(step_id,artifact_id,verifier,verdict,criteria_results,evidence_files,key) with each assigned criterion's actual PASS/FAIL/NOT_RUN and absolute retained evidence paths. Missing or failed criteria cannot yield PASS. Backend rechecks content and evidence; it does not authenticate a claimed process exit code. Never edit source/tests as verifier.
4. A candidate PASS authorizes only the matching candidate. The coordinator performs workspace_merge_preview then workspace_merge. Solo calls action="begin_integrated_review" instead of inventing a merge/workspace.
5. Capture scope="integrated" before running actual final main-directory tests; record integrated verification, then action="complete". After later merges change a completed step's approved scope, action="reverify" explicitly reopens integrated review without resetting the rework budget. A newer packet requires explicit step revision and verification, never inherited PASS.
6. Clean only successfully merged, currently integrated-verified workspaces. Only after every required step is done, packet criteria covered, business inbox work disposed and controls acknowledged may run_update(status="completed") succeed.

## Failure, controls and recovery

- Following the first failed delivery, use at most 3 automatic rework rounds (attempt0 is initial,1..3 are repairs). Duplicate FAIL records never consume another round. At needs_decision, stop automatic dispatch and report the preserved failed evidence; changing names or creating replacement steps is not a budget reset.
- HTTP/MCP write errors are uncertain until persisted state is inspected. Never automatically replay an RPC to obtain inline output or to hide a transport failure.
- A merge stopped between filesystem writes and its database receipt remains integrating/reconciliation_required. Explicit workspace_merge(reconcile=True) checks the existing target; it never reapplies files or commits. Partial/not-applied targets need a human/coordinator decision with the retained staging/backup, not a state-only success.
- Check board_status before/after native waits and at resume. It exposes stored steps, blockers, inbox counts, revisions and observations; summary validity is checked at gate use, not a fresh complete filesystem audit. Keep native state observations separate from registered state and treat observations older than30seconds as stale.
- Teamwork milestone dependencies and confirmed handoff primitives are stored by the same workflow. The root retains native lifecycle ownership; a project coordinator does not take over existing child sessions merely by receiving a plan.
