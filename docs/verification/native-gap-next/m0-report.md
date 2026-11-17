# M0 report — deterministic reproducible baseline

## Status

Six reproducible deterministic fixture cases completed on frozen product commit `9c29cdb308a92cfdbdf259289bdc284f160abdfd`. Each produced a real business-test assertion failure, then passed the same frozen business tests after explicit source repair, and passed the reused workflow/inbox contract checks. This establishes a measurement harness, not native-model capability improvement.

Owned changes only: `docs/verification/native-gap-next/m0/{cases.json,run_baseline.py,README.md}` and `tests/test_native_gap_baseline.py`, plus this report. No product source, production installation, service, UI, existing mode evidence, or `report.md` was changed. Concurrent M1 files were not modified.

## Evidence and commands

- Final six-case baseline: `C:/Users/Administrator/AppData/Local/Temp/tri-native-gap-m0-run3/results.json` and per-case `.log` / JUnit `.xml` files beneath the same directory.
- Archived source: `C:/Users/Administrator/AppData/Local/Temp/tri-native-gap-m0-run3/source`; Python source hashes before/after are checked by the runner and recorded in results.
- Git archive SHA256: `0a099fd26fb7894a914fe3ff56605e6134e106f174794c33a3fd1a570571a04d`.
- Runner invocation: `.venv/Scripts/python.exe docs/verification/native-gap-next/m0/run_baseline.py --output C:/Users/Administrator/AppData/Local/Temp/tri-native-gap-m0-run3`.
- Unit command: `.venv/Scripts/python.exe -m pytest -q -s -W error -c C:/Users/Administrator/AppData/Local/Temp/tri-native-gap-m0-run3/source/pyproject.toml tests/test_native_gap_baseline.py --basetemp C:/Users/Administrator/AppData/Local/Temp/m0-final-unit-v2 -p no:cacheprovider` → **4 passed**.
- TDD red: `m0/tdd-red.log` → **2 actual failed assertions** (missing executable runner); green/final: `m0/tdd-green.log`, `m0/tdd-final-green.log`; recovery-final `m0/tdd-final-green-v2.log`.
- All pytest subprocesses use `-W error`, `-p no:cacheprovider`, external basetemp, the existing repository `.venv/Scripts/python.exe`, and pinned archived product imports. No dependencies were installed.

## Six case outcomes (one final complete pass; prior attempts retained)

| Case | Actual injected failure and success gate | Red failed testcases | Reused baseline contract tests | Case wall seconds |
|---|---|---:|---:|---:|
| Boost known fix | unclamped bounds/reversed bounds fail; clamp repair makes unchanged frozen tests pass; original author/verifier and retry-cap checks pass | 2 | 1 | 0.8438 |
| Boost unknown root | repeated heterogeneous IDs survive; frozen counterexample fails; explicit normalizer repair passes; stale/foreign/previous-attempt artifacts rejected | 1 | 1 | 0.8202 |
| Teamwork two milestones | parser returns strings and dependent sum returns count; both frozen tests fail then pass; dependency handoff and post-dispatch stale checks pass | 2 | 1 | 1.3004 |
| Teamwork interrupted handoff | parser emits empty result; frozen test fails then passes; DB closes after handoff, reopens, rejects wrong/stale ACK, then exact current ACK dispatches dependency | 1 | 3 | 0.8767 |
| Swarm parallel messages | label whitespace/case and inverted parity fail in parallel, then pass in parallel; actual duplicate/conflicting sends preserve one winner | 2 | 2 | 0.8868 |
| Swarm stop/disconnect reconcile | unexpected effect result fails no-execution frozen test then passes; stopped worker stays undelivered, append-before-terminal receipt survives reopen with count=1, explicit stopped reconciliation and duplicate resend do not replay, disconnected subscription is released | 1 | 4 | 0.9769 |

Total: **9 real failed business testcases**, **12 green contract tests (10 existing + 2 recovery extensions)**, all six post-repair business phases green. A JUnit failure counts a failed testcase, not individual executed assertions or unique defects. Each case has one scripted repair intervention; the Boost contract additionally exercises its existing three-rework budget deterministically, not three new model repair attempts.

Exact executable node IDs, fault/repaired source bytes, test bytes and original test SHA256 provenance are in `m0/cases.json`; exact commands are captured in each phase log. Business-test hashes match `docs/verification/modes-20261117/native-fixtures.json`. Source fault seeds are explicit new variants; accepted repair text was copied from the previous local fixture files. No absolute old fixture path is needed to reproduce.

## Metrics and capability/source levels

