"""Message board (#seq, channels, @mentions, read cursors) and worker state machine."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from orchestrator.config import Settings
from orchestrator.db import (
    VALID_MSG_TYPES,
    VALID_WORKER_ROLES,
    VALID_WORKER_STATUSES,
    Database,
    NotFoundError,
)

ALL_CHANNELS_CURSOR = "__all__"
_MENTION_RE = re.compile(r"(?:^|(?<!\w))@([A-Za-z0-9_][A-Za-z0-9_.-]*)")


def extract_mentions(
    *texts: str,
    explicit_mentions: list[str] | None = None,
) -> list[str]:
    """Extract deduplicated @mentions from text blocks merged with explicit mentions."""
    seen: set[str] = set()
    ordered: list[str] = []

    def _add(raw: str) -> None:
        cleaned = raw.lstrip("@").strip().rstrip(".")
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            ordered.append(cleaned)

    if explicit_mentions:
        for item in explicit_mentions:
            if isinstance(item, str):
                _add(item)

    for text in texts:
        if not text:
            continue
        for match in _MENTION_RE.finditer(text):
            _add(match.group(1))

    return ordered


def _derive_summary(summary: str, body_md: str, max_len: int = 160) -> str:
    cleaned = summary.strip()
    if cleaned:
        return cleaned
    for line in body_md.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            if len(stripped) <= max_len:
                return stripped
            return stripped[: max_len - 3].rstrip() + "..."
    return ""


class BoardService:
    """High-level message board and worker lifecycle service backed by SQLite WAL."""

    def __init__(self, db: Database, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or db.settings

    # ------------------------------------------------------------------
    # Message Board
    # ------------------------------------------------------------------

    def board_post(
        self,
        run_id: str,
        sender: str,
        summary: str = "",
        body_md: str = "",
        channel: str = "main",
        sender_role: str = "",
        mentions: list[str] | None = None,
        msg_type: str = "info",
    ) -> dict[str, Any]:
        """Persist a board message and return a compact pointer without full body_md."""
        if msg_type not in VALID_MSG_TYPES:
            raise ValueError(
                f"Invalid msg_type {msg_type!r}; expected one of {sorted(VALID_MSG_TYPES)}"
            )
        sender_clean = sender.strip()
        if not sender_clean:
            raise ValueError("sender must be a non-empty string")

        resolved_channel = channel.strip() or "main"
        resolved_summary = _derive_summary(summary, body_md)
        resolved_mentions = extract_mentions(
            resolved_summary, body_md, explicit_mentions=mentions
        )

        msg = self.db.insert_board_message(
            run_id,
            sender=sender_clean,
            sender_role=sender_role.strip(),
            channel=resolved_channel,
            mentions=resolved_mentions,
            msg_type=msg_type,
            summary=resolved_summary,
            body_md=body_md,
        )
        return {
            "seq": msg["seq"],
            "run_id": msg["run_id"],
            "channel": msg["channel"],
            "mentions": msg["mentions"],
            "summary": msg["summary"],
        }

    def get_message(self, seq: int) -> dict[str, Any] | None:
        """Fetch a complete board message record by seq."""
        return self.db.get_board_message(seq)

    def board_read(
        self,
        run_id: str,
        *,
        since_seq: int | None = None,
        channel: str | None = None,
        mention: str | None = None,
        reader_name: str | None = None,
        advance_cursor: bool = False,
        limit: int | None = 100,
    ) -> dict[str, Any]:
        """Incrementally read board messages and optionally advance the reader's cursor."""
        if self.db.get_run(run_id) is None:
            raise NotFoundError(f"Run not found: {run_id}")

        norm_channel = channel.strip() if channel else None
        is_all_channels = norm_channel in (None, "", "all")
        query_channel = None if is_all_channels else norm_channel
        cursor_channel = ALL_CHANNELS_CURSOR if is_all_channels else str(norm_channel)
        reader = reader_name.strip() if reader_name else None

        if since_seq is not None:
            effective_since = max(0, int(since_seq))
        elif reader:
            channel_cur = self.db.get_read_cursor(run_id, reader, cursor_channel)
            all_cur = (
                channel_cur
                if is_all_channels
                else self.db.get_read_cursor(run_id, reader, ALL_CHANNELS_CURSOR)
            )
            effective_since = max(channel_cur, all_cur)
        else:
            effective_since = 0

        messages = self.db.query_board_messages(
            run_id,
            since_seq=effective_since,
            channel=query_channel,
            mention=mention,
            limit=limit,
        )

        cursor_seq = effective_since
        if reader:
            if advance_cursor and messages:
                cursor_seq = self.db.set_read_cursor(
                    run_id, reader, cursor_channel, messages[-1]["seq"]
                )
            else:
                cursor_seq = self.db.get_read_cursor(run_id, reader, cursor_channel)

        return {
            "run_id": run_id,
            "channel": "all" if is_all_channels else str(norm_channel),
            "since_seq": effective_since,
            "cursor_seq": cursor_seq,
            "last_seq": messages[-1]["seq"] if messages else effective_since,
            "count": len(messages),
            "messages": messages,
        }

    def board_channel_list(self, run_id: str) -> dict[str, Any]:
        """List channels and message counts for run_id (always including 'main')."""
        if self.db.get_run(run_id) is None:
            raise NotFoundError(f"Run not found: {run_id}")

        channels = self.db.list_channels(run_id)
        if not any(ch["channel"] == "main" for ch in channels):
            channels.insert(
                0,
                {
                    "channel": "main",
                    "message_count": 0,
                    "latest_seq": 0,
                    "last_message_at": None,
                },
            )
        return {"run_id": run_id, "channels": channels}

    def board_status(
        self,
        run_id: str,
        *,
        reader_name: str | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Return run summary: workers, stall flags, channels, and unread counts."""
        run = self.db.get_run(run_id)
        if run is None:
            raise NotFoundError(f"Run not found: {run_id}")

        ts = float(now) if now is not None else time.time()
        workers = self.db.list_workers(run_id=run_id)
        # File manifests belong to merge/diff operations, not status transport.
        workspaces = [
            {key: value for key, value in ws.items()
             if key not in {"snapshot_manifest", "snapshot_manifest_json"}}
            for ws in self.db.list_workspaces(run_id=run_id)
        ]
        channels_info = self.board_channel_list(run_id)["channels"]
        cursors = self.db.list_read_cursors(run_id)
        latest_task_packet = self.db.get_task_packet(run_id)

        with self.db.read_connection() as conn:
            msg_rows = conn.execute(
                "SELECT seq, channel, mentions_json FROM board_messages WHERE run_id = ? ORDER BY seq ASC;",
                (run_id,),
            ).fetchall()

        parsed_msgs: list[tuple[int, str, list[str]]] = [
            (int(r["seq"]), str(r["channel"]), json.loads(r["mentions_json"] or "[]"))
            for r in msg_rows
        ]
        latest_seq = parsed_msgs[-1][0] if parsed_msgs else 0
        channel_names = [ch["channel"] for ch in channels_info]

        def _compute_reader_unread(target_reader: str) -> dict[str, Any]:
            all_cur = cursors.get((target_reader, ALL_CHANNELS_CURSOR), 0)
            by_channel: dict[str, int] = {name: 0 for name in channel_names}
            eff_by_channel: dict[str, int] = {
                name: max(all_cur, cursors.get((target_reader, name), 0))
                for name in channel_names
            }
            mentions_unread = 0
            for seq, ch_name, mentions in parsed_msgs:
                eff_cur = eff_by_channel.get(
                    ch_name, max(all_cur, cursors.get((target_reader, ch_name), 0))
                )
                if seq > eff_cur:
                    by_channel[ch_name] = by_channel.get(ch_name, 0) + 1
                    if target_reader in mentions:
                        mentions_unread += 1
            return {
                "total": sum(by_channel.values()),
                "by_channel": by_channel,
                "mentions": mentions_unread,
            }

        ws_by_id = {ws["id"]: ws for ws in workspaces}
        ws_by_worker: dict[str, dict[str, Any]] = {}
        for ws in workspaces:
            if ws["status"] != "cleaned":
                ws_by_worker[ws["worker_name"]] = ws

        unread_by_worker: dict[str, dict[str, Any]] = {}
        enriched_workers: list[dict[str, Any]] = []
        stalled_workers: list[str] = []

        for w in workers:
            hb_age = round(max(0.0, ts - float(w["last_heartbeat_at"])), 3)
            is_stalled = run["status"] == "active" and (
                w["status"] == "working"
                and (ts - float(w["last_heartbeat_at"])) > float(w["stall_threshold_sec"])
            )
            if is_stalled:
                stalled_workers.append(w["name"])

            worker_unread = _compute_reader_unread(w["name"])
            unread_by_worker[w["name"]] = worker_unread

            ws = (
                ws_by_id.get(w["workspace_id"])
                if w["workspace_id"]
                else None
            ) or ws_by_worker.get(w["name"])

            enriched_workers.append(
                {
                    **w,
                    "heartbeat_age_sec": hb_age,
                    "is_stalled": is_stalled,
                    "unread_count": worker_unread["total"],
                    "unread_mentions": worker_unread["mentions"],
                    "unread_by_channel": worker_unread["by_channel"],
                    "assigned_port": ws["assigned_port"] if ws else None,
                    "workspace_status": ws["status"] if ws else None,
                    "workspace_path": ws["path"] if ws else None,
                }
            )

        effective_reader = (reader_name.strip() if reader_name else "") or "coordinator"
        reader_unread = _compute_reader_unread(effective_reader)
        enriched_channels = [
            {
                **ch,
                "unread_count": reader_unread["by_channel"].get(ch["channel"], 0),
            }
            for ch in channels_info
        ]

        from orchestrator.workflow import WorkflowService
        with self.db.read_connection() as conn:
            inbox_rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM inbox_messages WHERE run_id=? GROUP BY status",
                (run_id,),
            ).fetchall()
            blocking = conn.execute(
                "SELECT COUNT(*) FROM inbox_messages WHERE run_id=? "
                "AND (status='accepted' OR (status='pending' AND kind!='notice'))",
                (run_id,),
            ).fetchone()[0]
        counts = dict.fromkeys(('pending', 'accepted', 'completed', 'failed', 'cancelled'), 0)
        counts.update({row['status']: row['n'] for row in inbox_rows})

        from orchestrator.native_actions import NativeActionStore
        from orchestrator.watchdog import classify_run
        native = NativeActionStore(self.db).project(run_id)
        return {
            **native,
            "native_actions": native,
            "run": run,
            "run_id": run_id,
            "workflow": WorkflowService(self.db).summary(run_id),
            "plan_readiness": WorkflowService(self.db).plan_readiness(run_id),
            "resume_context": WorkflowService(self.db).resume_context(run_id),
            "progress_status": classify_run(self.db, run_id, now=ts),
            "inbox": {"counts": counts, "blocking_count": blocking},
            "latest_seq": latest_seq,
            "reader_name": effective_reader,
            "unread_total": reader_unread["total"],
            "unread_by_channel": reader_unread["by_channel"],
            "unread_by_worker": unread_by_worker,
            "channels": enriched_channels,
            "control_requests": self.db.list_worker_controls(run_id),
            "workers": enriched_workers,
            "stalled_workers": stalled_workers,
            "workspaces": workspaces,
            "latest_task_packet": latest_task_packet,
        }

    # ------------------------------------------------------------------
    # Worker State Machine
    # ------------------------------------------------------------------

    def worker_register(
        self,
        run_id: str,
        name: str,
        role: str,
        *,
        agent_id: str | None = None,
        status: str = "waiting",
        workspace_id: str | None = None,
        current_task: str = "",
        stall_threshold_sec: int | None = None,
        heartbeat_at: float | None = None,
    ) -> dict[str, Any]:
        """Register or update a worker node for run_id."""
        if role not in VALID_WORKER_ROLES:
            raise ValueError(
                f"Invalid worker role {role!r}; expected one of {sorted(VALID_WORKER_ROLES)}"
            )
        if status not in VALID_WORKER_STATUSES:
            raise ValueError(
                f"Invalid worker status {status!r}; expected one of {sorted(VALID_WORKER_STATUSES)}"
            )
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("worker name must be non-empty")

        return self.db.upsert_worker(
            run_id,
            clean_name,
            role=role,
            agent_id=agent_id,
            status=status,
            workspace_id=workspace_id,
            current_task=current_task,
            stall_threshold_sec=stall_threshold_sec,
            heartbeat_at=heartbeat_at,
        )

    def worker_heartbeat(
        self,
        run_id: str | None = None,
        worker_name: str | None = None,
        *,
        worker_id: str | None = None,
        current_task: str | None = None,
        status: str | None = None,
        heartbeat_at: float | None = None,
    ) -> dict[str, Any]:
        """Record a worker heartbeat, refreshing last_heartbeat_at and clearing stalled_alerted."""
        return self.db.update_worker_heartbeat(
            worker_id=worker_id,
            run_id=run_id,
            worker_name=worker_name,
            current_task=current_task,
            status=status,
            heartbeat_at=heartbeat_at,
        )

    def worker_status_update(
        self,
        run_id: str | None = None,
        worker_name: str | None = None,
        status: str | None = None,
        *,
        worker_id: str | None = None,
        current_task: str | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        """Transition a worker among waiting | working | stopped | killed (killed is terminal)."""
        if status is None:
            raise ValueError("status is required")
        return self.db.update_worker_status(
            status=status,
            worker_id=worker_id,
            run_id=run_id,
            worker_name=worker_name,
            current_task=current_task,
            workspace_id=workspace_id,
        )
