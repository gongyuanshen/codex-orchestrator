# Windows SDK daemon lifetime repair — 2026-11-17

## Current result

**PASS for the requested source repair, sustained verification, production installation and post-restart desktop reconnection. The user restarted Codex; this chat successfully called the actual desktop MCP tools. First-batch closeout is complete. Stop before M3.**

- Software remains the unreleased 0.4.0, schema6 / new workflow3; no database migration or business-contract change.
- Production `127.0.0.1:18790` is healthy with PID31832, launched through the newly installed WMI path.
- The approved installation run `run_ea663d60b66d` was actually exercised through the desktop's 28 tools, independently verified twice and archived before this lifecycle reinstall. All eight previous runs remain unchanged; no second production acceptance run was created. See [desktop acceptance](batch1-desktop-report.md).

## Root cause and minimal repair

The Windows startup path tried `CREATE_BREAKAWAY_FROM_JOB`, then silently retried without it when the parent SDK Job prohibited breakaway. The shared daemon consequently inherited a temporary client's kill-on-close Job. Closing that client could kill the shared service and break peer sessions.

`plugin/src/orchestrator/mcp_shim.py` now delegates Windows startup to `windows_daemon.py`: one explicit local `Win32_Process.Create` path outside the SDK Job, provider breakaway, hidden window, preserved environment/cwd, bootstrap error logging and an owned process handle. There is no inheriting-job fallback and no business RPC replay. Existing singleton locking, health/version checks and the POSIX path remain. The already-used pywin32 runtime dependency is now explicit in package metadata and both lockfiles.

Official API references: [Windows Job objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects), [Win32_Process.Create](https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/create-method-in-class-win32-process), [Win32_ProcessStartup](https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/win32-processstartup). Implementation and independent review details are retained in `lifecycle-fix/implementation-report.md` and `lifecycle-fix/review.md`. The earlier review's pending desktop verdict is superseded for that gate by [post-restart independent verification](lifecycle-fix/desktop-reconnected-independent-20261117.md); its historical findings remain intact.

## Executed verification

| Check | Result |
|---|---|
| Same real SDK tests against old source | 3 FAIL, original logs retained |
| Final focused lifecycle/concurrency tests | Author 13 PASS; independent 13 PASS |
| Normal SDK close / forced original-shim kill / five concurrent sessions | Original daemon survives; no substituted restarted PID |
| Actual SDK Job, user SID and visible-window checks | Daemon outside that exact SDK Job; same user; no visible window |
| WMI bad executable / unavailable provider / bootstrap error | Explicit failures and real error logs, no Popen fallback |
| Independent private environment / Unicode / cwd probe | Preserved values; private sentinel absent from argv and log |
| Full final regression | **600 passed, 1 POSIX-only skipped in98.76s** |
| JavaScript syntax / diff whitespace | Exit0 |
| Frozen installed runtime sustained test | **1800.157s, 259 rounds, 2852 RPC, 1295 actual writes, zero duplicates**, same PID/start identity |
| Sustained resource maxima | 233 handles, 11 threads, 33,251,328 private bytes; final minus warmup +3 handles / -5 threads / +491,520 bytes |
| Post-restart actual desktop MCP | 28 tools; both read calls succeed; existing nine runs and archived installation record read back |
| Actual production-cache Python, separate fresh isolated SDK smoke | **60.14s, 102 RPC, 9 rounds**, same PID, successful final-session closure and owned cleanup |

The 30-minute clock starts after preparation; 259 continuous samples have a maximum interval of7.187s. Predeclared ceilings were +256handles/+16threads/+128MiB from sample3. All37 payload hashes matched before and after, including the exact source, isolated installed runtime and final production cache. SQLite integrity and foreign-key checks passed. Test runs were archived; their owned daemons were stopped through retained handles with exit confirmation. The real10.8MB externalized result was byte/SHA256/full-body checked.

Evidence roots:
- Final suite: `lifecycle-fix/full-pytest-final.log`.
- Sustained test: `C:/Users/user/AppData/Local/Temp/tri-soak-中文 space-a9bbeagp/evidence`; independent audit `lifecycle-fix/independent-soak-audit.json`.
- Production-cache isolated smoke: `C:/Users/user/AppData/Local/Temp/tri-soak-中文 space-zknf5r4t/evidence`.
- Frozen package manifest: `C:/Users/user/AppData/Local/Temp/tri-lifetime-installed-dnes_17t/evidence/source-manifest.json`.
- Retained ZIP/wheel: `dist/lifecycle/`; original M2 archives are unchanged.

## Installation, failures and explicit recovery

1. The first reviewed installation stopped the idle old daemon and then the official same-version `codex plugin add` failed with `failed to back up plugin cache entry` / os5. Actual old shim/uv working directories held the version-cache directory. A prior loaded-executable test did not cover this cwd-lock case. A new isolated cwd-holder test reproduced that exact failure and proved release of the owned holder allowed official installation. No database/config/cache contamination was found.
2. A separately reviewed recovery retained handles to only the exact old shim chain15788→16560→17792. After the leaf stopped, its parent exited naturally between the redundant second liveness/identity observations; the helper failed before retrying installation. All three shims were then confirmed absent, while Codex17948 remained untouched. This is a second installation-helper failure, not a second product runtime failure.
3. A new reviewed no-termination resume admitted only those two exact checkpoints, all three original shims absent, and unchanged backup/source/cache/database identities. Under the startup mutex and exclusive port reservation, official same-version installation and locked runtime rebuild succeeded. New WMI startup and exact new command-line ownership checks passed. The old database was never restored or migrated; no config or role changes were made. Historical caches0.1/0.1.2/0.2/0.3 were preserved without copying old0.4 code over the fix.

Private consistent backup: `C:/Users/user/.codex/backups/tri-orchestrator-lifecycle-20261117`. All failure and recovery receipts remain separate under `lifecycle-fix/deployment/`, `recovery/`, and `recovery-after-exit/`. The source development `.venv` was restored to its original location. This was not a zero-intervention or uninterrupted installation.

Historical post-install failure: desktop `run_list` returned **Transport closed** after the old shim was closed to release the cache. Raw result remains `lifecycle-fix/postinstall-desktop-raw.json`; it has not been overwritten.

**2026-11-17 final reconnection:** after the user restarted Codex, root called actual desktop `run_list(limit=100)` and `board_status(run_ea663d60b66d)`. Both succeeded: 28-tool catalog, nine runs, the one installation run archived, its step done at revision8, two confirmed actions and zero next/reconciliation actions. Full raw tool results: `lifecycle-fix/desktop-reconnected-20261117.json`. No production run, native action, installation or test fixture was replayed. This is actual desktop evidence, not SDK substitution.

## Remaining boundary

No cross-day soak or non-Windows execution was performed. WMI must be available; failure is explicit rather than silently switching back to the broken lifecycle. Local evidence hashes are not host-signed identity. The earlier unexplained1397 foreground disappearance is not retroactively attributed to this defect. No commit, merge, push or public Release was made.
