# Batch 1 production preflight — 2026-11-17

Read-only observations only. No process stop, database mutation, installation, config/role change, reload or UI action was performed. Local evidence is under `batch1/preflight/`.

## Current production boundary

- `/api/health` and TCP/CIM agree: PID25084, 127.0.0.1:18790, software0.3.0/schema5; data `C:/Users/Administrator/AppData/Local/tri-orchestrator`.
- Owner command: Python3.12 pythonw.exe, `-m orchestrator.daemon --host 127.0.0.1 --port 18790 --data-dir C:\Users\Administrator\AppData\Local\tri-orchestrator`; parent10500. Full evidence: owner-cim.json and readonly-state.json.
- Read-only SQLite: quick_check=ok, foreign keys clean; eight historical workflow0 runs (Boost3completed+1archived; Swarm4completed; Teamwork0), zero active runs across all modes, zero pending worker_controls, zero inbox messages. No activity blocks maintenance at this observation instant; must recheck under a write reservation immediately before stop.
- Six global roles exactly match installed0.3.0, and all six differ from source0.4.0. Only those six are managed; hashes recorded without file contents.
- Current config SHA256 `670bf5fee8967491eb0886c746a6c40b91775165cfb5d04fe73081a1affd0b1a`. It differs from the historical0.3 installation report, so preserve current bytes; do not restore the old config backup.
- Full old cache inventories including `.venv` and excluding bytecode: 0.1.0=1774, 0.1.2=1779, 0.2.0=1783, 0.3.0=1783 files. Each full inventory/hash is recorded. `cache-*-inventory.json` is intentionally the narrower upgrade-helper payload inventory that excludes `.venv`; use `cache-*-full-runtime-inventory.json` for client preservation.

## Correct host installation route

Official CLI help exposes `plugin add`, not `plugin install`. Current selected plugin is `tri-orchestrator@tri-orchestrator`, enabled, version0.3.0. Configured local marketplace root is `G:/工具/codex-orchestrator`, plugin source `./plugin`, whose manifest is now0.4.0. Marketplace and installed selection receipts are saved. Remote marketplace listing had a network failure; the targeted local marketplace listing succeeded.

Exact official activation command for the coordinator's controlled maintenance phase:

```powershell
codex plugin add tri-orchestrator@tri-orchestrator --json
```

Expected new versioned root: `C:/Users/Administrator/.codex/plugins/cache/tri-orchestrator/tri-orchestrator/0.4.0` (currently absent). Resolve registered native Codex executable and freeze its hash as prior controller does; prefer direct executable over wrapper during controlled execution.

`docs/verification/modes-20261117/activate_030.py` is the existing one-shot controller. It is version-specific (old0.2,target0.3,schema5,old commit/gates) and must not be executed unchanged. Reuse reviewed mechanics, not its frozen values or historical resume entry points. `scripts/upgrade.py apply` explicitly rejects placing0.4 into a versioned0.3 cache; it does not perform host registry activation or daemon stopping. Its prepare backup excludes `.venv`, so is not sufficient by itself for complete old-client preservation.

## Executable coordinator sequence

1. Freeze current reviewed M1/M2 source/package inventories,0.4.0/schema6 and installed/marketplace identities. Separate installation precondition from unfinished batch acceptance; never fabricate the old controller's final-native-PASS gate.
2. Create a new private ACL-restricted backup, distinct from the retained030 backup. Copy current config, six roles and every old cache including `.venv`; take consistent SQLite backup and logical table snapshot. Require readback equality. Retain current config rather than historical backup bytes.
3. In one maintenance controller keep the shared startup mutex, obtain SQLite BEGIN IMMEDIATE writer reservation, repeat all-mode active/control/inbox checks, recheck health+owner receipt+CIM executable/command/creation identity, then stop only that verified daemon. Bind18790 exclusively without listening; close/rollback the write reservation only after offline equality while keeping mutex+port reservation. Do not stop Codex or old native clients.
4. Invoke official add command. Record actual returned selected identity/root/version. Preserve/restitch old caches from backup even if the CLI prunes them, and verify full inventories. Do not claim old shims accept schema6 solely because files survive.
5. Use installed `uv sync --locked --no-dev` with clean PYTHONPATH/VIRTUAL_ENV/TRI_* environment; verify the interpreter and runtime import path are installed0.4, not editable development. CLI may copy `.venv`: prior0.3 failure proved that asserting no `.venv` exists is wrong; inspect and synchronize it instead.
6. Migrate with installed0.4 under mutex+exclusive port. Verify schema6, all old table/old-column row projections/workflow0 data and integrity retained, no new production business tests. Atomically update exactly six managed roles only after preimage hash match. Require complete config-byte equality and unchanged old caches.
7. Release only own port lease and call installed `_spawn_detached_daemon` directly while retaining startup mutex (avoid recursively acquiring startup convenience helper). Verify new health/CIM/owner/software/schema/data/capabilities including native_actions_v1 and verification_receipts_v1.
8. Read back official selection and installed payload, HTTP three entries/static bytes, then use a fresh actual native context for28-tool/new-schema catalog invocation. Preserve old session files and clients; no promise of existing-chat hot refresh. User GUI acceptance and batch-wide native exception matrix remain separate real checks; stop before M3.
9. Any partial install/migration must retain checkpoints and move forward explicitly. Once new database writes occur, do not restore an old database snapshot or run0.3 againstschema6. Prior030 failure/resume history is not permission to replay its old recovery path.

Not executed: maintenance locking, backup, stop, install, migration, role/config writes, desktop reload, GUI or post-upgrade acceptance. This is an instantaneous preflight, not a maintenance lease or final batch acceptance.
