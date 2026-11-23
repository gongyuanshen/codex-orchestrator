# Resuming stored `workflow0..4` runs (read on demand)

Read this only when `board_status.workflow.workflow_version` of an existing run is not 5. New runs default to `workflow5` (`0.6.11/schema10`); unknown versions are unsupported and there is no public downgrade selector.

- Stored runs continue strictly by `board_status.workflow.workflow_version` without fabricated historical evidence: `workflow0` legacy, `workflow1` foundation, `workflow2` mode bindings, `workflow3` local collector, `workflow4` admission/handoff/grouped actions.
- `workflow1..4` bootstrap children remain waiting-only until admission and persisted delivery. Use the manual ledger in [transfers-and-recovery](transfers-and-recovery.md) for every wake.
- `workflow1..4` fallback dispatch (all modes): prebound verifier + `workspace_create` when isolating + `worker_register(run_id=run_id, name="<author>", role="<coder|swarm_worker>", status="waiting", workspace_id=ws["id"])` + `workflow_step_advance(action="dispatch")` + `followup_task`. Re-call `worker_register` with the real `agent_id` and report `status="working"` only after dispatch.
- Never use raw SQL, metadata, worker status, or a board PASS to disable gates on an old run.
