# Batch 1 metrics / closure inventory — final business snapshot; overall gate pending

Observation: 2026-11-17T09:08:25.694176+00:00 (17:08:25 Asia/Singapore). This is a read-only checkpoint, not overall acceptance. Root is still executing Teamwork/Swarm/cap and owns final closure. No product source, production DB, fixture or binding was changed by this metrics worker.

## Actual correctness and state

`batch1/metrics/snapshot-in-progress.json` is a consistent SQLite read transaction from the isolated current-byte fixture at `C:/Users/Administrator/AppData/Local/Temp/tri-batch1-中文 space-v92sdlfs/data/state.db`. Four workflow3 runs remain active; case closure must not be inferred from a completed step.

| Case | Observed business state | Actual verification evidence at checkpoint |
|---|---|---|
| Boost Solo | normalize done, attempt0; run active | Independent candidate and separate integrated records retained; root implementation is not a fictional native self-transfer. |
| Teamwork | parse done; sum handoff; attempt0; run active | Parse/sum actual receipts and designated handoff files retained; final run closure not yet established. |
| Swarm | parity done attempt0; label integrated_review attempt1; run active | Genuine label candidate failure followed by same-owner repair and candidate PASS; integrated/closure still in progress at checkpoint. |
| Retry cap | answer candidate_review attempt0; run active | Actual seeded author failure log exists; exhausted-cap outcome not yet established. |

11 immutable verification rows were present. All six frozen test hashes still match config. Original owner/verifier IDs, actual attempts and verdict rows are recorded in the snapshot. No token/host-cost or before/after performance metric is available, and none is fabricated.

## Defects, rework, rescue and duplicate effects

- Product changes in this batch: none reported by coordinator; this worker does not relabel fixture-helper fixes as product fixes.
- One runtime acceptance-helper repair: dispatch of author attempt>0 used task instead of required rework kind. Original helper/selfcheck preimages retained; external `helper-rework-red.log` and `helper-rework-green.log` establish RED→GREEN. Count as one helper/coordinator rescue, not zero-rescue execution and not a business retry reset.
- Intentional business rework: Swarm label attempt1 at this checkpoint. Parse/sum/Solo author RED→GREEN logs are implementation checks, not independent FAIL records or automatic business attempts. Retry cap is unfinished and must be counted from immutable final records later.
- Duplicate native source/action groups:0 in current fixture SQL grouping by run/source type/source ID/recipient/action type. This proves uniqueness of persisted action identities only, not arbitrary external exactly-once semantics.
- Main native inbox:18 completed,1 pending; pending controls0. These are in-progress counts, not unresolved failures or a clean final inbox claim.
- Control restart fixture: report and `restart2/db-readonly-audit.json` establish one persisted action and one actual recipient side-effect append for the tested source, explicit receipt reconciliation and no replay across five reads. The first uncertainty fixture also wrote one effect but expired its60s lease before restart: it is expiry evidence, not restart evidence. Separate second fresh action verified invoking+live lease immediately before actual restart and recorded reason=restart. Neither is host authentication.

## Control/setup limitations — distinct, not erased

1. Actual short-session Windows MCP SDK lifecycle failure: SDK job closure can terminate the auto-started daemon; concurrent peers observed ConnectError. The unchanged installed raw-stdio five-shim singleton test passed, but does not erase SDK lifetime failure. This is a material host/SDK compatibility boundary; no silent product fallback was added.
2. First restart scenario crossed the60s native lease before restart. It remains a failed setup for the intended restart claim; a fresh second context produced genuine restart-fence evidence. Do not count the first as restart PASS.
3. Other setup-only failures are preserved in their owning reports: relative package/config cwd startup attempts; controller isolated ZIP-path/prefix and temporary SQLite-handle fixes; independent probe connection cleanup and timestamp comparison fixes. These are not all the single helper dispatch rescue, and are not business verification failures. No zero-intervention claim is justified.
4. Stop-before-begin proves pending control blocks begin. The control worker did not execute actual native interrupt or claim worker termination; root-owned stop/interrupt closure must be assessed separately.

## Installation, desktop and GUI state boundaries

