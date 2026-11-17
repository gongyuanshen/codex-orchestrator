# Batch1 fixture handoff — 2026-11-17

## Actual state / ownership

Only `batch1/native/` helpers/config and fresh external temporary fixtures were created. No product, deployment controller, global config, production state, native agents or business verification records were changed. Root subsequently executed setup and created all four runs; do not replay setup or change bindings. Root must create waiting-only `/root/batch1_author_b` before setup; existing identities are `/root/m1_native_author`, `/root/m1_native_verifier`; Solo owner is actual `/root`, never an invented author agent.

Installed Python reused unchanged: `C:\Users\user\AppData\Local\Temp\tri-native-gap-m2-r658814l\安装 验收\.venv\Scripts\python.exe`.

Fresh external root: `C:\Users\user\AppData\Local\Temp\tri-batch1-中文 space-v92sdlfs` (projects, data, evidence separated). Daemon is already running on loopback port1397, owned exec session75387, using only this data directory. Root may reuse it or stop only this owned session. No venv was created. Daemon launch first failed because config path was relative to package cwd; retry with absolute config path succeeded. This was setup-only and produced no workflow.

Actual checks: offline syntax/frozen test hashes/Chinese-space paths passed; fixture and negative argparse passed (an import-side-effect defect found in negative help was fixed with a main guard); installed SDK readonly catalog=28 and new schemas passed. SDK catalog receipt: external `evidence/helper-readonly-catalog.json`. Not desktop catalog proof. No setup, author, collector, native followup or business PASS/FAIL has been executed by this worker.

## Root shell setup

```powershell
Set-Location 'C:\workspace\codex-orchestrator'
$h='C:\workspace\codex-orchestrator\docs\verification\native-gap-next\batch1\native'
$c=Get-Content -Raw "$h\config.json" | ConvertFrom-Json
$py=$c.python
$ev=$c.evidence
& $py -B "$h\selfcheck.py"
& $py -B "$h\fixture.py" setup
```

Setup creates four workflow3 runs and frozen packets. Each step is bound to exact packet/attempt/agent, with disjoint source-file ownership. Frozen tests live in each external project. Context IDs are persisted at `$h\context.json`, never replay setup. Every RPC receipt is external `evidence/rpc-UUID.json`.

All subprocess commands use exact environment allowlist: SYSTEMROOT, PATH, TEMP, PYTHONUTF8, PYTHONIOENCODING, PYTHONDONTWRITEBYTECODE. No env values are serialized. Collector is installed `tri-orchestrator-verify.exe`, not repo module. Candidate/integrated captures have separate manifests and output directories. Every phase tag includes case/step/packet/attempt/phase. Repeated same-phase collection rejects existing output directory instead of silently overwriting evidence.

## Shared native delivery protocol

Root performs every actual `collaboration.followup_task`. Helpers never call native host tools. Before each native call run dispatch; it persists inbox, claims exact projection and begins exact action:

```powershell
& $py -B "$h\fixture.py" dispatch swarm label author --body 'Original author: submit seeded label.py unchanged for actual failing acceptance; tests immutable. Then run fixture.py author swarm label author.'
```

Dispatch prints real agent ID, delivery and action. Root sends actual native followup using that identity, with absolute helper/python paths. Save actual tool result as JSON at `$ev\host-swarm-label-a0.json` using atomic local write, then record it immediately, before recipient changes attempt:

```powershell
& $py -B "$h\fixture.py" record swarm label author --body "$ev\host-swarm-label-a0.json"
```

Record consumes actual saved JSON, not a success template. It is scheduling confirmation only, not business verdict. Binding evidence files are `CASE-STEP-p1-aN-PHASE-delivery.json`, `-invoking.json`, `-confirmed.json`.

The ORIGINAL AUTHOR normally accepts before implementation, then explicitly submits after implementation. `author_begin` requires pending exact bound inbox, ACKs accepted, advances dispatched→implementing (repair already implementing remains), and returns without submitting/completing. `author_submit` requires the same accepted message plus implementing state, submits then ACKs completed. Both preserve exact packet/attempt tag. The old one-shot `author` command is retained only for explicitly unchanged seeded initial submissions; it is not the normal implementation route:

```powershell
& $py -B "$h\fixture.py" author_begin swarm label author
# Original author edits ONLY its assigned source and runs actual checks here.
& $py -B "$h\fixture.py" author_submit swarm label author
```