| Metric | Actual source / interpretation | Availability |
|---|---|---|
| Correct completion | exit codes plus JUnit failed-testcase/error/skip counts across red/green/contracts | deterministic fixture completion only; native completion null |
| Actual discovered defects | actual red failed-testcase output; named injected fault in manifest | injected fixture defects only; no inferred production defect count |
| Rework rounds | one explicit scripted source intervention; existing persisted attempt assertions separately exercised | fixture 1; native rework null |
| Non-plan human rescue | no native turn/rescue telemetry | null |
| Duplicate native side effects | no native action/receipt source | null; in-process duplicate-message invariant is not a native side effect |
| Unhandled messages | no native recipient lifecycle audit | null; contract assertions exercise durable pending/terminal state only |
| Empty model turns | no model turns executed | null, not zero |
| Total elapsed | `time.monotonic` around each complete case | measured deterministic wall time including process startup |
| Tokens and host state | no supported native/provider telemetry in this run | null, not zero |

Level 1: source and frozen-test hashes are directly recorded. Level 2: actual isolated pytest/SQLite/ASGI/thread-pool execution and file-side-effect counts are measured. Level 3: packaged/native tool sessions, native-model decisions, GUI and formal installation were **not executed**. String bindings in existing test fixtures are not host identity authentication. Swarm message-thread tests use existing workflow1 fixtures; they do not certify workflow2 native execution admission.

Fixed controls: six manifest tasks, explicit source start and test bytes, isolated filesystem/database permission scope, no native model (`null`), one sequential case at a time; parallel Swarm business subprocesses=2, existing inbox send threads=8/2. All contracts use archived existing code, not the concurrent worktree. The frozen archive was prepared before M1 changes and remains read-only during all cases.

## Harness corrections and retained failures

1. Initial runner attempt (`m0/first-run.log`, external `tri-native-gap-m0-first-run`) failed the frozen-test SHA assertion because Windows text output translated existing CRLF bytes. Test files now write exact UTF-8 bytes; no hash check was weakened.
2. An aborted earlier pass (`m0/run1.log`, external `tri-native-gap-m0-run1`) completed the known-fix case, then correctly stopped because the initial unknown-root fault did not fail the frozen test. Python equality treats `[True, 2, 4] == [1, 2, 4]` as true: the old single normalizer test does not independently expose bool-as-int acceptance. The final explicit seed retains duplicate IDs, producing a real assertion failure. The bool subtype blind spot remains documented; frozen tests were not changed.
3. `m0/run2.log` is the first complete pass of the initial case manifest; `m0/run3.log` is the complete pass after independent review added the two missing recovery tests. Earlier partial attempts are not concealed and are not extra successful trials. There has been no three-repeat comparison or distribution claim.

## Limitations / next evidence

- Boost unknown-root is a deterministic diagnosis fixture with a scripted known repair, not an autonomous unknown-root investigation. Teamwork interruption closes/reopens the actual isolated SQLite connection at the handoff boundary. Swarm stop/disconnect observes a real append side effect and durable receipt recovery. These remain deterministic caller-controlled tests, not desktop-native agent interruption or replay observation.
- The reused no-execution business fixture checks returned data only; the added recovery fixture independently appends a real message ID once and retains count=1 across disconnect, stopped reconciliation and same-key resend. Fixture duplicate count=0 is supported. Native duplicate count remains null; no native-model replay guarantee is inferred.
- Product before/after and Antigravity columns remain unavailable. The future 36-run old/new protocol is specified in `m0/README.md`; model equality and provider usage must be separately evidenced before native comparisons.
- Raw logs, databases, archived source and local receipts remain ignored/external. Required small gitignore exceptions: `m0-report.md`, `m0/README.md`, `m0/cases.json`, `m0/run_baseline.py`. Root owns ignore updates and independent review.

## Independent-review rework evidence

The reviewer identified missing actual connection interruption and missing observable effect counts. Both stayed assigned to the original M0 author. Added only two tests under owned `tests/test_native_gap_baseline.py`, reusing `mode_flow`, `linked`, `finish`, `advance` and inbox argument helpers from the pinned baseline. Contract config is pinned explicitly.

- Teamwork raw evidence: `tri-native-gap-m0-run3/teamwork-interrupted-handoff/contracts.log` and `contracts-tmp/test_teamwork_disconnect_reope0/handoff-reopen-evidence.json` (exact directory naming may be inspected under `contracts-tmp`). The log records reopened=true, preserved=true, dependent=dispatched, verifier=/root/verifier. Wrong recipient and stale artifact fail before current ACK.
- Swarm raw evidence: `tri-native-gap-m0-run3/swarm-stop-disconnect-reconcile/contracts.log` plus `effect-reopen-evidence.json` and `actual-side-effects.txt` under its external `contracts-tmp`. Observed actual fixture effects=1, fixture duplicates=0, terminal resend duplicate=true, terminal=cancelled, worker=stopped. No native metric was filled with those fixture numbers.
- The final result JSON records current manifest SHA256 and extension-test SHA256. `actual_failed_testcases` corrects the earlier imprecise `actual_failed_assertions` label; test outcomes are unchanged.
