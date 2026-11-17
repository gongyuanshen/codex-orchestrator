# SDD ledger — plan: C:/workspace/next-phase-plan.md

> Current state (2026-11-17): M3–M5 source implementation and bounded isolated acceptance are complete at 0.5.0/schema7/workflow4; the full second-batch gate remains partial. Final suite: 722 passed/1 POSIX skip. Production stays at first-batch0.4.0/schema6; no installation or publication authorized/performed. See [batch2 delivery](batch2-report.md) and [current plan](C:/workspace/next-phase-plan.md). Earlier status paragraphs below are dated history, not the current queue.

## Original execution scope and baseline (historical)
- Approved to begin implementation by user on 2026-11-17; no production installation, migration, UI automation or public Release.
- Baseline commit: 9c29cdb. Branch: codex/native-gap-next. Original checkout was clean.
- Native worktree creation rejected sourceRoot because chat workspace is the parent directory; reuse existing checkout on a new branch. Tests and data stay isolated. No production process changes.
- Workflow: M0 -> M1 -> M2 -> batch-1 verification -> M3 -> M4 -> M5 -> batch-2 verification. R1 is independent and bounded.
- This report owns execution status; original plan remains unchanged.

## Preflight
| Tasks | Shared interface / constraints | Decision |
|---|---|---|
| M0 | Six fixture failures, real measurements, unavailable host/token data | Reuse existing mode fixtures, no fabricated zero metrics |
| M1 | Projection vs durable action state; claim/begin race | One new ledger, read-only status, host calls remain root-owned |
| M2 | Local argv collection vs host authentication | No arbitrary command RPC; receipt is local evidence only |
| M3 | Historic dependency admission vs final artifact validity | New contract only; do not alter workflow2 |
| M4 | Strategy vs original author/verifier budget | Reuse existing roles and failure paths |
| M5 | Grouping vs per-message disposition | Reuse M1 ledger and inbox, no second scheduler |
| R1 | Current owning host vs new App Server | Public interface inspection first; absent supported path is bounded NO-GO |
| M0/R1 | Report directory | Separate subdirectories and files; coordinator owns report.md |
| M1/M2 | DB, RPC, workflow, metadata | Single integrator owns common files; batch version frozen before changes |
| M1/M3/M5 | Native actions, board projection | Serial integration and tests; no duplicate task state |
| M2/M3/M4 | Evidence, gates, skills | M2 contract before downstream gate work |
| M3/M4/M5 | Shared skills and native lifecycle | Single contract owner; preserve original bindings and budgets |

## Task status
- [x] M0 reproducible deterministic baseline — independent spec/quality PASS; native model metrics unavailable
- [x] M1 ledger and native handoff — source and documented bounded isolated/native acceptance; final independent spec/quality PASS (individual milestone gate; the separate batch gate is now complete)
- [x] M2 command collection — Windows source and bounded isolated-package/native acceptance; independent Stage1/Stage2 PASS (individual milestone gate; the separate batch gate is now complete)
- [x] Batch 1 independent acceptance — including final desktop reconnection after user restart
- [ ] M3 admission and handoff
- [ ] M4 routing
- [ ] M5 grouping and stalled-state handling
- [ ] Batch 2 independent acceptance
- [x] R1 bounded host research — current-version NO-GO, no bridge implemented
## 2026-11-17 earlier execution update (historical)
- Strict baseline: 480 passed, 74.80 seconds; baseline-pytest.log (local).
- M0 first independent review found missing actual interruption/reopen coverage; original author added explicit DB reopen and effect-counter scenarios; same reviewer is rechecking. Deterministic fixture measurements are not native-model performance results.
- M1 in implementation/independent review; shared skills/roles and read-only UI tests implemented. Candidate metadata is 0.4.0, schema6. Existing workflow0/1/2 semantics remain unchanged. Mandatory workflow3 receipt gates belong to M2 and are not yet enabled.
- R1 current-version public-interface pass: version-scoped NO-GO, no new supported owning-host path. See r1-report.md. No fixture/control probes repeated without new interface evidence.
- Root package/upgrade tests: 3 expected red then 8 passed; schema6 activation must retain workflow_modes_v2 and require native_actions_v1. No production activation performed.
- Pending: M1 adversarial review and actual native fixture, isolated package/readbacks; M2 and later milestones untouched.

