# Installed 0.1.2 activation and three-entry acceptance — 2026-11-16

## Delivered

The official Codex plugin installer selected and enabled **tri-orchestrator 0.1.2**. The installed package is `C:/Users/Administrator/.codex/plugins/cache/tri-orchestrator/tri-orchestrator/0.1.2`. Its 29 packaged files match the source package byte-for-byte. Imports, editable distribution metadata and MCP server version resolve to this installed package, not the source checkout.

This activation includes the earlier overview/log-folding and snapshot-performance fixes, Boost/Teamwork rework and workspace contracts, Git merge/cleanup proofs, and the three mode-filtered entry pages. Their original source and native-agent evidence remains in `../dashboard-20261116/`, `../workflows-20261116/report.md` and `../mode-entries-20261116/report.md`. Those earlier reports describe their historical source-only boundary; this report records the subsequent installation.

- `http://127.0.0.1:18790/`: mode chooser; opening a page does not start work.
- `http://127.0.0.1:18790/boost`: Boost tasks only.
- `http://127.0.0.1:18790/teamwork`: Teamwork tasks only.
- `http://127.0.0.1:18790/swarm`: Swarm tasks only.

## Activation and preservation

The active-run API was empty and the associated native Swarm chat had finished before installation. Only the orchestration daemon was restarted, under its existing `daemon.lock`: PID 7816 was replaced by PID 28572, which started at `1794830312.6477973`. No native agent or business process was stopped, resumed or rewritten by this deployment.

Before activation, original packaged files and `config.toml` were backed up under `../../backups/installed-20261116/`. SQLite's backup API created a consistent `state-before.db` there. The database migration changed the application's `schema_migrations` maximum from 2 to 3; `PRAGMA user_version` is not this application's schema tracker and remains 0.

All pre-existing rows and columns are unchanged: 4 projects, 7 runs, 291 messages, 31 workers, 28 task packets, 21 read cursors, 16 workspaces and 1 control request. The only schema/data additions are migration row 3 and two nullable workspace merge-proof columns. Legacy proof values remain NULL rather than fabricated. SQLite `quick_check` is `ok`; foreign-key checks are empty. The checks were repeated after browser acceptance. `config.toml` is byte-identical to its pre-install backup.

Evidence: `activation.json`, `active-before.json`, `db-before.json`, `db-after.json`, `independent-db-preservation.json`, `config-preservation.json`, `plugin-after.json`, `mcp-after.json`, `independent-hashes.json`.

## Installer interruption and resolved recovery

The first candidate was 0.1.1. A metadata review then found the MCP handshake version hardcoded to 0.1.0. A real failing regression test was added, and the shim now uses the package's `__version__`. Refreshing the occupied 0.1.1 cache with the supported installer failed with Windows access denied. The final package was bumped consistently to **0.1.2**, and the supported installer succeeded in a fresh version directory. Locked dependencies were synchronized in that installed directory; no new dependency was added.

The host installer also automatically pruned files belonging to the old 0.1.0 runtime. This temporarily broke an already-connected old MCP bridge: HTTPX could not load its missing Certifi certificate file. The existing daemon was healthy, but that bridge's actual `run_list` failed. Original 0.1.0 package files were restored from the pre-install backup; the original locked dependencies were rebuilt from the offline cache without replacing its locked Python launcher or disabling TLS verification. A direct `uv venv` attempt had failed on that locked launcher; the successful recovery used locked, hashed dependencies installed into the existing environment.

The same existing native MCP connection then successfully returned all 7 runs. Its restored 0.1.0 files support that already-loaded bridge only; the selected plugin and production daemon are 0.1.2. The old bridge's success is not presented as a fresh 0.1.2 handshake test. No uninstall, whole-app restart or global configuration rewrite was used.

Evidence: `metadata-red.txt`, `metadata-green.txt`, `plugin-refresh.txt`, `plugin-final-add.txt`, `uv-sync.txt`, `old-runtime-repair.txt` (failed attempt), `old-dependencies-restored.txt` (successful recovery), `restored-old-files.json`, `existing-mcp-recovery.json`.

## Verification

| Layer | Actual result |
|---|---|
| Full source regression | Independent run: **119 passed in 50.42s**. |
| Installed backend | Tests imported installed 0.1.2 with source pythonpath disabled: **14 passed in 6.92s**. Installed JavaScript syntax check exited 0. |
| Package identity | All 29 installed package files match source; Python package, distribution and MCP server metadata are 0.1.2. |
| Real installed stdio MCP | Fresh process using installed Python and modules: initialize reports `tri-orchestrator 0.1.2`; lists 19 tools; actual `run_list` returns 7 total / 3 Boost / 0 Teamwork / 4 Swarm. Mode ID sets partition the full list. Probe exits 0 with empty stderr; production PID/start time unchanged. |
| Live HTTP and assets | Root and all three entry routes return 200; HTML, JS and CSS bytes match installed assets. Unknown entry returns 404. |
| Live filtering | 3 Boost, 0 Teamwork and 4 Swarm runs; each filtered response contains only its requested mode. Exact-run metadata and project IDs match. No active runs. |
| Browser navigation | Root chooser opens Boost. Switching to Teamwork preserves the chosen project but clears Boost's task data and displays the true empty state. Direct Swarm opens the latest applicable Swarm task. |
| Existing long logs | Swarm's 38 log disclosures are all closed initially; opening message 291 opens exactly one disclosure with 894 text characters. Overview remains the default view. |
| Legacy link | `/?run_id=run_4d2e3c025fb7` resolves to `/swarm` with the same F03 run and correct project; no unrelated task is selected. |
| Browser errors | Captured warning/error log list is empty. Temporary acceptance tab was closed. |

Evidence: `independent-suite.txt`, `independent-installed-backend.txt`, `independent-imports.txt`, `independent-live-http.json`, `browser-checks.json`, `entry-chooser.png`, `boost.png`, `teamwork-empty.png`, `swarm-overview.png`, `swarm-log-expanded.png`.

The separate real stdio probe and complete response evidence are `stdio-acceptance.py`, `stdio-result.json` and `stdio-output.txt`. `final-live-state.json` records the final healthy daemon identity, no active runs and the unchanged configuration hash. The independent old-package audit also confirms all 29 restored 0.1.0 package files match the original backup (`independent-old-package.json`).

## Boundaries

This was an installed-package and read-only production-entry acceptance, not another business-task execution. No test run, message, cursor or workspace was inserted into production. Teamwork has no production history; its populated workflow and deliberate failure/rework paths were already exercised with real native agents on isolated data in the earlier workflow report. The deployed code was checked by hashes and installed-backend tests, without replaying those workflows against production.

Existing chats may retain skill instructions already loaded into their context; installing new skill files does not retroactively rewrite those messages. New invocations use the selected 0.1.2 package. Historical Git workspaces without the new merge proof still require verified re-integration before cleanup. Long-duration soak testing and a whole Codex application restart were not performed.

## Final independent gate

The independent two-stage review returned **PASS**, with no remaining confirmed P0/P1/P2 findings. See `independent-review.txt` for independently executed checks versus reviewed browser/stdio evidence. The production daemon remains running; only temporary acceptance clients were closed.
