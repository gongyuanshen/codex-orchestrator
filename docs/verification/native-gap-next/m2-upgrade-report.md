# M2 independent isolated upgrade / historical-data acceptance

## Result

**PASS for the executed schema5 → schema6 migration and representative historical-contract preservation; PASS for opening an existing M1 schema6 database copy with the installed M2 runtime.** This is isolated data/runtime acceptance, not production migration or exhaustive old-workflow regression.

Final evidence root: `C:/Users/user/AppData/Local/Temp/m2-upgrade-final-proof-4eabb42f251d4fc996a0aabedc0a623c`.

Reproducible script: `C:/workspace/codex-orchestrator/docs/verification/native-gap-next/m2/verify_upgrade.py`. It requires a new output directory, uses subprocesses for old/new import isolation, and raises on any mismatch. Three incremental independent fixture runs were retained; the final run includes the pin check and both upgrade paths. These are deterministic upgrade checks, not repeated model trials.

## Inputs / provenance

- Read frozen `m2-interface.md` and `m2/native/config.json` before execution.
- Actual old source: `C:/Users/user/AppData/Local/Temp/tri-native-gap-m2-r658814l/legacy-source.zip`, SHA256 `000f229457558d931b279183591ea1e99aef1554133d00a5e4ec746adabbf123`.
- All **12** old `plugin/src/**/*.py` archive members were compared with Git commit `9c29cdb308a92cfdbdf259289bdc284f160abdfd`. They match after CRLF→LF normalization. Raw bytes do not match because the provided archive has CRLF; the archive was not rewritten. Exact per-file results are in `old-source-pin-check.json`. An initial strict raw-byte check failed for all 12; inspection established the newline-only difference before this explicit normalized comparison was added.
- New code was imported from the independently installed package, not checkout `plugin/src`: `C:/Users/user/AppData/Local/Temp/tri-native-gap-m2-r658814l/安装 验收/.venv/Lib/site-packages/orchestrator/db.py`. Subprocesses clear inherited `PYTHONPATH`; only old-code subprocesses set it to the freshly extracted archive.
- Existing M1 isolated source was explicitly identified from its fixture config: `C:/Users/user/AppData/Local/Temp/tri-native-gap-m1-d4ecd25c10/state/state.db`. It was opened with SQLite URI `mode=ro` and copied via SQLite backup into the new independent Temp directory. All new-runtime migration/testing was against that copy, not the M1 original.

## Executed schema5 → schema6 checks

Old code itself initialized schema **5** and created **9** representative runs: every combination of **workflow0/1/2 × Boost/Teamwork/Swarm**. The old API's current default is workflow2; the fixture explicitly marks selected stored rows workflow0/1 to emulate historically persisted contracts, following existing test patterns. It does not claim the current old API exposes a public downgrade selector.

Each run has its own real project file, task packet, two worker bindings and board history. Workflow0 is deliberately unstructured: old `WorkflowService.step_save` rejects it as legacy. Each workflow1/2 run additionally has a submitted step, actual content capture and a historical **FAIL** verification record. These are explicitly labeled historical fixtures, not invented native success receipts. Boost records include its old challenge path. Worker identities are fixture strings, not host authentication.

Before migration, every row of every SQLite table was saved in `before-schema5.json`, with columns and scalar values intact and deterministic row ordering. The initial fixture contains:

| Table | Rows |
| --- | ---: |
| projects / runs / task_packets | 9 each |
| workers | 18 |
| board_messages | 41 |
| workflow_steps / workflow_artifacts / verification_records | 6 each |
| schema_migrations / sqlite_sequence | 1 each |
| inbox_messages / read_cursors / worker_controls / workspaces | 0 each |

Then the actual installed new `Database` migrated it to **6**. `after-migration-schema6.json` was captured **before any follow-up exercise**. Every pre-existing table row compares exactly equal; old migration rows remain and version6 is added. The additive native-actions table is empty: no native evidence was fabricated by migration.

Representative post-upgrade semantic checks:

- All nine existing run versions remain **0/1/2**, never rewritten to3.
- Workflow0 still rejects a structured step with the same legacy error text.
- All six workflow1/2 cases still capture content without `verification_commands` or a verification manifest. They reject workflow3-only command input rather than silently ignoring it.
- Three fresh runs, one per mode, default to **workflow3** without a downgrade selector.

These follow-up checks intentionally create additional artifacts/new runs **after** the complete migration-preservation comparison. Their mutations are not confused with migration damage.

Finally the real old schema5 code attempted to open the resulting schema6 database. It raised:

```text
Database schema 6 is newer than supported 5
```

The entire row snapshot and main database SHA256 are unchanged across that rejected opening (`old-rejection.json`). This demonstrates early refusal without rollback/relabeling or silent downgrade.

## Existing M1 schema6 copy

The backup copy includes **2 runs, 3 workers, 21 board messages, 6 inbox messages, 6 native_actions, 3 workflow artifacts, 3 verification records, 1 workflow step and 1 packet**. All **15** tables, including empty tables and SQLite sequence/migration rows, were compared before and after installed-new-code initialization plus `migrate()`. Full snapshots `m1-copy-before.json` and `m1-copy-after.json` are exactly equal. The result is recorded in `same6-result.json`.

This proves stored rows survived opening/migrating under the installed code. It does not re-execute M1 native effects, resume a member, refresh leases, start daemon recovery or claim authentication of stored receipts.

## Reproduction / raw evidence

Executed command (final run exit **0**):

```powershell
& 'C:\workspace\codex-orchestrator\.venv\Scripts\python.exe' -B 'C:\workspace\codex-orchestrator\docs\verification\native-gap-next\m2\verify_upgrade.py' --output 'C:\Users\user\AppData\Local\Temp\m2-upgrade-final-proof-4eabb42f251d4fc996a0aabedc0a623c' --m1-source 'C:\Users\user\AppData\Local\Temp\tri-native-gap-m1-d4ecd25c10\state\state.db'
```

For rerun, use another new external output path; existing evidence is never overwritten. The controller uses the installed interpreter named in fixture config for all three version-specific stages. Raw `seed.log`, `upgrade.log`, `reject.log`, `same6.log`, input/provenance, row snapshots, old/new databases and JSON outcomes remain beneath the evidence root. `m2/upgrade-evidence-path.txt` indexes this pass's external directories.

Observed terminal summary:

```text
schema5 seed: 9 runs / workflow0,1,2 x boost,teamwork,swarm
schema6 upgrade: all original rows preserved; legacy semantics retained; new defaults 3,3,3
Database schema 6 is newer than supported 5
same-schema6 M1 sqlite-backup copy: every table and row unchanged
```

## Unexecuted / preservation boundary

No production18790 access, global configuration change, GUI interaction, native-agent call, daemon start, product edit, dependency installation, commit or original-fixture mutation was performed. The script/report/index are the only repository outputs. Old0/1 behavior was sampled, not exhaustively re-certified; historical FAIL records were preserved, not upgraded to workflow3 collector proof. No legacy inbox/worker-control rows were seeded in the schema5 fixture; populated inbox/native ledger preservation is independently covered by the M1 schema6 copy. Production migration and package-wide release acceptance remain outside this report.
