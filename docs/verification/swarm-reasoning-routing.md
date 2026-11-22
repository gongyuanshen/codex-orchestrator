# Swarm reasoning-effort acceptance

This verifies task-level reasoning effort without changing the selected model or workflow acceptance gates. The daemon prepares dispatch; only the Codex host calls `spawn_agent`. A requested argument or a successful spawn receipt is not proof of the effective effort.

## Isolated native capability check

1. Use a disposable source checkout and a separate Tri-Orchestrator data directory and loopback port. Load the candidate Swarm skill from source; do not update the installed plugin or operate on the production ledger. Use two fresh matching Codex root sessions with `gpt-6.2-sol / ultra`, and verify that setting in each root turn. Verify that the selected role and `[agents]` defaults do not change the model or override the test effort. Use an already signed-in isolated test profile if a separate profile is needed; do not copy production credentials or change global settings.
2. Freeze the same source revision and the following two read-only task prompts. Each child returns the requested observations with exact source lines; it does not edit files or use any real run data. In both arms, send the identical task prompts, use `fork_turns="none"`, and omit `model`:

   | Task | Prompt and independent grading criteria | Inherited control | Routed arm |
   | --- | --- | --- | --- |
   | Bounded receipt check | In `tests/test_m8_workflow5_direct_spawn.py`, inspect the author pre-spawn and successful receipt assertions of `test_workflow5_direct_spawn_author_and_lazy_verifier_full_rework_and_freeze_loop`. Report the initially unbound owner, the expected `/root/author_1` path and the `working` status after a confirmed receipt. Grade all three against the frozen source. | Omit `reasoning_effort` | Explicit `medium` |
   | Deeper review | Trace the first-spawn confirmation path from `_handle_native_action_record_call` into `NativeActionStore` and the same test. Find where the worker, step checkpoint and inbox bind the real author, and explain why a receipt does not acknowledge the business task. Grade all four observations against the frozen source and `test_claim_begin_record_and_no_business_ack` in `tests/test_native_actions.py`. | Omit `reasoning_effort` | Explicit `high` |

3. Run the bounded check once more in a distinct task with `fork_turns="all"` as a compatibility control; omit both `model` and `reasoning_effort`. Keep this separate from the timing comparison. If the live schema, model, or permissions do not allow the requested explicit override, record the cause; classify that outcome as unsupported or unverified, not a passing split.
4. Record the planned tier/source and rationale before spawning (ticket or `main` board note), then match the exact bound child path under the root to its thread ID. On desktop, the read-only `threads` / `thread_spawn_edges` view in the active Codex state catalog can identify the child and its `rollout_path`. Match its first child-owned turn (for example, start and completion events with the same `turn_id`) to `turn_context.payload.model` and `payload.effort`. A full-history fork can copy a parent `turn_context` into the child log; do not take the first entry blindly. Capture only the required metadata and evidence location. If only a current catalog snapshot is available, keep first-turn verification open. Inspect the original arguments to establish whether a tier was sent; record a known role/default override separately. Do not change a grade to match a request.

Expected: control and complete-history children observe Sol / Ultra under the stated configuration; routed children observe Sol / Medium and Sol / High respectively. Report requested and observed effort separately and call out any mismatch or unavailable evidence. These checks do not require modifying host configuration.

### Native host smoke check (2026-11-22)

An existing Sol / Ultra desktop root spawned three read-only children directly against a disposable checkout. First child-owned turn records showed Sol / Medium (bounded receipt check), Sol / High (cross-module review), and Sol / Ultra (bounded check with full-history fork and no overrides). All answers met the listed source criteria. This establishes native tier behavior on that host, not a two-arm Swarm workflow performance comparison; no installed plugin or production ledger was used for the probe.

## Timing and quality comparison

Use the same model, revision, environment, task text, permissions and concurrency in both arms, and grades from the same independently checked criteria. Record coordinator wall time from first dispatch to both graded results, and each child wall time from spawn to its first completed answer. Record raw measurements and grades for both arms; one run is a capability check, not evidence of general speedup. For a rollout decision, repeat with fresh sessions, alternate the execution order to reduce cache bias, and report median plus range and any quality failures. An observed model difference invalidates the same-model comparison.

## Automated scope

`uv run --no-sync python -m pytest tests/test_m7_skill_contracts.py -q -k swarm_reasoning_effort` verifies that the shipped skill requires selecting per task and recording dispatch and first-turn observations. It does not measure model runtime or establish a real native result.
