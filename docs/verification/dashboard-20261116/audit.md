# Dashboard and active-run audit — 2026-11-16

## Read-only production observation
- Local daemon health: OK, PID 18676, started_at 1794806028.4241326.
- SQLite quick_check: ok. Six recorded runs, only F03 active. Ten registered F03 workers: nine stopped, one waiting. No pending control request. These are registered states, not proof of native agent liveness.
- The separate Codex F03 chat was active during both compact status observations; its commentary progressed from equip validation to cold-start account restoration. No message/control was sent to it.
- Several verifier board posts are newer than its registered current_task/status. The last task still refers to an earlier stop boundary. Board posting is not a heartbeat or native scheduling event; do not infer working/stopped from prose. Resume must explicitly register/update actual worker state. This audit does not rewrite that live state.

## Confirmed defects
1. Selection changes leave the old title/messages/members visible until snapshot completion. A/B inverted replies can reconnect SSE to A while B remains selected; project replies can similarly replace the newer project's run list. Deterministic Node VM reproduction confirmed all three cases.
2. Human-submit response handling lacks context ownership. A late receipt can enter a newly selected run; identified from code, no real message sent during audit.
3. Summary endpoint scans every non-cleaned workspace including merged snapshots. Measured production F03 request: 31.828 s, 6,781,797 bytes, 174 messages, six workspaces. Internal manifest and raw JSON duplicate accounted for 5,505,752 bytes.
4. board_status transports the same internal manifests. Real shim log recorded a 5,825,099-byte status result at 14:38:44 +08, delivered as a large-result file.
5. Full message Markdown is always rendered; visible selector labels are hidden by sr-only. Unknown sender roles default to swarm_worker. These explain user-visible density and misleading role meaning.

## Backend correction and evidence
- Board summary excludes snapshot_manifest and snapshot_manifest_json without altering database storage or workspace merge/diff algorithms.
- Dashboard snapshot no longer invokes workspace_status or fabricates a zero count after a filesystem error. Missing count is explicitly not inspected; detailed diff remains on demand.
- New regression first failed on all four cases (manifest inclusion, active/merged scan, cleaned fake count). Test setup initially omitted required title, corrected before recording actual RED. Affected backend suite: 20 passed in 6.96 s.
- New backend against an online SQLite backup, watchdog disabled: snapshot 0.051 s / 1,317,933 bytes; board_status 0.017 s / 107,998 bytes. The backup had 175 messages, one more than the production baseline; these are observed measurements, not an identical-payload benchmark or live activation proof.
- Evidence: snapshot-after.json, board-status-after.json, formal-health-before.json. Preview data is a consistent copy, never the live database.

## Delivery boundaries
Do not treat source tests or preview as installed backend acceptance. The formal daemon must not be restarted during active F03. Frontend may be atomically updated independently because static resources are read from disk per request. No model/proxy/global config changes, F03 business edits, database row rewrites or release publishing.

## Integrated source and browser checks
- Coordinator: full pytest 63 passed in 36.88 s; node --check passed; Impeccable mechanical detector returned []. Implementer separately ran 63 passed in 38.16 s.
- Browser preview: default entry selected active F03 instead of completed fixture. Light/dark desktop at 1280x720 and narrow layout at 390x844 visually checked. Narrow document and overview scrollWidth both 390 (no horizontal overflow).
- Recent-update click switched to logs and expanded one message; DOM inspection found exactly one nonempty Markdown body out of 175 message containers. Closed bodies are lazy-rendered, not only clipped by CSS.
- Task goal collapsed to three lines, paused/terminated members collapsed, recent six announcements visible in a second desktop column. Full content remains reachable. Human message composer remains collapsed until requested.
- No production message/control submissions were performed. Automated Node tests cover receipt context/draft protection; real user-message delivery and destructive workspace actions were not exercised against F03.
- Existing list APIs return at most 100 runs by default. An explicit old run outside that list may produce an explicit not-found selection error; this change does not implement history pagination.

## Final review and frontend installation
- Independent frontend review found one P2: stale old-backend worker count overrode newer on-demand diff. Fixed with a new failing regression (0 versus expected 2), then passed 13 dashboard tests; independent scoped re-review PASS.
- Final coordinator full suite: 64 passed in 36.25 s. Implementer: 64 passed in 36.39 s. JS syntax passed.
- Three web assets installed by per-file atomic replacement after checking each installed file still matched its backup. Source, installed and actual HTTP bytes match for all three; deployment.json records SHA-256 and sizes.
- Formal health remains OK with PID 18676 and identical started_at. Installed board.py/daemon.py remain unchanged. Backend performance fixes are source-only pending an idle activation window, not silently deployed or restarted.
- Formal browser acceptance: initial loading state showed the correct selected F03 title and no prior fixture data; loaded overview showed 177 logs and actual registered state (one waiting, eight stopped, one working). Screenshot: formal-overview.jpg. This later state naturally differs from the initial read-only audit, not a mutation by this task.
- Isolated preview process PID 19388 was stopped after checking its command-line data directory and port 18791. Formal daemon health remained OK, PID 18676. No preview process intentionally left running.
