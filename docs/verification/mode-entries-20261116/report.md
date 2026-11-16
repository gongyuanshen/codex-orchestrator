# Mode entry verification — 2026-11-16

## Outcome

Source-only change PASS. Keep production port 18790 and one daemon/database. `/` is an entry chooser; `/boost`, `/teamwork`, `/swarm` each show only their mode's tasks. Mode navigation uses native links, preserves project context and drops the previous mode's selected run. No additional dependency, production port or background service.

Explicit root links resolve to their actual mode. Old runs outside the latest 100 remain selectable through exact-ID links. Wrong run/mode/project metadata, snapshots and nested run updates produce visible errors before displaying task contents or opening its stream. Existing context-generation guards, lazy logs and late-message safeguards remain covered.

## Executed evidence

- Coordinator full regression: **118 passed in 52.10s**, exit 0 (`full-suite.txt`). Node syntax check exit 0.
- Independent reviewer: **118 passed in 52.12s**, exit 0; Node syntax check exit 0. No remaining P0/P1/P2 found (`review.txt`).
- Frontend: **41 passed** (`frontend-tests.txt`); backend HTTP module: **13 passed** (`backend-tests.txt`). RED evidence retained in corresponding `*-red.txt` files.
- Real source daemon on isolated **19890**, separate fixture database: three mode routes/lists/snapshots, exact old-run metadata, unknown run/path 404, empty mode project (`http-acceptance.json`).
- Real IAB browser: all three entries, mode-only selectors, native back/forward, empty project, old root link selecting the 101st list entry, wrong mode/project, unknown old link, root project preservation, manual refresh discovering only same-mode runs while retaining selection, and live Teamwork SSE update without the Swarm update (`browser-acceptance.json`).
- Desktop and **390 × 844** layouts, light/dark, no horizontal overflow; long log collapsed to a single summary. Screenshots retained; final chooser images end in `-final.png`. Browser checks before the final selector/copy refinement are retained rather than overwritten; final cases explicitly recheck that refinement.
- Browser QA found bootstrap errors left the project selector showing “加载项目中”. Added failing regression, fixed selector state/copy, and repeated browser verification. No silent fallback to another run.
- Impeccable static scan of the three web assets: `[]`, exit 0 (`design-scan.json`).

Fixture data is explicitly synthetic UI test data, not a claim of new native-agent execution. The prior real Boost Solo, Boost delegation and Teamwork fail/rework/pass lifecycle evidence remains in `../workflows-20261116/report.md` and is not substituted by this fixture.

## Production preservation and pending activation

`production-preservation.json`: production PID **7816** and `started_at` **1794815794.0946763** unchanged. All **29** installed files outside `.venv`/`__pycache__`, their hashes and the file set are unchanged. No production restart or installation update. The Swarm `run_3a9606089d82` remains active at the final check.

Isolated PID **3268** was verified by its port/data-directory command line before stopping. Test port 19890 is closed, fixture DB `PRAGMA quick_check` returned `ok`; production identity was rechecked after shutdown. No test writes were sent to production; only health and active-run reads.

**Installation remains deferred.** After the running Swarm ends, combine these entries with the already verified pending snapshot-performance and workflow/cleanup fixes; recheck live tasks, back up installed targets and runtime DB, activate together and verify the installed routes and migration. This turn did not create an automatic deployment monitor or claim installed acceptance. New task discovery still requires “立即同步”; SSE covers the selected task. The entries separate views, not access control or storage.

## Reproduction

From `C:\workspace\codex-orchestrator`:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --check plugin/src/orchestrator/web/app.js
$env:PYTHONPATH = (Join-Path (Get-Location) 'plugin/src')
.\.venv\Scripts\python.exe -m orchestrator.daemon --host 127.0.0.1 --port 19890 --data-dir 'C:\workspace\codex-orchestrator\docs\verification\mode-entries-20261116\data'
```

The last command runs the preserved isolated fixture service, not the installed service. In a second shell, run `.\.venv\Scripts\python.exe docs/verification/mode-entries-20261116/check_http.py`, then browse the isolated entry routes. `seed_fixture.py` intentionally refuses to overwrite an existing database.
