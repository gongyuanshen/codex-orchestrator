# Batch 1 real GUI acceptance — 2026-11-17

## Execution and evidence ownership

The coordinator executed the browser interactions in its available IAB session. This report is assembled from its explicit execution handoff and saved real screenshots/DOM observations in `batch1/gui/`; the report author independently inspected `long-message-narrow.jpg` and the saved measurement/DOM files. This is rendered-browser evidence, not the DOM-shell unit suite. No product code changed.

The first delegated attempt had no available IAB browser (`browsers: []`; `Browser is not available: browser2`). The coordinator subsequently performed the acceptance. One coordinator AX-click attempt reported a shadow-root tool error; fresh DOM semantic locators then succeeded. These are retained tool limitations, not silently counted as successful interactions or product failures.

## Observed acceptance

| Check | Actual observation | Evidence |
|---|---|---|
| Formal installation read-only | 18790 Boost historical page rendered; Teamwork and Swarm same-project empty states were inspected. No existing production run was written by GUI acceptance. | production-final-readonly.txt; production-swarm.jpg; production-swarm empty-state text file |
| Three routes/mode isolation | Isolated1397 Boost, Teamwork and Swarm rendered; switching modes isolated project selection rather than mixing runs. | boost-desktop-light.jpg; teamwork-desktop-light.jpg; swarm-desktop-light.jpg; teamwork-narrow.txt; swarm-final.txt |
| Evidence and ledger clarity | Swarm collection details expanded three evidence summaries and native ledger detail. Local collection and registered state were not presented as authenticated host identity/telemetry. | swarm-ledger-expanded.txt; swarm-desktop-final.jpg; swarm-desktop-final-dark.jpg |
| Actual uncertain transfer | Owned read-only GUI fixture on1402 displayed one reconciliation-needed action, explicit no-automatic-replay wording, and expanded exact message/action references. | swarm-uncertain.txt; swarm-uncertain.jpg |
| Actual invalid transfer |1402 invalid-target case displayed transfer failure rather than successful delivery. | swarm-invalid.txt; swarm-invalid.jpg |
| Theme/layout | Rendered1280×900 desktop and390×844 narrow views, light and dark. Narrow measured document scrollWidth390 equal viewport width390. | swarm-narrow-light.jpg; swarm-narrow-dark.jpg; swarm-narrow-dark.json; teamwork-narrow-light.jpg; boost-solo-narrow.jpg |
| Keyboard and visible focus | Space changed theme while themeToggleBtn retained solid focus outline; Tab moved focus to refreshSnapshotBtn with solid outline (coordinator observation). | swarm-narrow-dark.json pins theme focus; coordinator live interaction observation for Tab |
| Real-time update without reload | After coordinator SDK completed isolated Solo, the already-open DOM changed from in-progress to completed without reload. This demonstrates observed live UI update, not exhaustive SSE reconnect or ordering semantics. | boost-solo-live-completed.txt; boost-solo-narrow.txt |
| Real UI long-message submit | Coordinator submitted non-business UI_RENDER_ACCEPTANCE message#97 with no mention via isolated GUI. Visible log count changed13→14; full long-text disclosure expanded. Narrow screenshot wraps text; measured document width390, detail scrollWidth321 versus element width321.33. | ui-message-sent.txt; long-message-narrow.txt; long-message-narrow.jpg; long-message-measure.json |
| Browser diagnostics | Collected warning/error list was empty during the observed window. This is not a claim about all prior browser history. | console-warnings-errors.json (`[]`) |

## Restoration and limits

Coordinator restored theme to light and reset viewport after testing. The owned1402 GUI-only daemon was identified as session24660/PID32168; its shutdown is coordinator-owned and not asserted complete by this report.

Observed requirements above pass within these browser states and sizes. No screenshot/DOM inspected showed a blocking rendered layout failure. Real browser coverage does not certify every breakpoint, browser engine, screen reader, announcement timing, SSE disconnection/reconnect, or every accessibility behavior. Keyboard focus checks are bounded to the actual theme/refresh interactions, not an exhaustive tab-order audit.

Production GUI actions were read-only. The sole GUI data write was isolated1397 test message#97; SDK completion and fixture preparation are separately recorded native/SDK evidence, not GUI implementation actions. Product workflow correctness, production installation retention, real native receipt correctness and whole-batch approval remain separately owned acceptance rows. No M3 work was performed.

## Coordinator-owned daemon cleanup
Root subsequently verified health `/api/health`, listener owner, CIM command/data/PID for1397/PID21532 and1402/PID32168, then stopped only those exact temporary daemons. Both listeners are absent; production18790/PID21292 remains healthy. All fixture DB/evidence files remain. Receipt: batch1/owned-daemon-closeout.json. An initial `/health` probe returned404 before any stop; actual existing route was then verified and corrected.
