# M2 minimal dashboard synchronization

## Implemented

- Expanded both structured-workflow version checks to include workflow3 while preserving versions0/1/2 behavior and existing M1 changes.
- Used the coordinator-approved bounded artifact summary fields `evidence_level` and `verification_command_count`; no full descriptor, manifest command list, argv, environment or raw log is loaded/rendered by this change.
- Workflow3 displays `local_collector_v1` as local collection, explicitly NOT host identity authentication. Frozen command manifests, registered verification evidence and current gate validity are distinct. No artifact marker is described as successful collection or PASS.
- Collapsed details show at most three artifact summaries with scope, frozen command count and evidence-file registration count from existing verification evidence manifests. Overflow is explicit. No new backend field outside the agreed existing artifact summary, styling, dependency or action was introduced.
- Reused incumbent disclosure persistence/focus restoration, section/list classes and responsive/theme styles. No GUI operation or redesign.

## Verification

- Red before implementation: `m2-ui-red.log`, 2 failing Node-harness tests.
- `.venv/Scripts/python.exe -m pytest tests/test_verify_receipt_dashboard.py tests/test_native_action_dashboard.py tests/test_routea_dashboard.py tests/test_dashboard_behavior.py tests/test_mode_surfaces.py -q`: **113 passed**, `m2-ui-tests.log`.
- New cases exercise workflow3 visibility, bounded collapsed evidence details, absence of command/raw-log text, read-only rendering, explicit missing manifest and legacy version preservation.
- `node --check plugin/src/orchestrator/web/app.js` and scoped `git diff --check` passed.
- Impeccable mechanical detector returned `[]` in `m2-ui-design-check.json`.

## Limits

Browser-module tests use the existing DOM shell, not rendered GUI. Desktop/narrow-screen/light/dark visual acceptance remains user-operated and unverified. Tests do not prove collector execution, receipt validity, real host authentication or installed-package behavior. Backend artifact summary fields must be supplied by the integrator as coordinated.
