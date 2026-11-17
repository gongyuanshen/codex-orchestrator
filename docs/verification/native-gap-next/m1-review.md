# Independent M1 verification

VERDICT: PASS — M1 source, bounded fixture behavior, and the explicitly qualified isolated-package/native acceptance below. This is not GUI, refreshed desktop-catalog, production-installation, or complete batch-1 acceptance.

Stage 1 (M1 specification): PASS. Stage 2 (source quality and real verification): PASS. The earlier pending-native FAIL was held until the retained artifacts and isolated files/database were independently read and checked; see the final evidence addendum.

## Scope and ownership

Reviewed current M1 changes against `m1-brief.md`, `m1-interface.md`, and the additive 0.4.0/schema6 ruling in `report.md`. M0 has a separate review. M2 mandatory workflow gates are not part of this M1 verdict. No production source or tests were edited by this verifier; only this report and `review/` reproduction/evidence files were written. No production port, configuration, installation, GUI, commits, or native calls were operated by this verifier.

## Stage 1 — specification compliance

| Criterion | Status and direct evidence |
|---|---|
| Existing daemon/port/modes; no second scheduler or host invocation | MET: existing startup/watchdog integration only (`plugin/src/orchestrator/daemon.py:385`, `plugin/src/orchestrator/watchdog.py:55`); native module contains persistence/projection only (`plugin/src/orchestrator/native_actions.py:1`). |
| One additive ledger, source/run/step/packet/attempt/native binding/current revision association | MET: ledger schema and frozen source/step snapshot (`plugin/src/orchestrator/native_actions.py:16`, `:34`, `:91`). |
| Bounded read-only projection; original source facts retained, no ACK/cursor/dispatch side effects | MET: three output lists capped at 50 with exact counts (`plugin/src/orchestrator/native_actions.py:203`); status nesting (`plugin/src/orchestrator/board.py:327`); independent read-only and starvation probe. |
| Shared claim/begin/record/reconcile API, token/60-second lease/revision | MET: single catalog entry and shared dispatch (`plugin/src/orchestrator/rpc_handlers.py:453`, `:854`); lifecycle (`plugin/src/orchestrator/native_actions.py:118`). |
| Concurrent claim; same-key content conflicts | MET: serialized transaction and stable-key unique constraint (`plugin/src/orchestrator/native_actions.py:16`, `:126`, `:133`); independent two-Database-connection CAS and mutation probes. |
| Query then stop/packet/attempt/binding change; claim and begin reject stale work | MET: source validation and current digest checks (`plugin/src/orchestrator/native_actions.py:34`, `:130`, `:166`); nine-probe script covers both boundaries and actual public v2 redispatch. |
| Persist business message/step context before native wakeup | MET: explicit step metadata freezes at inbox send (`plugin/src/orchestrator/inbox.py:85`); no legacy missing-context invention (`plugin/src/orchestrator/native_actions.py:57`); shared sequence (`plugin/skills/route-a-workflow.md:17`). |
| Stop priority, no automatic resume/replay, original human/control identifiers | MET: pending control/stopped checks (`plugin/src/orchestrator/native_actions.py:84`), canonical human seq (`:71`), only definite non-call/rejection reclaim (`:139`), original control source (`:62`). |
| Record actual result separately from business completion/control ACK | MET: record preserves supplied result/evidence and source race (`plugin/src/orchestrator/native_actions.py:171`); existing ACK state is not updated by ledger. Tests exercise normal ACK before record and stop after begin. |
| Restart/lease expiry/late tokens remain fenced; unknown outcome not success | MET: recover/expiry (`plugin/src/orchestrator/native_actions.py:96`), inner lease recheck with committed fence (`:111`, `:155`), independent DB close/reopen before/after simulated effect, begin/record lease-boundary probes. |
| All six statuses; cancellation requires definite non-call evidence | MET: update transitions (`plugin/src/orchestrator/native_actions.py:118`, `:179`); cancellation test rejects unknown. |
| Human ambiguity preserves original text/seq, no broadcast | MET for ambiguous mention lists: `plugin/src/orchestrator/native_actions.py:225`; independent 51-ambiguous-prefix probe. |
| Skills, inline roles, packaged roles, UI and HTTP/MCP updated together; model inheritance unchanged | MET: shared contract `plugin/skills/route-a-workflow.md:12`; all mode sections at line 8; six packaged role additions; `tests/test_native_action_contracts.py:1`; read-only UI `plugin/src/orchestrator/web/app.js:2005` and dashboard tests. |
| Existing workflow0/1/2 meaning unchanged; candidate version/schema/capability synchronized | MET: metadata 0.4.0; `plugin/src/orchestrator/db.py:17`; `plugin/src/orchestrator/mcp_shim.py:40`; `scripts/upgrade.py:300`; metadata, upgrade, migrations and existing mode regression checks passed. Metadata-only explicit-step freeze does not broaden existing inbox admission gates. |
| Actual native child message → handoff → receipt → rework chain | MET with byte-scope qualification: actual original author/verifier chain retained FAIL → attempt1 repair → candidate PASS → integrated PASS; final-byte package separately completes a real notice transfer. See final addendum and `m1-native-report.md:5`. Unit, ASGI and SDK fixtures alone are not used as proof of native-host execution. |
| Separate source, fixture, isolated package, real host, GUI and formal installation acceptance | MET as an evidence boundary, not a claim all tiers passed. Source/fixture and bounded package/native scope verified; GUI, refreshed desktop catalog and production installation intentionally not performed. |

