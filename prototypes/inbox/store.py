"""Durable inbox for trusted local prototype participants, separate from plugin schema."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from orchestrator.board import BoardService
from orchestrator.db import Database, NotFoundError


KINDS = frozenset({"task", "rework", "review", "result", "notice"})
ACK_STATUSES = frozenset({"accepted", "completed", "failed"})


class InboxConflictError(ValueError):
    """A durable key, recipient or receipt conflicts with existing inbox state."""


def _validate_text(**fields: str) -> None:
    for name, value in fields.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")


def _message(row: Any) -> dict[str, Any]:
    message = dict(row)
    receipts = json.loads(message.pop("receipts_json"))
    message["receipts"] = receipts
    message["evidence"] = receipts[-1]["evidence"] if receipts else None
    message["requires_reconciliation"] = message["status"] == "accepted"
    return message


class InboxStore:
    def __init__(self, db: Database, board: BoardService) -> None:
        if board.db is not db:
            raise ValueError("InboxStore and BoardService must use the same Database instance")
        self.db = db
        self.board = board

    def initialize(self) -> None:
        with self.db.transaction() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS inbox_messages (
                    id TEXT PRIMARY KEY,
                    board_seq INTEGER NOT NULL UNIQUE REFERENCES board_messages(seq),
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    sender TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    key TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK (kind IN ('task','rework','review','result','notice')),
                    summary TEXT NOT NULL,
                    body_md TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','accepted','completed','failed')),
                    receipts_json TEXT NOT NULL DEFAULT '[]',
                    UNIQUE (run_id, sender, key),
                    FOREIGN KEY (run_id, recipient) REFERENCES workers(run_id, name)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_inbox_pending
                ON inbox_messages(run_id, recipient, board_seq)
                WHERE status IN ('pending', 'accepted')
            """)

    def send(
        self, run_id: str, sender: str, recipient: str, key: str,
        kind: str, summary: str, body_md: str,
    ) -> dict[str, Any]:
        content = dict(run_id=run_id, sender=sender, recipient=recipient,
                       key=key, kind=kind, summary=summary, body_md=body_md)
        _validate_text(**content)
        if kind not in KINDS:
            raise ValueError(f"Invalid kind {kind!r}; expected one of {sorted(KINDS)}")
        with self.db.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM inbox_messages WHERE run_id=? AND sender=? AND key=?",
                (run_id, sender, key),
            ).fetchone()
            if existing is not None:
                if any(existing[name] != value for name, value in content.items()):
                    raise InboxConflictError("Inbox key already exists with different content")
                return {"message": _message(existing), "duplicate": True}

            run = self.db.get_run(run_id)
            if run is None:
                raise NotFoundError(f"Run not found: {run_id}")
            if run["status"] != "active":
                raise InboxConflictError(f"Run is not active: {run_id}")
            self._require_recipient(run_id, recipient)
            # BoardService's nested savepoint commits only with this outer transaction.
            pointer = self.board.board_post(
                run_id=run_id, sender=sender, summary=summary, body_md=body_md,
                channel="inbox", mentions=[recipient], msg_type="handoff",
            )
            message_id = "msg_" + uuid.uuid4().hex
            conn.execute("""
                INSERT INTO inbox_messages
                    (id, board_seq, run_id, sender, recipient, key, kind, summary, body_md)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (message_id, pointer["seq"], run_id, sender, recipient,
                  key, kind, summary, body_md))
            row = conn.execute("SELECT * FROM inbox_messages WHERE id=?", (message_id,)).fetchone()
            return {"message": _message(row), "duplicate": False}

    def get(self, message_id: str) -> dict[str, Any]:
        _validate_text(message_id=message_id)
        with self.db.read_connection() as conn:
            row = conn.execute("SELECT * FROM inbox_messages WHERE id=?", (message_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"Inbox message not found: {message_id}")
            return _message(row)

    def ack(self, message_id: str, recipient: str, status: str, evidence: str) -> dict[str, Any]:
        _validate_text(message_id=message_id, recipient=recipient, status=status, evidence=evidence)
        if status not in ACK_STATUSES:
            raise ValueError(f"Invalid status {status!r}; expected one of {sorted(ACK_STATUSES)}")
        with self.db.transaction() as conn:
            message = self.get(message_id)
            if message["recipient"] != recipient:
                raise InboxConflictError("Only the message recipient may acknowledge it")
            for receipt in message["receipts"]:
                if receipt["status"] == status:
                    if receipt["evidence"] != evidence:
                        raise InboxConflictError("Receipt already exists with different evidence")
                    # A historical accepted retry must never revert a terminal message.
                    return message
            current = message["status"]
            if not ((current == "pending" and status == "accepted") or
                    (current == "accepted" and status in {"completed", "failed"})):
                raise InboxConflictError(f"Invalid inbox transition: {current} -> {status}")
            message["receipts"].append({
                "id": "rcpt_" + uuid.uuid4().hex,
                "message_id": message_id,
                "recipient": recipient,
                "status": status,
                "evidence": evidence,
                "created_at": time.time(),
            })
            # At most two receipts; one row keeps status and history an atomic read snapshot.
            conn.execute(
                "UPDATE inbox_messages SET status=?, receipts_json=? WHERE id=?",
                (status, json.dumps(message["receipts"], ensure_ascii=False), message_id),
            )
            return self.get(message_id)

    def pending(self, run_id: str, recipient: str) -> list[dict[str, Any]]:
        _validate_text(run_id=run_id, recipient=recipient)
        if self.db.get_run(run_id) is None:
            raise NotFoundError(f"Run not found: {run_id}")
        self._require_recipient(run_id, recipient)
        with self.db.read_connection() as conn:
            rows = conn.execute("""
                SELECT * FROM inbox_messages
                WHERE run_id=? AND recipient=? AND status IN ('pending','accepted')
                ORDER BY board_seq
            """, (run_id, recipient)).fetchall()
            return [_message(row) for row in rows]

    def _require_recipient(self, run_id: str, recipient: str) -> None:
        if self.db.get_worker_by_name(run_id, recipient) is None:
            raise NotFoundError(f"Recipient not registered in run {run_id}: {recipient}")
