# Reliable transport and acknowledged control

> For agentic workers: use subagent-driven-development, with exclusive snapshot ownership and independent verification.

## Approved short design

User request: 我现在重新安装了新版，新版是改了UI界面，没动功能。你现在开始完善这个功能。
User approved: 提交停止请求 → 协调者调用原生中断工具 → 回执后更新状态；未执行明确显示待处理；同时修复连接和状态错误。

**Goal:** Make the existing plugin's transport and control behavior truthful and reliable without replacing its UI or Codex orchestration architecture.
**Architecture:** Keep SQLite/HTTP/SSE and native Codex agents. Persist the native agent binding and control requests. Only the coordinator can acknowledge execution after using native tools; the daemon never claims to kill a Codex process itself.
**Tech stack:** Existing Python 3.12, SQLite, httpx, MCP, Starlette and plain JavaScript. No new dependency.
**Approaches:** User chose acknowledged cooperative control over only correcting displayed state. A standalone Codex scheduler is excluded: no supported direct control interface is available to this daemon.

## Constraints and interfaces

- Preserve UI layout/styles, historical data, unrelated files and model/proxy configuration.
- Use additive schema migration. Optional `agent_id` on worker_register is the actual returned Codex agent target, never an invented ID.
- `Database.request_worker_control(worker_id, status)` returns `{id, run_id, worker_id, agent_id, requested_status, status, result, created_at, resolved_at}`; status is pending/applied/failed. Same pending request is idempotent; conflicting pending requests fail explicitly. Unbound worker or inactive run fails clearly.
- `Database.list_worker_controls(run_id)` returns retained requests. Board status adds `control_requests`.
- New MCP `worker_control_ack(request_id, outcome, result)` accepts outcome applied/failed and nonempty native-tool evidence/error; successful ack atomically updates requested worker status. Duplicate identical ack is idempotent; contradictory ack fails.
- Existing dashboard POST worker/status becomes request-only (HTTP 202), not immediate state mutation. UI renders pending/failed/applied separately; existing status tool remains coordinator reporting, not execution.
- Awaiting control must block unacknowledged status changes and agent rebinding; preserve pending request identity across restart.
- Stalled means currently working in active run and overdue, never a historical alert on a stopped worker.
- MCP errors remain visible; never replay an RPC whose side effects may already have happened. Busy/invalid health must not be interpreted as permission to spawn another daemon.

## Tasks / ownership

- [x] Transport worker: mcp_shim.py + test_shim_concurrency.py. RED first for request replay, health identity/busy conditions, module logger and error survival. GREEN minimal changes. Isolated snapshot, no live-service restart.
- [x] Control worker: db.py, board.py, rpc_handlers.py + test_worker_control.py/test_db_board.py. RED first for migration, pending/ack/rejection/duplicate/restart and stopped alerts. GREEN implementation.
- [x] Coordinator: daemon.py, test_daemon_web.py, three skills, README. Dashboard worker: web/app.js and tests/test_dashboard_behavior.py. RED HTTP request semantics and dashboard contracts, then wire exact interfaces and document coordinator lifecycle/control loop.
- [x] Merge each snapshot after conflict preview; run full suite and independent two-stage verifier; no claim of real Codex control until native interrupt + acknowledgement smoke test is observed.

## Verification and review focus

- Baseline: `.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider`: 23 passed (18.45s).
- Test a timeout after request acceptance without second execution; healthy wrong-service/wrong-data-dir and busy listening endpoint; shim subsequent call after error.
- Test old schema and terminal worker stale alert, duplicate/conflicting control, failed ack, pending request across restart, unbound targets and completion race.
- HTTP/UI test confirms 202 does not claim paused and failed result is escaped; check JavaScript syntax.
- Tests use temporary databases and distinct ports, never existing user runs. Keep model network outage diagnosis separate from local MCP fixes.

## Execution ledger

- 2026-11-15: User approved recommended control semantics; source baseline passes. Two non-git snapshot workspaces created via existing WorkspaceManager. No git repository exists; no branch/commit is manufactured.

- Implementation: transport, control and dashboard snapshots merged after conflict-free previews. Root HTTP and PID-lifecycle tests reproduced three failures before implementation.
- Independent review found P1: applied acknowledgement changed DB but omitted worker_updated SSE. Same control worker fixed it with real EventBus RED/GREEN tests; integrated full suite now 36 passed in 23.17s.
- Source/UI index.html and styles.css match installed new UI byte-for-byte. Model/proxy config untouched.
- User explicitly approved installed sync/service restart and will restart Codex themselves. Plugin (excluding regenerable .venv) and consistent SQLite backup: C:/Users/Administrator/.codex/backups/tri-orchestrator-20261115-224302.
- Remaining gate at this entry: independent recheck, installed hash/data validation, browser/native interrupt smoke, then final closure.

## Final acceptance — 2026-11-15

- Independent source gate: PASS, board #35; installed fresh stdio/hash gate PASS #36; final heading rework PASS #38.
- Browser exposed an additional stale title after selecting another run. One-line root fix refreshes top controls after applying the snapshot. New regression failed before the fix and passed afterward.
- Final full suite: 37 passed in 23.38s, exit 0. JavaScript syntax check exit 0. No new dependencies.
- Installed 9 scoped source/skill files via per-file atomic replacement after backup. Runtime restarted through the existing MCP lifecycle; old PID 30168 -> new PID 22764. The combined manual launcher was rejected by the execution layer; existing MCP cold-start succeeded. Codex itself was not restarted.
- Existing data preserved at migration: projects=2, runs=2, task_packets=7, workspaces=6, workers=8, board_messages=35, read_cursors=5. Compared every pre-install row using all original columns; SQLite quick_check=ok. Schema versions 1 and 2.
- Fresh installed MCP subprocess: initialize successful, 19 tools including worker_control_ack; intentional unknown tool returned isError, then run_list succeeded in the same session; all stdout JSON-RPC, failure logged. No daemon duplication.
- Native/browser acceptance run run_95ac81b1964c, worker wrk_524b29b99531, request ctl_0125664486f9: browser request remained pending and worker working; actual native interrupt returned previous_status=running and list_agents confirmed interrupted; only then applied acknowledgement changed worker to stopped. SSE updated browser to 已执行 / 已暂停 without refresh. Test run completed; records retained.
- Final installed browser retest: switch between development and acceptance runs updates title, tooltip, status and selected data correctly. Stopped worker is not shown as stalled after 6 minutes. Screenshot: 2026-11-15-native-control-acceptance.jpg.
- Source, installed app.js and actual HTTP /app.js bytes match. UI index.html/styles.css and model/proxy configuration remain unchanged.
- Backup and machine-readable evidence: C:/Users/Administrator/.codex/backups/tri-orchestrator-20261115-224302 (original plugin, consistent pre-install database, deployment-manifest.json, deployment-result.json, native-control-acceptance.json).

### Remaining operational boundaries

- User will restart Codex to refresh this existing chat's cached tool schemas and skills. Fresh installed-process loading was verified; user restart itself has not occurred during acceptance.
- Controls are cooperative: the coordinator must be running, read pending requests and execute native interruption. Pending is not paused; this is not a standalone daemon scheduler or an OS process killer.
- This fixes reproducible local MCP and state defects, not the separate upstream model/proxy network outage. The historical os error 10061 endpoint remains unproven.
- Historical workers without a native binding are intentionally not directly controllable. New registrations bind actual returned native targets.
- IPv6 live-network integration and mobile-layout regression were not performed. No Git release, commit, remote publication or unrelated code changes were made.
