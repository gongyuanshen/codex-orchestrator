# Large MCP responses and repeated reconnection

## User request

Repeated Tri Orchestrator restarts in chat `01a1e03a-bff2-7342-9eab-f2bfc9c68f3c` must be diagnosed and fixed. Preserve the active F03 task, user data, existing UI and model/proxy configuration.

## Evidence (Asia/Singapore, 2026-11-16)

- 02:00:20 verifier invoked `workspace_diff(ws_e1a75bbee282)`. It failed at 02:00:28; explicit `Transport closed` followed at 02:00:42. Parent board_status and other agents then failed on the same connection.
- 03:53:43 workspace_status succeeded with 1,306 changed files; the immediately following `workspace_diff(ws_2e8beffc11b3)` failed within 5.4 seconds. Subsequent diff, heartbeat and board_status remained closed.
- Both first errors were obscured by callers parsing a plain tool error as JSON. Neither failure window contains a shim RPC/fatal traceback.
- Installed Codex binary `22cfb1d302613eae/codex.exe` includes the modern bounded-stdio closing warning and 0x800000 limit. Official implementation: <https://raw.githubusercontent.com/openai/codex/main/codex-rs/rmcp-client/src/bounded_stdio_transport.rs>, lines 24 and 74-80: an oversized line closes the transport.
- Legacy rmcp has a different 16 MiB discard limit; it is not the same failure policy. Matched revision: <https://raw.githubusercontent.com/modelcontextprotocol/rust-sdk/98a5478/crates/rmcp/src/transport/async_rw.rs>.
- Offline measurement against an isolated database copy and read-only retained F03 workspace: 1,887 changed files, 30,012,080 diff UTF-8 bytes, 33,060,621 original result JSON bytes, 35,340,518 encoded CallToolResult bytes. These are current retained-workspace values, NOT reconstructed historical byte counts.
- Installed Python SDK alone accepts a 33,574,155-byte response and continues the same session. Prior unrestricted Python readline tests therefore missed Codex compatibility.
- Baseline full suite: 37 passed in 23.88s. Full binary read and missing binary diff header reproduced with two failing tests before the root fix.

## Narrow implementation scope

- Worker: `plugin/src/orchestrator/mcp_shim.py`, `tests/test_mcp_result_delivery.py`. Explicit full-result file delivery at the common MCP output boundary; wire-byte budget accounts for JSON escaping and UTF-8. Preserve small-result schemas and error state. Atomic local artifact, byte count and SHA-256; no implicit truncation or RPC replay.
- Coordinator: `plugin/src/orchestrator/workspace_mgr.py`, `tests/test_binary_diff_io.py`. Bounded binary detection; distinguish a present binary file from an absent file. Text diff semantics remain unchanged.
- Coordinator: three workflow skills and README document the file receipt, bounded local reads, error-first parsing and no replay. No new dependencies, tools, UI changes or model/proxy changes.
- Independent verifier: real modern-protocol stdio large response and next call in the same session, artifact integrity, failure path, full regression and installed hashes.

## Deployment constraints

Backup: `C:/Users/user/.codex/backups/tri-orchestrator-reconnect-20261116-094810`. Includes source/installed plugin, consistent database and logs. Current F03 is active; do not terminate its workers or service. Plugin reload timing was asked explicitly. Do not restart Codex.

## Proof boundaries

The defective oversized transport path is confirmed from installed client code and measured payloads. Exact historical response bytes/client closing log were not retained. Do not claim every possible future transport failure or unrelated model-network error is fixed. Artifact files are retained, not automatically deleted.


## Verification and installation checkpoint

