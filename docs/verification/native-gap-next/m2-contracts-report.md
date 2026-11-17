# M2 skill and role contract synchronization

## Changed scope

Minimal additions to shared Route A, Boost/Teamwork/Swarm, every inline role and six packaged role TOMLs. Added tests/test_m2_contracts.py. No runner/runtime/UI/metadata files changed; no model/effort/sandbox changes, subagents, commit, installation or process/config changes.

## Contract

- New runs use workflow3 with inherited workflow2 semantics and the existing M1 transfer sequence. Historical workflow0/1/2 are unchanged, without fabricated evidence or unsupported command input silently ignored.
- Original verifier freezes verification_commands in artifact_capture: unique command id, exact argv, absolute cwd, timeout_sec, environment key names only and criterion coverage. Retain returned manifest_path/manifest_digest.
- Original verifier runs `tri-orchestrator-verify --manifest ABS --output-dir NEW_ABS` through existing host terminal permissions, not a command RPC. A fresh external output directory and external logs/test outputs prevent source-scope contamination.
- verification_record receives actual receipt_files, retained evidence and every criterion's real judgment. Candidate/integrated captures and gates remain separate. Finished/exit0 is never automatic PASS; incomplete/failing evidence remains truthful.
- local_collector_v1 is not host identity authentication or a signature. Environment values are not serialized; raw command stdout/stderr retains its source and can itself contain secrets. Temporary mutation followed by exact restoration between checkpoints is explicitly undetectable.
- Preserve original author/verifier and three-repair budget. Incorporated final M1 reconcile-only cancellation and explicit handling of legacy nonnotice inbox sources lacking frozen context; no invented historical context.

## Failure-first checks

Before contract edits:

`.venv/Scripts/python.exe -m pytest tests/test_m2_contracts.py -q`

**10 failed**, retained m2-contracts-red.log.

After final verifier-role edit:

`.venv/Scripts/python.exe -m pytest tests/test_m2_contracts.py tests/test_native_action_contracts.py tests/test_workflow_contracts.py tests/test_mode_contracts.py tests/test_routea_contracts.py -q`

**39 passed in 0.05s**, retained m2-contracts-green.log. Owned-file `git diff --check` returned no errors.

## Verification boundary

These static contracts check order, omissions, all modes/inline/installed roles and compatibility wording. They do not prove subprocess/runtime/native identity, cancellation, receipt validation or packaged installation behavior. Runtime/full-suite/real-host acceptance remains with implementation and independent verification.

## Environment allowlist clarification

Updated only shared/mode/inline/six-role instructions and tests/test_m2_contracts.py after the final interface clarification. env_keys is the exact child environment allowlist, not a required-only list; unlisted variables are not inherited. Values come from the verifier terminal. The daemon validates key syntax/uniqueness without requiring secret values in its environment; the collector checks listed values exist. Needed SystemRoot/PATH/TEMP and other keys must be explicitly listed; values remain unserialized.

New static assertion first produced **1 failed, 10 passed** (m2-env-contracts-red.log). After synchronization, the same five contract suites produced **40 passed in 0.05s** (m2-env-contracts-green.log). Owned-file git diff --check was clean. No runtime/UI/package metadata, installed runtime or package changes; no new runtime/full-suite/native acceptance claim.
