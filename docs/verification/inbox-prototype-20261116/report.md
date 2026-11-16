# Reliable inbox isolated prototype acceptance

## Current verdict

**PASS for the approved isolated prototype.** Native receipt, original-author rework, independent acceptance, duplicates, bounded waits, ended-child resume, client reconnect and separate-process restart/reconciliation all passed. Independent final review also passed. No installed version was updated.

## Implemented scope

The separate `prototypes/inbox/` addon reuses existing SQLite transactions, BoardService, EventBus, HTTP application and MCP SDK. Four tools provide durable send/get/ack and bounded wait. Exact duplicates keep their message ID; altered content conflicts. Receipts distinguish accepted/completed/failed. Accepted but unfinished work remains reconciliation-required rather than being replayed automatically. Waiting is asynchronous, limited to 25 seconds, and never acknowledges messages.

No production `plugin/` changes or new dependencies. Prototype port is 19891; production remains 18790.

## Evidence already passed

| Check | Actual result | Evidence |
|---|---|---|
| Original regression plus addon suite after recovery | 270 passed in 52.02 seconds, exit 0 | `post-recovery-regression.txt` |
| Independent final review | 151 addon + 11 fixture tests passed; no actionable implementation defect | `independent-review-final.md`, `review-final-tests.txt`, `review-final-fixture.txt` |
| Real stdio MCP concurrency | Same session waits and sends without serial blocking | `bridge-tests.txt`, `prototypes/inbox/tests/test_bridge.py` |
| Error and large result handling | Conflicts preserve MCP error; full file payload hash verified | `bridge-tests.txt` |
| Active native writer receives queued work | Existing subscription before send; actual tool result delivered | `native-wait-subscribed.json`, `writer-initial-wait.json` |
| First author delivery | 11 passed | `writer-initial-after-test.json` |
| Disclosed real fault independently rejected | 2 failed, 9 passed; review receipt is failed | `fault-injection.json`, `verifier-first-result.json` |
| Ended child does not auto-wake | After rework post: native child completed, inbox pending, no receipts, artifact unchanged | `ended-child-before-resume.json` |
| Same author explicitly resumed | Native followup, actual inbox read, repair then 11 passed | `writer-rework-wait.json`, `writer-rework-after-test.json` |
| Same independent verifier rechecks | 11 passed; frozen tests unchanged; PASS not inferred from receipt | `verifier-second-result.json` |
| Coordinator forwards actual results | Durable result receipts retained for initial/failure/repair/final reports | `coordinator-msg_*.json` |
| Duplicate message and historical ACK | One message/board post; old accepted ACK does not revert completed state | `edge-send-repeat.json`, `edge-historical-ack.json` |
| Maximum bounded wait | 25.0045 seconds, explicit timeout, no ACK | `edge-timeout-measure.json`, `edge-max-timeout.json` |
| Native child bounded timeout | Exactly one 1000 ms wait, then native turn ended | `writer-rework-bounded-timeout.json` |
| Client disconnect cleanup | HTTP wait canceled; subscribers return from 1 to 0 | `edge-client-disconnect.json` |
| Read after client reconnect | Unacknowledged durable message still present | `edge-post-disconnect-read.json` |
| Restart preserves accepted receipt | Same ID/history, accepted and reconciliation-required | `edge-restart-received.json` |
| Reconcile uncertain side effect | Existing file verified; action count remains 1; no replay | `edge-recovery-verdict.json`, `edge-reconciled.json` |
| Test run closed and server exited normally | No pending waits/subscribers; 19891 stopped | `prototype-run-completed.json`, `prototype-closed-wait.json`, `restarted-service-clean-shutdown.json` |
| Final production preservation | DB inventory, installed/source package hashes, config and daemon PID/start unchanged | `preservation-before.json`, `preservation-after.json`, `review-final-evidence.txt` |

The writer and verifier are the actual native agents `/root/inbox_native_writer` and `/root/inbox_native_verifier`; original-agent `followup_task` was used for rework/review. They executed real tests and exchanged actual persisted messages through the SDK bridge.

## Completed restart checkpoint

The user manually stopped the original isolated process **27220**. Read-only checks confirmed the process and 19891 listener absent. Automatic force termination had previously been blocked before execution; no stop was observed during eight bounded 25-second waits. Those timeout/failure records are retained, not counted as a successful in-flight service-loss test.

The first recovery preflight timed out at 2 seconds. Measurement showed Windows connection refusal after approximately 2.003 seconds, with HTTP ConnectError at 2.115 seconds when given a 5-second deadline. The test-harness deadline was corrected to 5 seconds; timeout was never reclassified as proof of a stopped service. Only this harness setting changed; production configuration and the inbox wait contract did not.

Recovery then ran under new process **25884**, using the same isolated database. The original accepted receipt survived, remained reconciliation-required and did not automatically execute work. The existing one-line side effect and hash were checked before explicit completion. Its count remained **1**. The test run was closed and Uvicorn exited normally. No detached test service remains; production **28572** on **18790** retained its original start time.

Executed successfully:

```powershell
Set-Location 'C:\workspace\codex-orchestrator'
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe docs/verification/inbox-prototype-20261116/restart_recovery.py
.\.venv\Scripts\python.exe docs/verification/inbox-prototype-20261116/verify_evidence.py
.\.venv\Scripts\python.exe docs/verification/inbox-prototype-20261116/preservation.py after
```

Outputs: `restart-recovery-execution-2.txt`, `final-evidence-check.txt`, `acceptance-verdict.json`, `preservation-after.json`. The evidence checker verified ten terminal inbox messages, unchanged frozen tests, matching independent failure/repair artifacts, a single unreplayed side effect and SQLite integrity.

These are historical execution commands, not an invitation to rerun write-once scripts against completed evidence. Use a new isolated directory/run for another experiment. The independent final audit rechecked actual outputs, hashes, database state, production preservation and the closed test port without overwriting evidence. The full original-plus-addon regression subsequently passed 270 tests again.

## Proof boundaries

- Native agents used terminal tools to invoke a real stdio MCP SDK client. This is not registration of a new tool in the desktop's live MCP catalog and does not prove that catalog's host timeout behavior.
- In-flight MCP server-loss response was not successfully measured; actual client cancellation/reconnect and separate-process restart recovery were verified. The manual stop is not a plugin-native lifecycle capability.
- Posting is not host lifecycle control. Ended children require native coordinator followup; a pending message cannot hard-interrupt another running tool.
- The prototype trusts local participants; recipient strings are not authenticated identities. No arbitrary-side-effect exactly-once guarantee.
- Isolated source/transport/fixture tests are not packaged production deployment or GUI acceptance. The installed 0.1.2, global configuration, production DB and service were not changed.

## Subsequent native-host and reboot follow-up

The report above is the completed initial checkpoint; its native-registration and in-flight-loss boundaries were subsequently tested in `../inbox-host-followup-20261116/report.md`. That follow-up records 271 passing regression tests, fresh-desktop cross-agent native receipt/ACK/run closure, real in-flight service error and recovery, and disabled/stopped temporary test infrastructure. Same-agent desktop concurrent wait/send failed and remains an explicit limitation. The follow-up also distinguishes unrelated production run additions from preservation of all preexisting rows. No installed update was performed.