For review root dispatches phase `candidate` or `integrated`, performs and records actual native followup, then ORIGINAL `/root/m1_native_verifier` executes installed collector via:

```powershell
& $py -B "$h\fixture.py" verify swarm label candidate --verdict FAIL --body 'Both actual assertions require brackets; inspect raw pytest output and record observed failures, no source/test edits.'
```

Verifier must read actual stdout/stderr, inspect every frozen test/criterion, and provide truthful explicit verdict/basis. Helper rejects collector infrastructure failures, incomplete receipts, mismatched frozen test hashes and verdict/exit contradictions. It prints real raw pytest stdout and saves collector call, manifest-backed receipt and criterion judgment. It never returns synthetic PASS. Root may tell verifier to inspect tests/output before final completion; raw receipt paths are printed in verification JSON. Phase candidate/integrated creates fresh review inbox and fresh collection each time.

## Solo sequence (root implements, original verifier checks)

```powershell
& $py -B "$h\fixture.py" transition solo normalize dispatch
& $py -B "$h\fixture.py" transition solo normalize accept
```

Root changes ONLY external `projects/solo/normalizer.py`: `normalize(values)` returns sorted unique IDs. Frozen test requires `[3,1,3] -> [1,3]` and empty->empty. Root then submits directly (no fictional author/native self-transfer):

```powershell
& $py -B "$h\fixture.py" transition solo normalize submit
& $py -B "$h\fixture.py" dispatch solo normalize candidate --body 'Original independent verifier: inspect frozen two normalizer tests and run verify solo normalize candidate with actual PASS or FAIL.'
```

Root performs native followup and `record solo normalize candidate --body REAL_RESULT_JSON`; verifier runs `verify solo normalize candidate --verdict PASS --body ACTUAL_CRITERION_BASIS`. After genuine candidate PASS:

```powershell
& $py -B "$h\fixture.py" transition solo normalize begin_integrated_review
& $py -B "$h\fixture.py" dispatch solo normalize integrated --body 'Original independent verifier: separately collect integrated normalizer tests; inspect actual output.'
```

Actual followup/record then verifier `verify solo normalize integrated --verdict PASS --body ACTUAL_BASIS`; root `transition solo normalize complete`.

## Teamwork parse→sum and designated handoff

`parse` authorA owns parse_ids.py; tests parse `1, 2,-3` and empty. `sum` authorB owns sum_ids.py; implementation imports parse_ids and sums; tests `1, 2,-3,4 -> 4` and empty->0. No source overlap; tests immutable.

Before A dispatch prove dependency refusal:

```powershell
& $py -B "$h\negative.py" transition teamwork sum before-dependency --action dispatch --contains 'A dependency is not complete'
& $py -B "$h\fixture.py" transition teamwork parse dispatch
& $py -B "$h\fixture.py" dispatch teamwork parse author --body 'Original authorA: run author_begin teamwork parse author FIRST, then implement only parse_ids.py per frozen tests, preserve tests, then author_submit teamwork parse author.'
```

Root actual followup/record; A runs author_begin, implements, then author_submit. Dispatch candidate→actual verifier→record→verify PASS; begin_integrated_review; dispatch integrated→actual verifier→record→verify PASS. Then:

```powershell
& $py -B "$h\fixture.py" transition teamwork parse complete
# Original authorA executes handoff sender after actual integrated acceptance:
& $py -B "$h\fixture.py" transition teamwork parse handoff --body 'Actual authorA transfers frozen parse checkpoint to designated authorB after integrated PASS.'
# Root persists/begins designated B notice, performs actual followup, then records real result:
& $py -B "$h\fixture.py" dispatch teamwork parse handoff --body 'Original B: inspect exact current parse handoff and integrated receipt; execute handoff_ack teamwork parse handoff with actual readback basis.'
```

Root actually follows up `/root/batch1_author_b` to inspect board/context A checkpoint, frozen tests, actual receipt and current packet/attempt, then B executes:

```powershell
& $py -B "$h\fixture.py" handoff_ack teamwork parse handoff --body 'Actual designated authorB read current frozen parse checkpoint and integrated receipt; ready for dependent sum work.'
```

