# M2 runner implementation report

Date: 2026-11-17. Owned files only: `plugin/src/orchestrator/verify_runner.py`, `verify_process.py`, `tests/test_verify_runner.py`, `test_verify_process.py`, this report. No commit, dependency installation, production installation/configuration/process change, DB/RPC/workflow change, or alteration of accepted M0/M1 work.

## Implemented contract

- CLI and frozen exports: `validate_manifest`, `collect`, `validate_receipt`; public `validate_evidence_path(path, artifact)` shared with integration. Evidence overlap is checked against actual captured paths, not unnecessarily against all of the workspace root.
- Exact frozen argv via direct process creation; no shell. Windows CRT-style quoting through `list2cmdline`. Relative executable paths and relative PATH entries resolve against frozen command cwd, not the collector cwd. Batch scripts rejected on Windows. Arbitrary custom executable argument parsers are not promised CRT semantics.
- Environment is an explicit `env_keys` allowlist as clarified by root. Only listed existing caller-environment keys are passed; no implicit PATH/SystemRoot/TEMP additions. Environment values are not serialized into metadata. Command-produced bytes are retained unchanged, including anything the command itself prints.
- New output directory only; atomic/fsynced running receipt before any collected process launch; separate direct-to-file binary stdout/stderr with streaming SHA256 and byte counts. Bounded CLI summary contains status, receipt path, manifest digest, never raw logs.
- Windows creation-time JOB_LIST plus explicit inherited stdin/stdout/stderr HANDLE_LIST; sole non-inherited KILL_ON_JOB_CLOSE job handle. Timeout/cancellation terminate owned job and require zero active processes. A root exiting with living descendants is incomplete, not success. No PID/name sweeping. API failures retain primary and cleanup context.
- Nonzero process exits remain truthful fully collected `finished` evidence but nonzero CLI status. Startup/timeout/cancel/capture/source-change errors remain incomplete with null top-level end time. Forced collector death leaves running plus raw partial logs; no manufactured final receipt.
- Receipt validation checks frozen identity/order, completion structure, sequential/toplevel timestamp consistency, present regular output paths, byte counts/hashes, pre/post/current source snapshots when success is requested. Null digests on actual partial files remain allowable only as incomplete evidence; those files are returned for backend evidence-chain capture. A directory cannot impersonate a missing partial log.
- POSIX minimum standard-library path uses a new session/process group, kills only that group on cancellation/timeout, and reports inability to prove cleanup. CLI SIGTERM requests cancellation. This path is not hardware/platform-validated on this Windows host.

## Actual RED evidence

Before product files existed:

```text
python -m pytest tests/test_verify_runner.py -q --basetemp .../m2-runner-red
ModuleNotFoundError: No module named 'orchestrator.verify_runner'
1 error in 0.08s
python -m pytest tests/test_verify_process.py -q --basetemp .../m2-process-red
ModuleNotFoundError: No module named 'orchestrator.verify_process'
1 error in 0.07s
```

Additional genuine failing regression tests before their respective fixes:

- Explicit env allowlist: actual child returned exit 1 because undeclared variable leaked; 1 failed in 0.09s. Fixed by constructing only declared environment entries.
- Hash fault masking launch failure: expected `executable not found`, observed only `OSError: injected output hash failure`; 1 failed in 0.06s. Fixed preserving primary plus capture exception context.
- Impossible finished receipt timelines: receipt ended before command completion and second command started before first finished; 2 failed in 0.14s. Fixed cross-command/cross-level timing checks.
- Relative `.\\python.exe` under frozen cwd: actual startup failure while executable existed; 1 failed in 0.05s. Fixed executable/PATH resolution at frozen cwd.
- Partial stdout replaced by a directory with null hash: validation incorrectly accepted; 1 failed in 0.09s. Fixed explicit present-non-file rejection.
- Initial UTF-8 test assumed the child interpreter's locale output codec; actual raw bytes were CP936. Corrected fixture to explicitly emit UTF-8 bytes, not collector decoding/redaction.

## Final actual verification

Final harness cwd was external `C:\Users\user\AppData\Local\Temp`; all receipt/log/source fixtures use independent external pytest directories. Earlier RED/iterative pytest harnesses ran from the repository but real process fixture cwd and generated files were external temp directories. The relative-executable regression intentionally uses the existing venv Scripts cwd read-only to test cwd resolution.

```powershell
$env:PYTHONPATH='C:/workspace/codex-orchestrator/plugin/src'
& 'C:/workspace/codex-orchestrator/.venv/Scripts/python.exe' -m pytest `
  'C:/workspace/codex-orchestrator/tests/test_verify_runner.py' `
  'C:/workspace/codex-orchestrator/tests/test_verify_process.py' -q `
  --basetemp C:/Users/user/AppData/Local/Temp/m2-runner-final3
```

Actual exit code: 0. Actual output:

```text
......................s...                                               [100%]
25 passed, 1 skipped in 2.34s
```

Raw final command output: `C:\Users\user\AppData\Local\Temp\m2-runner-final3-output.txt`.

Actual tests include: real exit 7; nonexistent program; Unicode/spaces/empty/quotes/trailing slash/metacharacter argv; 3 MB separate raw output and full hashes; mutated output rejection; old attempt rejection; real source mutation invalidation; temporary mutation/restoration counterexample; required explicit secret environment with undeclared-variable exclusion; hash fault and primary error preservation; malformed bindings; invalid receipt timelines; directory partial-output rejection; CLI bounded summary for actual exit 0 and 7; real timeout and pre-cancel; root exit with live child; actual root/child/grandchild timeout and cancellation; forced collector kill; API cleanup failures; relative executable/PATH cwd resolution.

The tree tests retain SYNCHRONIZE handles for actual root/child/grandchild identities before intervention, require all handles signalled, and keep a concurrent unrelated same-image sentinel alive. Forced-death test never duplicates/retains the collector Job handle and verifies receipt remains running with three evidence paths (receipt plus raw stdout/stderr).

API fault tests inject only failure into TerminateJobObject/QueryInformationJobObject; real process creation and all other APIs remain real. No mocked successful process return is used.

## Intentional evidence boundary and incomplete acceptance

The actual temporary-mutation fixture writes different source bytes, verifies the changed contents were read, and restores exact original bytes before the final checkpoint. Collection succeeds, demonstrating the documented pre/post snapshot limitation, NOT a prevented attack. Local JSON+SHA is collection evidence, not signature/host authentication or protection from same-user recomputation. Backend registration/gates must anchor the original manifest/evidence chain and per-criterion verifier judgment.

Skipped/not established by this runner report: POSIX execution (1 platform skip), restrictive nested-host-job behavior, startup-kill timing stress, exhaustive kernel-interruption guarantees, full project suite, installed isolated package acceptance, and author/verifier/integrator end-to-end rework acceptance. Those last integration/package items are owned by root/integrator, not asserted here. No production install was performed.

Risk: externally brokered descendants are outside Job ownership; arbitrary custom Windows command-line parsers are outside exact CRT argv guarantees. POSIX forced SIGKILL has no Windows Job last-handle-close guarantee. Cleanup API failure stays explicit incomplete evidence, never successful completion.
