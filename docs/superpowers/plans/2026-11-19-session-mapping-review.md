# Session Mapping Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the independent-review findings before integrating the `0.6.1` session-mapping release.

**Architecture:** Keep existing Schema 9 storage and `workflow5`; reconcile historical projects additively and route turn attribution through stored identifiers. Gate daemon startup separately from offline migration. Use the existing session catalog and one global stream to keep the dashboard current without changing old links or exact-snapshot APIs.

**Tech Stack:** Python 3.12+, SQLite WAL, Starlette, pytest, vanilla JavaScript, Node VM, Chromium.

**Spec:** [0.6.1 会话映射合入审查修复设计](../specs/2026-11-19-session-mapping-review-design.md).

## Global Constraints

- Preserve `0.6.1 / schema9 / workflow5`, tool names and strict requirements, and the original `GET /api/runs/{run_id}` response.
- Do not fabricate historical bindings, change old-column values or reorder old rows. Keep worktree and protected boundaries as documented in `HANDOFF_PROGRESS.md`.
- Do not call `18790` or write installed Codex/runtime data. Only T7 may opt into reading the real snapshot source. Use independent loopback ports and temporary data directories.
- Leave the current working tree and `HANDOFF_PROGRESS.md` intact; no staging, commits, push, or deployment without a later integration decision.
- Run the steps from `C:\workspace\test-worktree` with `.\.venv\Scripts\python.exe`. The `tests/conftest.py` isolation in Task 1 also protects the plain `pytest` entry.

## Review Focus

1. Prior schema with zero active rows is still an upgrade, not a fresh boot; Task 1 tests both active and finished snapshots and checks unchanged rows/schema.
2. A Git worktree slice and an independent nested Git worktree can both have `is_git=1`; Task 2 exercises both, including a Codex-only canonical parent and a similarly named unrelated root.
3. A delayed/replayed Hook can arrive after `run_create`, while a later prompt may already be stored; Task 3 tests scope, matching preview, event privacy and stale ownership.
4. An outside directory that shares a textual prefix or is reached through a symlink must not become a task's execution placement; Task 2 tests a sibling prefix and verifies failed registration is atomic.
5. History list truncation and global events during keyboard navigation are distinct from changing the selected task; Task 4 tests cache/fresh precedence, focus, and stale snapshot rejection after switching runs.

---

### Task 1: Gate Runtime Startup and Isolate Tests

**Files:** `tests/conftest.py` (new test isolation fixture), `plugin/src/orchestrator/db.py`, `daemon.py`, `mcp_shim.py` (one shared preflight), `tests/test_s4_upgrade_t7.py`, `tests/test_shim_lifecycle.py`, `README.md`.

**Interfaces:** Add `require_runtime_schema(db_path: Path) -> None` and `SchemaUpgradeRequired` in `db.py`. Call the guard before the daemon opens an owned `Database`, and in the MCP cold-start branch before `_spawn_detached_daemon`. Existing explicit `Database` migrations and `scripts/upgrade.py` retain their entry points.

- [ ] **Add test-process isolation.** An autouse `tests/conftest.py` fixture sets `CODEX_HOME` to a temp fixture home; test-specific and T7-specific overrides still work. Set `TRI_ORCH_DATA_DIR` and `TRI_ORCH_PORT` to temp/independent values for default paths unless a test explicitly exercises configuration defaults.
- [ ] **Write failing tests.** Add `test_cold_start_refuses_schema8_without_writing`, parametrized active/finished; seed the existing `_schema8_fixture`, assert startup reports `scripts/upgrade.py`, `MAX(schema_migrations.version) == 8`, and all old row hashes match. A new empty data directory must boot to Schema 9. Add `test_shim_reports_upgrade_required_before_spawning`: an isolated old state must raise the actionable error and never call the spawn boundary.

  ```python
  before = upgrade.database_snapshot(settings.db_path)
  with pytest.raises(RuntimeError, match=r"scripts/upgrade\.py"):
      with TestClient(create_app(settings, start_watchdog=False)):
          pytest.fail("Schema 8 reached daemon startup")
  assert upgrade.database_snapshot(settings.db_path) == before
  ```

