# Batch1 actual desktop acceptance — 2026-11-17

## Result

PASS for the previously missing desktop tool catalog and one approved production installation-acceptance write/read/archive loop. No M3 work. This is not the lifecycle-fix soak result.

- Actual current desktop exposes 28 tools, including `native_action_update`, `artifact_capture.verification_commands`, and `verification_record.receipt_files`.
- The user-approved single new run is `run_ea663d60b66d`, titled `安装验收 — desktop 0.4.0 M1/M2 20261117`; it is archived with its audit retained. Step `step_1c526f6b179d498b` is done at attempt 0 / revision 8.
- Root authored a small external normalization fixture after a real unittest failure (one failure / one pass); the corrected source passed both tests. Frozen tests were not changed.
- Root used actual desktop tools for packet/step creation, new-parameter artifact capture, inbox projection, claim/begin/record and completion/archive. Root made two real `collaboration.followup_task` calls to the same original independent verifier `/root/m1_native_verifier`. The host returned empty response bodies; receipts state that fact rather than fabricating agent results.
- The original verifier independently ran the installed collector twice, separately for candidate and integrated artifacts. Each executed two unittest tests with exit 0. The normal unittest report appeared on stderr, not stdout. Both independent judgments and `receipt_files` were recorded through actual desktop tools.
- Final native actions: 2 confirmed, 0 uncertain; inbox: 2 completed, 0 blocking; no pending next action. Candidate and integrated artifacts/verdicts remain separate.
- Read-only SQLite comparison confirms every preexisting row across the 14 business tables remains present with identical values; the eight prior runs are unchanged. The new ninth run is archived. Integrity and foreign-key checks passed; frozen test SHA256 is unchanged.

## Evidence

- `batch1/desktop/config.json` identifies the external project/evidence root and frozen test hash.
- `batch1/desktop/context.json`, `candidate-artifact.json`, `integrated-artifact.json` retain exact packet/step/artifact/action identities.
- `batch1/desktop/root-tool-receipts.json` contains the actual desktop catalog and root tool request/response history. The earlier candidate checkpoint is retained separately.
- External `evidence/production-before.json` and `production-preserved.json` retain the old-row baseline and final comparison.
- External `evidence/candidate/` and `integrated/` contain separate installed-collector receipts, stdout and stderr; sibling judgment and verifier raw-tool JSON files retain independent review evidence.
- Independent audit: `lifecycle-fix/desktop-independent.json`.

The first attempt to persist the entire root response history in one PowerShell command exceeded the Windows command-line limit before execution. It was then saved in bounded chunks and parsed successfully; this did not replay any production tool call. The earlier stale 27-tool evidence remains historical and is not relabeled.

## Boundary

Local collector/hash receipts are not host-signed authentication. This acceptance adds no production business task, changes no historical task, and does not enter M3. The newly requested Windows lifetime repair and sustained testing are tracked separately.