## 2026-11-17 M0/M1 handoff (historical)

### Delivered this iteration
- M0: six reproducible deterministic RED→GREEN cases; fixed9c29cdb baseline;12 workflow/inbox contracts. Initial review defects repaired by original author; same reviewer final spec/quality PASS. See [M0 report](m0-report.md) and [review history](m0-review.md).
- M1: additive native transfer ledger and read-only pending/reconciliation/clarification projection; central migration/schema6, one shared HTTP/MCP tool, fixed60-second CAS leases, restart/expiry fencing and explicit evidence reconciliation; original lifecycle/budget retained. All-mode explicit inbox step metadata is frozen at send; old missing metadata is not fabricated. Skills, six roles, UI projection, package and activation checks synchronized. See [implementation](m1-report.md), [interface](m1-interface.md), [contracts](m1-contracts-report.md), [UI](m1-ui-report.md), [independent review](m1-review.md).
- R1: version-scoped NO-GO after current owner/public-interface inspection; no unproven attachment/control path reattempted. See [R1 report](r1-report.md).

### Fresh coordinator verification
- Final source: full `pytest -q -W error --strict-markers --strict-config` with external unique basetemp: **522 passed in80.25s**. `final-pytest.log` retained locally. `node --check plugin/src/orchestrator/web/app.js` and `git diff --check` passed.
- Actual native original-author/original-verifier chain: seeded source FAIL(two actual pytest failures) → original-author repair attempt1 → same-verifier candidate PASS → separate integrated PASS → explicit completion. Initial post-call restart uncertainty was fenced/reconciled, never retransmitted.
- Final source package separately rebuilt/installed after original run closure;16 runtime files match final source,28 unique tools including native_action_update, old completed data/receipts retained; sixth real final-byte notice delivered/acknowledged. Actual schema5→6 migration retained records and old program refused schema6. Active-run upgrade rejected. Only owned isolated test daemon processes stopped; production not activated.
- First full repair chain ran preceding wheel with one subsequent projection-consistency patch. Final-byte notice/readback is narrower than full exact-byte chain replay. One notice evidence field typo was preserved and corrected append-only by original author. Build network timeout and successful offline retry retained. See [native/package evidence and limits](m1-native-report.md).
- Retained local install artifacts: `dist/tri-orchestrator-0.4.0-final-candidate.zip` and `dist/tri_orchestrator-0.4.0-py3-none-any.whl`; machine evidence index `native/evidence-index.json`. Raw data/logs/artifacts remain ignored, not auto-committed.

### Unfinished queue / next step
1. M2 command collector and new mandatory workflow contract; then first-batch independent acceptance. Candidate software0.4.0/schema6 is frozen for that batch; existing workflow0/1/2 remain unchanged. Do not re-label M1 as the complete first batch.
2. M3 admission/handoff, M4 route strategy, M5 batching/stall work; second-batch acceptance afterward.
3. Current desktop MCP catalog loading/tool use, user-operated GUI/visual acceptance and any production installation/migration remain separate, unperformed steps. No automatic production installation was approved.
4. Before/after36-run measurement, Antigravity comparison, native token/host-cost metrics are unperformed/unavailable; no performance or native-equivalence claim.

### Repository state
Work retained on `codex/native-gap-next` in the existing checkout, without commit, merge, push or Release. Original external plan was not overwritten. Test helpers/evidence paths remain available for the next milestone; no agents or new scheduler were installed into the production runtime.

### Final independent M1 verdict
Same reviewer completed final evidence readback: Stage1 PASS / Stage2 PASS for M1 source plus the explicitly bounded isolated-package/native acceptance. It independently checked final installed16 files, wheel/ZIP hashes, actual readonly DB, six distinct completed/confirmed transfers (one claim/begin each), original failure/rework/verification records, preserved restart fence/reconciliation, schema5 retention and append-only evidence correction. GUI, refreshed desktop catalog, formal installation and full first-batch acceptance remain open.