- [ ] **Verify red.** `.\.venv\Scripts\python.exe -m pytest -q tests/test_s4_upgrade_t7.py tests/test_shim_lifecycle.py -k "cold_start or upgrade_required"` must currently show that cold startup reaches Schema 9 or enters the spawn branch.
- [ ] **Implement guard and docs.** Inspect with an existing-file SQLite `mode=ro` connection before the ordinary runtime connection can set WAL mode or migrate. A missing file or genuinely empty SQLite database is fresh; an older populated database (including legacy app tables without a version record) is not. Refuse newer/corrupt data rather than treating it as fresh. Keep the offline CLI's lock, backup, old-row verification, rollback and managed-cache protections. README distinguishes zero-script first install from upgrading a populated runtime, and does not direct users to overwrite a versioned cache.
- [ ] **Verify green.** `.\.venv\Scripts\python.exe -m pytest -q tests/test_s4_upgrade_t7.py tests/test_shim_lifecycle.py tests/test_shim_concurrency.py tests/test_routea_upgrade.py`; zero failures and real-machine opt-in skipped.

### Task 2: Preserve Project Identity and Constrain Execution Paths

**Files:** `plugin/src/orchestrator/db.py` (canonical reconciliation and create validation), `daemon.py` (reconcile owned runtime on startup), `rpc_handlers.py` (input description), `workspace_mgr.py` (guard historical paths on new workspace creation), `tests/test_schema9_migration.py`, `tests/test_s2_rpc_isolation.py`.

**Interfaces:** Add `Database.reconcile_codex_projects() -> None` using `codex_bridge.list_codex_projects()` as the only external metadata reader. Re-use `_refresh_canonical_projects()` for persisted projection. Add a shared `resolve_execution_root(execution_root: str, project_root: str | Path) -> str` in `db.py`, returning `""` for unused or the resolved absolute path; outside roots raise `ValueError` mentioning `execution_root`.

- [ ] **Write failing migration tests.** Seed a literal Schema 8 database with a Git parent, a nested slice carrying the inherited `is_git=1` flag but no `.git` marker, and an independent nested repository with a `.git` file. `test_legacy_git_slice_and_codex_only_parent_keep_original_ids` also registers a nested Codex project only in a fixture Codex catalog. Assert both slices point to the appropriate parent (the Codex project itself stays top-level), the independent repo is not absorbed, unrelated prefixes stay separate, and `require_old_rows()` and exact run/project IDs are unchanged. Re-run migration and boot Schema 9 with the same Codex metadata; no duplicate parent.
- [ ] **Verify migration red.** `.\.venv\Scripts\python.exe -m pytest -q tests/test_schema9_migration.py -k "legacy_git_slice"` must fail on the inherited Git flag or missing parent projection.
- [ ] **Implement reconciliation.** Detect a directory with its own Git marker, including a linked worktree; retain the existing `is_git` as a conservative hint only when the old directory cannot be inspected. Normalize path identity and use a strict ancestor check rather than a string prefix. Treat a known Codex project root as a canonical boundary and append only missing parents that will absorb stored rows. Do not rewrite old project names/Git flags. Do this on explicit legacy migration and on owned daemon startup for earlier Schema 9 copies; do not write during GETs. A later `ensure_project()` must not undo these associations. On missing/unusable Codex metadata, fall back to registered projects.
- [ ] **Verify migration green.** `.\.venv\Scripts\python.exe -m pytest -q tests/test_schema9_migration.py tests/test_codex_bridge.py tests/test_s3_api_sessions.py tests/test_s4_upgrade_t7.py`; zero failures.
- [ ] **Write failing execution tests.** `test_execution_root_rejects_outside_paths_atomically` creates a sibling with the same name prefix and, where supported, a child symlink to that sibling. Assert both `project_id` and `project_root` creation paths reject with no persisted run/project from the failed request; root/equal/nested cases still work. Cover a previously stored outside path by rejecting the creation of new workspaces, without modifying its stored value.
- [ ] **Verify execution red.** `.\.venv\Scripts\python.exe -m pytest -q tests/test_s2_rpc_isolation.py -k "execution_root_rejects"` must currently accept the sibling.
- [ ] **Implement path guard.** Validate after resolving the actual project within the create transaction and before persisting run state or creating a workspace. The default workspace placement still follows a valid `execution_root`; the source and merge paths remain in registered-project coordinates. Preserve explicit workspace `project_root` overrides when they are not using the run's separately stored `execution_root`.
- [ ] **Verify execution green.** `.\.venv\Scripts\python.exe -m pytest -q tests/test_s2_rpc_isolation.py tests/test_workspace_mgr.py tests/test_daemon_web.py`; zero failures.