- Production change: common MCP output delivery; encoded inline budget 1 MiB minus envelope reserve. Complete JSON is atomically retained under runtime results/. Small results unchanged; large errors retain isError=true; serialization/storage failures are explicit and do not replay RPCs.
- Worker RED reproduced a 15,300,174-byte response. New regressions cover escaped Unicode, double JSON encoding, storage failure, serialization failure, small values, and same-session followup.
- Proof correction: sending the future version through legacy initialize silently negotiated 2025-11-25. Strict tests exposed this; tests now use genuine server/discover and protocol/client metadata on every modern request, plus a separate exact legacy handshake. No weakened assertion.
- Final root full suite: 49 passed in 36.60s; JavaScript syntax exit 0. Independent verifier full suite: 49 passed in 36.28s. No remaining source P0/P1 findings.
- Installed 5 files atomically: mcp_shim.py, workspace_mgr.py and three skills. Source/installed/manifest hashes match; original backup hashes preserved. Global Codex configuration unchanged. No service or Codex restart performed.
- Actual installed-process acceptance uses the real daemon HTTP backend and retained F03 workspace, NOT a mocked diff: 1,887 changed files, full original JSON 32,930,565 bytes; receipt frame 657 bytes; exact artifact byte count and SHA verified; 20 following run_list requests succeed in the SAME modern stdio connection; child exit 0, stderr empty, daemon PID remains 20824. This response used the still-running daemon's old binary reader; earlier offline source measurement included the newly visible binary headers, hence different result byte counts.
- Initial installed probe incorrectly omitted protocol metadata on discovery and got Method not found before any workspace call. SDK source hashes match; corrected metadata matches the tested protocol and passes. This is retained as a probe error, not reported as a product failure.
- Independent installed gate: all 5 file hashes and the full 32,930,565-byte artifact checked again; daemon health OK. The recorded 20 followup calls were performed by coordinator, not rerun by verifier.
- Machine-readable evidence: deployment.json, installed-live-acceptance.json and large-diff-measurement.json under the named backup directory.

## Earlier activation checkpoint — before manual toggle

- User confirmed all other tasks are finished and authorized the one-time plugin reload. Codex itself was not restarted.
- Created a fresh SQLite online backup `pre-activation-state.db`; integrity_check=ok. Verified the exact formal daemon identity and restarted only it through the existing startup helper: PID 20824 -> 18676. New health started_at=1794806028.4241326.
- Independent verification: health OK; 5/5 source/installed/manifest hashes match; global config hash unchanged; fresh snapshot integrity OK.
- The original F03 workspace is now merged and clean. The initial post-reload probe expected a large receipt and failed its delivery-key assertion because the result was legitimately small, not because transport failed. No business files were modified.
- Created an explicitly isolated acceptance run `run_7f69c1ffcb28` and workspace `ws_1ec0f629621f` under the backup directory. A 14,800,000-byte UTF-8/escaped-text file produces a complete 16,800,466-byte JSON result. Fresh installed modern stdio process -> new real daemon: 657-byte receipt, exact size/SHA verified, 20 subsequent calls on the same connection succeed, exit 0, no stderr; daemon PID unchanged. Evidence: post-daemon-activation-acceptance.json.
- Existing desktop shim remains the old 09:33:49 process. The desktop control socket is unavailable; generated local schema exposes config/mcpServer/reload but no reachable desktop API endpoint was established. No speculative config changes, UI automation, forced shim termination, or Codex restart was performed. User was asked to toggle only this plugin off/on once.
- Current Codex connection large-result acceptance remains pending until that one-time reload; do not label the fresh-process probe as current-desktop proof. The isolated fixture is retained for the final actual-tool acceptance.

- The isolated acceptance run was archived (not falsely marked complete) while waiting for the manual toggle; its workspace/artifact remain available for final desktop acceptance.

## Final activation acceptance — 2026-11-16 13:32 +08:00

- User confirmed the one-time plugin off/on toggle. Current desktop MCP shim is a new process (PID 3744, started 13:31:12; launcher chain 22660/31176). Codex app-server PID 28260 was not restarted. Formal daemon remains PID 18676.
- Actual tools in THIS Codex conversation, not a separate stdio client: workspace_diff(ws_1ec0f629621f) returned a file receipt for a complete 16,800,466-byte result. The old inline encoding would be 19,600,582 bytes (recorded by the new shim), above the host frame budget. Full file size/SHA and byte-for-byte equality with the fresh-process artifact were verified.
- Immediately following that result, 20 sequential run_list(limit=1) calls on the same desktop MCP connection all succeeded. Shim and daemon PIDs were unchanged after the calls; no restart or reconnection intervention occurred.
- Current desktop evidence: actual-codex-activation-acceptance.json; artifact results/3eccb363c89b45649eb2bf332948c917.json. Source, installed and deployment manifest hashes match for all 5 installed files. Global configuration hash still matches the pre-fix backup.
- The isolated acceptance run is now completed; the fixture and all evidence are retained. No F03/business files, model/proxy settings, or Codex application process were changed by this acceptance.
- Closure scope: the oversized-MCP-result failure path is fixed, installed and accepted through the current desktop connection. Long-duration continuous load was not tested; unrelated model/network reconnection causes are outside this fix.
