# Batch1 control / restart / transport evidence — 2026-11-17

## Scope and actual result

Installed software0.4.0/schema6, existing M2 package/venv reused. No product source, native fixture helpers/config/projects/live runs, deployment controller, production18790 or global config was changed. No M3 work. All new helpers are in `batch1/control/`; config root is `C:\Users\Administrator\AppData\Local\Temp\tri-control-中文 space-mx86d0gc`, with project/data/evidence separated, loopback1399 only. All evidence below is under that external root's `evidence/` unless stated otherwise.

| Check | Actual result | Exact primary evidence |
|---|---|---|
| Stop after claim, before begin | PASS for admission blocking: actual HTTP stop request202, begin409 `Recipient has pending control`, original inbox no longer projected | stop-request.json, stop-begin-rejected.json, stop-readback.json |
| Raw stdio singleton | PASS: unchanged existing five-concurrent-shim test, installed Python `-I`, one daemon, unique/pure tool catalog, successful run creation | singleton-installed-pytest.log: 1 passed in6.14s; harness-selfcheck.json |
| SDK short-session auto-daemon lifetime | Observed FAIL, retained: one short SDK session closes Windows job containing auto-spawned daemon, concurrent peers get ConnectError | rpc-0ffbdb1d72ec4636ac8163cb0178644c.json, data/shim.log, sdk-job-source.json |
| Actual restart before native record | PASS on second isolated action: original invoking with live lease→daemon restart→needs_reconciliation, history reason=restart | restart2/before-restart.json, actual-restart-history.json, restart-reconciled.json, db-readonly-audit.json |
| Stale revision and stale token | Both rejected: old revision→`Stale native action revision`; current revision + old token→`Stale native claim token` | restart2/stale-record-rejected.json, stale-token-current-revision-rejected.json |
| No automatic replay / unknown | Reclaim409, unknown remains needs_reconciliation; five board readbacks yield no duplicate projection | restart2/replay-claim-rejected.json, restart-reconciled.json |
| Real native receipt reconcile | Actual root followup delivered exact message to original `/root/batch1_fixtures`; one real append, atomic recipient receipt; explicit reconcile confirmed; same source has exactly one ledger action | restart2/native-side-effects.jsonl, native-recipient-receipt.json, db-readonly-audit.json; root separately saves original host result |
| Large actual backend result | PASS: real board_post→real board_read file delivery10,800,461bytes, exact SHA256; frame text438bytes;20 followup run_list calls; daemon PID unchanged31300 | large-post.json, large-result.json, large-execution.log; data/results/63b798bdc350471d9fecb8a31452b87d.json |

Large result SHA256: `cff9b5ea7b848095ba627f619b58e8f247c89e5f2989b669acdbbd156ad8b336`. This is a real current backend payload, not a mocked HTTP server or historical16MiB claim.

## Singleton / SDK failure investigation

First `control.py prepare` concurrently opened five actual SDK stdio sessions against absent same-data1399 daemon. Tool catalogs initialized, then one run_create returned connection failure. Daemon log shows auto-start attempts at16:58:33 and16:58:35; no listener survived. The error RPC and shim stack remain unchanged.

Inspection of installed MCP SDK documents the actual mechanism: `mcp/os/win32/utilities.py` uses JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE / process job assignment; `mcp/client/stdio.py` shutdown closes surviving Windows job members through close_process_job. Installed orchestrator daemon spawning attempts CREATE_BREAKAWAY_FROM_JOB then retries without breakaway when unavailable. A short SDK-owned shim's job closure can therefore kill the auto-started daemon. Environment was correct, with SYSTEMROOT retained,1399 supported, no PYTHONPATH present. This is a host/SDK lifecycle compatibility limit, not established prevention. No product change or silent recovery was added.

To distinguish transport singleton from that SDK ownership interaction, the existing `tests/test_shim_concurrency.py` was copied byte-for-byte to the external project and its raw-stdio five-shim test run with installed Python `-I`. SHA256: `44a131ea9bd62ec5ad5fffa2ac19d80251718ddefe79f01b6234da1bdd367eeb`; copy equality recorded in harness-selfcheck.json. It passed and cleaned only its own daemon. The raw test PASS does not erase the real SDK failure or establish arbitrary SDK short-session persistence.

```powershell
$c=Get-Content -Raw 'G:\工具\codex-orchestrator\docs\verification\native-gap-next\batch1\control\config.json'|ConvertFrom-Json
& $c.python -I -B -m pytest -q -W error -p no:cacheprovider "$($c.project)\test_installed_singleton.py::test_five_concurrent_shims_spawn_single_daemon_pure_stdout" --basetemp "$($c.root)\singleton pytest"
```

