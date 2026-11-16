# Route A — first-batch source and isolated package acceptance

## Result and remaining work

**PASS for the first-batch source, automated regression and isolated formal-package acceptance. Not completion of the entire development plan.** Source version is0.2.0, schema4,27tools. The installed0.1.2 package and production schema3/database remain unchanged.

The approved plan is C:/workspace/plan.md; its original bytes are retained in approved-plan.md. Source progress is checked there. Production cutover remains unchecked because run_aa711e5bb5ed is still an active Boost run. Its absent listener at initial inspection was not treated as proof the task ended. No task was stopped or marked completed to enable installation. Second-batch complete Boost/Teamwork/Swarm specialization remains pending after first cutover.

## Delivered source

- Formal durable inbox in the existing package and central migration: atomic inbox/board commit, stable IDs, duplicate/conflict handling, native-binding snapshot, explicit cancelled disposition, historical receipt idempotency and reconciliation-required accepted work.
- Asynchronous bounded waits (default20s/max25s), real disconnect/cancel/shutdown cleanup and one unified HTTP/MCP catalog. Production-port bans remain only in historical prototype test wrappers, not in the formal service.
- New strict runs and preserved legacy runs; persisted steps, CAS revisions, dependencies, rework0..3, immutable candidate/integrated records, backend content/evidence hashing and explicit observation/checkpoint primitives.
- Shared run-completion, merge and cleanup gates: raw board PASS/metadata/worker status cannot authorize completion. Frozen ownership and approved artifact scope cannot shrink between candidate and integration.
- Snapshot fixed staging, per-file atomic replacement, persisted merge journal and explicit readback reconciliation; Git gates bind the actual fetched candidate commit/tree. Partial/unapplied work is not automatically replayed.
- New-client/old-daemon compatibility rejection using health software/schema/capabilities; no live restart on mismatch. Future schema is rejected before migration DDL.
- All3skills,6roles and inline-role instructions load a shared strict workflow contract. Compact dashboard displays structured blockers, stages, attempts, inbox counts and separately timestamped native observations without turning heartbeats into telemetry.
- Controlled offline package-upgrade helper with backups, all-mode active checks, stopped-owner checks, migration readback and no-new-write rollback constraints. It does not edit the Codex registry, kill services or relabel a managed version cache as another version.

## Executed verification

| Layer | Actual result | Evidence |
|---|---|---|
| Original baseline |119passed51.36s | baseline-tests.txt and original source/tests snapshot |
| Final coordinator regression |348passed69.18s,exit0 | release-candidate-suite.txt |
| Independent final regression |348passed67.41s,exit0;no confirmed openP0/P1/P2 | review-final-report.md and associated full output |
| Dashboard code/data contract |49focused/existing UI tests and Node syntax passed | ui-blocker-green.txt, review-ui-blocker-recheck.json |
| Formal in-flight failure |actual stdio formal shim -> real HTTP500/plaintext error after owned pending-request cancellation; same client recovers, accepted receipt and one file effect retained | transport-formal-loss-report.md, transport-formal-loss-result.json |
| Upgrade helper |20tests; real temporary current-package schema3->4 migration and no-write rollback | upgrade-report.md, upgrade-real-package-green.txt |
| Packaged backend |0.2.0/schema4/27tools, separate data and19893; three entry routes HTTP200 and current static JS bytes verified | native-context.json, packaged-http-routes.json |
| Native author initial delivery |actual SDK receive/accept/implement/submit/report,3tests passed | native-writer-initial-* |
| Independent actual rejection |disclosed first-seen-order fault,1failed/2passed, candidate FAIL recorded | native-fault-injection.json, native-verifier-first-* |
| Original author rework |same native author/workspace,3passed, frozen test unchanged,attempt1 | native-writer-rework-* |
| Same independent verifier recheck |3passed,candidate PASS only | native-verifier-second-* |
| Real integration |snapshot preview+approved merge,coordinator3passed | native-merge-preview.json,native-merge-result.json,native-root-integrated-tests.txt |
| Final independent integration |capture before real main tests,3passed,integrated PASS,stepdone | native-verifier-integrated-* |
| Closure/restart |workspacecleaned,2workersstopped,runcompleted,5messages/10receipts; final package restart preserves state | native-run-completed.json,native-final-package-readback.json,review-native-and-final-fixes-selfcheck.json |
| Artifact build |full33-file plugin ZIP and Python wheel built; source/package/wheel hashes checked | candidate-archive-audit.json,release-candidate-hashes.json,build-wheel-release-candidate.txt,preservation-final.json |

