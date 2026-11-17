# M1 implementation report

## Scope and status

Implemented additive schema6 native handoff ledger. No workflow0/1/2 business transition or gate was changed. Native host calls remain exclusively the root coordinator's responsibility. Existing inbox/control ACKs remain separate.

Owned implementation: native_actions.py, db.py migration, board.py status projection, rpc_handlers.py shared HTTP/MCP tool, daemon.py startup recovery and snapshot, mcp_shim.py capability, watchdog.py existing maintenance hook. Root approved the metadata-only inbox.py change: explicit step_id now freezes packet/attempt/contract_revision in every mode; existing inbox admission rules stay unchanged. Old business sources without frozen context are rejected by the new ledger and preserved for explicit cancel/reissue.

## Delivered invariants

- Stable source/version/recipient/native-agent/action key, source digest from projection, immutable authoritative lookup, canonical positive decimal human seq. Conflicting source content, stale snapshots, packet/attempt/contract/step-revision/binding changes and pending stop controls reject claim/begin.
- Atomic SQLite claim/begin/result/reconciliation. Fixed 60-second lease, CAS revision and opaque token. Expiry is rechecked under the update transaction, with the fence committed before rejection is raised.
- claimed/invoking restart or expiry becomes needs_reconciliation with history. Old tokens cannot submit. Unknown results never automatically replay; only evidenced not_called/rejected allows reclaim.
- Reconcile-only cancel=true requires definite not_called/rejected evidence and reaches terminal cancelled. Unknown cannot be hidden by cancellation.
- record persists actual result/evidence separately from business ACK. Normal recipient ACK/progress is allowed; post-begin stop/control/binding/contract races preserve the actual result and require reconciliation.
- board_status/snapshot are read-only. Projection output retains at most 50 rows per list; streaming eligibility scan prevents old ambiguous/blocked sources starving later actions. Counts are exact; has_more marks output truncation. Scan cost O(outstanding sources), retained projection memory O(limit). No cursors, ACKs or dispatches advance on reads.
- Human original text/seq remains authoritative, ambiguous recipients are not broadcast. No new dependency, daemon or native caller.

## Test-first evidence and verification

- `m1/red.txt`: initial missing-module collection failure before implementation.
- `m1/green-initial2.txt`: first 3 ledger tests passing.
- `m1/green-api.txt`: catalog cancel-field failure detected before correction (15 passed/1 failed).
- Independent reviewer reproduced three P1s: eligibility starvation, old Boost/Teamwork source reinterpretation, and lease-gap stale begin; all fixed and covered. Human seq alias duplicate was also reproduced, fixed and covered.
- `m1/green-reviewfix2.txt`: 23 ledger/API tests passed, including canonical aliases.
- `m1/green-redispatch.txt`: strengthened packet-change fixture initially failed due changed acceptance criteria, not production behavior; `green-redispatch2.txt` preserves corrected public-API redispatch sequence, 21 ledger tests passed.
- `m1/full-strict-final.txt`: full strict suite 522 passed in 75.85 seconds. Command: `.venv/Scripts/python.exe -m pytest -q --strict-markers --strict-config -W error --basetemp C:/Temp/tri-m1-final-strict`.
- Last targeted check after final projection consistency adjustment: `m1/green-final-targeted.txt`, 99 passed in 5.29 seconds with strict markers/config and warnings as errors.

## Remaining boundaries

Real native author/verifier handoff/rework, isolated package installation, desktop GUI and production installation are owned by root and are not claimed by this implementation report. SQLite and the native host have no common transaction; the ledger intentionally does not promise exactly-once external side effects. Evidence references/digests and actual result text are persisted as supplied by the coordinator, not a new host authentication mechanism.
