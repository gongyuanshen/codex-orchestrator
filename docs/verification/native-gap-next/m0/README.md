# M0 deterministic baseline

Run from the root of a Git checkout that contains the baseline commit below, with the development environment installed in `.venv`. These PowerShell commands use fresh directories outside the repository:

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$output = Join-Path ([IO.Path]::GetTempPath()) "tri-native-gap-m0-$([guid]::NewGuid())"
.venv/Scripts/python.exe docs/verification/native-gap-next/m0/run_baseline.py --output "$output-reproduce"
.venv/Scripts/python.exe -m pytest -q -W error tests/test_native_gap_baseline.py --basetemp "$output-unit" -p no:cacheprovider
```

The runner archives commit `9c29cdb308a92cfdbdf259289bdc284f160abdfd`, imports only its `plugin/src`, and runs its existing workflow/inbox tests and two owned recovery extensions in isolated temporary databases. Contract pytest explicitly uses the archived pyproject config so absolute extension-test paths cannot select the mutable checkout package. It never starts a daemon, invokes a native model, installs a package, or touches port 18790. No new state machine is implemented.

`cases.json` contains all six tasks, actual executable fault seeds and scripted repairs, original frozen business-test bytes and SHA256 provenance, fixed permissions, concurrency, and machine failure/success criteria. Business tests come from the previous modes-20261117 native fixture manifest; they are embedded here so old absolute temporary paths are not runtime dependencies. The original manifest/script files remain read-only.

Expected terminal output: six case lines, each `RED=N failed testcases; GREEN; contracts=N`. A red phase must exit 1 with an actual failed assertion and no collection errors; a green phase and baseline contract phase must exit 0 with no failures, errors, or skips. Any unexpected result stops the runner with its traceback. Reusing an output directory fails rather than erasing evidence. Full commands, stdout/stderr, JUnit XML, isolated state, archived source, source hashes, and measured phase timing remain in the external directory.

## Measurement boundary

These are deterministic fixture controls, not six autonomous native-model trials. Scripted repair is the experiment intervention, not a model diagnosis or native author rework. The unknown-root task deliberately withholds a cause in its task text, but this runner already knows the repair; autonomous diagnosis remains unmeasured. The stop/disconnect case combines a frozen no-execution business assertion with actual existing stopped-delivery and disconnect-cleanup checks plus an isolated real append-before-disconnect/reopen/reconciliation counter test; it does not create a native-model side effect.

Swarm business fixtures use two concurrent pytest subprocesses. Existing duplicate-send and competing-payload tests use 8 and 2 threads respectively; those legacy workflow1 fixtures are explicitly not workflow2 execution authentication. Teamwork sequencing uses the existing workflow2 helpers. An additional isolated test closes the database after handoff and before ACK, reopens it, rejects wrong-recipient/stale ACKs, restores the exact current artifact and dispatches the dependent milestone. No new orchestration state machine exists. The Swarm extension appends one actual message ID after accepted, disconnects before terminal ACK, reopens with requires_reconciliation, then explicitly stops and reconciles without execution replay. A duplicate same-action resend stays terminal and the file count remains 1. This fixture duplicate count 0 is not a native-model measurement. Other business phases are sequential with concurrency 1.

Metrics: `correct_fixture_completion` is the observed combined exit/count gate; `actual_failed_testcases` sums red JUnit failed testcases (neither executed assertion count nor unique defects); `scripted_fixture_rework_rounds=1` is the explicit controlled red-to-green source intervention; wall time is `time.monotonic` around each case including subprocess startup. `injected_fault` names the seed, not a newly discovered product defect. Native completion, native rework, human rescue, duplicate native side effects, unhandled native messages, empty model turns, tokens and host state are null/unavailable, not zero.

## Future comparison protocol

Keep this manifest, frozen tests, task text, permissions, source start, model and concurrency fixed per comparison. Run each of six cases three times per version (18 old + 18 new = 36 isolated runs). Report median and min/max per case, not P95 or percentage improvement. A single first run supplies no stability estimate. New source versions require a separately explicit baseline parameter/change and independent evidence; this M0 runner intentionally remains pinned to the old version. Do not mutate its pin and call that a repeated old run.

Native Codex and Antigravity must later execute the same case tasks with independently recorded tool receipts, model identity and token sources. Keep unavailable measurements null. If model equality cannot be established, label that confounder. No Antigravity results or native same-task winner is established here.
