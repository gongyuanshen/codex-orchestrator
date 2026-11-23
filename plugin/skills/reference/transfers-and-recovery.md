# Transfers & recovery (read on demand)

Read this when a dispatch response is lost or times out, when `board_status` shows `needs_reconciliation`, when several messages wait for one recipient (`next_action_groups`), when you must use the manual transfer primitives, or when a merge is interrupted. The tools named here that are not in the default list are called through `tool_advanced` ([advanced-tools](advanced-tools.md)).

## Lost dispatch response & `needs_reconciliation`

- If `workflow_prepare_dispatch` times out or its response is lost, its outcome is unknown: SQLite can commit after the caller disconnects. Do not issue the host call or re-run dispatch (including under a different key). Use read-only `inbox_get(run_id=..., sender=..., message_key=...)` to look up the original message, step, native action and workspace. If it returns `not_visible_yet`, the write may still be running; absence does not prove rollback. Recheck the same key; if still unresolved, report it as unresolved and keep the workspace rather than retrying. A found `invoking` status is the ledger's begin stage, not proof the host tool was called.
- Lease expiry, restart, disconnect, or lost results after `begin` fence into `needs_reconciliation` and lock the step/workspace. **Never re-claim or automatically replay an unknown action** (this is not exactly-once execution).
- For `spawn_agent`, call native `list_agents(path_prefix="/root/<task_name>")` first, then pass the observed host state to `native_action_record_call(..., reconcile=True)` or `native_action_update(action="reconcile", ...)`: if `/root/<task_name>` exists, bind that real `agent_id` with `outcome="confirmed"`; **only** when `list_agents` proves `not_found` (or `spawn_agent` synchronously returned an explicit rejection) may you pass `outcome="not_called"|"rejected", cancel=True` to release the lease and re-dispatch (`unknown` stays fenced).
- Reconcile a lost preparation response using the recovered action and current revision. If lookup shows an expired lease still recorded as `invoking`, a reconcile write persists the fence and may reject the stale revision; read the same key again and use the new revision with the real host observation. A pre-existing agent in `list_agents` does not prove a `followup_task` was delivered; keep the outcome unknown when delivery evidence is missing. Use a new key only for a separately authorized new attempt after reconciliation. Cancellation does not clean the workspace. If the author did not start, observe the workspace status and list an unmerged checkout as retained; never force cleanup or treat a clean status as proof of merge. On old records without a frozen workspace pointer, treat the current step pointer as current state only.

## Manual primitive transfer ledger & grouped transfers

For fallback, controls, human guidance, or stored `workflow1..4` runs (where admission precedes native implementation dispatch):

1. Persist admitted business work with `inbox_send` using a stable key and `step_id` before waking the original author or original verifier (at most 3 automatic rework rounds).
2. Query `board_status(compact=True)` to inspect `next_actions`, `needs_reconciliation`, `needs_clarification`, and pending controls before business wakeups.
3. Call `native_action_update(action="claim")` with `run_id`, `source_type` (`inbox|control|human`), `source_id`, `recipient`, `action_type` (`spawn_agent|followup_task|send_message|interrupt_agent`), and `source_digest` to obtain `action_id`, `claim_token`, lease deadline, and revision.
4. Call `native_action_update(action="begin")` with `action_id`, `claim_token`, and `expected_revision` right before invocation; stop if claim/begin fails.
5. Make the actual native call (`spawn_agent` for a `workflow5` first turn, `followup_task` for idle/ended children, `send_message` for working children, `interrupt_agent` for controls). The daemon never invokes the host.
6. Call `native_action_record_call` or `native_action_update(action="record")` with `action_id`, `claim_token`, `expected_revision`, `outcome` (`confirmed|failed|not_called|rejected|unknown`), `evidence_ref`, lowercase SHA-256 `evidence_digest`, and nonblank `result`. Note that inbox ACK, business PASS, and control ACK are separate; `worker_control_ack` is still required after interruption.

- Controls use `control_request_id`; human guidance preserves `original seq` and verbatim text, leaving ambiguous recipients in `needs_clarification` without broadcast.
- **Grouped transfers** (`workflow4`/`workflow5`): inspect `board_status.next_action_groups` (`compact=True`). Handle controls first; no notice-only wakeups. Claim a shared recipient/binding/action_type batch via `native_action_update(action="claim_group", run_id=..., recipient=..., action_type=..., members=[{source_type,source_id,source_digest},...])` (`group_id` + individual ledger rows). Passing complete group membership is mandatory. Call `action="begin_group"` with every member's `action_id`/`expected_revision`/`claim_token`, invoke one native wake, and record via `native_action_record_call(group_id=..., members=...)` or `action="record_group"`. One expired member fences all into `native_action_groups` (`frozen_member_ids`), resolved via `action="reconcile_group"` without replay.
- Keep one in-flight business wake per recipient, per-message `inbox_ack`, new arrivals outside frozen membership, and one `inbox_wait` per worker turn (default 20000ms / max 25000ms).

## Stall classifier states

`board_status` classifier states (`ready|completed|inactive|normal_wait|dependency_wait|user_decision|needs_reconciliation|host_unknown|no_progress`, plus planned `potential_candidates`) distinguish normal waiting, dependency not ready, user decision, `needs_reconciliation`, host state unknown, and waiting for coordinator from an unfinished task with no executable progress and an actionable blocker. Use `action="observe"` only with a real native `observation={agent_id, state, observed_at, evidence}` (older than 30s is stale; heartbeat expiry is not host telemetry or death).

## Merge recovery & post-merge reverify

Never replay uncertain writes blindly: an interrupted merge enters `reconciliation_required`; resolve it with `workspace_merge(reconcile=True)`. If a later merge touches approved scope, reopen the affected step with `action="reverify"` before completion or `needs_decision`.
