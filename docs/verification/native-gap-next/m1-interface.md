# M1 public interface

`native_action_update(action, ...)` is the shared MCP/HTTP RPC tool. No host calls run in daemon.

- `claim`: required `run_id`, `source_type` (`inbox|control|human`), `source_id` (string; human original decimal seq), `recipient`, `action_type` (`followup_task|send_message|interrupt_agent`), `source_digest` from projection. Fixed 60-second lease. Returns ledger row including `action_id`, `claim_token`, `revision`, `lease_expires_at`.
- `begin`: required `action_id`, `expected_revision`, `claim_token`; checks frozen authoritative source/context again.
- `record`: same CAS fields, `outcome` (`confirmed|failed|not_called|rejected|unknown`), `evidence_ref`, SHA256 lowercase `evidence_digest`, `result` (actual native result text, not business ACK).
- `reconcile`: `action_id`, `expected_revision`, same evidence/outcome/result; only needs_reconciliation rows. No old token accepted. Unknown stays fenced. Only evidenced not_called/rejected can subsequently claim again.

`board_status` spreads projection fields and includes `native_actions` nested copy. HTTP snapshot includes `native_actions`. Event `native_action_updated` carries run_id/action_id/status.
Projection: next_actions rows include claim identity/source_digest, agent_id and original source. needs_reconciliation rows identify fenced/expired ledger actions. needs_clarification rows retain original human seq/text on ambiguous mentions. counts has all ledger status counts. limit=50. Native actions never advance inbox or workflow.

Reconcile-only `cancel: bool=false`: true only with evidenced not_called/rejected; transitions to cancelled, never reclaimable. Unknown/confirmed/failed cannot cancel.
Projection metadata: `limit=50`, per-array `next_actions_count`, `needs_reconciliation_count`, `needs_clarification_count`; these are bounded-window lower bounds if `counts_complete=false`/`native_actions_has_more=true`. Ledger status `counts` are exact SQL aggregates. Confirmed/fenced sources are filtered before source LIMIT to prevent starvation.

Final projection implementation streams all candidate source rows with O(limit) retained output memory and O(outstanding sources) lookup cost. Counts are now exact (`counts_complete=true`); `native_actions_has_more` means bounded arrays omit rows, not inaccurate totals. Eligibility filtering precedes output limiting, including ambiguous/blocked sources.