This ACK command MUST be run by designated original B, not root impersonation. Then root dispatches sum workflow, durable author inbox/native protocol, B runs author_begin before implementing sum_ids.py, then author_submit. Repeat separate candidate/integrated gates and complete for sum milestone. Actual original B then executes `transition teamwork sum handoff --body ACTUAL_B_CHECKPOINT_BASIS` (sender B, recipient coordinator /root). Root reads the sum handoff and final artifacts directly, then executes `transition teamwork sum ack_handoff --body ACTUAL_ROOT_READBACK_BASIS`; no fictional native self-call or unused invoking notice. Final ACK is root, not B accepting its own work. Mappings are frozen in helper before setup: parse A→B, sum B→root. No M3 semantics added.

## Swarm real parallel and seeded FAIL→original author repair

Dispatch both workflow steps first, then persist/begin author inbox for label and parity, perform BOTH native followups without waiting for one author to finish. Root records true native results. Label authorA must first submit seeded `return text` unchanged; parity authorB preserves correct seeded predicate but inspects/runs frozen tests and submits its own independent step. Two real active native authors are required; helper alone is not concurrency evidence.

```powershell
& $py -B "$h\fixture.py" transition swarm label dispatch
& $py -B "$h\fixture.py" transition swarm parity dispatch
& $py -B "$h\fixture.py" dispatch swarm label author --body 'Original A: submit seeded label.py unchanged for intentional actual FAIL, tests immutable; author swarm label author.'
& $py -B "$h\fixture.py" dispatch swarm parity author --body 'Original B: inspect parity.py and frozen tests, actual author submission; author swarm parity author.'
```

Actual followup/record each. Independent label candidate must really FAIL two bracket assertions; same original verifier records FAIL. Root `transition swarm label start_rework` produces attempt1; new author delivery and actual originalA followup runs author_begin, repairs label only to bracket text, then author_submit. Same verifier separately candidate PASS→integrated PASS→complete. Parity candidate/integrated verification occurs separately and must truthfully PASS two tests. No test mutation or green substitution.

## Real cap0→1→2→3 and refusal4

Separate `cap/answer` fixture isolates deliberate exhausted failure from successful Swarm. Criterion remains answer()==42. Start attempt0 dispatch and originalA submits seeded answer0. Original verifier collects actual one-test FAIL. For each attempts1,2,3 root uses `start_rework`, fresh author inbox/native call to originalA, A first runs author_begin, changes ONLY answer.py to return that attempt number (1/2/3), then author_submit; same verifier runs fresh candidate FAIL. Do not alter packet/author/verifier/test/criterion to manufacture retry. Each step's attempt appears in all filenames.

After actual FAIL at attempt3:

```powershell
& $py -B "$h\negative.py" transition cap answer cap-repair4 --action start_rework --contains 'Action start_rework is not permitted from rework'
```

Negative helper saves real rejected RPC and proves state/attempt/revision unchanged. No successful repair4 or synthetic FAIL chain is created.

## Retained receipt and log-digest probes

During real label attempt1 candidate review, ORIGINAL verifier uses the following instead of ordinary verify. It first collects genuinely passing tests, then attempts retained attempt0 receipt as current PASS and a temporary real stdout mutation while state is still candidate_review, proves both exact refusals/state unchanged, restores bytes in finally, then registers the genuine current PASS:

```powershell
& $py -B "$h\fixture.py" verify swarm label candidate --verdict PASS --body 'Actual bracket assertions both passed; inspect raw output before completing.' --old-receipt "$ev\swarm-label-p1-a0-candidate-collector\receipt.json" --probe-log
```

Evidence: `swarm-label-p1-a1-candidate-negative-old-receipt.json`, `-negative-log-digest.json`, `-restored-log.json`. Exact expected refusals are receipt manifest identity mismatch and output digest/size mismatch. Generic `negative.py receipt` remains callable only while the supplied artifact scope matches current review state; otherwise state refusal is not targeted digest proof. No PASS row from negative probes. Restored-transient-change and same-user rehash remain explicit boundaries, not prevented attacks.

## Remaining ownership / limitations

Root owns real native calls, live concurrency evidence, unavailable/dead agent, control/restart uncertainty races, desktop tool catalog, production migration and GUI. This fixture worker did not execute business tasks, claim full acceptance or generate final agent evidence. Human judgment strings above are invocation instructions, not proof; use actual observed basis/result when invoking. Native token/host authentication/performance remain unavailable. No M3 functionality was implemented.

## Author lifecycle correction

`author_begin` / `author_submit` were added after root setup, before normal author execution. No setup, binding, verifier logic or live Solo state was changed by this edit. Normal implementation always uses begin→edit/check→submit. One-shot author is for explicit unchanged seed delivery only.
