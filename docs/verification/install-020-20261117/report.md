# Local 0.2.0 activation and desktop-native acceptance — 2026-11-17

## Delivered

**PASS:** official Codex plugin installation selected and enabled tri-orchestrator0.2.0. The installed package is C:/Users/user/.codex/plugins/cache/tri-orchestrator/tri-orchestrator/0.2.0. Production127.0.0.1:18790 reports software0.2.0/schema4, PID24760. All33installed packagefiles match the frozen source.

The root mode chooser and /boost,/teamwork,/swarm return200 with installed HTML. JS/CSS bytes match the installed assets. Mode lists partition all8historical tasks:Boost4/Teamwork0/Swarm4. This is real HTTP/API acceptance, not visual browser acceptance.

## Explicit stop, backup and activation

The user confirmed that the other startup/reconnection task had been stopped. Purpose-built desktop thread reads confirmed its parentidle/turninterrupted and both childrenidle/completed. The paused plugin run run_aa711e5bb5ed was saved with its packet version, original worker states and stop proof, then archived for explicit resume. It was **not** markedcompleted, its native chat was not archived, and no agent was woken or interrupted by this deployment. Two workflow worker states were updated to stopped to match the completed/interrupted native execution; an informational deployment notice was appended.

Private rollback material is stored outside the Git checkout, at the path in locations.json: complete0.1.0/0.1.2caches, original managed roles, config.toml, and consistent SQLite backups both before the user-pause checkpoint and before migration. No secret configuration contents were copied into public project evidence.

Activation used the existing daemon startup mutex and an exclusive reservation of18790 after stopping only the verified idle orchestrator daemon22612. The desktop application and its App Server processes were not stopped/restarted. The supported built-in CLI command plugin add tri-orchestrator@tri-orchestrator --json returned0.2.0 and exit0. The fresh0.2.0version directory was used rather than rewriting an occupied cache directory. uv sync --locked --no-dev populated its runtime; installed Python/distribution/module identity was verified before schema4migration.

The official installer pruned old runtime files. They were restored from the private backup while startup remained gated, so already-loaded old MCP clients did not lose their files. Independent readback checked1774non-bytecodefiles for0.1.0 and1779for0.1.2. They match the backup. Current selected release remains0.2.0; retaining old client support files is not selecting an old release.

All6global role files matched the old managed originals before update, so their new package versions were applied atomically after backup. config.toml is byte-identical to the immediate preinstallation backup. No model, proxy, credential or unrelated plugin configuration was rewritten. The broken codex.cmd launchers were deliberately not changed.

The startup mutex does not provide a general HTTP maintenance admission mechanism: direct HTTP callers or requests already forwarded before the lock could bypass it. This cutover used the independently observed user-stopped parent/children, no pending controls, a zero-active-run check before stopping, and offline port reservation. It is a bounded local cutover, not a claim of atomic maintenance against arbitrary external writers.

## Actual evidence layers

| Layer | Observed result | Evidence |
|---|---|---|
| Official selection |0.2.0installed/enabled; fresh version path | official-plugin-add.json,plugin-selected-after.json |
| Installed imports/migration |actual installed module/distribution0.2.0;schema4 | installed-migration.json |
| Installed backend tests |5core modules proved loaded from cache;160passed,1pytestpreimportwarning;exit0 | installed-backend-tests.txt |
| Installed JavaScript |node --check exit0 | coordinator command transcript |
| Fresh direct stdio MCP |actual installed Python/runtime;initialize0.2.0;27tools;read-onlymode/closed-run checks | fresh-installed-stdio.json |
| Current desktop native child |27tools loaded;7actual native read-onlycalls,including newlyadded inbox_wait;no SDK/HTTP/CLI substitution | installed-native-results.json,installed-native-report.md |
| New native tool |inbox_wait(timeout_ms=0) on archivedrun returnedrun_closed, actual25ms;no execution or fake receipt | installed-native-results.json |
| Existing root connection |original native run_list returned active0 after old runtime preservation;root catalog still19 | existing-root-native-read.json |
| Live entry/API/assets |3entry routes/chooser correct,assetsbyteidentical,modepartition4/0/4 | live-http-entries.json |
| Production history |allpre-migrationoldcolumns/rowsunchanged;allhistoryworkflow_version0;4newbusiness tablesempty;integrityok | final-live-preservation.json,audit-installed-preservation.json |
| Independent final gate |PASS for actual local activation/read-onlyacceptance | audit-final-report.md |

The160-test run emitted one PytestAssertRewriteWarning about already-imported anyio; it was retained, not suppressed. It did not report failed/skipped tests. Those tests used temporary data while importing production-installed modules; they did not create business fixtures in the production database.

The earlier348-test/fullnativefailure-rework-merge chain is recorded separately under ../route-a-20261116/. Its source hashes match this installation. No artificial business run, message, cursor or workspace was inserted into production to repeat that chain here. The only pre-migration task changes are the user-approved pause checkpoint, two worker state updates and one deployment notice; the actual migration adds version4andlegacyworkflow_version0withoutfabricatedPASSrecords.

## Why the CLI observation was correct

Get-Command/where found two PATH launchers, C:/ProgramData/chocolatey/bin/codex.cmd and C:/Users/user/.local/bin/codex.cmd. Both contain an absolute reference to .../Codex/bin/6257d9e8f63c0f2b/codex.exe, which does not exist. An actual codex --version returned The system cannot find the path specified. The executable inside the current desktop application directory22cfb1d302613eae exists and reports codex-cli0.166.0-alpha.5.1.

Thus the failed item is the stale command launcher, not proof that ChatGPT itself or the current bundled executable is broken. A desktop update leaving an old pinned path is a plausible explanation; the installer that created those launchers was not traced, so attribution is not asserted.

CLI commands provide the supported plugin installation interface and reproducible program-level checks without controlling the user's desktop. A separate App Server/stdio client is not the same live desktop session, and its success alone is not desktop acceptance. Accordingly this deployment also tested the new tools through an actual fresh child in the current desktop host. Official protocol reference: https://learn.chatgpt.com/docs/app-server .

## Limits and next work

- Old root-chat tool/skill hot-refresh was not proven; it still listed19cached tools during this turn. New desktop children loaded27and invoked the new tool successfully. No whole-app restart was needed for that proof.
- GUI appearance/settings badge/manual visual accessibility were not operated or accepted. The user remains in control of any UI action.
- The archived startup/reconnection task remains stopped and requires a new explicit resume; installation does not fix its networking question or resume its work.
- The stale codex.cmd launchers remain unchanged. Repairing those entrypoints is separate from this plugin installation.
- Second-batch mode specialization and its full native workflows remain pending. No GitHub release was created or changed.
