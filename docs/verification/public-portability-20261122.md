# v0.6.8 public install and portability audit — 2026-11-22

The published `v0.6.7` tag (`11b1f04`) is the comparison baseline. The changes target `v0.6.8`; the plugin identity, `workflow5` and schema 10 stay the same.

| Reproduced issue | Candidate change |
| --- | --- |
| The Bash installer wrote through linked role files and treated a directory at a role path as a successful install. | Reject linked or non-regular destinations before changing the profile. Replace regular role files by name so other hard links keep their original contents. |
| Both workspace RPC entry points accepted `copy_env: "false"` and treated it as true. | Validate Boolean arguments at the shared RPC boundary, before creating a workspace or copying local environment files. |
| New project paths resembling the maintainer's verification fixtures were classified as fixtures. | Keep that heuristic only for legacy migration. Do not reclassify existing history. Tests use invented project names while still covering Unicode paths. |
| In a fresh Codex home, the plugin was installed without enabling native V2. Role selection also assumed the default home. | Document the feature commands and a first task. Resolve custom roles against the active `CODEX_HOME` and the host's actual tool schema. |

## Pre-release audit evidence (before version bump)

- Windows, Python 3.12.14, locked dependencies: **996 passed, 2 skipped** in a clean checkout with the candidate edits. The skips were the T7 rehearsal requiring explicit machine inputs and the POSIX process-group case. After the final exact-target replacement change, the installer and retention suites passed **24 tests**.
- Used real Codex CLI `0.166.0-alpha.5.1` with separate new profiles to install the public GitHub tag and the local candidate. Both started with `multi_agent_v2=false`; `codex features enable multi_agent_v2` changed it to `true`. No custom roles were installed.
- Cold-started the stdio server using the transport reported by `codex mcp list --json`. Both profiles exposed 35 tools and started their own schema 10 daemon. A test repository verified Git isolation, `copy_env: false`, message-board round trip and the dashboard snapshot. The candidate also returned HTTP 400 for the string value. Both test daemons were stopped and their ports released.
- Checked both lockfiles and built a wheel containing all five CLI entry points and the dashboard assets.

The Bash cases ran in Git Bash on Windows. This audit did not run native macOS/Linux, a signed-in model-driven workflow, or against the user's active Codex configuration or database. The dashboard remains a local, unauthenticated service.
