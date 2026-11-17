# R1 — installed owning-host capability review, 2026-11-17

## Decision and stop point

**NO-GO for implementing an independently operating plugin bridge to existing desktop-native V2 children on the inspected installation.** No new supported attachment route to that owning host was established. This is a version/launch-configuration-scoped engineering decision, not proof that every host channel is absent or that these capabilities are impossible on Windows.

This was a short current-version/interface pass, not two engineer days of work. The two-day ceiling was not consumed. In accordance with the brief, no duplicate native fixture or repeat of previously failed control RPCs was run after the attachment prerequisite remained unestablished. Continue M1–M5 without claiming host takeover; reopen R1 only with new public interface evidence.

## Current local evidence

Evidence directory: `C:/workspace/codex-orchestrator/docs/verification/native-gap-next/r1/`.

- `owner-version.txt`: the actual desktop-owned executable reports `codex-cli 0.166.0-alpha.5.1`, matching the previous investigation's version.
- `owner-processes.json`: desktop-owned app-server PID **17948**, desktop parent PID **5568**, executable under `C:/Users/user/AppData/Local/OpenAI/Codex/bin/22cfb1d302613eae/`. Its command line has no `--listen` override. Another observed app-server PID **11320** explicitly uses stdio; its ownership is not inferred and it was not contacted.
- `owner-binary-hash.json`: SHA-256 of the actual owning executable is `3097C909FB6D5CD63510D44ACEB1D42DF9F6E85722A3E447E477DEF1ECF60E16`. This identifies this pass's binary; no byte-for-byte comparison with the prior binary was made.
- `owner-app-server-help.txt`: local CLI documents stdio as the default and separately exposes Unix socket/WebSocket/off transports, WebSocket authentication, and `--code-mode-host`.
- `owner-daemon-help.txt` and `owner-proxy-help.txt`: management/proxy commands remain available. Help availability is not a running control endpoint or permission to reconfigure the owner. No management command was executed.
- `endpoint-observation.json`: at **2026-11-17T15:05:34.8936806+08:00**, default control directory and socket were absent; TCP listener count for the two observed app-server PIDs was zero. `owner-tcp-listeners.json` is empty PowerShell output for that filtered query; the explicit count is in `endpoint-observation.json`.

These observations support the default-stdio attachment obstacle. They do **not** enumerate or disprove private IPC, a custom Unix socket hidden in configuration, or every future/managed-host bridge. No private pipe was connected, configuration/authentication secret read, or endpoint guessed.

## Capability matrix — separate conclusions

| Capability | R1 result | Evidence and exact boundary |
| --- | --- | --- |
| Same owning host | **NO-GO: supported attachment not established** | Current CLI/owner process/default endpoint observations above. Public server lifecycle documentation does not establish external attachment to this already-running desktop stdio owner. No connection or new server was started. |
| Autonomous event wake of the existing native child | **NO-GO: not demonstrated for this target** | No owner control/subscription route established. Official MCP Events is a different documented Work/Cloud subscription feature; it is not evidence for addressing a local existing V2 child. No event delivered, child woken, or root-idle acceptance run in R1. Native coordinator tools remain different from an independent plugin callback. |
| Same-agent interruption and reconnect | **NO-GO: prerequisite blocked; not re-tested** | The prior report has genuine native interrupt/continue evidence and failed separate-server controls. R1 adds no new runtime interruption, reconnect, pending-action reconciliation, or delivery evidence. Current docs' lifecycle operations do not prove owning-host reachability or V2-child direct-input permission. |
| State and identity provenance | **NO-GO for authenticated native-agent binding** | Process parent/PID/hash identify observed local processes only. No host-issued credential binding an MCP call to a native child was obtained. Thread IDs, names, caller parameters, client metadata and shared MCP connections are not accepted here as that credential. No MCP request-context fields were read and no redacted SDK context sample was collected; their authenticity was not tested. |
| Long-wait cost | **NO-GO for a quantified or zero-cost claim** | No same-owner event subscription, waiting fixture, or measured host usage/cost trace exists in R1. A documented usage notification is not a billing result. Neither repeated 25-second waits nor absence of visible output establishes cost. No long-wait trial was run. |

## Official OpenAI documentation checked

Fetched on 2026-11-17; full fetched Markdown retained as `r1/official-app-server.md` and `r1/official-mcp-events.md`. These are current documentation, not a claim that every documented field exists in this installed alpha.

1. [Codex App Server](https://learn.chatgpt.com/docs/app-server) — documents client transports, thread/turn lifecycle, interruption, usage notifications and client metadata. The remote Code Mode host configures tool execution, not attachment to another app-server owner. Connection authentication is distinct from attestation of an individual native child's MCP identity. No documented bridge for this already-running desktop owner was established from this page.
2. [MCP Events](https://developers.openai.com/plugins/build/mcp-events) — documents webhook-triggered subscribed Work chats, with desktop availability requiring Cloud selection. It is not documented here as direct wake/control of existing local desktop V2 children. This scope mismatch stops this route; it is not a blanket claim that event-triggered chats do not work.

The initial guessed documentation URL `https://developers.openai.com/plugins/events` returned 404; the canonical Events page above was then obtained through the official App Server page's documentation navigation. No product endpoint was guessed or probed.

The session exposes native collaboration tools and Codex app chat tools as host-provided tools. Their presence inside an executing agent turn does not establish a public callback/control endpoint available to the external Python daemon. No app chat messaging, chat creation/migration, UI automation, or tool invocation against other chats was performed by this pass.

## What is inherited versus newly established

Read first: `C:/workspace/codex-orchestrator/docs/verification/host-interface-20261116/report.md` and its `transport-report.md`.

**Prior evidence, not rerun:** native message/wake/interrupt/continuation of the dedicated child; separate App Server persisted reads; rejected external start/steer/interrupt/resume; failed proxy connection; static V2 direct-input guard. The prior runtime omitted `canAcceptDirectInput`; neither prior work nor R1 converts that omission to a returned false. Prior source-level guard evidence is not a new observed runtime rejection.

**Newly established:** today's actual owning process and parent, executable version/hash, current CLI help, default endpoint absence/listener count, and the current official documentation scope review. Owner PIDs changed; no new supported attachment interface was established. Version equality alone is not treated as proof of identical executable bytes.

**Not supported by the checked documentation:** using the documented Work/Cloud MCP Events route as a local V2-child targeting API; using client-supplied identity metadata as host-issued child authentication.

**Not proven and not re-tested:** custom supported desktop attachment, every possible host IPC channel, same-owner disconnect/reconnect, uncertain-action recovery, current V2 direct-input behavior, authenticated MCP-agent binding, autonomous same-child wake, and long-wait billing. No claim of universal impossibility follows from these gaps.

## Preservation and reopening criterion

No production source, runtime, database, global configuration, authentication material or installed package was modified by R1. Only the assigned report/evidence paths were written. No alternate runtime, new native agent, daemon, UI session, service or Git commit was created. This is a statement of performed actions, not a hash-based global preservation audit.

Reopen only when a supported public interface explicitly supplies attachment to the existing desktop owner and applicable native-child lifecycle authority, or another documented route meets the same owning-host requirement. Before implementation, validate that new route with one disposable fixture and separately prove identity, wake, interruption/reconnect, pending-action handling and waiting usage. A plugin-managed new runtime or migrated chat does not satisfy this R1 contract.
