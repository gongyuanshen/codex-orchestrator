# Plan admission, milestones & handoff (read on demand)

Read this when steps share files, run in sequence on the same files, or a milestone hands off to another coordinator.

## Plan admission

1. Create work items with `workflow_step_save(run_id, step_key, owner, verifier, workspace_id when used, dependencies, criteria)` covering all frozen packet criteria without weakening checks, keeping step `id` and `expected_revision` from fresh stored state. Author and verifier must be a different native agent, not merely different names; Solo uses the coordinator as author and a separate native verifier.
2. Inspect read-only `plan_readiness` (hints only; draft steps allowed) before atomic admission, which rejects concurrent Windows case, glob, containment, and path-alias overlaps. Sequential same-file reuse requires explicit dependency, predecessor final PASS from the original verifier, confirmed handoff, and real native quiescence at first admission; merge-chain proofs govern post-merge repairs, and affected steps reopen via `action="reverify"` before completion or `needs_decision`.
3. `resume_context` is a read-only hint for resuming; it never grants admission.

## Milestones & coordinator handoff (Teamwork)

Each milestone step requires predecessor integrated `PASS` and confirmed handoff before dependent admission: `action="handoff"` with `recipient_agent_id=next_coordinator_agent_id`, `next_responsibility`, `forbidden_actions`, `decision_seqs` (stored in `handoff_history`), confirmed by the intended recipient via `action="ack_handoff"` with the exact `handoff_digest`. Single-root Teamwork may set `handoff_required=False` at step creation; ordinary notices do not invalidate packs; resume via read-only `resume_context`. The root retains child lifecycle ownership.
