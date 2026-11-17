# M1 skill and role contract synchronization

## Scope

Updated only the shared Route A workflow, Boost/Teamwork/Swarm skills, their inline role contracts, six packaged role TOMLs and tests/test_native_action_contracts.py. No model/reasoning/sandbox values changed. No production installation, UI action or commit was performed.

## Contract

- Business admission → durable inbox_send → read-only bounded board_status projection → native_action_update claim → begin → real host call → record. Bootstrap children wait only.
- Control sources retain control_request_id; human sources retain original seq/text. Ambiguous targets require clarification, not broadcast.
- Claim/begin recheck version, attempt, binding and controls. Unknown/expired/restarted calls require reconciliation, never automatic replay. Existing author/verifier identities and three-repair budget remain intact.
- Native ledger confirmation, inbox ACK, business PASS and worker_control_ack remain separate. No exactly-once host guarantee or daemon host invocation.
- Additive software0.4.0/schema6; workflow0/1/2 unchanged; workflow3 deferred to M2.
- Cross-checked final docs/verification/native-gap-next/m1-interface.md and confirmed flat responses with implementation author. Documented claim source_digest, begin/record CAS, actual result/evidence SHA256 and outcome enum. Only evidenced not_called/rejected is reclaimable; generic failed/unknown is not.

## Failure-first evidence

Command: `.venv/Scripts/python.exe -m pytest tests/test_native_action_contracts.py -q`

Before edits: **10 failed**; retained `m1-contracts-red.log`. Assertions detect missing shared ordered transfer stages, missing fields/recovery rules and omitted mode/inline/packaged-role obligations.

Final command: `.venv/Scripts/python.exe -m pytest tests/test_native_action_contracts.py tests/test_workflow_contracts.py tests/test_mode_contracts.py tests/test_routea_contracts.py -q`

After final edits: **29 passed in 0.03s**; retained `m1-contracts-green.log`. `git diff --check` for owned skills/roles/new test returned no errors. An initial expanded run caught the existing phrase-level dispatch regression; preserved the existing admission wording and re-ran successfully.

## Remaining boundaries

These are static behavioral instruction contracts, not runtime ledger or real host acceptance. Runtime/transport/concurrency/native-agent-chain validation belongs to implementation and independent verification. No full-suite, package, installer or GUI validation was performed in this assignment.
