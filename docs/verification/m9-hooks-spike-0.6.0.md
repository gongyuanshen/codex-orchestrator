# M9 Gating Spike Verification Report (`v0.6.0` Lifecycle Hooks on `codex-cli 0.166.0-alpha.5.1`)

> **Date:** 2026-11-18  
> **Host Binary Tested:** `<LOCALAPPDATA>\OpenAI\Codex\bin\22cfb1d302613eae\codex.exe` (`codex-cli 0.166.0-alpha.5.1`, Windows 10.0.22631 x86_64)  
> **Isolation Boundary:** Ephemeral `CODEX_HOME` and mock OpenAI Responses SSE HTTP server on `127.0.0.1:<ephemeral_port>` under `%TEMP%`. Zero reads/writes against user `~/.codex` or live `127.0.0.1:18790`.

---

## 1. Host Feature Flags & Embedded Wire Schemas

Running `codex features list` in an isolated `CODEX_HOME` confirmed:

```text
hooks                                    stable             true
plugins                                  stable             true
plugin_discovery_prefetch                stable             true
plugin_hooks                             removed            false
```

`hooks` and `plugins` are both `stable` and enabled by default in `0.166.0-alpha.5.1` (the legacy `plugin_hooks` feature flag was removed after merging into `hooks`).

### 1.1 Exact Embedded Draft-07 Command Input / Output Wire Schemas

Extracted directly from the `codex.exe` binary (`codex_hooks` crate) and validated against live hook execution:

| Hook Event | `stdin` Required Fields (`additionalProperties: false`) | `stdout` Allowed JSON Fields (`additionalProperties: false`) |
| :--- | :--- | :--- |
| `SessionStart` | `cwd`, `hook_event_name: "SessionStart"`, `model`, `permission_mode`, `session_id`, `source` (`"startup" \| "resume" \| "clear" \| "compact" \| "fork"`), `transcript_path` | `continue`, `stopReason`, `suppressOutput`, `systemMessage`, `hookSpecificOutput: {"hookEventName": "SessionStart", "additionalContext": "<str>"}` |
| `PostCompact` | `cwd`, `hook_event_name: "PostCompact"`, `model`, `session_id`, `transcript_path`, `trigger` (`"manual" \| "auto"`), `turn_id` (+ optional `agent_id`, `agent_type` on subagents) | `continue`, `stopReason`, `suppressOutput`, `systemMessage` (**Note:** `hookSpecificOutput` is NOT permitted on `PostCompact`!) |
| `UserPromptSubmit` | `cwd`, `hook_event_name: "UserPromptSubmit"`, `model`, `permission_mode`, `prompt`, `session_id`, `transcript_path`, `turn_id` (+ optional `agent_id`, `agent_type` on subagents) | `continue`, `decision` (`"block"`), `reason`, `stopReason`, `suppressOutput`, `systemMessage`, `hookSpecificOutput: {"hookEventName": "UserPromptSubmit", "additionalContext": "<str>"}` |
| `Stop` | `cwd`, `hook_event_name: "Stop"`, `last_assistant_message`, `model`, `permission_mode`, `session_id`, `stop_hook_active` (`bool`), `transcript_path`, `turn_id` | `continue`, `decision` (`"block"`), `reason` (required when `decision == "block"`), `stopReason`, `suppressOutput`, `systemMessage` (**Note:** `hookSpecificOutput` is NOT permitted on `Stop`; subagent turn stops fire `SubagentStop` instead of `Stop`) |

---

## 2. Four Critical Empirical Findings from Isolated Fixture Testing

### Finding 1 — `HookHandlerConfig::Command` Windows Shell & Environment Variables
1. `hooks/hooks.json` command handlers accept `{"type": "command", "command": "...", "commandWindows": "...", "timeout": <sec>}`.
2. On Windows, Codex executes `commandWindows` (or `command` if `commandWindows` is omitted) via **PowerShell**. A bare quoted executable path without PowerShell's call operator `&` (e.g. `"C:\...\python.exe" "script.py"`) fails PowerShell parsing; prefixing `& "..."` or using conditional `if (Test-Path ...) { & ... }` succeeds cleanly and forwards `stdin` verbatim.
3. Codex expands `${CLAUDE_PLUGIN_ROOT}` in the hook command string to the installed plugin cache path and injects five plugin environment variables into the hook process:
   - `CLAUDE_PLUGIN_ROOT` = `<CODEX_HOME>\plugins\cache\<marketplace>\<plugin>\<version>`
   - `PLUGIN_ROOT` = `<CODEX_HOME>\plugins\cache\<marketplace>\<plugin>\<version>`
   - `CLAUDE_PLUGIN_DATA` = `<CODEX_HOME>\plugins\data\<plugin>-<marketplace>`
   - `PLUGIN_DATA` = `<CODEX_HOME>\plugins\data\<plugin>-<marketplace>`
   - `CODEX_HOME` = `<CODEX_HOME>`

