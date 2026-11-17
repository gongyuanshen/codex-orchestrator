# M2 runtime integration report

## Frozen scope

Software 0.4.0 / schema 6 unchanged. All new runs use workflow 3; run_create exposes no downgrade field. Historical workflow 0/1/2 meanings are retained. No production installation/configuration/process changes, no new command RPC, no commit. Existing uncommitted M0/M1 work was preserved.

## Runtime changes

- `workflow.py`: workflow3 inherits every mapped workflow2 binding/dependency/challenge/handoff gate; workflow3 capture requires exact command mappings covering every frozen step criterion. The source snapshot is captured before workflow metadata and embedded in a noncircular verification_manifest.
- The manifest is embedded in the immutable artifact descriptor and written once with exclusive creation under the backend data directory. Historical files cannot be overwritten. Artifact responses expose manifest_path/manifest_digest. Reads compare exact canonical bytes to the embedded SQLite anchor; missing/changed files are never recreated.
- Backend/collector share validate_evidence_path. Manifest location is outside the actual captured scope, not necessarily outside the entire project root for partial captures. Full-root overlap is rejected.
- `verification_record` adds receipt_files only for workflow3, validates every receipt against the frozen manifest, and captures the receipt plus all present raw stdout/stderr and manifest files through the existing capture_evidence chain. Incomplete FAIL evidence retains the actual partial bytes. PASS still requires explicit PASS for every criterion plus complete successful collection; exit0 alone is insufficient.
- Gate use first compares the original registered file hashes/current artifact, then revalidates structured receipts and checks every returned path belongs to the original evidence chain. Source/manifest/receipt/output changes invalidate acceptance. Historical idempotent record returns remain historical observations, not a fresh PASS.
- `db.py` new INSERT default3 and explicit supported versions/budget preservation; `inbox.py` extends workflow2 linked-execution semantics to3. No new table or migration version.
- Shared RPC catalog contains verification_commands and receipt_files, used by HTTP/MCP without additional execution tools. Daemon/shim require verification_receipts_v1.
- Summary artifacts expose only evidence_level and verification_command_count, not full argv or embedded manifest.
- Existing legacy merge/inbox/RPC fixtures explicitly set workflow2 instead of bypassing new receipt requirements. New-run/migration assertions expect3. Version3 was added to applicable original-binding/budget/unsupported-version gate matrices.

## Real red/green evidence

1. Before product edits, `tests/test_verify_receipts.py`: **2 failed** (new default was2; version3 rejected before required-command validation).
2. Initial existing gates plus base receipt checks: **32 passed**.
3. Initial new expanded tests had **9 failed / 3 passed** due to a missing Path import in the test; corrected test scaffolding, not product logic.
4. First broad compatibility selection: **13 failed / 59 passed**; old inbox fixtures needed explicit2 and MCP health fixtures needed the newly required capability. Existing assertions were retained.
5. First full tests run: **12 failed / 543 passed**. Eleven old merge cases lacked explicit workflow2 fixture setup; one new-run migration assertion expected2. Corrected only those fixture/default contracts. Log: `C:/Users/user/AppData/Local/Temp/m2-runtime-suite.txt`.
6. Second full tests run: **1 failed / 571 passed / 1 skipped**, 78.69s. The sole failure was runner-owned primary-exception retention during injected hash failure; reported to runner author, who subsequently fixed and independently verified it. Log: `C:/Users/user/AppData/Local/Temp/m2-runtime-suite2.txt`. This is not reported as a clean full-suite acceptance.
7. Extended scope support regression first failed on test role scaffolding, then genuinely failed against root-wide manifest rejection. Replaced the overly broad boundary with the runner's shared captured-scope validator. Both valid root-local/partial-outside placement and full-scope overlap are asserted. Independent review then exposed that full-root scanning hit Windows SQLite SHM permissions before the overlap check: the overlap validator was moved before capture_content, the original full-scope assertion was retained, and the 21 receipt cases passed (1.50s). No PermissionError was swallowed or reclassified.
8. Runtime integration selection before the final shared-boundary adjustment: **170 passed**, 30.87s. Log: `C:/Users/user/AppData/Local/Temp/m2-integration-final.txt`.

9. Final frozen runtime/tests selection: **171 passed**, 32.10s. Command: `.venv/Scripts/python.exe -m pytest tests/test_verify_receipts.py tests/test_workflow_gates.py tests/test_mode_workflows.py tests/test_mode_inbox.py tests/test_routea_merge.py tests/test_routea_migrations.py tests/test_routea_rpc.py tests/test_routea_transport.py tests/test_mcp_result_delivery.py tests/test_daemon_web.py -q --tb=short`. Log: `C:/Users/user/AppData/Local/Temp/m2-integration-final3.txt`. Runtime/tests frozen after this scope fix; root owns the final combined full-suite and installed-package acceptance.

## Exact M2 files changed by this integration owner

- plugin/src/orchestrator/workflow.py
- plugin/src/orchestrator/db.py
- plugin/src/orchestrator/inbox.py
- plugin/src/orchestrator/rpc_handlers.py
- plugin/src/orchestrator/daemon.py
- plugin/src/orchestrator/mcp_shim.py
- tests/test_verify_receipts.py (new)
- tests/test_mode_workflows.py
- tests/test_mode_inbox.py
- tests/test_routea_merge.py
- tests/test_routea_migrations.py
- tests/test_routea_rpc.py
- tests/test_routea_transport.py
- tests/test_mcp_result_delivery.py
- docs/verification/native-gap-next/m2-integration-report.md

artifacts.py and board.py needed no additional M2 edits; existing shared capture/evidence helpers and the prior M0/M1 changes remain in place. Runner/process modules, roles/skills, frontend and package metadata were not edited by this integration owner.

## Actual scenario coverage

New tests use real subprocess collection, not mocked successful receipts: nonzero7 -> FAIL -> original author repair -> same verifier retest -> integrated gate -> run completion; timeout produces incomplete FAIL with all actual raw files registered; mutated receipt identity/command ID/raw output rejected at record; separately mutated receipt/stdout/stderr/source/manifest rejected at gate; missing/whitespace-changed manifests rejected without reconstruction; exit0 with FAIL/NOT_RUN criteria rejected; env key names may be frozen without daemon possessing the verifier terminal's secret values. HTTP artifact_capture -> actual `python -m orchestrator.verify_runner` -> verification_record -> integrated completion uses exactly the shared catalog and bounded CLI JSON.

## Limits / independent acceptance

This is local collector consistency evidence, not signed host identity or authenticated native-verifier evidence. Temporary source mutation followed by byte-for-byte restoration between source checkpoints is intentionally not detectable; the runner owner tests/documents that boundary. Same-permission recomputation of all hashes is outside this guarantee. Native-agent fixture acceptance, isolated-wheel execution, GUI and formal installation are separate tiers owned by root; this report does not claim them. POSIX process coverage is not established by this Windows integration run. First `uv run` rebuilt the development editable environment; later commands used `.venv/Scripts/python.exe` without synchronization. No production install occurred.
