# M0 independent review — first pass, 2026-11-17

## Verdict

- **Spec compliance: FAIL / incomplete.** Six reproducible deterministic red/green fixtures and a fixed source baseline work, but the named interrupted-handoff and no-replay scenarios do not yet exercise those actions end to end. The report and README were absent at this review snapshot.
- **Quality: FAIL pending the findings below.** The executable runner does not fake test success, keeps unavailable native metrics null, and uses isolated archived code. However scenario labels overstate what the selected contracts prove, and one metric is mislabeled.

Read-only review of implementation: no source, fixture, test or production file was changed. This review and its evidence-path index are the only review outputs. No full-suite pytest run was performed.

## Findings

### P1 — interrupted-handoff case does not actually interrupt or recover a handoff

`C:/workspace/codex-orchestrator/docs/verification/native-gap-next/m0/cases.json:103-123`

The task says recover an interrupted milestone handoff, but the business fixture is a parser returning an empty list. The selected frozen contracts check wrong-recipient/stale-artifact rejection and stale verification. Neither interrupts a handoff or closes/reopens its persistence boundary. Thus a broken interruption-recovery path can coexist with this case's green result. This is not a request to control real native agents: an isolated persisted workflow interruption/reopen fixture is sufficient if that is the intended scope, and must be clearly distinguished from native interruption.

Evidence: inspected the exact archived `9c29cdb` implementations `tests/test_mode_workflows.py:227-248` and `:341-347`; reran the selected case successfully. The successful contracts do not contain an interruption/reopen action. Couple a real interruption boundary to the resumed handoff and assert exact recipient/current evidence before counting this required case complete, or explicitly report it incomplete rather than claiming all six required scenarios are covered.

### P1 — stop/disconnect case does not establish absence of native side-effect replay

`C:/workspace/codex-orchestrator/docs/verification/native-gap-next/m0/cases.json:177-201`

The business test calls a constant-return `effect()` and asserts its string; the repair changes the string. The selected reopen contract preserves accepted/terminal message rows, but does not execute or count a side effect or run a dispatch/reconciliation path after reopening. It can prove durable accepted state, not the task's claimed recovery without replay. The stopped-delivery and ASGI-disconnect contracts independently exercise real local behavior; those are valid but do not fill this missing link.

Evidence: exact archived `tests/test_inbox.py::test_reopen_preserves_accepted_for_reconciliation_and_terminal_receipts` sends/ACKs, closes/reopens DB and compares stored rows. There is no effect executor/counter. An isolated real counter with an accepted-but-unconfirmed action, interruption/reopen, reconciliation and unchanged count would establish the intended local property; native duplication must remain unavailable unless actually observed on a native host.

### P2 — failing test-case count is labeled assertion count / defect count

`C:/workspace/codex-orchestrator/docs/verification/native-gap-next/m0/run_baseline.py:101-108`

`counts['failures']` comes from JUnit's failed test-case count, not the number of assertions reached or distinct discovered defects. For example a test function with multiple failing conditions stops at its first assertion. Rename this to failed test cases and keep it separate from defects actually discovered. Do not use this value as a model-discovery metric. This review observed 9 failing test cases, not 9 demonstrated independent defects.

### Pending — final documentation unavailable at review snapshot

`C:/workspace/codex-orchestrator/docs/verification/native-gap-next/m0-report.md` and `C:/workspace/codex-orchestrator/docs/verification/native-gap-next/m0/README.md` did not exist at initial inspection or after the independent run. Final claims, dependency command, metrics-source table, levels/source provenance, and explicit missing 36-run/product comparison cannot yet be reviewed. This may reflect concurrent author work, not a permanent missing-deliverable defect; request another pass after publication.

## Independent execution evidence

Command executed from the checkout (not a full pytest suite):

```powershell
& 'C:\workspace\codex-orchestrator\.venv\Scripts\python.exe' -B 'C:\workspace\codex-orchestrator\docs\verification\native-gap-next\m0\run_baseline.py' --output 'C:\Users\user\AppData\Local\Temp\m0-independent-venv-c8cc57d3500c4d54b496ea00a861c1a6'
```

Exit code **0**; all six cases produced real business pytest RED then GREEN, followed by **10** passing frozen contract tests. RED totals by case were **2, 1, 2, 1, 2, 1 failed test cases**. Logs/XML and `results.json` are in that output directory. A separate result self-check asserted the exact baseline, six case records, total 9 failed test cases, and null tokens/host state/native completion; it passed.

An initial run with system `C:/Python314/python.exe` failed because that interpreter lacks pytest. Its raw `red-clamp.log` retains `No module named pytest`; the runner raised missing XML rather than returning fake success. That is an environment mismatch, not counted as a scenario failure or product defect. Both output paths are indexed in `m0-review-evidence-path.txt`.

## Confirmed strengths and bounds

