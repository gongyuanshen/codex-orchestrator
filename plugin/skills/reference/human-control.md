# Human messages, controls & native state (read on demand)

Read this when `board_status` shows pending `control_requests` or unread human posts, or before closing a run that had operator input.

## Native agent binding & state

- In `workflow5`, `workflow_prepare_dispatch(task_name=...)` registers `status="waiting"` (`pending_agent_path="/root/<task_name>"`) and `native_action_record_call` atomically binds `agent_id` and sets `status="working"` after `spawn_agent`. On manual/stored paths, call `worker_register(run_id=run_id, name="<worker>", role="<role>", status="waiting")` pre-spawn, then re-call with `agent_id` and report `status="working"` only after dispatch. Never invent IDs or overwrite bindings.
- Inspect native `list_agents` / `wait_agent` for real host state (`working/waiting/stopped/killed`); never revive killed workers or auto-resume paused ones without explicit user instruction.

## Controls (Web UI pause/stop)

Before dispatching/reworking, before and after `wait_agent(timeout_ms=30000)`, and before closure, inspect `board_status(run_id=run_id, compact=True)` and handle every pending `control_requests` entry within `<=30s`: interrupt the snapshotted `agent_id`, confirm idle/stopped state, and call `worker_control_ack(request_id=..., outcome="applied"|"failed", result=...)`. Uncertain interruptions stay `needs_reconciliation`; do not bypass with `worker_status_update` or raw SQL.

## Human message consumption

At control checkpoints — before dispatching/reworking, before and after waits, and before run closure — read `batch = board_read(run_id=run_id, reader_name="coordinator", advance_cursor=False)` without channel or mention filters (durable cursor). Handle every `channel="human"` or `msg_type="human"` post before further dispatch or closure, preserving verbatim text and `#seq` (the board does not automatically deliver text: Solo handles locally; Delegation routes via `send_message` when running or `followup_task` when idle). Record received `#seq`, planned action, and separate completion evidence via `board_post` (advancing the cursor is not completion; not exactly-once delivery). Only after recording the batch dispositions, call `board_read(run_id=run_id, reader_name="coordinator", since_seq=batch["since_seq"], limit=batch["count"], advance_cursor=True)`. Update the task packet first if guidance changes scope.