The native writer and verifier are the actual /root/routea_native_writer and /root/routea_native_verifier children. Coordinator used the original-agent followup for repair and review. Their terminal tools invoked a real SDK stdio client loading the isolated **formal** shim, not the old experimental bridge. This layer is not current-desktop tool-catalog registration/hot refresh.

The final package adds a handoff-input validation correction after the Boost native chain. The chain did not exercise handoff; final handoff tests and full348-test regression cover that change. The test service was stopped before updating that packaged module, restarted on the same isolated data and read back through the final formal shim. No business work was replayed to manufacture a final-package result.

## Defects and failed attempts retained

Independent review found and reproduced three defects, fixed by the original owners and independently rechecked:
1. Integrated scope could omit the actual merged/candidate files, allowing stale content to pass. Candidate/ownership scope coverage, integrated coverage and merge-proof binding now reject that path.
2. UI rendered structured blocker objects as [object Object]. The UI now resolves the actual step name and translated state; test fixtures use real backend objects.
3. Whitespace-only handoff acknowledgement could complete the step. Both handoff actions now require nonblank string identity/evidence, rejecting inputs without changing state/revision/checkpoint.

Early integration logs intentionally retain incomplete-source and old-fixture failures. Existing mechanical v0 fixtures were explicitly labeled legacy; strict new-run tests do not use a public bypass. Original result-delivery HTTP test fixtures were updated to advertise the newly required health contract; the compatibility guard was not weakened. The upgrade helper's actual Windows SQLite handle leak was reproduced and repaired with closing connections.

Two coordinator inline acceptance drivers hit SyntaxError before any tool call, before merge and final readback respectively. Neither attempted a business RPC; corrected drivers executed normally. The executable final readback is native_finalize.py. No failed invocation is counted as successful evidence.

## Preservation and shutdown

preservation-final.json verifies production tables byte-value hashes equal the opening snapshot, production schema remains3, and all29 installed package files retain their opening hashes. No production migration, restart, install/registry edit or GUI action occurred.

Global config hash did change during the development interval. A strict equality assertion failed and is recorded, not hidden: this implementation issued no global-config write; attribution was not established and the file was neither restored nor overwritten. Do not report global configuration as byte-unchanged. The observed hash/mtime boundary is recorded without copying secret configuration contents.

Owned isolated services were ended with Ctrl+C, not forced process termination. Their interrupted wrappers returned1 with KeyboardInterrupt.19893 subsequently returned Windows connection-refused10061, its owner PID file was removed, and SQLite integrity/foreign-key checks passed. Before the last shutdown, health reported zero subscribers. Test data and failed evidence remain retained intentionally.

## Proof limits / next checkpoint

- Production installation and actual desktop MCP refresh are pending, not represented by a candidate ZIP or SDK session.
- Real GUI appearance/manual accessibility acceptance was not performed; code-level/data-contract/Node checks are separate evidence.
- Full second-batch native workflows, including Teamwork two-milestone handoff and Swarm multi-worker specialization, remain pending after first rollout. Generic primitives already have unit coverage; that is not full native mode acceptance.
- Controlled cancellation/restart is not forced OS crash at an in-flight write. External filesystem writers cannot be made atomic with SQLite.
- Trusted-local names/evidence are not authenticated host identities or cryptographic proof that a submitted exit code is truthful. Evidence records and receipts are not arbitrary-side-effect exactly-once guarantees.
- Same-desktop-agent wait/send serialization remains a known host path limitation. Use the verified separate coordinator/worker topology; ended agents still require actual native followup.