## 2026-11-17 M2 continuation
- User requested continuing approved plan. Resume M2; do not repeat completed M0/M1/R1.
- Reuse active branch/checkout; pre-M2 working-tree snapshot retained at C:\Users\user\AppData\Local\Temp\tri-m2-start-iyk3obbl.
- Ruling: first-batch version remains software0.4.0/schema6; freeze new mandatory verification contract as workflow3, preserving workflow0/1/2. M2 manifest/receipt interface will be recorded before implementation.
- M2 scope: CLI collector/process ownership, frozen verification inputs and gate rechecks, API/catalog/skills/roles/packaging and bounded evidence presentation; independent review and isolated package/native fixture follow. No production installation or GUI automation.

### M2 in-progress evidence
- Pre-M2 strict baseline: 522 passed in75.64s; working-tree snapshot and raw log retained under m2/.
- Frozen interface: m2-interface.md. Native Windows Job research includes real ownership/termination probe; no production process touched.
- Scope separation: runner/process files, single shared-runtime integrator, single skill/role owner, single UI owner. Root owns metadata/upgrade and acceptance. Independent review queued against pre-M2 snapshot, not mixed prior M1 diff.
- Package/upgrade checks:2 expected RED, then10 PASS. Shared contracts:10 expected RED, then39 PASS. UI contract suite:113 PASS; no GUI operation.
- Desktop current tool catalog still exposes artifact_capture without verification_commands and verification_record without receipt_files; native_action_update is absent. Do not count SDK acceptance as refreshed desktop-catalog acceptance.
- Ruling: env_keys is an explicit child-environment allowlist, values remain at caller terminal and are never serialized by collector. Raw command output is preserved; commands that print secrets can place those bytes in logs. This is not an output-secret scrubber.

## 2026-11-17 M2 current handoff

### Completed implementation and executed evidence
- M2 local collector/workflow3 integration, shared MCP/HTTP schemas, six roles/three skills/inline contracts, bounded UI, package entry point and activation capability are synchronized. Software0.4.0/schema6 remain frozen; workflow0/1/2 are preserved.
- Root final full strict regression after all runtime/contract changes: **593 passed,1 POSIX-only skipped in79.60s**, `m2/final-with-contracts-pytest.log`. Node syntax and final whitespace checks pass.
- Actual installed/native Boost chain closed: initial helper environment omission caused genuine setup exit3 (no business verdict); original verifier stopped. Explicit fresh frozen review then produced actual2failed→original-author attempt1 repair→same-verifier candidate2passed→separate integrated2passed. Old failure artifacts/receipt retained;6 ledger-controlled native deliveries confirmed, old failed delivery explicitly disposed, no unresolved replay. One genuine coordinator setup repair is recorded, not hidden as zero rescue.
- Final Chinese/space-path package independently installed;36 source/package files and18 site-package runtime/web files read back. Final repackage after native run added only10 skill/role allowlist clarifications; runtime bytes unchanged. Controlled prepare/apply/verify preserved all new records; active-run preparation rejected. Only verified owned fixture daemons stopped; none left running.
- Independent schema5→6 migration preserved9 old contract/mode combinations and6 historical FAIL evidence chains; new runs default3. Old code refusedschema6 without row or main-DB hash mutation. M1 original database used only read-only SQLite backup; all15 tables of its copy preserved.
- Source review fix round1: original authors repaired frozen-cwd executable search, partial-output directory rejection, receipt time ordering and scope-preflight before SQLite SHM scan. Independent raw failing runs retained, followed by179 Windows-targeted passes,15 adversarial probes,26 compatibility passes and11 final-contract passes. Final independent verdict is owned by m2-review.md.
- Nonblocking minor: Windows PATH/Path aliases are distinct literal manifest strings but refer to identical current values; independent subprocess probe found one effective child PATH and no behavior ambiguity. No speculative API expansion.
- Reports: [M2 overview](m2-report.md), [native/package](m2-native-report.md), [independent review](m2-review.md), [upgrade](m2-upgrade-report.md). Reproducibility/evidence index: `m2/evidence-index.json`. Retained reviewed wheel/ZIP: `dist/m2/`. Raw logs and artifacts remain local/ignored.

