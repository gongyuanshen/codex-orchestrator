# Task-first Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkboxes for tracking.

**Goal:** A task-first readable console with context-correct live updates and lightweight status responses.
**Architecture:** Retain existing HTML/CSS/JS rendering and BoardService/daemon API. Fix summary boundaries rather than disk algorithms.
**Tech Stack:** Vanilla JS, CSS, Python, Starlette, SQLite, pytest and Node.
**Spec:** ../specs/2026-11-16-dashboard-design.md

## Global Constraints
No new dependencies. No changes to F03 business files, live database, daemon lifecycle or global configuration. No implicit success/zero fallback. Preserve all workspace operations. Existing checkout is not Git; keep file backups instead of fabricating commits/worktrees.

## Review Focus
- A/B/A selections and late project/snapshot/SSE responses retain the current context.
- Failed loads never display old data as current or enable misleading sends.
- Long/empty/malicious messages are bounded, expandable, escaped, and remain stable through updates.
- Unknown workspace state is not rendered as clean; missing roles/tasks remain explicitly unrecorded.
- Narrow screens and keyboard access retain all secondary operations.

## Task 1 — Frontend (worker ownership)
Files: plugin/src/orchestrator/web/{index.html,app.js,styles.css}, tests/test_dashboard_behavior.py.
Interfaces: existing snapshot fields and GET /api/projects, /api/runs; missing unmerged_files_count means not checked, not zero. Workspace diff remains authoritative on demand.
- [x] Add failing regressions for delayed/failed/context-switch loads and collapsed messages.
- [x] Implement approved overview and role explanations, collapse full messages, retain secondary capabilities.
- [x] Apply request generation checks at selection and post-response boundaries; preserve explicit URL context.
- [x] Run dashboard tests and JS syntax check: 13 dashboard tests; independent final spec/quality PASS.

## Task 2 — Summary backend (coordinator ownership)
Files: plugin/src/orchestrator/{board.py,daemon.py}, tests/{test_db_board.py,test_daemon_web.py}.
Interfaces: board_status workspaces contain public metadata but no snapshot_manifest or snapshot_manifest_json; dashboard snapshots never call workspace_status. Counts absent until inspected.
- [x] Add failing tests asserting manifest omission, preserved internal manifests, and no snapshot scan.
- [x] Filter internal fields at summary boundary; remove synchronous workspace scans and false-zero exception path.
- [x] Run affected tests: 20 passed; independent backend/workspace/control/transport review: 45 passed. Full integrated suite follows frontend completion.

## Task 3 — Integration and delivery (coordinator + verifier)
- [x] Review source against backups, run full pytest, syntax and rendered desktop/narrow checks. Final coordinator suite: 64 passed in 36.25s.
- [x] Verify production health remains unchanged and document audit facts and limits.
- [x] Atomically back up/install frontend only after verification; all three source/installed/HTTP bytes match. Backend activation waits for an idle service window. No restart during F03.

## Evidence
Baseline: 49 passed in 36.09s. F03 snapshot: 31.828s / 6,781,797 bytes, 5,505,752 bytes of duplicated manifests. Deterministic Node reproduction confirmed stale title/body, wrong final SSE after A/B response inversion, and cross-project late response replacement.
Backups: docs/backups/dashboard-20261116/.

Backend gate: independent verifier spec/quality PASS; no findings. Source preview uses an online database backup on port 18791, watchdog disabled, not the formal data directory. Measured snapshot 0.051s and board_status 0.017s. Formal PID remains 18676. No Git commits are possible in this directory without initializing a repository; none created.

Frontend review round 1 found P2: old-backend worker count overrode a newer on-demand diff. Added RED 0 != 2 regression, changed diff precedence, GREEN, independent re-review PASS. No other findings. Desktop goal/retired members were additionally collapsed after visual review so recent activity appears on the first screen.

Remaining activation work: install the verified board.py/daemon.py source and reload the daemon once F03 is idle, then measure formal snapshot and actual MCP board_status. This was intentionally not done in the active-task window. No automatic reload or future scheduling was created.
