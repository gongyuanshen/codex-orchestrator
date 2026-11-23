# Advanced-tier tools (read on demand)

To keep the fixed per-session schema cost low, MCP `tools/list` advertises only the core tools by default. These primitives stay fully supported but are reached through the `tool_advanced` proxy:

| Tool | Typical use |
| :--- | :--- |
| `native_action_update` | Manual claim/begin/record, grouped transfers (`claim_group`…), `action="reconcile"` |
| `inbox_send` | Manual step-bound delivery (`task/rework/review/result`) outside `workflow_prepare_dispatch` |
| `artifact_capture`, `verification_record` | Collector primitives behind `workflow_verifier_turn` |
| `workspace_create`, `workspace_cleanup` | Primitives behind `workflow_prepare_dispatch(create_workspace=True)` and `workflow_closeout_run` |
| `workspace_discard` | Remove a never-merged workspace of a failed/archived/completed run that is identical to its baseline (`dry_run=True` only checks); refuses any change, unresolved native action or still-working author |
| `worker_status_update` | Blocker or non-step status notices |
| `board_channel_list`, `session_bind` | Channel inspection; manual hook binding |

- **Describe**: `tool_advanced(tool="<name>")` returns its `inputSchema`.
- **Call**: `tool_advanced(tool="<name>", arguments={...})`. Validation, gates and errors are identical to a direct call; check `isError` and never retry a write.
- Operators who want every schema listed can start the MCP server with `TRI_ORCH_TOOL_TIER=full`.
- `workflow_prepare_dispatch` accepts its low-frequency fields either top-level or inside `options={...}` (giving the same field in both places is rejected).