- Source archive uses fixed full commit `9c29cdb308a92cfdbdf259289bdc284f160abdfd`; working-tree candidate code is not silently used as the baseline. Output creation is exclusive, and product Python source hashes are checked after execution.
- Business tests are real pytest processes; required RED rejects collection errors, and GREEN rejects failures/errors/skips. Each test's embedded SHA-256 is checked. Tests were not weakened to obtain green.
- Repairs are fixed literal code written by the runner. Header, measurement tag and metric naming explicitly distinguish deterministic fixtures from native-model execution. No model capability improvement has been established.
- `tokens`, `host_state`, native duplication/messages/empty turns/human rescues/rework/completion are null rather than invented zeroes. Elapsed times are actual monotonic measurements.
- Existing contract tests are reused rather than a second product state machine being built. The remaining concern is precisely what these contracts cover, not whether they actually run.
- Only one independent initial baseline run was performed. No candidate comparison, 36-run release comparison, Antigravity comparison, native-host acceptance, installation or GUI acceptance was attempted.

## Same-defect recheck — final M0 revision

**Current verdict: Spec compliance PASS; Quality PASS for the bounded deterministic M0 harness.** The initial FAIL and findings above remain retained as history, not reset or renamed. Both P1 gaps, the P2 naming error, and pending documentation are resolved at this revision. This is not native-agent or product-comparison acceptance.

### Exact finding disposition

1. **P1 interrupted handoff: CLOSED.** `C:/workspace/codex-orchestrator/tests/test_native_gap_baseline.py:51-87` now closes the actual isolated DB after persisted handoff and before ACK, reopens it, verifies the same handoff, rejects wrong-recipient and stale-artifact ACKs, restores the original bytes, ACKs the intended recipient and dispatches the dependent milestone. Attempt counts remain zero. This covers local persistent interruption/recovery, not a native turn interruption.
2. **P1 effect replay observation: CLOSED at local fixture scope.** `C:/workspace/codex-orchestrator/tests/test_native_gap_baseline.py:90-129` appends an actual message ID once after accepted and before terminal ACK, closes/reopens the DB, observes reconciliation-required state, explicitly stops the worker, writes a cancelled reconciliation receipt, resends the same action and verifies terminal duplicate plus empty poll. Actual append file remains exactly one ID. This measures the caller-controlled reconciliation path and durable message idempotency, not automatic execution admission or a native replay guarantee.
3. **P2 misleading metric: CLOSED.** `C:/workspace/codex-orchestrator/docs/verification/native-gap-next/m0/run_baseline.py:105-111` now records and prints `actual_failed_testcases`, and README/report explicitly distinguish it from assertions and independent defect counts. Native metrics remain null.
4. **Pending documents: CLOSED.** Read final `m0/README.md` and `m0-report.md`. They provide the .venv invocation, exact pin, metric sources, deterministic/scripted versus native boundaries, retained failed attempts, single-run limit, future 36-run protocol, missing product/Antigravity comparisons and two recovery-extension scopes. No claim of autonomous diagnosis or model improvement was found.

### Independent re-execution

Executed the four owned tests with archived baseline imports/config, not candidate product imports:

```powershell
$env:PYTHONPATH = 'C:\Users\user\AppData\Local\Temp\tri-native-gap-m0-run3\source\plugin\src'
$env:PYTHONUTF8 = '1'
& 'C:\workspace\codex-orchestrator\.venv\Scripts\python.exe' -B -m pytest -q -s -W error -c 'C:\Users\user\AppData\Local\Temp\tri-native-gap-m0-run3\source\pyproject.toml' 'C:\workspace\codex-orchestrator\tests\test_native_gap_baseline.py' --basetemp 'C:\Users\user\AppData\Local\Temp\m0-review-recheck-6dcf099077734283bdc4a4fa39232452' -p no:cacheprovider
```

Actual result: **4 passed in 0.13s**, exit **0**. Printed and independently read JSON evidence:

- `test_teamwork_disconnect_reope0/handoff-reopen-evidence.json`: database reopened=true; handoff preserved=true; dependent state=dispatched; verifier=/root/verifier.
- `test_swarm_effect_then_disconn0/effect-reopen-evidence.json`: database reopened=true; accepted requires reconciliation=true; actual fixture effects=1; duplicates=0; terminal=cancelled; resend duplicate=true; worker=stopped.

Both are beneath the external basetemp above, also indexed in `m0-review-evidence-path.txt`.

Read final author `tri-native-gap-m0-run3/results.json`: six cases, twelve green contract tests, exact `9c29cdb308a92cfdbdf259289bdc284f160abdfd` baseline. Independently recomputed current manifest and extension-test SHA256 values match that result's recorded hashes. Checked tokens, duplicate-native-effects and native-completion are still null for all cases. The original independent six-case red/green run plus this targeted same-defect rerun support this PASS; the reviewer did not rerun the entire revised six-case runner or full project pytest suite.

Residual limits: a deterministic test explicitly choosing not to replay does not certify an external autonomous executor. Frozen normalizer equality still does not independently detect bool-as-int acceptance; that retained limitation is documented and no longer mistaken for a failing seed. No additional blocking defect was found within the M0 contract.
