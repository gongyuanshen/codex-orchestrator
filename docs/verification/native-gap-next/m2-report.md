# M2 command collector — implementation and bounded acceptance

Date: 2026-11-17. Plan: `G:/工具/下一阶段开发方案.md`, M2. Software0.4.0/schema6 remain the frozen first-batch candidate. Independent Stage1/Stage2 PASS applies to the bounded Windows M2 scope. Full batch1 acceptance is not complete.

## Delivered scope

- `tri-orchestrator-verify` executes frozen exact argv through the verifier's existing terminal, not a daemon command RPC. Separate streamed binary stdout/stderr, full hashes, actual exit codes/timestamps, atomic running receipts, incomplete-state preservation and bounded CLI JSON.
- Explicit child environment allowlist; values never serialized into collector metadata. Windows creation-time owned Job assignment and last-handle-close cleanup; timeout/cancel cleanup checks membership and leaves an explicit failure when ownership cleanup is not established. No process-name sweeps.
- New default workflow3 inherits all workflow2 mode/binding/control/three-rework gates. Existing workflow0/1/2 retain their original semantics and never receive fabricated receipts.
- `artifact_capture(verification_commands=...)` freezes a noncircular manifest in the existing descriptor and backend-owned file. `verification_record(receipt_files=...)` registers receipt plus all raw evidence; record and later gate use recheck binding, original hashes, complete command results and current artifact checkpoints. Zero exit is not per-criterion PASS.
- Shared HTTP/MCP schema, health capability, package entry point/activation checks, all three skills, shared contract, six roles/inline roles and bounded evidence UI were synchronized. No new database table or dependency for M2.

## Evidence

- Pre-M2 strict baseline:522 passed in75.64s.
- Root final strict full suite after final contract clarification: **593 passed, 1 skipped in79.60s**; skip is POSIX-specific on Windows. Raw `m2/final-with-contracts-pytest.log`. Node syntax and `git diff --check` passed; raw outputs retained.
- Actual red-before-green records: package/upgrade2 failures→10pass; contracts10 failures plus allowlist clarification failure→40pass; collector missing-module/negative-path failures→25pass/1platform skip; integration default-contract/legacy-fixture/scope tests and receipt-chain regressions are documented in owner reports.
- Independent review found and original authors fixed frozen-cwd executable resolution, incomplete-output directory acceptance, invalid receipt timelines and preflight scope checking before a full-source scan could hit active SQLite SHM. Initial failing logs remain retained. Root's first full run failed the scope-preflight test (591passed/1failed/1skip); final full regression passed after the source fix.
- Actual installed-wheel/native chain: infrastructure setup failure retained and explicitly corrected; true FAIL→original-author attempt1 repair→same-verifier candidate PASS→separate integrated PASS→explicit completion. Six tracked real native deliveries, no hidden result replay. See [native/package evidence](m2-native-report.md).
- [Independent review](m2-review.md), [collector](m2-runner-report.md), [integration](m2-integration-report.md), [contracts](m2-contracts-report.md), [UI](m2-ui-report.md), [Windows ownership research](m2-process-report.md), [upgrade/retention](m2-upgrade-report.md), [frozen interface](m2-interface.md).

## Limits and next gate

M2 source/Windows isolated-package/native acceptance does not complete the whole first batch. Remaining batch1 evidence includes refreshed desktop MCP catalog and actual new-parameter calls, user-operated GUI acceptance and the full specified cross-mode native/exception matrix. Production activation remains a separately requested operation. M3–M5 are not started; the approved order places first-batch acceptance before M3.

Source changes restored between checkpoints are intentionally undetected, demonstrated by an actual counterexample. Local JSON/SHA is not a signature or host/verifier identity proof. Non-Windows runtime, restrictive nested Job behavior and startup-kill stress remain unverified. The Windows PATH/Path alias observation is a nonblocking minor: literal name uniqueness permits both aliases, but the independent probe found identical values and one effective child PATH; no functional defect or ambiguity was reproduced.

No commit, merge, push, public Release or production data migration. Work remains on `codex/native-gap-next`. Original external plan and earlier M0/M1/R1 evidence are preserved.