### Finding 2 — `HookTrustStatus` State Machine (`untrusted` -> `trusted`)
Querying `codex app-server --stdio` `hooks/list` immediately after `codex plugin add` returns `"trustStatus": "untrusted"` with a composite `currentHash` (`"sha256:<command_hash>/sha256:<plugin_content_hash>"`). Untrusted plugin hooks are skipped until approved by the user in the Codex UI (or recorded in `config.toml` under `[hooks.state]`):

```toml
[hooks.state]
"spike-plugin@spike-mp:hooks/hooks.json:stop:0:0" = { enabled = true, trusted_hash = "sha256:<command_hash>", trusted_content_hash = "sha256:<plugin_content_hash>" }
```

Passing `--strict-config` accepted `[hooks.state]` with 0 warnings and transitioned all 4 hooks (`sessionStart`, `postCompact`, `userPromptSubmit`, `stop`) to `"trustStatus": "trusted"`.

### Finding 3 — Dual-Manifest Loader Precedence (`plugin/plugin.json` vs `plugin/.codex-plugin/plugin.json`)
Empirically tested via `codex app-server --stdio` `hooks/list`:
- **Case A (both root `plugin/plugin.json` with `$schema: "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"` and `plugin/.codex-plugin/plugin.json` present):** `codex_core_plugins::loader` selects the Agent Plugins 1.0 manifest (`plugin/plugin.json`) and overlays `.mcp.json`, but Agent Plugins 1.0 has no `hooks` field (adding `"hooks"` to root `plugin.json` logs `WARN ignoring unknown Agent Plugins manifest field`), resulting in `"hooks": []` in `hooks/list`.
- **Case B (`plugin/.codex-plugin/plugin.json` with `"hooks": "./hooks/hooks.json"`):** `hooks/list` discovers all 4 plugin hooks cleanly.
- **Resolution for `v0.6.0`:** Both `plugin/plugin.json` (`version = "0.6.0"`) and `plugin/.codex-plugin/plugin.json` (`version = "0.6.0"`, `"hooks": "./hooks/hooks.json"`) are preserved so `test_package_metadata.py` and `install.ps1` remain 100% compatible, while `plugin/hooks/hooks.json` + `tri-orchestrator-hook` are ready for `.codex-plugin/plugin.json` discovery, user/project `hooks.json` forwarding, and future Codex Agent Plugins hooks overlay support.

### Finding 4 — `Stop` Single-Shot Block & Context Injection Semantics
In both `codex exec` and `codex app-server` (`thread/start` -> `turn/start` -> `thread/compact/start`):
- `SessionStart` (`hookSpecificOutput.additionalContext`) and `UserPromptSubmit` (`hookSpecificOutput.additionalContext`) are injected into the model request as `{"role": "developer", "content": [{"type": "input_text", "text": "..."}]}`.
- When `Stop` (on Turn 1, `stop_hook_active: false`) outputs `{"decision": "block", "reason": "SPIKE_STOP_BLOCK_CONTINUE_ONCE"}`, Codex keeps the turn open, appends `<hook_prompt hook_run_id="stop:...">SPIKE_STOP_BLOCK_CONTINUE_ONCE</hook_prompt>` as a user-role continuation prompt, runs Turn 2 automatically, and invokes `Stop` a second time with `"stop_hook_active": true`. Returning empty stdout + `exit 0` on `"stop_hook_active": true` cleanly completes the turn.

---

## 3. Verbatim Raw JSON Evidence (`codex exec` & `codex app-server`)

### 3.1 `hooks/list` Discovery Payload (`app-server`)

```json
{
  "key": "spike-plugin@spike-mp:hooks/hooks.json:stop:0:0",
  "eventName": "stop",
  "handlerType": "command",
  "command": "& \"<REPO_ROOT>\\.venv\\Scripts\\python.exe\" \"<TEMP>\\m9-appsrv-turn-3n_yq88l\\codex_home\\plugins\\cache\\spike-mp\\spike-plugin\\0.6.0\\run_spike_hook.py\"",
  "async": false,
  "matcher": null,
  "timeoutSec": 5,
  "statusMessage": null,
  "additionalContextLimit": null,
  "sourcePath": "<TEMP>\\m9-appsrv-turn-3n_yq88l\\codex_home\\plugins\\cache\\spike-mp\\spike-plugin\\0.6.0\\hooks\\hooks.json",
  "source": "plugin",
  "pluginId": "spike-plugin@spike-mp",
  "environmentId": null,
  "displayOrder": 3,
  "enabled": true,
  "isManaged": false,
  "currentHash": "sha256:9a69ebef98827289470c59873155a3b4c66603a7e0b76303f82ebc541a0fee19/sha256:e8341398d56406633de77d154d8888af50ab457eee7183d674adaa51065c7f3e",
  "trustStatus": "trusted"
}
```