### Unfinished queue at M2 handoff (historical)
1. **First-batch full acceptance remains incomplete.** Current desktop catalog lacks the new parameters/tool; SDK success is not desktop-catalog success. User-operated GUI acceptance and the complete batch-wide cross-mode native/exception matrix remain open. Formal plugin activation/production migration are separately approved operations, not implied by continuing development.
2. After first-batch acceptance: M3 admission/handoff → M4 strategy → M5 batching/stall → second-batch acceptance. Do not bypass the documented batch gate or label M1/M2 bounded evidence full batch acceptance.
3. Non-Windows runtime, restrictive nested Job configurations/startup-kill stress,36-run comparisons, Antigravity and native token/host-cost metrics remain unperformed/unavailable. Local JSON/SHA/checkpoints are not host authentication or protection against temporary mutation/restoration or same-user recomputation.
4. Branch remains `codex/native-gap-next`, uncommitted. No merge/push/Release, production config/cache change or GUI automation. Original external plan and M0/M1/R1 records preserved.

### Final independent M2 verdict
Same independent reviewer returned Stage1 PASS / Stage2 PASS for the stated Windows source/runtime, isolated installation and original-agent repair chain. It independently rehashed15 registered evidence entries, verified6 confirmed actions each with claim/begin/record,5 completed+1 explicitly failed inbox deliveries,36 final package files and18 installed runtime/web files. The initial setup exit3 was not a business verification. All36 reviewed product/test/config files match its frozen hashes; coordinator README is the additional37th changed non-report file. Full batch1, desktop catalog, GUI and production installation remain open.

## 2026-11-17 batch1 closure authorization and scope
- User explicitly requested completing the previously outstanding production installation, desktop catalog, GUI and first-batch acceptance, then stopping before M3. This supersedes only the earlier absence of installation/GUI authorization; it does not authorize M3, a public Release, push, extra features, or destructive data rollback.
- Root owns production cutover and browser acceptance. Preflight is read-only; a separate author prepares a one-shot040 controller and an independent reviewer audits it before execution. Native fixture author owns only new isolated fixture scripts/data; real host dispatch stays with root and the original verifier.
- Current production preflight:0.3.0/schema5 PID25084,8 historic runs,0active/0pending controls/0inbox. Recheck under maintenance ownership immediately before changes. Current config hash (not old history) and all old cached runtimes must survive.
- Source plugin/.venv is present. Root will move only that checked, unused directory to a retained sibling backup before official CLI installation, then restore it; controller rejects an unhandled source runtime. No manual overwrite of managed0.3 cache with0.4 bytes.
- Native new-parameter/write-loop, three-mode browser acceptance, remaining Solo/Teamwork/Swarm and exception fixtures are tracked in batch1-matrix.md. SDK is not desktop catalog proof. No blanket performance/equivalence claims;36-run comparisons remain outside this closeout.


## 2026-11-17 batch1 executed checkpoint — stop before M3
- User approved retaining exactly one labeled production installation-acceptance run; it has not been created yet because actual desktop tool schemas remain stale. Existing8 production runs are unchanged.
- Production official0.4.0/schema6 activation and independent retention checks passed; source .venv restored. Browser three-mode and isolated native/negative/control/transport acceptance executed. Fresh full regression593passed/1POSIX skip in78.77s; Node/diff checks pass.
- Solo/Teamwork/Swarm are completed; cap deliberately failed after four actual verdicts and refused repair4. Final15verification rows/25confirmed actions/25completed inbox deliveries; helper and foreground-session rescues explicitly retained.
- See [current closeout checkpoint](batch1-report.md), [independent review](batch1-review.md), [GUI](batch1-gui-report.md), [control](batch1-control-report.md), [metrics](batch1-metrics-report.md). Earlier uninstalled/unverified statements above are historical checkpoints, not the current installation state.
- Overall first-batch closure remains pending actual desktop28-tool refresh and authorized one-run new-parameter write/read/archive. Restart desktop then resume this chat for that bounded step; do not repeat activation or enterM3. No commit/push/Release.

- Final cleanup: verified temporary1397/1402 daemons stopped, DB/evidence retained; formal18790/PID21292 remains healthy. See batch1/owned-daemon-closeout.json.


