# Batch 1 installer controller — implementation and isolated checks

`batch1/activate_040.py` is a standalone one-shot 0.3.0/schema5 to 0.4.0/schema6 controller. It reuses retained 030 low-level Windows command/identity and upgrade snapshot primitives, not its execute/resume flows. Default and `--preflight` inspect only. `--execute` is the only mutation route. Backup/evidence destinations are external arguments, must be new and disjoint from managed runtime paths. Existing target/backup/evidence refuses implicit restart/resume.

## Scope implemented

- Final accepted 36 payload/source/ZIP hashes, actual plugin-prefix ZIP, software/schema and local marketplace source checked. Source development `.venv`/unexpected excluded directories are explicit blockers; known `__pycache__` is recorded and allowed; controller never moves/deletes them.
- Current config bytes and six noncustomized managed role preimages frozen; all four full old caches including `.venv` inventoried/backed up (stable comparisons exclude `.pyc`, consistent with the preflight). Private backup has restricted ACL and SQLite online consistent backup/readback.
- Shared startup mutex, final SQLite writer reservation with all-mode idle/control/unresolved-inbox checks, exact original PID25084 and health/CIM/owner equality. Termination uses a retained Windows handle plus final CIM/creation identity check, never a later numeric-PID stop. Exclusive bound18790 socket held through install/migration.
- Real versionless official `plugin add ... --json`; no overwrite/relabel of old0.3. Root explicitly approved old-cache restitch in finally on CLI success/failure: only backed-up files to old-version original paths; per-file receipt, no deletion of extra files, no DB/config rollback. Any final inventory mismatch fails.
- Installed locked uv runtime sync/check, installed import/software/distribution/schema identity before migration. Old row projections/counts, workflow0 history, schema migration history and full integrity checked. Exactly six role atomic replacements after preimage rechecks. Installed direct daemon launcher used while holding mutex; new owner/schema/capabilities, selected target, installed source and HTTP static bytes read back.
- Original command streams/checkpoints/failure retained. Final status explicitly leaves desktop MCP refresh/GUI/native acceptance pending. No automatic recovery or production business writes.

## Actual isolated verification

Command from repository root:

```powershell
$env:PYTHONUTF8='1'
.venv/Scripts/python.exe -B docs/verification/native-gap-next/batch1/test_activate_040.py
```

Final `isolated-tests-r6.log`: 8 tests passed, including actual own Windows subprocess termination via retained handle, actual temporary cache `.venv` restoration/readback, changed-runtime detection, real SQLite backup/projection corruption refusal, atomic role byte copy, accepted source/archive and explicit development-environment refusal. The eighth test takes an online read-only SQLite backup of the actual eight-run production state into a temporary directory, migrates only that isolated copy with actual0.4 code, then exercises complete old-row/history/count/integrity guards. No mock success response or production mutation.

Initial `isolated-tests.log` and r2 remain: first test run exposed wrong local ZIP path and fixture SQLite handle lifetime; second exposed ambiguous ZIP suffix matching. Fixed archive to actual retained M2 location, fixture explicit close, exact `plugin/` archive prefix. r3 passed5; r4 added2 tests and passed7. Failure logs not overwritten.

A real read-only production preflight succeeded before the final two test additions; receipt `controller-preflight.json`. Root explicitly required known `__pycache__`/`.pyc` not to block installation or create a new mutation surface. Controller records these directories; package payload comparisons continue to exclude bytecode. Development `.venv` remains an explicit blocker; no source cleanup is automatic.

## Not executed

No `--execute`, production stop/install/migration, global role/config write, old production cache restoration, desktop reload or GUI performed by this implementer. Full end-to-end maintenance is pending independent review and root execution. Existing chats may retain their old catalog; controller does not claim hot refresh.