### Task 3: Use Exact Turn Ownership and Minimize SSE Data

**Files:** `plugin/src/orchestrator/db.py` (turn lookups and snapshot projection), `rpc_handlers.py` (matching explicit turns and event shape), `tests/test_s3_api_sessions.py`, `tests/test_s2_rpc_isolation.py`.

**Interfaces:** Add `Database.get_session_turn(session_id: str, turn_id: str) -> dict[str, Any] | None`; keep `list_run_session_turns(run)` and the persisted `session_turns` columns. `session_updated` carries `run_id`/`session_id` and optional `turn_id`, not a turn object.

- [ ] **Write failing ownership tests.** `test_trigger_moves_from_provisional_run_without_adopting_unlinked_turns` drives Hook → create → snapshots with a passive out-of-project turn, an invocation provisionally attached to the old run, and a newer passive turn. Assert the new run gets the explicitly chosen prompt, the invocation appears only in the new snapshot unless both runs explicitly declared it, and out-of-project turns never enter either timeline. Check explicit unknown turn does not inherit a different preview. Replay an already claimed Hook after a subsequent run begins; it must not become the later task's turn. Replace the timestamp-only expectation in the old S3 test with explicit continuation ownership, keeping cross-session and update-time checks.

  ```python
  assert new_run["trigger_prompt"] == "start the new run"
  assert [turn["turn_id"] for turn in old_snapshot["session_turns"]] == ["passive-in-project"]
  assert [turn["turn_id"] for turn in new_snapshot["session_turns"]] == ["invoke"]
  ```

- [ ] **Write failing SSE expectation.** Extend `test_sse_global_and_run_scoped_lifecycle_events`: the unscoped turn sends only identifiers globally and does not reach a run-specific stream. When a provisional association is displaced, old and new subscribers get an invalidation for their respective run; verify these events contain no prompt or directory.
- [ ] **Verify red.** `.\.venv\Scripts\python.exe -m pytest -q tests/test_s3_api_sessions.py`; distinguish wrong prompt, leaked fields and double attribution from fixture/setup errors.
- [ ] **Implement attribution.** Look up the exact `(session_id, turn_id)` for explicit IDs; otherwise consider the latest observed turn as before, only if it has not already triggered another run and its known directory is in the selected scope. In the same transaction as run creation, attribute a matching stored trigger to the new run; historical/late Hook rows are still projected by explicit trigger IDs first. Only show a provisionally linked continuation if no other run in its session claimed the turn. Remove the timestamp-only ownership fallback. Emit identifiers only, invalidating both previous and newly affected run snapshots when attribution moves.
- [ ] **Verify green.** `.\.venv\Scripts\python.exe -m pytest -q tests/test_s3_api_sessions.py tests/test_s2_rpc_isolation.py tests/test_s2_hooks_isolation.py tests/test_schema9_migration.py`; zero failures.

### Task 4: Make Dashboard Updates Reliable Across Tabs

**Files:** `plugin/src/orchestrator/web/app.js`, `index.html`, `styles.css`, `tests/test_s3_web_ui.py` and the existing `test_dashboard_behavior.py` VM harness if needed.

**Interfaces:** Keep DOM IDs and existing selector functions. `connectGlobalEvents()` owns one shared stream for S3-capable tabs; `connectEventSource(runId)` reuses it when enabled or uses the old scoped endpoint for legacy responses. Existing global/run APIs stay intact. Add `#projectWriteRisk` for persistent warning in the selected project context.

