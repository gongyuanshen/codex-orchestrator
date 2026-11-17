VERDICT: FAIL

# Batch 1 independent review — staged gates

**Overall batch: pending, not accepted.** The pre-execution controller gate below is **PASS** for the exact reviewed bytes. This authorizes the coordinator to execute the already user-approved maintenance sequence; it does not claim production activation, desktop catalog, GUI, or remaining native-matrix results. M3 is out of scope and must not start.

## Gate A — Controller pre-execution review: PASS

Reviewed controller SHA256: `b0d263ad9b0942750518f449eca6038fb16e3f3849f6a9166e07f8ec6f40357e`.

Dependencies read directly: retained `activate_030.py` low-level command/identity/snapshot helpers, `scripts/upgrade.py`, and `plugin/src/orchestrator/mcp_shim.py`. No old030 execute/resume/recovery flow is called. This reviewer did not edit author source/tests or execute production maintenance/GUI. Own probes used external temporary databases, sockets, lock files and owned children only; the provided migration test reads production through SQLite mode=ro and migrates its private backup, never production.

### Stage 1 — Spec compliance

| Criterion | Status / source |
| --- | --- |
| Exact accepted payload/version/schema and official local-marketplace identity | MET — `batch1/activate_040.py:41`, `batch1/activate_040.py:88`; 36 source/archive entries, fixed ZIP SHA, version0.4/schema6, registered native Codex executable/hash and official plugin/MCP selection. |
| Current config and exactly six managed roles, not historical config restore | MET — `batch1/activate_040.py:99`, `batch1/activate_040.py:127`, `batch1/activate_040.py:228`; preimage/hash checks, private current-byte copies and atomic six-role update. No config write/restore path. |
| Complete old caches including .venv; private DB backup and equality | MET — `batch1/activate_040.py:82`, `batch1/activate_040.py:127`; all four old versions copied, full runtime inventories compared, SQLite consistent backup compared. Bytecode retained but excluded from stable comparison. ACL is restricted before secrets are copied. |
| Shared startup lock + all-mode idle/control/inbox checks + writer reservation | MET — `batch1/activate_040.py:72`, `batch1/activate_040.py:182`; reuse exact shared lock, then BEGIN IMMEDIATE, reread unchanged production state before stop. |
| Stop only verified actual daemon owner, including PID-reuse safety | MET — `batch1/activate_040.py:274`; retain process handle, require live before/after final CIM creation/executable/command comparison, terminate that handle only. Real mismatch and same-image sentinel probes passed. |
| Keep exclusive port ownership through install/migration | MET — `batch1/activate_040.py:189`; exclusive bound non-listening socket; writer reservation released only after offline equality, while startup lock/socket stay held. |
| Official add; preserve CLI-pruned old clients even on CLI failure | MET — `batch1/activate_040.py:149`, `batch1/activate_040.py:199`; finally restoration restricted to approved backed-up old-cache files, per-file evidence and full inventory. No DB rollback, config restore, or deletion of unexpected extra files. |
| Installed environment/import identity; clean execution environment | MET — `batch1/activate_040.py:207`; locked installed uv sync/check and installed module/distribution/schema assertions. Reused environment removes PYTHONPATH/VIRTUAL_ENV/TRI_* before setting the intended production endpoint. The review process had no UV_PROJECT_ENVIRONMENT/UV_PROJECT/UV_WORKING_DIR/UV_PYTHON/PYTHONHOME overrides. |
| Old row/old-column projections retained; no production business fixture | MET — `batch1/activate_040.py:110`; exact old projections, row counts, eight historical workflow0 runs, schema history/integrity/FKs; new native_actions empty. Separately approved installation-probe task is not created by this controller. |
| Direct daemon launcher under startup lock, no recursive convenience-lock acquisition | MET — `batch1/activate_040.py:235`; close only own socket then direct installed `_spawn_detached_daemon`, verify health/CIM/owner/version/schema/new capabilities; recheck official selection and HTTP/static bytes. |
| Failure visible, checkpointed, no implicit resume/rollback | MET — `batch1/activate_040.py:303`; occupied target/backup/evidence refuses replay, command streams/checkpoints/fatal error retained. A partial change requires a separately reviewed forward recovery, never stale DB restoration. |
| Source development .venv managed by root, not hidden controller cleanup | MET — `batch1/activate_040.py:60`; blocked if development environment remains; known __pycache__/pyc excluded from accepted payload without automatic cleanup. Root's move receipt retained; restoration is a root-owned postcondition still pending. |
| File ownership and scope | MET — controller/test/report only; product runtime unchanged. No production or GUI action by this reviewer; no nested agents; no M3 work. |

