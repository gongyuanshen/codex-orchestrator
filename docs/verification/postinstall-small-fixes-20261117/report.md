# Post-install small fixes — 2026-11-17

## Scope and result

The user approved three installation follow-ups: repair the broken command entries,
verify a fresh native session, and manually inspect the three dashboard entries.
These checks passed. No second-batch mode development or plugin reinstall occurred.

## Command entries

- Reproduced the original failure: both existing codex.cmd files pinned the deleted
  extraction directory 6257d9e8f63c0f2b; codex --version exited 1.
- Backed up both originals, then atomically replaced only those existing files:
  C:/ProgramData/chocolatey/bin/codex.cmd and
  C:/Users/user/.local/bin/codex.cmd.
- The new entry resolves exactly one registered OpenAI.Codex Appx package and its
  app/resources/codex.exe. It does not sort cache directories, hardcode a version,
  install a second CLI, add a third launcher, or change PATH.
- Zero/multiple registered packages or a missing executable fail explicitly. The
  existing argument forwarding and CLI exit code are retained.
- The registered executable reports codex-cli 0.166.0-alpha.5.1 and has the same
  SHA-256 as the working desktop extraction-cache executable.
- Actual CMD and Windows PowerShell processes passed using only Machine + User
  persistent PATH, without the desktop host's process-only CLI PATH entry. Both
  explicit launchers passed version, quoted Unicode/space path, and invalid-argument
  exit-code checks. Package-count and missing-file errors used isolated registry
  function fixtures; no real Windows package registration was changed.
- Real plugin list through the repaired entry confirms installed/enabled 0.2.0.
  Independent readback confirmed both original backups and unchanged launcher ACLs.

Evidence: before.json, backup.json, replacement.json, codex.cmd,
check_launchers.py, launcher-test-red.txt, candidate-test.txt,
launcher-test-green.json, plugin-list-after.json, review/installed-test.json,
review/independent-backup-acl-check.json.

## Native host and dashboard

- Fresh desktop child /root/fresh_tools_check actually loaded 27 native tools,
  versus the existing root session's contemporaneous cached 19-tool catalog.
- Native run_list and board_status calls succeeded. A completed historical run's
  inbox_wait(timeout_ms=0) with an existing recipient returned run_closed. An initial
  unregistered-recipient 404 is preserved, not hidden or converted into success.
- The current active Boost task had already been explicitly resumed by another
  chat. Its native run ID/status/latest_seq=309 matched the contemporaneous HTTP
  snapshot. No state correction, inbox wait, control request or write was applied
  to that active task.
- /boost, /teamwork and /swarm returned HTTP 200 with installed HTML. Mode API lists
  partition the eight historical tasks correctly: Boost 4, Teamwork 0, Swarm 4.
- The user manually opened the three URLs and replied exactly: 页面正常. This is a
  user-reported manual check, not an automated screenshot/accessibility acceptance.
  No populated Teamwork task was available for this manual inspection.

Evidence: native/report.md and its indexed raw responses; entry-acceptance.json.

## Regression and verification-side cleanup

The independent full suite's first command put basetemp inside this Git repository.
Snapshot fixtures then discovered the parent repository and took the git_clone path:
341 passed, 7 failed. The full failing output is retained in review/full-suite.txt.
No product source/test code was changed to make this green.

Rerunning the full suite with basetemp outside the repository passed:
348 passed in 65.76s, exit 0; review/full-suite-normal-temp.txt.

The first command also created ten test-only clones under the repository's workspace
directory and appended one local Git exclude rule. Their ownership was traced to
that command's isolated SQLite fixtures. The ten clones were moved, not deleted,
to C:/workspace/.test-backups/postinstall-small-fixes-20261117/first-suite-workspaces.
The three November-15 workspaces were retained. Only the attributable newly appended
exclude suffix was removed with an atomic backup; all preceding bytes were kept.
No pre-test exclude snapshot existed, so complete historical-byte identity is not
claimed. A post-move PowerShell comparison-precedence error was corrected by an
independent readback; no move was replayed. The detailed cleanup evidence is retained.

Evidence: review/first-suite-workspace-side-effects.json,
test-workspace-move-plan.json, test-workspace-cleanup.json, test-exclude-cleanup.json.

## Preservation and boundaries

- Production remains the same PID 24760 and started_at 1794847036.1577575 on 18790,
  version 0.2.0/schema 4. No service/app restart or desktop automation occurred.
- All 33 installed package files match the frozen installation manifest; global
  config.toml matches the start-of-this-turn hash. No production business test
  fixtures, task/workflow/control writes or automatic task resume occurred.
- Original command backups are recorded in backup.json under
  C:/Users/user/.codex/backups/codex-command-entries-20261117-010418.
- Old root-chat hot-refresh is not claimed. Active inbox delivery/rework/acceptance
  chains were not repeated in production. Full manual responsive/accessibility
  coverage and a populated Teamwork dashboard remain untested this round.
- If a future desktop package changes its internal CLI layout, this launcher will
  report the missing file rather than silently selecting a different executable.
- Second-batch Boost/Teamwork/Swarm enhancements remain pending in the approved plan.

Final readback: the repaired command still works, production PID/start time and
config hash remain unchanged. The active-run list became empty during this work;
the separately observed task state is retained in other-task-final-state.json.
This turn issued no task/control writes and does not claim completion of that
other chat's work. Independent final verdict: PASS; review/report.md.