- [ ] **Write failing catalog test.** `test_t6_context_change_keeps_cached_active_runs_outside_page` serves a full session catalog with an older active run omitted from the next limited `/api/runs` result. Switch project/mode and select active filter; assert the old active remains visible and visible fresh rows/statuses override cached ones.
- [ ] **Verify catalog red.** `.\.venv\Scripts\python.exe -m pytest -q tests/test_s3_web_ui.py -k "keeps_cached_active"` must fail because the active run disappears after the switch.
- [ ] **Implement catalog merge.** In `loadRuns(projectId, preferredRunId, exactRun)`, merge matching catalog rows into the fresh list before default selection, only using cached rows for IDs absent from the fresh list. Preserve request order, exact-link loading and legacy fallback.
- [ ] **Verify catalog green.** Re-run `.\.venv\Scripts\python.exe -m pytest -q tests/test_s3_web_ui.py -k "keeps_cached_active"`; zero failures.
- [ ] **Write failing stream test.** `test_t6_single_stream_updates_two_scopes_and_ignores_previous_run` boots a modern page using the real JS controller and VM EventSource boundary. Assert one live stream, unchanged through run switches; nonselected board/workflow messages cannot alter current details, while global events still refresh the catalog. Include re-open synchronization. Preserve all existing scoped SSE legacy tests.
- [ ] **Verify stream red.** `.\.venv\Scripts\python.exe -m pytest -q tests/test_s3_web_ui.py -k "single_stream"` must observe a second live stream.
- [ ] **Implement stream reuse.** Enable global mode from actual S3 catalog support, not `project_kind` alone; close any existing run-scoped stream before switching. Share event handling across global and legacy sources in `app.js`, compare `run_id` with the current selection, retain context/request version guards, and coalesce invalidation. Re-sync both scopes on re-open.
- [ ] **Verify stream green.** `.\.venv\Scripts\python.exe -m pytest -q tests/test_s3_web_ui.py tests/test_dashboard_behavior.py`; zero failures.
- [ ] **Write failing interaction tests.** `test_t6_live_updates_preserve_selector_focus` focuses a project item, session summary/run button and palette result, updates activity, then checks the same stable ID remains focused (or its local search field when removed). Add a test showing two active Runs in one session are counted separately from one active session, and the write-risk warning stays visible after navigating to Logs.
- [ ] **Verify interaction red.** `.\.venv\Scripts\python.exe -m pytest -q tests/test_s3_web_ui.py` must show detached focus and missing distinct activity/warning in the added regressions.
- [ ] **Implement interaction fixes.** In `renderProjectPopover()`, `renderRunSidebar()` and `renderCommandPalette()`, skip unchanged redraws; otherwise restore visible focused items by stable ID, not list index. Use distinct session counts when available, while retaining total active tasks and existing fallback labels. Render `#projectWriteRisk` at top-bar level and keep the existing Overview warning.
- [ ] **Verify interaction green.** `.\.venv\Scripts\python.exe -m pytest -q tests/test_s3_web_ui.py tests/test_ui_redesign_dashboard.py`; zero failures.
- [ ] **Verify browser behavior.** Use a temporary daemon with synthetic data only; switch cross-mode and cross-project, exercise keyboard focus during an unrelated live update, verify warning visibility across tabs against light/dark styles, and check one `/api/events` connection per tab.

### Final Integration Gate

- [ ] `.\.venv\Scripts\python.exe -m pytest -q -rs --tb=short`: inspect all failures and skips; runtime tests use isolated ports/data. Re-run the explicit T7 using the documented temp scenario; verify project collapse, retained binding, and no live database write.
- [ ] Confirm `node --check plugin/src/orchestrator/web/app.js`, both `uv lock --offline --check` commands, offline wheel build and extracted-wheel smoke test. Recheck `git diff --check` and excluded handoff file.
- [ ] Request read-only follow-up review on the actual patch; no integration if important findings remain. Present integration options after fresh green results; no automatic commit or push.