### 3.2 Verbatim `stdin` & `stdout` Traces Across All 4 Lifecycle Events (`app-server` Session)

1. **`SessionStart`**
```json
{
  "stdin": {
    "session_id": "01a1ea89-ca0f-7cb0-8a5a-6dccfc8a33f8",
    "transcript_path": "<TEMP>\\m9-appsrv-turn-3n_yq88l\\codex_home\\sessions\\2026\\11\\18\\rollout-2026-11-18T01-24-21-01a1ea89-ca0f-7cb0-8a5a-6dccfc8a33f8.jsonl",
    "cwd": "<TEMP>\\m9-appsrv-turn-3n_yq88l\\project",
    "hook_event_name": "SessionStart",
    "model": "gpt-5",
    "permission_mode": "default",
    "source": "startup"
  },
  "stdout": {
    "hookSpecificOutput": {
      "hookEventName": "SessionStart",
      "additionalContext": "SPIKE_SESSION_START_CONTEXT_INJECTED"
    }
  }
}
```

2. **`UserPromptSubmit`**
```json
{
  "stdin": {
    "session_id": "01a1ea89-ca0f-7cb0-8a5a-6dccfc8a33f8",
    "turn_id": "01a1ea89-ca23-76f0-82a0-5dc683411298",
    "transcript_path": "<TEMP>\\m9-appsrv-turn-3n_yq88l\\codex_home\\sessions\\2026\\11\\18\\rollout-2026-11-18T01-24-21-01a1ea89-ca0f-7cb0-8a5a-6dccfc8a33f8.jsonl",
    "cwd": "<TEMP>\\m9-appsrv-turn-3n_yq88l\\project",
    "hook_event_name": "UserPromptSubmit",
    "model": "gpt-5",
    "permission_mode": "default",
    "prompt": "Run $boost in desktop"
  },
  "stdout": {
    "hookSpecificOutput": {
      "hookEventName": "UserPromptSubmit",
      "additionalContext": "SPIKE_USER_PROMPT_CONTEXT_INJECTED"
    }
  }
}
```

3. **`Stop` (First Pass — `stop_hook_active: false` -> Single-Shot Block)**
```json
{
  "stdin": {
    "session_id": "01a1ea89-ca0f-7cb0-8a5a-6dccfc8a33f8",
    "turn_id": "01a1ea89-ca23-76f0-82a0-5dc683411298",
    "transcript_path": "<TEMP>\\m9-appsrv-turn-3n_yq88l\\codex_home\\sessions\\2026\\11\\18\\rollout-2026-11-18T01-24-21-01a1ea89-ca0f-7cb0-8a5a-6dccfc8a33f8.jsonl",
    "cwd": "<TEMP>\\m9-appsrv-turn-3n_yq88l\\project",
    "hook_event_name": "Stop",
    "model": "gpt-5",
    "permission_mode": "default",
    "stop_hook_active": false,
    "last_assistant_message": "Assistant turn 1 reply."
  },
  "stdout": {
    "decision": "block",
    "reason": "SPIKE_STOP_BLOCK_CONTINUE_ONCE"
  }
}
```

4. **`Stop` (Second Pass — `stop_hook_active: true` -> Immediate Pass-Through)**
```json
{
  "stdin": {
    "session_id": "01a1ea89-ca0f-7cb0-8a5a-6dccfc8a33f8",
    "turn_id": "01a1ea89-ca23-76f0-82a0-5dc683411298",
    "transcript_path": "<TEMP>\\m9-appsrv-turn-3n_yq88l\\codex_home\\sessions\\2026\\11\\18\\rollout-2026-11-18T01-24-21-01a1ea89-ca0f-7cb0-8a5a-6dccfc8a33f8.jsonl",
    "cwd": "<TEMP>\\m9-appsrv-turn-3n_yq88l\\project",
    "hook_event_name": "Stop",
    "model": "gpt-5",
    "permission_mode": "default",
    "stop_hook_active": true,
    "last_assistant_message": "Assistant turn 2 reply."
  },
  "stdout": null
}
```

5. **`PostCompact`**
```json
{
  "stdin": {
    "session_id": "01a1ea89-ca0f-7cb0-8a5a-6dccfc8a33f8",
    "turn_id": "01a1ea89-d2a9-7492-88b1-2797b3a35267",
    "transcript_path": "<TEMP>\\m9-appsrv-turn-3n_yq88l\\codex_home\\sessions\\2026\\11\\18\\rollout-2026-11-18T01-24-21-01a1ea89-ca0f-7cb0-8a5a-6dccfc8a33f8.jsonl",
    "cwd": "<TEMP>\\m9-appsrv-turn-3n_yq88l\\project",
    "hook_event_name": "PostCompact",
    "model": "gpt-5",
    "trigger": "manual"
  },
  "stdout": {
    "systemMessage": "SPIKE_POST_COMPACT_SYSMSG"
  }
}
```