That exact command was executed once successfully. Do not rerun with the same basetemp if preserving that directory matters.

## Stop-before-begin boundary

The stop request remained pending. No native interrupt was invoked or falsely ACKed; worker process termination is NOT claimed. The proven guarantee is that pending control blocks a previously claimed inbox action at begin and suppresses its new delivery projection. No actual already-begun stop race or dead-agent invalidation was performed by this worker; root separately owns those real host calls.

## Two uncertainty attempts, preserved separately

First run `run_4276abb2c207`, message `msg_69cf72f0195048ce8b385d0a737e4bc6`, action `nat_c189d061c70e42849fe15501419c1755`: root delivered a true native followup and recipient wrote one side effect. However, native coordination exceeded the60s lease. Readonly audit found reason=lease_expired before restart. API checks and receipt reconciliation passed, but this is explicitly NOT restart-fence evidence. `first-lease-expired-audit.json` and first receipt are retained; no history was rewritten or relabeled.

Second run `run_ddb4b064b2c8`, message `msg_e1690411113a480dbc690cfd7f9e8922`, action `nat_6bfa4ffa1c6b4ed4b1eac5193b1a6826` used fresh context/evidence in `batch1/control/restart2/` and external `evidence/restart2/`. Root again performed actual followup to the same original recipient. That recipient created its distinct side-effect file exactly once and atomic receipt. No successful ledger record happened before restart.

The second helper asserted readonly DB status=invoking and lease_expires_at>current time immediately before terminating only verified owned daemon37680. It then actually started replacement27624 and asserted DB history's last fence reason=restart / previous_status=invoking. A projected needs_reconciliation row had revision3 and null lease. Old record and old token were both refused. Reclaim was refused; explicit unknown retained uncertain state. Only the exact real original recipient receipt was used to reconcile confirmed. Five readbacks yielded no replay; readonly DB count for source was1; side-effect file count remained1.

Restart output and strict assertions are in `restart2/control.py`, `restart2/restart-execution.log`. Host result JSON is root-owned and is not synthesized by these helpers. Local recipient receipt and SHA are evidence, not host-authentication/signature.

## Execution / cleanup

Actual command sequence: initial failing prepare; unchanged installed singleton test PASS; explicit owned foreground daemon1399; prepare-control; real stop-before-begin and invoking setup; actual root native followup; actual large backend check; first restart+receipt reconciliation; readonly discovery of lease_expired; distinct second context; explicit owned foreground daemon1399; prepare-control; immediate actual root followup; strict second restart; readonly final audit.

Two relative-path setup attempts failed before the retry used the correct cwd/absolute config; no hidden success or product edit resulted. Source/receipt assertions expose errors rather than silently retrying. Helpers never call native collaboration APIs.

Final Get-NetTCPConnection1399 returned no listener. Original explicitly owned daemon sessions37665 and7450 were terminated only by the verified exact owned-PID restart; replacement daemon exited with its SDK-owned job when the session closed. Singleton pytest cleaned its separately owned daemon. No process-name/tree sweep or production daemon stop occurred.

All new Python helper files were parsed successfully; diff whitespace check passed. Real SDK lifecycle failure remains a material boundary. Desktop native catalog, GUI, production migration, real interrupt race/dead target and long-duration stress are outside this report's established results.

## Additional real stop-race / invalid-target harness (prepared; root executes host calls)

A distinct external isolation root `C:\Users\Administrator\AppData\Local\Temp\tri-stop-invalid-中文 space-bvos6i6c`, own data/project/evidence and port1402 is configured in `batch1/control/races/config.json`. Foreground owned daemon session41661. Helpers did not call native host tools. Setup was run once: actual workflow3 packet1/attempt0/disjoint acceptance fixtures, original bindings retained. Race run `run_9f7a586b4520`, step `step_6381a8c6add84a88`, inbox `msg_c1229e07592f40caa756fe00cc0de133`, worker target `/root/batch1_control_target`; invalid run `run_34cccbb9c047`, step `step_ed78f4a7cbcd422c`, inbox `msg_fc1f8cc826d24011a8168338fad90235`, frozen missing target `/root/batch1_missing_target`. No claims were made during setup, so root chooses exact begin time rather than exhausting the60s lease while reading instructions.

