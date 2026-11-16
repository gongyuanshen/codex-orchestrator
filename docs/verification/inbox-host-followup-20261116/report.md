# Reliable inbox: native-host and reboot follow-up

## Result

The approved **coordinator sends / separate native worker waits** route passed current-desktop native MCP acceptance. The original receive -> independent rejection -> original-author repair -> same-verifier acceptance chain remains evidenced in the initial prototype report. The combined current suite passed **271 tests in 54.24 seconds**, exit 0.

This is an isolated prototype result, not an installed-plugin update or a claim that every host dispatch pattern supports concurrency. The same-agent desktop concurrent wait/send experiment **failed** and remains a documented limitation, not a rewritten success.

## Current evidence

| Check | Observed result | Evidence |
|---|---|---|
| Combined original + addon regression | 271 passed, no failed/skipped tests | post-reboot-review-regression-20261116-222133.txt |
| Saved desktop configuration after OS reboot | Actual SDK initialization loaded 23 tools; previous runs and completed receipt history were unchanged | post-reboot-saved-config-sdk.json |
| Separate installed App Server | Native MCP routing, 25-second wait, concurrent requests, duplicate/conflict/receipt checks passed | host-native-report.md, host-native-result.json |
| Fresh current-desktop child | All 23 native tools available; native run_list and one 1000 ms wait passed | native-desktop-results.json (prior observations explicitly distinguished from retained raw records) |
| Native maximum wait | 25000 ms requested, actual result after 25009 ms was timeout with no messages | native-desktop-results.json |
| Same-agent native concurrent wait/send | FAIL: wait returned timeout at 25012 ms; send returned only afterward, taking 24018 ms | native-desktop-results.json, native-desktop-report.md |
| Durable recovery of that late message | Sequential native receive, exact duplicate, changed-content 409, accepted/completed and historical ACK checks passed | native-desktop-sequential-results.json |
| Different-agent native coordinator -> worker | Receiver's single 25-second wait returned the sent message after 17521 ms; sender observed a live subscription and sent once in 12 ms | native-cross-sender-results.json, native-cross-receiver-results.json |
| Native receipt and run closure | Accepted/completed retained two receipts; get matched; completed run returned run_closed | native-cross-receiver-results.json |
| In-flight service loss | Real pending HTTP request cancellation returned MCP error with actual HTTP 500/plaintext body; same MCP session recovered on replacement server | server-loss-report.md, server-loss-summary-after-fix.json |
| Recovery side effect | Accepted receipt required reconciliation; existing file remained one write, no replay | server-loss-report.md; earlier separate-process evidence in initial report |
| Final isolated state | Two runs completed, three messages completed, three board rows, integrity/foreign-key checks clean | post-reboot-final-audit.json |
| Cleanup | User disabled inbox_probe; owned 19892 server completed graceful lifespan shutdown; 19891/19892 refused connections | post-reboot-pre-shutdown.json, post-reboot-final-audit.json |

## Root-cause and proof boundaries

The same-agent experiment placed wait and delayed send in one functions.exec invocation. The wait timed out before send completed. Direct SDK and directly driven App Server concurrency pass, and separate current-desktop agents communicate during the bounded wait. This isolates the observed difference to the desktop same-agent invocation path; **serialization inside host scheduling is an inference**, not a source-level identification of its exact internal gate. No plugin fallback, retry or fabricated receive was added to conceal this failure.

Use the intended topology: the coordinator sends from its own agent context while the worker makes one bounded wait. Do not schedule the producer behind the consumer in the same desktop agent call batch. After timeout, return control explicitly; do not create perpetual waiting or claim zero-cost standby.

The existing root conversation retained a failed startup client after boot. A fresh desktop subagent loaded and invoked the actual native tools. This proves fresh-child native registration/invocation, **not hot refresh of the root's stale client**. No private IPC or desktop automation was used.

The tested service-loss case is real graceful server shutdown with pending-request cancellation, not a forced OS crash. The user's actual OS reboot additionally preserved existing completed runs/messages, but did not provide a timed crash at an in-flight SQLite transaction or external side effect.

The initial source fix preserves status/body for non-JSON HTTP errors in prototypes/inbox/mcp_bridge.py. Its strengthened regression failed before the fix and passed afterward. Terminal errors remain errors; there is no implicit business retry or success fallback.

A receipt records the agent's claim, not independent business acceptance. Ended agents still require the coordinator's native followup_task; posting alone does not wake or interrupt them. Recipient names remain trusted-local identifiers, not authenticated identities.

## Preservation and cleanup

Installed/source plugin hashes match the pre-prototype baseline. Production 18790 remained PID 20464 with the same start time throughout this turn. The user's computer reboot had replaced earlier production PID 28572 before this turn; this is not represented as an unchanged PID across reboot.

A strict whole-production-database equality assertion failed during final audit. Investigation found a new unrelated Boost run with its own appended messages, workers, packet and cursor. Every preexisting table-row prefix still matched its baseline hash. No data was rolled back or deleted to manufacture equality. See post-reboot-production-delta.json and post-reboot-final-audit.json. The active unrelated run was left alone.

Global configuration was not restored wholesale. The temporary inbox_probe entry was manually disabled by the user, not removed; other concurrently changed settings were preserved. It is not enabled for automatic startup. Test port 19892 was stopped using Ctrl+C only after all test runs were closed and subscriptions were zero. Uvicorn reported Application shutdown complete and Finished server process 2512; the interrupted shell wrapper returned 1, not 0. Closed listeners were independently verified with connection-refused code 10061.

No installed package update, production restart, production write, GUI action or new background autostart was performed in this continuation. Retained prototype data and failed-test evidence remain on disk.

## Independent final gate

`post-reboot-review-final-report.md` and `post-reboot-review-final-raw-selfcheck.json` independently confirm the approved cross-agent contract and retained raw evidence. The review explicitly marks an unconditional all-paths claim **FAIL** because the same-agent concurrency route failed and root-client hot refresh remains unproven. The scoped cross-agent result must not be promoted to an all-paths PASS.

After the temporary entry was disabled and the HTTP backend stopped, the desktop still retained its previously initialized idle stdio bridge launcher/runtime processes (16348/1732), parent App Server 4204. See `post-reboot-retained-host-bridge.json`. They have no listening test backend, and no wait subscriptions remained before shutdown. They were not force-killed or used as a reason to restart the user's desktop/production tasks; host-owned session teardown is left to the host. Accordingly cleanup here claims disabled temporary configuration and stopped test HTTP service, not destruction of every cached host client.
