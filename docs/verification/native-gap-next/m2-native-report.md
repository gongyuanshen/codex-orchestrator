# M2 isolated package and original-native-verifier acceptance

Date: 2026-11-17. This is Windows, isolated-package and actual native-agent evidence, not production activation or refreshed desktop MCP catalog acceptance.

## Installed execution boundary

- Fixture root: `C:/Users/user/AppData/Local/Temp/tri-native-gap-m2-r658814l`.
- Package: `安装 验收`, independent `.venv`, frozen runtime dependencies, test-only pytest9.2.0. The installed `tri-orchestrator-verify.exe` executes from site-packages, not the development editable environment. No new runtime dependency.
- Isolated service: 127.0.0.1:1392; own data directory `state`. Software0.4.0/schema6, capability `verification_receipts_v1`.
- Backend calls use actual SDK stdio MCP through the installed shim. Native deliveries use actual desktop `collaboration.followup_task` with the existing author/verifier identities. SDK calls are not relabeled desktop-catalog calls.
- Root, source, installed runtime/web files and package contracts were read back; final36 package-source files and18 installed runtime/web files match. Final SDK catalog has28 unique tools, updated `verification_commands`/`receipt_files` schemas and `native_action_update`; no command-execution RPC.

## Actual chain, including setup failure

Run `run_ec93ed420bf4`, step `step_d58b88c784ff4604`, Boost workflow3. Original author `/root/m1_native_author`; original verifier `/root/m1_native_verifier`.

1. Root persisted initial work, claimed/began its ledger action, then used the real native delivery tool. Original author accepted/submitted the intentionally faulty `clamp.py` unchanged; no PASS claimed.
2. Original verifier captured an artifact and challenge. The first collector finished with actual pytest exit3 during startup (`WinError10106`), without running either business test. The acceptance helper had converted `os.environ` to a regular dict, then looked up mixed-case `SystemRoot`, missing actual `SYSTEMROOT`. This was a real fixture-author mistake, not a product default silently filled in. Old helper, artifact/challenge, logs and receipt remain retained.
3. Verifier stopped without `verification_record` or business verdict. Root fixed only the helper's explicit environment name, persisted a NEW review message and used a NEW ledger action. Same verifier explicitly marked the old accepted delivery failed, froze a new manifest/challenge, and executed the original tests. No old delivery replay or business-attempt reset.
4. Actual pytest exit1, **2 failed**. Artifact `artifact_4437d6e0a53d4412`; FAIL `verification_596c036b42a04353`, attempt0. Both clamp upper bound and reversed-range behavior were genuinely wrong.
5. Original author repaired only `clamp.py`, preserving frozen tests. Root used the existing `start_rework`; attempt became1. Author actual checks:2 passed. This author result did not satisfy the independent gate.
6. Same verifier captured/retested the exact attempt1 candidate with installed CLI: **2 passed**, exit0; independently judged the frozen criterion. Artifact `artifact_4375903067354839`; candidate PASS `verification_67f87625d4a545d7`.
7. Separate integrated capture and another real installed-collector execution: **2 passed**, exit0. Artifact `artifact_f530dd3dac1e4b50`; integrated PASS `verification_bcea96e7c458463a`. Root then explicitly completed step and run.

Six distinct transfer actions are confirmed, including the explicit fresh review after setup repair. All business messages are completed except the original startup-failed delivery, which is failed. No unresolved or unknown transfer was automatically replayed. The final step is done at attempt1, with original bindings unchanged. The setup correction was an actual unplanned coordinator intervention; this run is not a zero-rescue performance claim.

## Packaging, upgrade and readback

- Actual offline wheel build and independent installation succeeded in the Chinese/space path. Build/import/CLI output is retained under `m2/` and the fixture evidence directory.
- Upgrade preparation while the native run was active was rejected before creating the backup destination.
- The full business chain used the final runtime bytes. A later change clarified environment allowlist wording in10 skill/role files only; `contracts-package-delta.json` verifies no runtime change. After closing the run, root stopped only its verified fixture daemon, used controlled `prepare/apply`, reinstalled the final wheel, restarted the isolated daemon and ran `upgrade.py verify` successfully.
- Final package readback retained all completed run, step, six ledger actions and three verification records. Final SDK reads confirmed updated catalog and retained state after restart. No production daemon/config/cache was modified.
- Independent reviewer additionally ran the installed CLI from an external directory with `-I`, without PYTHONPATH/VIRTUAL_ENV; actual separate stdout/stderr and bounded JSON were verified. See `m2/independent-installed.json`.
- Separate real schema5→6 and old-schema refusal evidence is in [upgrade report](m2-upgrade-report.md).

Retained local artifacts (ignored, not published):

| Artifact | SHA256 |
|---|---|
| `dist/m2/tri-orchestrator-0.4.0-m2-reviewed.zip` | `b8e0cf8f9f6f5da63ca361b4a8f6bf18d0dee7f370771073df57b4cc23436353` |
| `dist/m2/tri_orchestrator-0.4.0-py3-none-any.whl` | `0f35cf3e44c79dc465adedde258207c836aadc836116aade3c35aabf8b7c5280` |

Machine index: `m2/evidence-index.json`. Important fixture files: `evidence/native-chain-closed.json`, `final-package-retention.json`, `final-package-artifacts.json`, `final-sdk-catalog.json`, `final-sdk-status.json`, each phase's collector receipt/raw stdout/raw stderr, independent criterion judgments, and all original SDK receipts. Original failure evidence was not overwritten.

## Unperformed / guarantee boundaries

- Current desktop tool catalog still lacks the new parameters/ledger tool. No desktop MCP refresh or production installation was approved or performed. No GUI operation/visual acceptance.
- This is one actual Boost delegated repair chain, not all first-batch modes/exception combinations. Boost Solo, Teamwork handoff and Swarm parallel batch-wide native reruns remain separate acceptance work; M1's earlier native evidence is not silently relabeled new M2 evidence.
- POSIX runtime test is skipped on Windows. Restrictive nested Job configurations, startup-kill timing stress and externally brokered descendants are not exhaustively established.
- Local receipts prove checkpoint/evidence consistency, not authenticated host identity. Temporary source mutation restored between checkpoints and same-permission rehashing are outside the guarantee. Raw output remains exact: a command that prints a secret can put it in its own logs.
- No36-run before/after or Antigravity comparison, native token/cost or performance-improvement claim.