## Stage 2 — code quality and executed verification

Source code quality verdict: PASS for reviewed bounded M1 behavior. No open P0/P1 source finding after same-author corrections. Required native-chain evidence was subsequently verified in the final addendum.

All following commands ran from `G:\工具\codex-orchestrator`, with `PYTHONDONTWRITEBYTECODE=1`; no test cache provider was used. Logs are retained under `docs/verification/native-gap-next/review/`.

1. `uv run --no-sync python docs/verification/native-gap-next/review/probe_m1.py` — exit 0; **9 independent probes passed** (`probe-m1.log`). Includes two independent SQLite connections, real DB close/reopen, source aliases, blocked-source starvation, immutable-source conflicts, stale packet/attempt/binding/control checks, and injected scheduler delay across both begin and record lease boundaries. Binding/attempt mutations are explicit isolated persistence-fault injections; external effect counter is a fixture, not a host claim.
2. `uv run --no-sync pytest -q -p no:cacheprovider tests/test_native_actions.py tests/test_native_action_api.py tests/test_native_action_contracts.py tests/test_native_action_dashboard.py tests/test_mode_inbox.py tests/test_worker_control.py tests/test_mode_workflows.py tests/test_routea_migrations.py tests/test_mode_upgrade.py tests/test_package_metadata.py tests/test_routea_transport.py tests/test_mcp_result_delivery.py` — exit 0; **129 passed in 19.10s** (`targeted-pytest.log`). No skips.
3. Final core rerun: `uv run --no-sync pytest -q -p no:cacheprovider --strict-markers --strict-config -W error tests/test_native_actions.py tests/test_native_action_api.py tests/test_native_action_contracts.py tests/test_native_action_dashboard.py` — exit 0; **35 passed in 0.57s** (`final-core-pytest.log`). No skips or relaxed warning handling.
4. `git diff --check` — exit 0, no output. Full changed-file diffs and new files were read, not only author summaries.
5. Earlier independent focused run of native/core/contracts/UI/upgrade/metadata/transport/result-delivery checks — exit 0, **50 passed in 17.70s**. This is superseded by commands 2 and 3.
6. Read the actual author logs `m1/full-strict-final.txt` (**522 passed, 75.85s**) and `m1/green-final-targeted.txt` (**99 passed, 5.29s**). These are corroborating author-produced evidence, not misrepresented as this verifier's own full-suite execution.

One initial inspection command used shell brace expansion unsupported by PowerShell and failed parsing (exit 1); it made no changes. It was rerun with explicit paths successfully. This was not a product/test failure.

### Findings reproduced before correction, now resolved

| Priority | Concrete original failure | Corrected source and independent verification |
|---|---|---|
| P1 | 51 old ambiguous human or ineligible inbox sources permanently hid a later valid source because LIMIT ran before eligibility. | Streaming eligibility before output limiting, `plugin/src/orchestrator/native_actions.py:203`; valid human and notice both visible after 102 blocked sources. |
| P1 | Boost/Teamwork old task body persisted under packet1 acquired packet2 meaning at first claim after redispatch because null frozen context was filled from current state. | Freeze explicit-step send metadata, `plugin/src/orchestrator/inbox.py:85`; reject legacy null context, `plugin/src/orchestrator/native_actions.py:57`; public packet2 redispatch probe rejects old body. |
| P1 | Expire precheck followed by scheduling delay accepted begin with an already-expired token. Same gap affected record. | Under-lock deadline check and committed fence, `plugin/src/orchestrator/native_actions.py:111`, `:155`; both operations now reject and retain needs_reconciliation. |
| P1 | Human source IDs `1` and `01` resolved to the same SQLite seq but created separate ledger claims. | Canonical ASCII positive decimal validation, `plugin/src/orchestrator/native_actions.py:72`; six alias spellings rejected, one row remains. |
| P2 | Duplicate native_action_update catalog/import/handler definitions in initial integration. | Single entry at `plugin/src/orchestrator/rpc_handlers.py:453`; catalog test passes. |

### Reviewed source fingerprints

