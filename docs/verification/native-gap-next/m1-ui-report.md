# M1 dashboard implementation report

## Scope and interface

- Changed only `plugin/src/orchestrator/web/app.js` and added `tests/test_native_action_dashboard.py`; no CSS, dependency, installation, skill, Python product, or existing-test changes.
- Read `m1-brief.md` and `m1-interface.md`; coordinated final projection metadata directly with the implementation owner. Consumes snapshot `native_actions`; subscribes to `native_action_updated` using the existing run-scoped, context-version-owned SSE refresh path.
- Compact overview adds waiting transfer, confirmed transfer, reconciliation, clarification and ledger-state counts. The details element is collapsed by default and preserves its expansion/focus through the incumbent overview mechanism. Uses existing theme tokens, wrapping count row and responsive step styles; no redesign.
- Bounded-window action counts are explicitly lower bounds (`≥`) when `counts_complete=false`; `limit` and `native_actions_has_more` remain visible in expanded details. Ledger status aggregates are distinct. No full historical ledger endpoint was added: expanded rows show projected next/reconciliation/clarification entries.
- Missing projection is explicitly not loaded, not an invented zero. Source identities, human seq, recipient and reason/summary are escaped. Expired invoking/claimed rows in the reconciliation projection are labeled as needing reconciliation, not as a fresh executable action.
- Registration, native transfer receipt, host observation and business/independent acceptance remain distinct. No transfer mutation, ACK, native tool invocation or auto replay is performed by rendering.

## Verification

- Red first: `m1-ui-red.log`: 2 actual Node-harness failures before implementation. Initial system Python lacked pytest; reran with the existing project `.venv`, without install.
- `.venv/Scripts/python.exe -m pytest tests/test_native_action_dashboard.py tests/test_routea_dashboard.py tests/test_dashboard_behavior.py tests/test_mode_surfaces.py -q`: **111 passed**; final output in `m1-ui-tests.log`.
- New tests cover bounded counts, escaped content, collapsed/preserved disclosure, missing/reset projection, read-only rendering and run-scoped SSE refresh with stale stream rejection.
- Existing tests cover tab keyboard/focus/live-region behavior, theme and mode/context separation.
- Impeccable scoped context and craft floor read. No PRODUCT.md/DESIGN.md exists; incumbent code is the authority. Mechanical detect result `[]` in `m1-ui-design-check.json`.

## Limits

No GUI automation or screenshots were performed: GUI is operated by the user. Actual visual acceptance at desktop/narrow widths and light/dark remains unverified; tests inspect browser-module output in the existing DOM shell. These results do not prove real host transfer delivery or formal installation.
