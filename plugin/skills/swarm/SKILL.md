---
name: swarm
description: "Concurrent multi-worker swarm coordination ($swarm) using tri-orchestrator SQLite #seq message board, domain channels (main/#backend/#ui/#verify), isolated .swarm-workspaces clones, anti-context-explosion short-pointer replies, incremental board_read(since_seq), and heartbeat stall recovery. Use for large multi-stream interactive parallel work with peer handoffs and live dashboard observability."
metadata:
  short-description: "Concurrent #seq board swarm with isolation & stall recovery"
---

# `$swarm` — `#seq` board swarm with isolated workers

First load [the shared Route A contract](../route-a-workflow.md) (hard rules, subagent rules, `workflow5` normal path, polling, workspaces). This file adds only what is Swarm-specific. Dashboard: `http://127.0.0.1:18790`.

Every `task/rework/review/result` is bound with `step_id=step["id"]` to the step's owner/verifier and attempt (via `workflow_prepare_dispatch`). Make one `inbox_wait` per worker turn (default 20000ms, max 25000ms). When several messages wait for one recipient (`board_status.next_action_groups`), use one grouped wake ([transfers-and-recovery](../reference/transfers-and-recovery.md)); handle controls first, no notice-only wakeups.

### 1. Run, plan & channels

`run_list(status="active", mode="swarm")`, else `run_create(mode="swarm", ...)` per Route A. For non-trivial decomposition spawn `planner` (`fork_turns="none"`); freeze `task_packet_save` with mutually exclusive ownership; open channels with `board_post` (`main`, `#backend`, `#ui`, `#verify`).

### 2. Isolated workers

Register the logical `verifier` (or per-slice `verifier_1`, `verifier_2`, ...), then per slice: `workflow_prepare_dispatch(create_workspace=True, task_name="backend_1", owner="backend-1", verifier="verifier", dispatch_action="dispatch", ...)` → `spawn_agent` (`fork_turns="all"` when context is warm) → `native_action_record_call`. Pick each worker's reasoning tier per task ([reasoning-effort](../reference/reasoning-effort.md)).

### 3. Anti-context-explosion (CRITICAL)

Workers never paste diffs, logs or long prose into replies. They submit with `workflow_author_turn(phase="submit", summary=..., body_md=..., post_board_note=True)` (one board note; a standalone `board_post(channel="#backend", msg_type="diff"|"test"|"handoff", mentions=["verifier"])` only for peer handoffs/blockers) and reply with ONLY a 1-line pointer: `已完成：<任务>，详见 #14 (@verifier)` or `阻塞：<原因>，详见 #15 (@coordinator)`.

### 4. Incremental `#seq` loop & stall recovery

1. Between `wait_agent(timeout_ms=30000)` stretches: `board_status(run_id, reader_name="coordinator", compact=True)` and `board_read(run_id, since_seq=last_seq, reader_name="coordinator", advance_cursor=True)`, handling human posts and watchdog alerts ([human-control](../reference/human-control.md)).
2. **Stall & Silent-Exit Recovery**: inspect `workspace_status`, `workspace_diff` and `board_read`; get an actual native observation via `list_agents`/`wait_agent` (a heartbeat timeout is not evidence of death); wake an observed idle original worker on the SAME thread via `followup_task` (`send_message` if running), never replacing its identity or resetting its rework budget.

### 5. Cross-verification, sequential merge & close

1. On the first `#verify` handoff, spawn the verifier per Route A step 5 (`task_name="verifier_1"`, `fork_turns="none"`; it runs `workflow_verifier_turn` prepare → verify → record) and reuse it via `followup_task` for all later handoffs, rework and integrated gates.
2. On `VERDICT: FAIL`, route rework to the owning `swarm_worker` thread via `followup_task`.
3. Run `workspace_status`/`workspace_diff`/`workspace_merge_preview` across verified workspaces as parallel calls, then `workspace_merge(workspace_id, strategy="merge")` sequentially.
4. Integrated gate (`scope="integrated"`), then `workflow_closeout_run(run_id)`; report leftovers.