### Stage 2 — Real independent verification

Exact command executed from `C:\workspace\codex-orchestrator`:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.venv\Scripts\python.exe -B -W error docs/verification/native-gap-next/batch1/test_activate_040.py
```

Exit **0**; **8 tests passed**, 2.155 s, no skips. Raw output: `batch1/independent-controller-tests.log`. Tests include real read-only production-to-private SQLite backup followed by actual schema5→6 migration of that private copy, old-row preservation guard, real owned child termination, full-runtime cache restoration/detection, atomic role bytes and source/archive matching.

Independent stdin probes additionally executed with exit **0** (`batch1/independent-controller-locks-r2.log`):

1. Actual BEGIN IMMEDIATE blocks another SQLite writer; no row changes.
2. Actual child process attempting the same temporary startup lock times out.
3. Exclusive bound non-listening temporary socket rejects a second binder.
4. Mismatched creation identity rejects termination and leaves both owned target and same-image sentinel alive; correct retained handle terminates only the owned target.

The first reviewer probe failed during temporary-directory cleanup because the probe used SQLite's transaction context manager without closing the connection. Its failure log is retained at `batch1/independent-controller-locks.log`. Correcting only the probe to explicitly close its temporary connections yielded the successful four-check result. No author assertion was weakened, and no product/controller fix was needed.

The old030 numeric-PID stop race was raised before review; the new040 retained-handle implementation removes that race. No unresolved P0/P1 defect was found in this bounded pre-execution controller review.

### Preconditions still enforced by the controller, not assumed from this review

The coordinator must use these exact reviewed bytes and permit the controller's fresh preflight/lock-held comparisons to fail if owner, source, config, roles, old caches, task/control/inbox state or destinations drift. This review is not a maintenance lease. Failed execution must retain its state and evidence; it must not be blindly rerun. Root restores its displaced source `.venv` separately after installation.

## Gate B — Production / desktop / GUI / complete batch: NOT YET ACCEPTED

MET — production installation/readback independently verified below; desktop/GUI/batch closure remains pending.
GAP — actual desktop new-schema claim/begin/record/readback and original-chat next-turn catalog not yet established.
GAP — Boost Solo, Teamwork dependency/designated handoff, Swarm parallel/rework/cap, current-byte native exception matrix and transport acceptance not yet reviewed.
GAP — rendered three-route GUI, narrow/light/dark/keyboard/live-region checks not yet reviewed.
MET — installation-time historical rows/current config/six roles/old runtimes and root source `.venv` restoration verified. GAP — separately authorized installation-probe task has not yet been observed/archived; final batch/no-M3 closure remains pending.

The batch1-matrix remains the acceptance checklist. Existing bounded M1/M2 evidence may be reused only with matching installed bytes and its original limitations. No overall PASS until every required row has real evidence.

Untested: actual maintenance execution and abrupt controller death during inherited suspended-create/assign command startup; the review does not certify every kernel interruption interleaving.
Risk: a mid-maintenance failure can leave service unavailable or a partially activated selection; checkpoints preserve that fact, and no stale database rollback is authorized.


## Gate B1 — Executed production installation: PASS (readback scope only)

The coordinator executed the reviewed controller; `batch1/deployment/checkpoint-finished.json` and `final-readback.json` retain its actual successful activation. This reviewer then performed read-only production SQL/file/HTTP/CIM/official-CLI queries and an installed-interpreter import; no business writes, service stop/start, role/cache/config writes or GUI operation.

Independent assertions, all exit0:

- Health0.4.0/schema6 with all six expected capabilities. Actual PID **21292**, listening owner, daemon.pid.json and CIM executable/command/creation timestamp agree with installed-owner evidence. Private backup ACL is protected, with a single explicit Administrator full-control entry. `batch1/review/production-owner-acl.json`.
- All **8 historical workflow0 runs** unchanged; all old-row/old-column hashes across **14 existing tables** match the pre-install snapshot. Only the expected schema_migrations row was added; native_actions remains empty. No installation-probe run existed at this observation. Integrity/FKs clean. `batch1/review/production-readback.json`.
- The actual private `state-before-migration.db` is schema5 and independently matches all14 baseline table hashes/counts, not just a copied controller claim.
- Current config exactly matches its pre-install hash and private backup. All six global roles exactly match accepted0.4 source/target; their backed-up old bytes match preimages.
- Full old cache and backup inventories independently rehashed: **0.1.0=1774; 0.1.2=1779; 0.2.0=1783; 0.3.0=1783** stable files, including .venv. Every accepted **36-file** new payload matches source and installed target.
- Installed `.venv/Scripts/python.exe -B -I -c` imports the installed0.4 source path, distribution0.4 and schema6, with inherited PYTHONPATH/VIRTUAL_ENV/TRI_* removed. It does not import the development checkout.
- Official native CLI executable hash rechecked. Actual `plugin list --marketplace tri-orchestrator --json` selects enabled/installed0.4; actual `mcp list --json` points cwd and PLUGIN_ROOT at cache0.4. `batch1/review/official-selection.json`. This is registration evidence, not proof the current desktop context refreshed its tool schema.
- Root's `source-runtime-restored.json` read; actual source `.venv` exists and temporary move destination no longer exists. This reviewer did not restore or alter it.

One reviewer CIM comparison initially compared a formatted timestamp string to PowerShell ConvertFrom-Json's automatically parsed DateTime. It rejected equal instants with different textual precision. The read-only probe was corrected to compare UTC DateTime values while still requiring exact executable/command identity; the corrected check passes. No production state or controller code changed.

## Gate B2 — Actual desktop catalog: FAIL / still stale

In this new native verifier turn, `functions.ALL_TOOLS` exposes exactly **27** tri tools. `native_action_update` is absent. The actual `artifact_capture` declaration contains only paths/scope/step_id, not verification_commands. The actual `verification_record` declaration has no receipt_files. Original full tool metadata/declarations retained at `batch1/review/desktop-catalog-native-turn.json`.

This was actual host-provided tool metadata, **not** SDK tools/list or HTTP catalog. Official registration and healthy0.4 backend do not satisfy the desktop-refresh requirement. No substitute SDK write was made by this reviewer, and no production probe run was created.

Overall batch verdict remains FAIL/pending until the actual desktop write/read loop, authorized single probe/archive audit, GUI and remaining matrix evidence are independently established. Stop before M3.


## Gate B3a — Native acceptance helper rescue: PASS (infrastructure only)

Read-only review of `batch1/native/fixture.py:52` dispatch expression against `plugin/src/orchestrator/inbox.py:180-185`: an author repair with attempt > 0 uses rework, while initial author dispatch uses task; candidate review and handoff remain review/notice. Existing runtime correctly rejected the old helper's task delivery during implementing. Original evidence retained at `C:/Users/user/AppData/Local/Temp/tri-batch1-中文 space-v92sdlfs/evidence/rpc-07a524106dfc4335a713503926087844.json`: actual inbox_send error409 for run_42fd48a3ae35 / step_3728b987d3684e59 attempt1. This is an acceptance-infrastructure rescue, not a product defect, and no runtime/test-source change was required.

Independent command `.venv/Scripts/python.exe -B -W error docs/verification/native-gap-next/batch1/native/selfcheck.py` exited0. Frozen fixture test hashes and syntax, explicit begin/submit lifecycle checks and the five-case AST/eval dispatch matrix passed. Output: `batch1/review/native-helper-selfcheck.log`. These are offline checks only; they do not prove later native repair execution or complete the batch. No production write, RPC, GUI operation or M3 work was performed by this reviewer.


## Final checkpoint audit — 2026-11-17 (supersedes earlier pending observations, not failure history)

**VERDICT: FAIL for whole-batch completion.** The current limited progress statement in `batch1-report.md:3` is supported: installed, bounded native/GUI acceptance executed, actual desktop refresh/write loop pending. No newly identified product P0/P1 defect prevents that limited statement. This is not an all-tests/all-platforms/whole-batch PASS. M3 remains unstarted.

### Stage 1 — criterion-by-criterion current assessment

| Criterion | Assessment and precise evidence boundary |
|---|---|
| Production old data/config and deployed payload | MET again — independent `batch1/review/final_audit.py:47` onward invokes only reviewed read-only preservation checks; all eight old runs and all old-row projections/counts remain, schema6/native_actions0, config unchanged. All36 manifest entries independently match source, production cache0.4 and isolated installed package. Earlier six-role/7119 old-cache verification remains installation-time evidence, not newly rerun here. |
| Boost Solo / Teamwork / Swarm | MET for bounded isolated cases — actual readonly DB has three completed runs; Solo owner remains /root, all original A/B/verifier bindings retained; six frozen tests match. Fifteen immutable records are10PASS/5FAIL. Teamwork dependency rejection409 and designated handoff records retained. Swarm A/B actual active markers overlap from09:01:15.4567459Z through09:01:18.7865849Z; original A repair1/same verifier preserved. `batch1/native/fixture.py:52`; `batch1/review/final-independent-audit.json`. |
| Repair cap / failed business result retained | MET — cap is failed with needs_decision/attempt3/revision17, four actual FAIL records0/1/2/3. `negative-cap-repair4.json` is real409 with expected_revision17; readonly final state remains17. No failed attempt relabeling. |
| Evidence tamper and stale attempt rejection | MET — actual negative-old-receipt400 manifest identity mismatch and negative-log-digest400 output digest/size mismatch; all75 currently registered evidence files across15 records independently rehashed. Receipt manifest digest, artifact/attempt binding and actual exit-code consistency checked; no false PASS from either rejection. |
| Native transfer accounting | MET within local receipt trust —25 confirmed action evidence digests and25 completed inbox messages, zero duplicate persisted source/action groups; no remaining normal-fixture inbox. Local receipts are not authenticated host telemetry. Cap1 original completed inbox and actual-call evidence reconcile the same action after restart fence, not a new native replay. |
| Stop / invalid / restart matrix | MET within observed boundary — independent readonly action histories retain stop-race needs_reconciliation with pending-control source race, separate confirmed interrupt, actual failed missing binding; second restart action has invoking→fence(reason=restart)→explicit reconciliation, first retains lease_expired. All available control action evidence digests rehashed. Pending control-only probe actions remain uncertain in isolated exception DB by design, not clean business completion. `batch1-control-report.md:104`. |
| Transport | MET for raw singleton/large response only — actual retained installed singleton output1pass; real10,800,461-byte result independently rehashed,438-byte frame and20 subsequent calls with samePID recorded. SDK short-session daemon persistence remains a real failure, not superseded by raw singleton. `batch1-control-report.md:20`. |
| Rendered GUI | MET for reported bounded browser interactions, independently inspected saved evidence — three routes/DOM,390px light/dark focus measurement and long content widths, uncertainty screenshot with correct no-replay/host distinction. Reviewer viewed swarm-narrow-dark.jpg, long-message-narrow.jpg and swarm-uncertain.jpg directly. Actual browser actions belong to root; reviewer did not drive GUI. Keyboard/live completion observations are bounded, not exhaustive assistive-technology or SSE-reconnect acceptance. `batch1-gui-report.md:9-28`. |
| Desktop new schema / one installation run / whole batch | GAP — actual27-tool catalog stale, no new parameters or native_action_update; approved production installation-verification run not yet created. Existing8 remain untouched. Actual old desktop run_list succeeds but is read-only compatibility evidence, not new-schema acceptance. `batch1-report.md:21`. |

### Stage 2 — executed checks and findings

Executed from repository root:

```powershell
$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -B -W error docs/verification/native-gap-next/batch1/review/final_audit.py
```

Exit0, assertions and full readonly rows/digests saved in `batch1/review/final-independent-audit.json`; output `final-audit-r2.log`. No daemon start, SDK session, RPC write, production mutation or product/test edit. Initial reviewer probe incorrectly assumed receipt held attempt directly instead of its digest-bound manifest; KeyError retained in `final-audit.log`, corrected only own probe to validate receipt→manifest→record identity. No product failure was hidden.

Root's fresh regression log was read directly:593passed/1skipped in78.77s. It is supporting root-run evidence, not claimed as an independently rerun suite or no-skip result. Existing bounded M2 source review remains applicable because deployed/source payload hashes match.

- **[P1 acceptance gap, not new product defect]** Actual desktop new-tool/new-parameter write/read loop and authorized single installation-run/archive audit unexecuted (`batch1-report.md:21`). This blocks whole-batch PASS. Resume after actual host refresh; do not reinstall or recreate completed native fixtures to substitute SDK evidence.
- **[P2 operational boundary]** Real short-lived Windows SDK session job lifetime can kill its auto-spawned daemon (`batch1-control-report.md:20`). Do not claim arbitrary SDK-session persistence or all lifecycle cases passed. No new workaround/product changes authorized in this review.
- **Unresolved cause, explicitly retained:** disappearance of original1397 foreground session/PID34316 is not explained by available evidence. Cap1 restart fence/reconciliation recovery is supported; attributing disappearance to the separate SDK failure is not. This blocks a reliability/root-cause-complete claim, not the narrower retained-state/reconciliation result.

Untested/caveats: desktop refreshed schema/new write path; single production probe/archive; POSIX skip; arbitrary host/job lifetimes; long-duration stress; exhaustive browser/breakpoint/tab order/screen reader announcement/SSE reconnect; native token/host-cost metrics. The reviewer made no cleanup claims before parent verifies owned daemon shutdown. The current root closeout report honestly preserves these limits; no additional blocker to its limited progress statement was found.
