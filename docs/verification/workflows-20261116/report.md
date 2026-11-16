# Workflow repairs and native-agent acceptance — 2026-11-16

## Delivery boundary

Five audited issues are repaired in source. Tests used a separate server at http://127.0.0.1:19890, separate SQLite data under this evidence directory, and three disposable fixture projects. Actual Codex native agents implemented, reviewed and reworked the fixtures. Orchestration requests used the supported HTTP/RPC endpoint, not the installed production MCP connection.

Production http://127.0.0.1:18790 was not restarted or upgraded. Its PID 7816 and started_at were unchanged; all 21 inventoried installed source/skill/role files retained their hashes; original swarm run_3a9606089d82 remained active. See production-preservation.json. This proves the checked preservation boundaries, not that the unrelated business task is correct.

## Repairs

1. Git cleanup: persisted fetched source commit and actual target commit; cleanup rejects missing proof, changed branch/detached HEAD, or a new post-merge commit. Schema v3 adds nullable fields without fabricating legacy proof. Files: G:/工具/codex-orchestrator/plugin/src/orchestrator/db.py:417 and G:/工具/codex-orchestrator/plugin/src/orchestrator/workspace_mgr.py:1424.
2. Repeated cherry-pick: source/target ancestry proof, not whitespace-insensitive patch equivalence. A review found the latter could conflate different Python indentation; real RED tests were added and the implementation was reworked. Source-only merge commits require the merge strategy. Workspace manager:1184.
3. Dependency contract: link_ro_cache remains a writable shared link, explicitly not isolation. Isolated dependency writes use copy/fresh_venv_uv. Ordinary copy-versus-link write behavior has fixture coverage. Teamwork skill:41-48,85.
4. Boost Solo: coordinator repairs its own work; delegated work resumes the original coder; both reuse the original verifier. Boost skill:99-101.
5. Boost human text: read without advancing, record actual disposition, then advance the same bounded batch. Resume checks receipts/native state; no automatic-delivery or exactly-once claim. Boost skill:39-45.
6. Teamwork and shared workflow contracts: Git commits/clean checks apply to git_clone only; snapshot uses reviewed file diffs, actual tests and three-way integration. Inline contracts, dispatch briefs, README and static regression checks were updated consistently.

Items 2 and 6 include directly connected corrections needed to make the original five fixes complete; no mode-specific servers or new dependencies were added.

## Real native workflows

| Path | Actual failure | Rework and final result |
|---|---|---|
| Boost Solo, run_71cb359cf937 | Same verifier /root/boost_solo_gate, #2: order assertion failed, 1 failed / 2 passed | Coordinator repaired labels.py, tests unchanged, no coder invented. Same verifier #8: 3 passed plus focused whitespace test. Human message #4 was read, receipt #5 distinguished pending action, #6 recorded executed test evidence. Cursor survived the isolated-server restart. |
| Boost delegated Git, run_ee4f1264cc6a | Initial /root/boost_delegated_writer passed 23 tests. Explicit upper-bound fault injected; /root/boost_delegate_gate #9 found 2 failed / 21 passed | Same coder followup repaired and committed d3bfe0405758b98b9c96b33c1abf40f2efd3c9e5. Same verifier checked candidate, then independently checked real preview, merge, main 23 passing tests and cleanup; final full-run PASS #20. |
| Teamwork snapshot, run_1dcf68848f12 | Native interviewer froze requirements #3; native planner saved packet v2 and handoff #11. Two native coders independently passed IDs 25 / totals 10 tests. Exact planned +1 fault produced actual 3 failed / 7 passed totals at same verifier #23 | Original /root/teamwork_totals_writer repaired its original snapshot, no Git initialized. Same /root/teamwork_gate reran 25 + 10 tests, candidate PASS #27. Sequential IDs then totals previews/merges, coordinator-owned integration test, main 36 passing tests, both cleanups, final same-verifier AC01–09 PASS #29. |

Fault injection is explicit test stimulus, not a claim that these initial implementations spontaneously failed. All FAIL/PASS outcomes came from real command execution, not instructed verdicts.

The Teamwork gate also rejected two evidence/process mistakes: the coordinator initially used an empty-input -1 stimulus instead of the planner's frozen +1 stimulus (#21); it was corrected and rerun without changing the requirement. Totals writer messages #16/#25 had empty bodies due to a caller argument error; #26 supplied retained genuine command/output evidence using body_md and verified readback. These failed attempts remain in the board and artifacts rather than being erased.

Premerge gates certify applicable candidate criteria only. Merge, combined tests and cleanup remain explicitly pending until the same verifier's final full-run check. Runs were closed only after that check; all eight registered test workers are stopped and all three test workspaces are cleaned.

## Regression evidence

- Backend initial Git RED: 9 failed; migration RED: 2 failed; later indentation/target-history RED: 2 failed.
- Workflow contract RED: 12 failed; cursor-order revision RED: 1 failed / 11 passed.
- Final coordinator suite: `.venv/Scripts/python.exe -m pytest -q` — **87 passed in 50.43s**, exit 0. `node --check plugin/src/orchestrator/web/app.js` — exit 0.
- Independent final source suite: **87 passed in 49.50s**, exit 0. Targeted paired-proof/contracts suite: **46 passed in 23.37s**.
- Additional independent disposable probes covered a commit arriving after fetch, partial merge proofs, target ancestry and later-arriving human messages.

See full-suite.txt, observed-checkpoints.json, the three *-final.json snapshots, *-lifecycle.json integration receipts, preserved faulty candidates, actual HTTP records, and the retained test SQLite board.

## Rollout and limits

The source fixes and prior dashboard-backend optimization are not installed into the running production cache. Activation should be a separate coordinated step after active swarm work is finished; no hot replacement was attempted. Old Git workspaces without merge proof intentionally require verified re-integration before cleanup.

Cleanup assumes writers have stopped. Arbitrary extra local branches/reflogs and concurrent external Git modification between the check and deletion are not covered by these tests. Controlled fixtures demonstrate complete representative native paths, not every future business task, a live production MCP upgrade, or long-duration load.

For mode navigation, prefer separate Boost / Teamwork / Swarm entry pages and mode-filtered task lists on one backend/port. Separate ports alone do not isolate a shared database or improve agent dispatch. Separate processes and databases make sense only for independently operated installations. This round did not change navigation or add production ports.

## Final independent gate and shutdown

Independent overall verifier: PASS, no open acceptance criteria or confirmed P0/P1/P2 findings. It freshly reran all three integrated fixtures (3/23/36 passing), independently checked native board evidence and final database state, and rechecked installed hashes and live swarm preservation.

The isolated server PID 24444 was stopped after all reviews. Port 19890 is no longer listening; test SQLite quick_check remains ok. Production PID/start and installed hashes were checked again after shutdown and remain unchanged. See shutdown.json. Source fixes remain deliberately uninstalled while production swarm is active.
