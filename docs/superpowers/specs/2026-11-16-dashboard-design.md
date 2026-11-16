# Task-first dashboard

Approved by the user in this chat on 2026-11-16 (Asia/Singapore).

## Intent and scope
Make the local orchestration console understandable without knowing internal agent handles. Keep the existing vanilla JS/CSS and Python service; add no dependencies. This is an Operate surface, not a marketing redesign.

## Approaches
The user chose task overview first over retaining the message stream with cosmetic fixes. Preserve all existing capabilities through secondary views. Keep neutral light/dark styling, but reduce competing controls and make reading hierarchy explicit.

## Approved design
- Visible Project / Task labels; select active work on entry without a requested URL context.
- Default overview: actual task state, member responsibilities/current work, and short recent updates. No invented completion percentage or inferred agent liveness.
- Full message content expands on demand. Show plain-language role meaning, with raw handles secondary.
- Logs, collaboration graph, workspace inspection, and human messages remain accessible without permanently filling the main view.
- Context changes clear old data synchronously. Late responses and old SSE connections must not overwrite the selected context; errors remain visible.
- Summary snapshots must not scan workspaces or carry internal duplicate manifests. Detailed workspace checks remain on demand, with unknown status explicitly unknown.

## Non-goals and constraints
No F03 business changes, run/worker state rewriting, service restart, Codex config changes, dependencies, or release publishing. Preserve workspace merge/diff manifests internally. Source and installed/runtime delivery are separate verification boundaries.
