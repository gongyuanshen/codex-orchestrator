# Existing Codex sub-agent host-interface probe — 2026-11-16

## Decision

**NO-GO for a Python plugin daemon directly taking control of existing desktop-native V2 sub-agents through the currently available public App Server connection.** Native collaboration controls work, but an external public control connection to their owning desktop instance was not available. A separate App Server can read persisted records; that is not live attachment or ownership.

This is a bounded result for installed `codex-cli 0.166.0-alpha.5.1` on this Windows host. It does not mean that Windows cannot support control sockets, or that no future/supported host integration can provide the needed bridge. No private named-pipe protocol, remote-control enabling, desktop restart or source modification was attempted.

## Target and scope

The dedicated fixture was created through the real native collaboration tool, not through the external probe: `/root/host_probe_target`, thread `01a1e44c-a504-7ba1-8791-6c077acc60e5`, parent `01a1e30f-6583-7c90-b1a1-b0bde53d076f`.

It already existed before the external client was started. Its only work was reporting its thread identity, returning explicit nonce strings and sleeping for interruption tests. All mutating RPCs were restricted to that exact disposable child. Production business agents and the active parent were never resumed, messaged or interrupted by an external probe.

## Results

| Capability | Native collaboration control | External public App Server probe |
|---|---|---|
| Discover existing child | Native agent is listed; child reports its actual thread ID. | Exact `thread/read` returns the same ID, parent and `source.subAgent.thread_spawn.agent_path`. Status is `notLoaded`; the separate server's loaded list is empty. This is persisted-record visibility only. |
| Deliver while running | `send_message` delivered `DELIVER_NONCE_9b7426`; the child returned it and the unchanged thread ID. | `turn/steer` returns `-32600: thread not found` for that same thread and an actual recorded turn ID. |
| Wake from idle | `followup_task` resumed the previously finished child; it returned `NATIVE_WAKE_c4728e`. | `turn/start` returns `-32600: thread not found`. No external nonce was delivered. |
| Interrupt | Native list shows running; `interrupt_agent` returns previous status running; next list shows interrupted. The child's persisted trace contains one `turn_aborted`. | `turn/interrupt` returns `-32600: thread not found`. This failure is an instance/loaded-thread boundary, not proof that interruption itself is prohibited for all V2 children. |
| Continue after interrupt | `followup_task` resumed the same child, returned `RESUME_AFTER_INTERRUPT_f93d24`, and finished normally. | `thread/resume` returns `-32600`: unloaded V2 child cannot be resumed through its parent until that parent is loaded. The real active parent was deliberately not loaded into a second server. |
| Reconnect client | Native post-interrupt continuation was tested, not a native-host transport reconnect. | Default public `proxy` cannot establish a connection. Closing a separate stdio probe and starting another still permits only persisted reads with `notLoaded`; it does not reconnect to the desktop owner. Same-owner disconnect/reconnect remains blocked by the unavailable public endpoint. |

Native evidence is extracted directly from the fixture and parent rollout records, excluding reasoning/developer context: `native-evidence.json` contains the actual calls, returns, identity outputs, nonce messages and lifecycle events. `collect_native_evidence.py` asserts all four expected markers and exactly one aborted turn. This is genuine control testing, not a mocked success response.

## Why the external path fails

### 1. Current desktop transport

The desktop App Server PID 15396, parent ChatGPT PID 13308, has no `--listen` override and therefore uses stdio. The other observed App Server explicitly uses stdio. Neither exposes a TCP listener, and the default control directory/socket does not exist. Both the official CLI's daemon-version probe and `app-server proxy` fail with Windows error 10050; the JSON-RPC initialization sent through proxy never succeeds.

Codex-related named pipes exist, but no evidence establishes them as public App Server transports. They were not connected to or reverse-engineered. Official source contains Windows Unix-socket support, so the failure is not reported as a blanket Windows limitation. See `transport-local.json`, `transport-proxy.txt`, `public-proxy.jsonl` and `transport-report.md`.

### 2. Persisted identity is not live ownership

The isolated stdio servers correctly read the existing native child's record but have no loaded instance of it. That explains the actual `thread not found` errors for all three control calls. The `thread/resume` error additionally exposes the V2 parent/session-tree requirement. Reading the same UUID from disk does not establish that a new server controls the original live agent.

### 3. A second restriction must not be ignored

The installed binary also contains the diagnostic `direct app-server input is not allowed for multi-agent v2 sub-agents`. Current official source uses this guard for direct start/steer input, while interrupt takes a different path. This is static evidence, separate from the actual errors reached in this probe. The actual runtime `thread/read` response omitted `canAcceptDirectInput`; its absence is not recorded as a returned false value. See `schema-summary.md` and the exported version-specific schemas.

## Consequence for the plugin design

Do not implement automatic mentions by blindly calling `turn/start` with a registered native `agent_id`. Do not present a newly started App Server or a read of a historic thread as control of the existing desktop agent.

Two distinct routes remain:

1. Keep existing desktop-native children and use the native collaboration bridge within the coordinator's execution context. Reliable independent backend control requires a supported host bridge that exposes those lifecycle operations to the plugin; that bridge has not been established here. Workflow state, evidence gates and handoff records can still be improved without claiming automatic host control.
2. Separately investigate plugin-managed sessions owned by its own App Server connection. Those would be a new execution model, not takeover of the current native children. Approval behavior, model configuration, UI integration and reconnect semantics would need their own acceptance before adopting it.

No such architectural switch was made during this probe.

## Preservation and artifacts

`preservation-before.json` and `preservation-after.json` compare all existing production SQLite table values, the 29 installed package files and global configuration hash. They are identical. The production orchestrator remains PID 28572 with its original start time. The desktop owning process remains in place. No production board messages, tasks, control requests or cursors were added.

All external probe processes exited; raw requests, responses, errors and process exits are retained in the `*.jsonl` files and companion `*-result.json` files. The fixture agent ended normally after the post-interrupt nonce. No new user-facing root chat was created.

Executable probes: `stdio_probe.py`, `external_controls.py`, `collect_native_evidence.py`, `probe_preservation.py`. Evidence output names are intentionally non-overwriting where raw event logs are created. They are spike/acceptance artifacts, not installed plugin functionality.

Official protocol reference: https://learn.chatgpt.com/docs/app-server . Version-specific schemas were generated from the installed executable; current upstream source is clearly separated from observed behavior.

## Independent review

`independent-review.md` records **PASS for the bounded investigation and evidence accuracy**, and **NO-GO for external takeover of existing desktop-native children through the tested public route**. The distinction is intentional: this spike did not deliver a working background-control integration.