## 2026-11-17 requested lifecycle repair and sustained closure
- User explicitly requested resolving the previously retained Windows SDK short-session lifetime defect and missing sustained validation. No M3 authorization.
- Minimal Windows WMI startup path removes the job-inheriting retry; package dependency metadata is explicit. Real old3FAIL→final focused13PASS; final full600PASS/1POSIXskip98.76s. Old failed test-harness runs remain preserved.
- Actual desktop28-tool/new-parameter/native-call loop completed in one approved production run, independently candidate/integrated verified and archived. Original8 runs unchanged.
- Frozen37-file installed runtime ran1800.157s /259rounds/2852RPC/1295writes without duplicates or daemon replacement; resource ceilings passed. Independent audit confirmed all samples, DB and hashes.
- Official same0.4 reinstall initially failed on real cache cwd lock; exact shim cleanup then hit a parent-exit observation race. Both failures retained. Reviewed no-termination forward resume completed official installation, locked runtime rebuild and patched WMI launch. Formal18790/PID31832 healthy; schema6, nine closed runs, wholeDB/config/roles preserved; no rollback. Source .venv restored.
- Final actual installed-cache smoke60.14s/102RPC passed in an isolated database, cleaned by retained handle. Post-install desktop call honestly returned Transport closed after old shim shutdown: host restart/reconnection remains the sole local activation action, not a new code or M3 task. Source/runtime/SDK evidence does not fabricate desktop reconnection.
- Reports: lifecycle-report.md, batch1-desktop-report.md, lifecycle-fix/review.md. No public Release, push, commit, M3, cross-day or non-Windows claim.


## 2026-11-17 final desktop reconnection and plan synchronization

- User restarted Codex and requested completion confirmation plus reformatting/updating `C:/workspace/next-phase-plan.md`.
- Root actual desktop calls `run_list(limit=100)` and `board_status(run_ea663d60b66d)` succeeded. Actual catalog28; nine runs, archived installation task, step done/revision8, two confirmed actions, zero pending/reconciliation actions. Original raw responses are retained in `lifecycle-fix/desktop-reconnected-20261117.json`.
- This final step is read-only against production: no second installation task, replay, reinstall, source change or M3 work. Source/cache identity and old data preservation are independently rechecked.
- Updated the external plan with a current status table, completed-task archive, unchanged M3–M5 requirements, installation/acceptance rules and evidence index. Original M0–M2 specifications are preserved in `batch1-spec-report.md`; full preimages are retained under `docs/backups/20261117-plan-closeout/`.
- README and the current batch/lifecycle summaries now show the host gate closed; historical Transport closed evidence remains unchanged. Full test/soak results are retained prior executions, not claimed as rerun during documentation editing.

## 2026-11-17 second-batch development delivery

- User resumed M3–M5 from the updated plan. Existing dirty feature checkout retained; original files/plans backed up, shared state-machine ownership serialized.
- M3 atomic conservative scope admission, durable serial dependency proof/final original-verifier review, compact digest handoff, mapped review findings, explicit coordinator-local completion and single-use local revision proof implemented/reviewed. M4 all three skills/six roles/inline synchronized. M5 grouped per-message transfer and read-only stalled classification/transactional alerts implemented/reviewed. Shared initial health-bind startup race corrected inside the existing mutex.
- Final strict full suite722PASS/1POSIXskip; Node/diff checks exit0. Independent M3, M5 and root integration reviews; old0.4 API→final-wheel schema7 migration preserves all old rows/columns in15 tables and old code rejects future schema without mutation.
- Actual original native authors/verifier: five completed runs/seven done steps; six businessFAIL and fifteen freshPASS records, all32 inbox/native action rows completed/confirmed. Teamwork same-file merge and final original-A verification/handoff completed. Runtime uses isolated wheel/HTTP/collector, not the installed desktop catalog.
- Helper decoding/scope failures, two explicit isolated package forward updates and migration probe correction retained. Handoff read/ACK direct followups remain separately reported outside M1 ledger; no fake native-idle, synthetic PASS, unknown-outcome replay or replaced binding.
- Current detailed evidence, retained packages and remaining manual/installation/M0/real-host-exception gates: [batch2-report.md](batch2-report.md). No full second-batch completion or measured net benefit claim; no commit/merge/push/Release.
