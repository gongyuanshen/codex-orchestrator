# Verification collector, challenge & review gates (read on demand)

The verifier role contract already carries these rules; the coordinator reads this file only when it must judge a collector, challenger or review-dimension question itself.

## Evidence-based routing

Investigate only substantive unknowns, post `route_decision` (`msg_type="info"`), and add no extra author for known local fixes. Pure investigation uses the Solo report contract without invented edits or merges.

## Local verification collector (`local_collector_v1`, `workflow3`..`workflow5`)

- The original verifier freezes commands before running checks: it calls `workflow_verifier_turn(run_id=..., step_id=..., phase="prepare", scope="candidate"|"integrated", ...)` (primitive: `artifact_capture`) with nonempty `verification_commands` (`id`, exact `argv`, `cwd`, `timeout_sec`, `env_keys` or `env_profile="windows_minimal_py"|"posix_minimal_py"`, and `criteria` covering each criterion); pass `message_id` to verifier preparation when needed. `env_keys` is the exact child environment allowlist (use `env_profile` or list `SystemRoot/PATH/TEMP` explicitly): values come from the verifier terminal, the daemon validates only key syntax/uniqueness, and environment values are never logged or serialized. Keep `manifest_path`, `manifest_digest`, and logs outside captured source, and capture `candidate` and `integrated` separately.
- For independent checks, label consecutive commands with the same `parallel_group` and pass a positive `max_parallel` to the prepare/capture call. Only commands within that consecutive group can overlap, up to that limit; ungrouped commands run serially and separate groups. Omit `max_parallel` or set it to `1` for serial execution.
- Run the returned `verify_argv_template` (executable and argument list) in the verifier terminal. For a manual invocation, use `verify_command_hint` as a reference: `tri-orchestrator-verify --manifest ABS --output-dir NEW_ABS --heartbeat-url URL` with the returned `verify_executable` and heartbeat URL unchanged. Use PowerShell's `&` call operator before a quoted executable path. Keepalive updates an already-working verifier; it does not prove an agent is alive or change worker status. High-complexity checks may also run `tri-orchestrator-challenge` from `cli_executables`. There is no command execution RPC.
- Call `workflow_verifier_turn(phase="record", ...)` (primitive: `verification_record` with real `receipt_files`, per-criterion `PASS/FAIL/NOT_RUN`, explicit `inbox_ack` `accepted` -> `completed`, and Boost candidate `action="challenge"` bound to `artifact_id=candidate["id"]`). Note that raw stdout/stderr may hold command-origin secrets while environment values stay unlogged; `local_collector_v1` is local collection, not host authentication, and cannot detect temporary modification restored between checkpoints.

## High-complexity review & challenger findings

- High-complexity steps (`complexity="high"`) allow an independent read-only challenger (or host `tri-orchestrator-challenge`) to map counterexamples via `workflow_review(step_id, expected_revision, agent_id, criterion, evidence)` to an existing acceptance criterion on the same failed step.
- The original verifier's record requires `review_dimensions` (`specification`, `code`, `stress`, `evidence` as `PASS|FAIL|NOT_RUN`, with all four `PASS` required on `complexity="high"`; optional on normal complexity).
- The original verifier records `FAIL` and the original author repairs within at most 3 automatic rework rounds (then `needs_decision`; a genuinely new requirement needs explicit packet revision, and only new independent scope gets a new step). Substantiated `FAIL` repairs resolve via `resolved_finding_ids` on a later `PASS`; only an original-verifier actual `PASS` may dismiss unsubstantiated items via `dismissed_finding_ids` without increasing attempt (a substantiated `FAIL` cannot be dismissed).
