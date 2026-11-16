# Isolated reliable inbox prototype

This addon reuses the installed project's Python environment, SQLite, message board, EventBus, HTTP application and MCP SDK. It does not change `plugin/`, the installed package or the desktop MCP registry.

## Run

From `G:\工具\codex-orchestrator`:

```powershell
.\.venv\Scripts\python.exe -m prototypes.inbox.server --port 19891 --data-dir "G:\工具\codex-orchestrator\docs\verification\inbox-prototype-20261116\data"
.\.venv\Scripts\python.exe -m pytest -q prototypes/inbox/tests -o "pythonpath=.;plugin/src"
```

Use a separate explicit data directory for a new acceptance run. The retained acceptance directory contains prior message IDs and receipts; its write-once evidence scripts intentionally reject accidental reruns that would overwrite evidence.

The server rejects production port 18790 and production database aliases. The test MCP bridge requires an explicit loopback URL/data directory and checks the HTTP service identity before forwarding. It never launches a daemon automatically.

## Contract

- `inbox_send`: durable message plus board entry in one transaction. The unique key is `(run_id, sender, key)`. Identical retries return the same ID; changed content conflicts.
- `inbox_get`: retrieve the message and receipt history.
- `inbox_ack`: `pending -> accepted -> completed/failed`. Identical historical receipt retries do not roll back later state. A completion receipt is an agent claim, not independent acceptance.
- `inbox_wait`: default 20 seconds, maximum 25 seconds. Async waiting holds neither a database lock nor a parked executor thread. SQLite is the source of truth; EventBus is a notification signal. Waiting never acknowledges work.

Accepted-but-uncompleted messages are returned with `requires_reconciliation=true`. Inspect persisted effects before deciding whether to complete, fail or explicitly schedule a new repair. This protocol does not claim exactly-once execution of arbitrary external side effects.

## Coordinator and agent lifecycle

An active native agent can call the bounded wait through its terminal tool using `prototypes.inbox.client` and receive a real stdio MCP result. It must explicitly acknowledge, execute and report. The coordinator forwards independent rejection to the original writer, then asks the same verifier to retest.

A native agent that already ended has no pending tool call. Posting to its inbox does not wake it. Only the coordinator's actual native `followup_task` resumes that child; the resumed child reads the durable payload instead of having it substituted in the follow-up instruction. A worker's registered status is not host lifecycle telemetry.

This is a trusted-local prototype: a recipient string is not authenticated identity. Temporary desktop-native tool registration was manually tested through fresh native subagents; it is not installed into the production plugin. There is no arbitrary host takeover, hard interrupt via a message, or perpetual zero-cost standby. Wait timeout must return to the agent; continued waiting requires another explicit bounded call and occupies a native agent slot.

## Evidence

`docs/verification/inbox-prototype-20261116/report.md` separates unit, actual HTTP/stdio transport, actual native-agent, and production-preservation evidence. It records deliberate faults and failed attempts rather than converting errors into successful timeouts.

## Desktop invocation boundary

The current desktop serialized a same-agent concurrent wait/send experiment: the wait timed out before the queued send completed. This route failed and is not presented as supported concurrency. The intended **separate coordinator sends / native worker waits** route passed: the receiver returned the message during its single bounded wait, then acknowledged it. Do not put the producer behind the wait in one agent's tool batch.

The optional test MCP requires its explicitly configured isolated HTTP server to be running before initialization; it does not auto-start the backend after a computer reboot. A fresh desktop child loaded the native tools after the backend was restored, while the preexisting root client retained its earlier startup failure. Root hot-refresh is not claimed.

See `docs/verification/inbox-host-followup-20261116/report.md` for the additional native-host, real in-flight service-loss, reboot, 271-test regression and cleanup evidence. Temporary `inbox_probe` is now disabled and 19892 stopped. This does not change the installed 0.1.2 package.