- `plugin/src/orchestrator/native_actions.py`: SHA256 `90160AF1BF8126D3DE211506B0F648982AFE77F38784DD1DC8B2000CA8D3E592`.
- `plugin/src/orchestrator/inbox.py`: SHA256 `112BCC4FA6092FC15A0D7A31E9AE0A1BEE82281FD3B96D692E1B954B8C454EFF`.

## Untested paths / caveats

- Five-transfer repair chain ran on the preceding wheel, whose only final source difference is the inspected native projection consistency adjustment and removal of an unused import. Final-byte wheel/install identity and a sixth actual native notice transfer were separately verified; the full repair chain was not repeated on those exact final bytes.
- No production install/upgrade, GUI automation, full long-duration/load stress, or forced OS process crash was performed by this verifier. DB close/reopen and lease fault injection are explicitly bounded recovery evidence.
- Projection output is bounded, not a fixed-work/time query. It scans source history and performs per-source ledger lookups; the new ledger has no secondary source/run index. No production-scale latency claim is made.
- Host result evidence is retained as supplied; M1 is not host authentication or an exactly-once side-effect protocol. SQLite cannot cancel a call already issued to the host.

## Final native/package evidence addendum — 2026-11-17

Independent readback used `native/config.json` to locate the isolated package and evidence root. The verifier did not start any daemon or call the production service.

Executed:

- `uv run --no-sync python docs/verification/native-gap-next/review/check_final_wheel.py` — exit 0: all **34** final source-manifest entries match the reviewed checkout; all **16** runtime files in the final wheel match. Log: `review/final-wheel-hash-check.log`.
- `uv run --no-sync python docs/verification/native-gap-next/review/check_native_evidence.py` — exit 0: independently hashes actual installed files, retained wheel/ZIP, and ledger/verification evidence; reads both isolated SQLite databases using `mode=ro`; checks six distinct confirmed source transfers, completed inbox receipts, original bindings/attempt1, FAIL/PASS/PASS records, restart fencing, and append-only correction. Log: `review/native-evidence-check.log`.
- An initial inline hash inspection failed because Python's default Windows GBK decoder was used on a UTF-8 config. It was corrected to explicit UTF-8 and rerun successfully; the retained verification script uses explicit UTF-8. No product change or evidence was lost.

Observed evidence:

1. **Real original-author rework chain:** `native-chain-closed.json` contains five confirmed transfers and completed inbox deliveries, a completed run, a done step at attempt1, the same `/root/m1_native_author` and `/root/m1_native_verifier`, and FAIL/PASS/PASS verification records. Retained actual test outputs show two failures at attempt0, two candidate passes at attempt1, then two independent integrated passes. All verification evidence file hashes match their recorded manifests.
2. **Native-result loss handled without replay:** first action history is claim → begin → restart fence → reconcile confirmed. The actual original-author receipt and root-observed native delivery are retained. In the read-only database, all six action histories contain exactly one claim and one begin; no second delivery is disguised as recovery. This is local observable evidence, not cryptographic host attestation.
3. **Final bytes, separate from preceding-wheel chain:** independently verified the final wheel and installed `site-packages/orchestrator` files against the reviewed checkout, including `native_actions.py` SHA256 above. The final installed package then completed the sixth actual notice transfer in its own run. The earlier five-transfer chain is not represented as having run unchanged final bytes.
4. **Truthful notice evidence correction:** original accepted receipt said `body: None` because the author used the wrong field name. It is still present. The same original author reread `body_md`, compared the exact original text, and appended correction at board seq21. This verifier read that actual board row and verified that it includes the exact notice body and explains the `None` error. The correction is not counted as a seventh ledger-controlled transfer and does not rewrite or silently repair the original receipt.
5. **Package/upgrade boundary:** final SDK catalog has 28 tools and exactly one `native_action_update`. Prepared/applied/verified upgrade logs report the owned isolated port8387 and schema6/capability identity. Completed workflow/inbox/ledger contents match across before/after-upgrade evidence. The actual migrated legacy database retains its original run, has schema6 and zero invented native ledger rows; retained old-code output rejects schema6 with exit1. No production catalog/installation acceptance is inferred.
6. **Final state:** read-only database shows the business run completed and notice run archived, six unique confirmed actions, all six inbox messages completed, and no unresolved native actions. Final fixture cleanup is root-owned; it is not a claim about production lifecycle.

Retained distributable hashes independently checked:

- Wheel SHA256: `cd267b1458e138372a888ba0fd2ad64b82cc52ac7130aed7968ce53caf06598a`.
- Plugin ZIP SHA256: `ef0b4dda5161e21e220ac87d9609175c947b0362cd0095347dc7ed293cb6e9b2`.

The native smoke and its deliberately narrow final-byte notice establish the requested M1 handoff/receipt/rework integration, not exhaustive native interruption, every mode on hardware, long-running performance, GUI, refreshed desktop catalog, formal installation, M2–M5 or complete first-batch acceptance.