```powershell
$h='G:\工具\codex-orchestrator\docs\verification\native-gap-next\batch1\control\races'
$c=Get-Content -Raw "$h\config.json"|ConvertFrom-Json
$py=$c.python
$ev=$c.evidence
& $py -B "$h\races.py" begin race
```

Root actually follows up `/root/batch1_control_target` to remain running awaiting interrupt, without writing/submitting any business implementation; save real native result as `$ev\race-host-followup.json`. Observe real agent running via native status and preserve observation rather than inferring host state from ledger worker status. Then immediately:

```powershell
& $py -B "$h\races.py" control-request race
& $py -B "$h\races.py" interrupt-begin race
```

Root actually interrupts the SAME original target and saves real interrupt result `$ev\race-host-interrupt.json`. Do not ACK if actual interrupt did not succeed. Preserve exact stop race ordering and complete within the action60s leases:

```powershell
& $py -B "$h\races.py" record race --proof "$ev\race-host-followup.json"
& $py -B "$h\races.py" interrupt-record race --proof "$ev\race-host-interrupt.json"
& $py -B "$h\races.py" ack-control race --proof "$ev\race-host-interrupt.json"
& $py -B "$h\races.py" check race
```

Delivery record must retain actual followup result but become needs_reconciliation due to stop-control race; interrupt action separately confirmed; only then control ACK applied. Check asserts worker stopped, original binding preserved, original inbox not projected; three real claims must reject `Recipient is stopped`. Do not send followup to revive stopped target. Root saves actual native status afterward to bound observed non-resumption; no infinite-time guarantee is implied. Actual outputs, not this preparation section, determine acceptance.

Invalid-target sequence:

```powershell
& $py -B "$h\races.py" begin invalid
```

Root actually calls followup against exactly `/root/batch1_missing_target`, retains true missing-agent error as `$ev\invalid-host-followup.json`; do NOT create or substitute a target. Then:

```powershell
& $py -B "$h\races.py" record invalid --proof "$ev\invalid-host-followup.json"
& $py -B "$h\races.py" check invalid
```

The invalid delivery is recorded outcome failed (no replay admission), original missing binding retained. No verifier/business PASS is created. Evidence and stopped state are separate from control endpoint availability or identity authentication. Root owns true host calls and JSON provenance. Existing first SDK failure and two setup path errors remain disclosed above; these additional fixtures do not erase them.

## Independent final read-only audit of actual race / invalid execution

Root subsequently executed the real native calls described above. This fixture worker independently read actual JSON evidence plus SQLite using `mode=ro`, without opening a shim session or restarting the daemon. Full audit: external races `evidence/independent-readonly-audit.json`.

- Race original delivery `nat_5eb6fce133c64800ae2553426ae548f4` retains actual followup result, status needs_reconciliation, outcome confirmed, and history source_race=`Recipient has pending control`. It is not falsely converted to clean confirmed delivery or instantaneous stop.
- Real interrupt evidence says previous_status=running for original `/root/batch1_control_target`. Separate interrupt ledger action is confirmed. Control `ctl_9e0afbe3245d` is applied; DB timestamps prove delivery-race record happened before control ACK (`1794906547.9974058 < 1794906550.9079475`). Worker is stopped, binding unchanged. All three real later claim attempts reject `Recipient is stopped`; final next_actions_count=0. Independent actual `collaboration.list_agents` observation showed this target still interrupted; no followup was issued to revive it. This is bounded observed non-resumption, not an unbounded guarantee.
- Invalid original `/root/batch1_missing_target` actually returned `live agent path ... not found`. Ledger `nat_465a2dae1def419288e8b6eba24ea8ee` is failed/outcome failed. Original missing binding is unchanged; no substitute agent, successful business verdict or automatic projection was introduced. Final next_actions_count=0.
- All recorded evidence_ref files match their original evidence_digest SHA256; each fixture source has exactly one inbox native action. These are local observed receipts, not authenticated host signatures.

Cleanup audit:1402 health connection was already refused, Get-NetTCPConnection had no listener, pidfile35256 referred to no extant Win32_Process, and owned exec session41661 returned Unknown process id. Therefore no kill or restart was issued; an already exited daemon was not resurrected merely to close it. Cause of that prior process exit was not established by this read-only audit. Evidence directory and state DB are preserved. No operation in this control subtask stopped/restarted1397 or production18790. Earlier control restarts targeted only health-verified1399 PIDs31300 and37680; unchanged singleton test targeted only its separately owned random-port PID. Root's separate cap action fence is not explained or repaired here, and no native resend was attempted by this worker.