Independent production review GateB1 records installed0.4/schema6, preserved8 historical workflow0 runs/14 old-table projections, current config,6 roles,4 old runtimes and36 payload files. Installation success does not imply desktop schema or GUI acceptance. At this checkpoint the retained review's GateB2 reports27-tool stale catalog; newer root/new-context evidence may supersede it only with actual metadata/call receipts. GUI worker reported unavailable browser binding and no rendered assertions; parent owns remaining GUI. These historical pending records must not be treated as current final FAIL/PASS without later readback.

## Primary sources

- `batch1/native/config.json`, `context.json`; external fixture evidence directory and immutable database snapshot.
- `batch1-control-report.md`: real SDK failure, raw singleton, stop admission, first expiry vs second restart, large actual10,800,461-byte result and20 subsequent calls.
- `batch1-review.md`: independent installer and production readback; actual desktop catalog limitation at review time.
- `batch1/deployment/final-readback.json`; `batch1/review/production-readback.json`; `batch1-fixtures-report.md`; `batch1-gui-report.md`.

The earlier section is retained as the explicitly timed in-progress observation. The final readback below supersedes only its state/counts, not its failure history.


## Final read-only business snapshot — 2026-11-17T09:20:35.302734+00:00

Consistent isolated SQLite evidence: `batch1/metrics/snapshot-final.json`. Earlier state retained separately as `snapshot-in-progress-retained.json`.

| Case | Final actual state | Correctness / rework accounting |
|---|---|---|
| Boost Solo | run completed; normalize done attempt0 | Candidate PASS and separate integrated PASS. |
| Teamwork | run completed; parse and sum done attempt0 | Each candidate/integrated PASS; dependency/designated handoff preserved. |
| Swarm | run completed; parity done attempt0, label done attempt1 | Label genuine initial FAIL, original-owner one repair, same-verifier candidate/integrated PASS. Parity candidate/integrated PASS. |
| Retry cap | run failed; answer needs_decision attempt3 | Four genuine independent FAIL records at attempts0/1/2/3. Expected exhausted-cap business failure is not rewritten to success; coordinator reports fourth repair409 unchanged. |

- **15 immutable verification records:10 PASS,5 FAIL.** Five intentional failure observations are label initial failure plus cap four failures, not five product defects.
- **25 native actions, all confirmed;25 inbox messages, all completed;0 pending controls;0 duplicate persisted native-source/action groups.** No pending/accepted inbox remains in this isolated fixture. This is not a universal exactly-once guarantee.
- All six frozen test hashes still match. Original owner/verifier bindings remain explicit in final snapshot. Business retry attempts are label1 plus cap3; author implementation RED→GREEN checks are not additional business retries.
- Helper runtime repair count remains1 (dispatch repair kind). **Additional actual coordinator infrastructure rescue occurred**: original native1397 foreground session75387/PID34316 disappeared and no listener remained; SDK self-start fenced cap attempt1 author action while invoking. Root retained `rpc-a80408bfe81b4bc2873aff7e626e2c6b.json` and readonly action history (`reason=restart`), explicitly started owned foreground session98029/PID21532, then reconciled the original author's completed inbox and actual host receipt. No native followup was replayed and no attempt reset. Primary evidence: `cap-a1-native-action-readonly.json`, `cap-a1-actual-recipient-and-host-reconciliation.json`, `cap-a1-explicit-reconciled.json`. The evidence does not independently establish why the original foreground session disappeared; do not infer a cause from the separate SDK job-lifetime finding.
- Coordinator added one manual long-text board post, #97, to the isolated Solo case for rendered GUI testing. It was not forwarded to agents and is not a business instruction, verification failure, duplicate effect or token metric.

### Production final readonly scope

`batch1/metrics/production-final-readonly.json` independently confirms exactly the original8 workflow0 runs (7completed/1archived),0 native_actions and0 inbox messages. **No installation acceptance/probe run was created in production.** Actual fresh desktop-catalog/probe acceptance is deferred until desktop restart/new catalog; SDK/backend success is not substituted for it.

This snapshot closes the isolated business-state accounting only. It does not promote the overall batch gate to PASS, supersede a remaining desktop catalog gap or invent GUI assertions. M3 was not entered. Host tokens/cost, comparative throughput, long-duration stress and arbitrary SDK-lifetime persistence remain unavailable/unverified; no zero-rescue claim.
