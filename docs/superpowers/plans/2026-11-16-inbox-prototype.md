# Isolated Reliable Inbox Prototype Implementation Plan

**Goal:** Demonstrate receipt, original-author rework and independent verification with real native agents; exercise duplicate messages, bounded timeout, completed agents and disconnect recovery without changing installed 0.1.2 or production port 18790.

**Architecture:** New `prototypes/inbox/` addon imports the existing orchestrator Database, BoardService, EventBus and web application. It owns a separate SQLite data directory and loopback port 19891. Its stdio MCP bridge is driven by an explicit test client from native agents' terminal tools; it is not installed into the desktop's production MCP registry. This proof layer must be named honestly.

**Tech stack:** Existing Python 3.12, SQLite, Starlette, httpx, uvicorn and MCP SDK. No dependency additions. The checkout is not Git; the addon, fixture and test data are isolated by directory rather than inventing a Git worktree.

## Frozen prototype contract

- `inbox_send(run_id, sender, recipient, key, kind, summary, body_md)` returns `{message, duplicate}`. `kind` is task/rework/review/result/notice. Require existing active run and registered recipient. The durable key is unique per `(run_id,sender,key)`; same canonical payload returns the same message, different payload conflicts. Message and board post commit atomically using existing nested transactions. Notify only after commit.
- `inbox_get(message_id)` returns the message and receipt history.
- `inbox_ack(message_id, recipient, status, evidence)` returns the updated message. States are pending -> accepted -> completed/failed. Exact repeated receipt is idempotent; different terminal receipt or wrong recipient is an explicit conflict. `evidence` is a nonempty string; it is an agent claim and not automatic proof of business correctness. Accept does not mean complete.
- `inbox_wait(run_id, recipient, timeout_ms=20000)` accepts integer 0..25000 (not bool). It does not advance a read cursor or acknowledge messages. Return `{status, messages}` where status is messages/timeout/run_closed/worker_paused/control_requested. Read nonterminal messages, including accepted items tagged for reconciliation, so recovery never silently re-executes them. Paused/killed workers do not resume on mentions. Report errors as errors, not timeout.
- Receipt and message IDs are stable. Only a coordinator using native tools can restart an ended child. Posting a message never pretends to have woken it.
- Wait asynchronously without holding DB locks or occupying an executor thread. Subscribe before querying; re-query SQLite after event wake or deadline. EventBus is only a signal. Cancellation/disconnect must release the subscriber. Shut down only the isolated server.
- Test bridge exposes the existing tool catalog plus the four inbox tools. It requires explicit URL/data directory, verifies isolated identity, never auto-starts production, and uses the existing MCP SDK. Bound all test waits beneath HTTP limits.

## Ownership

- Inbox store worker: `prototypes/inbox/store.py`, `prototypes/inbox/tests/test_store.py` only.
- Async server worker: `prototypes/inbox/server.py`, `prototypes/inbox/tests/test_wait.py` only.
- Coordinator: package marker, MCP bridge/client, native fixture/spec, acceptance drivers, preservation captures and final report.
- Independent reviewer: read-only implementation review and evidence checks; may create its review report only.

## Tasks and acceptance

- [x] Capture production DB/table hashes, installed/source package hashes, configuration and daemon identity; reject fixture data/port aliases of production.
- [x] RED then GREEN store tests: exact duplicates, changed-key payload conflict, atomic rollback, recipient isolation, receipt transitions/retries and reopen recovery.
- [x] RED then GREEN async tests: delivery before/after subscription, deadline, overflow signal with durable DB retrieval, many waiters plus fast posting, cancellation/disconnect and closed/paused states.
- [x] Exercise actual stdio MCP wait and simultaneous send on one connection; native workers must receive real tool output, not scripted verdicts.
- [x] Native writer receives task, fixes fixture and acknowledges. Coordinator injects a disclosed real fault, verifier detects it with actual failing tests, original writer receives rework, same verifier reruns tests and confirms the corrected artifact.
- [x] Duplicate send yields same ID/no second board post. Duplicate acknowledged work is not offered again. Accepted but uncompleted work survives disconnect as reconciliation-required, never auto-completed.
- [x] Native child ends; subsequent message stays pending until explicit native followup resumes the same child. Demonstrate no background-wakeup claim.
- [x] Disconnect a waiting client; preserve unacknowledged message. Restart isolated server; receipts survive, reconciliation precedes resumed action. No replay of an uncertain side effect.
- [x] Independently review, rerun relevant/full regressions, stop test server and confirm production/source preservation.

## Non-goals

No installed plugin update, global MCP registration change, new user-owned chat, perpetual zero-token standby, hard interrupt through an ordinary message, complete production authentication design or app-server takeover. The prototype assumes trusted local test participants; recipient arguments are not cryptographic sender authentication. Retained evidence is not deleted.

## Checkpoint
Complete for the approved isolated prototype: native repair chain, duplicate/timeout/ended-child cases, client reconnect, separate-process restart/reconciliation, clean shutdown, production preservation and independent final gate passed. Post-recovery regression: 270 passed. The user manually stopped original isolated PID 27220; restarted test PID 25884 exited normally. No installed update or desktop-native MCP registration. In-flight MCP server-loss response was not successfully observed and is not claimed. See docs/verification/inbox-prototype-20261116/report.md.

## Subsequent user-approved host verification

The initial checkpoint above remains historical. The user later manually created and enabled a temporary `inbox_probe` MCP for additional native-desktop verification, then disabled it after completion. Follow-up evidence: `docs/verification/inbox-host-followup-20261116/report.md`.

- [x] Real in-flight service cancellation, explicit HTTP error diagnostics and recovery on the same MCP session.
- [x] Installed App Server native routing and bounded wait.
- [x] Fresh current-desktop native child tool calls and separate coordinator/worker concurrent delivery with receipt/run closure.
- [x] Record same-agent concurrent wait/send failure honestly; use the independently verified separate-agent topology without a hidden retry.
- [x] Run combined regression (271 passed), close isolated runs, disable temporary MCP and stop the owned test server.
- [x] Preserve production package hashes and preexisting rows; retain unrelated concurrently appended Boost run data.

Root-client hot refresh, forced OS crash at an in-flight write, authenticated sender identities and production deployment remain outside the completed proof.
