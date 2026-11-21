"""Durable local-agent inbox and bounded asynchronous receipt waits."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from starlette.requests import Request
from orchestrator.board import BoardService
from orchestrator.db import Database, NotFoundError

KINDS = frozenset({"task", "rework", "review", "result", "notice"})
ACK_STATUSES = frozenset({"accepted", "completed", "failed", "cancelled"})
_MESSAGE_FIELDS = ("id", "board_seq", "run_id", "sender", "recipient", "key", "kind",
                   "summary", "body_md", "status", "receipts_json", "recipient_agent_id", "step_id",
                   "step_packet_version", "step_attempt", "step_contract_revision")
_STEP_FIELDS = ("run_id", "owner", "verifier", "owner_agent_id", "verifier_agent_id",
                "packet_version", "attempt", "contract_revision", "state")
_STEP_SELECT = ",".join(f"s.{name} AS ctx_{name}" for name in _STEP_FIELDS) + """,
    r_step.workflow_version AS ctx_workflow_version,
    o.agent_id AS ctx_current_owner_agent_id,v.agent_id AS ctx_current_verifier_agent_id,
    o.pending_agent_path AS ctx_current_owner_pending_path,v.pending_agent_path AS ctx_current_verifier_pending_path,
    (SELECT MAX(p.version) FROM task_packets p WHERE p.run_id=s.run_id) AS ctx_current_packet_version"""
_STEP_JOINS = """LEFT JOIN runs r_step ON r_step.id=s.run_id
    LEFT JOIN workers o ON o.run_id=s.run_id AND o.name=s.owner
    LEFT JOIN workers v ON v.run_id=s.run_id AND v.name=s.verifier"""

class InboxConflictError(ValueError):
    """A durable message, binding or receipt conflicts with persisted state."""


def _validate_text(**fields: str) -> None:
    for name, value in fields.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")


def _message(row: Any) -> dict[str, Any]:
    message = {name: row[name] for name in _MESSAGE_FIELDS}
    receipts = json.loads(message.pop("receipts_json"))
    message["receipts"] = receipts
    message["evidence"] = receipts[-1]["evidence"] if receipts else None
    message["requires_reconciliation"] = message["status"] == "accepted"
    return message


class InboxStore:
    def __init__(self, db: Database, board: BoardService) -> None:
        if board.db is not db:
            raise ValueError("InboxStore and BoardService must use the same Database instance")
        self.db, self.board = db, board

    def initialize(self) -> None:
        """Validate the central migration, never create tables from a business call."""
        with self.db.read_connection() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(inbox_messages)")}
        if not set(_MESSAGE_FIELDS) <= columns:
            raise RuntimeError("Inbox schema is missing; complete the database migration")

    def send(self, run_id: str, sender: str, recipient: str, key: str,
             kind: str, summary: str, body_md: str, step_id: str | None = None) -> dict[str, Any]:
        content = dict(run_id=run_id, sender=sender, recipient=recipient, key=key,
                       kind=kind, summary=summary, body_md=body_md)
        _validate_text(**content)
        if kind not in KINDS:
            raise ValueError(f"Invalid kind {kind!r}; expected one of {sorted(KINDS)}")
        if step_id is not None:
            _validate_text(step_id=step_id)
        content["step_id"] = step_id
        with self.db.transaction() as conn:
            existing = conn.execute("SELECT * FROM inbox_messages WHERE run_id=? AND sender=? AND key=?",
                                    (run_id, sender, key)).fetchone()
            if existing is not None:
                if any(existing[name] != value for name, value in content.items()):
                    raise InboxConflictError("Inbox key already exists with different content")
                return {"message": _message(existing), "duplicate": True}
            run = self._require_active_run(run_id)
            worker = self._require_recipient(run_id, recipient)
            pre_spawn = False
            if worker["agent_id"]:
                recipient_agent_id: str | None = worker["agent_id"]
            elif not run["workflow_version"]:
                recipient_agent_id = None
            elif (
                run["workflow_version"] == 5
                and worker.get("pending_agent_path")
                and kind in {"task", "review"}
                and step_id is not None
            ):
                recipient_agent_id = ""
                pre_spawn = True
            else:
                raise InboxConflictError("Recipient requires a native agent binding before delivery")
            context = {field: None for field in
                       ("step_packet_version", "step_attempt", "step_contract_revision")}
            step = self._step_context(conn, step_id) if step_id is not None else None
            if step is not None and step["ctx_run_id"] != run_id:
                raise InboxConflictError("Inbox step must belong to the same run")
            if step is not None:
                context = {'step_' + name: step['ctx_' + name] for name in
                           ('packet_version', 'attempt', 'contract_revision')}
            if pre_spawn or self._linked_execution(run, content):
                if step is None:
                    raise InboxConflictError("Swarm execution requires a same-run workflow step_id")
                context = {"step_" + name: step["ctx_" + name] for name in
                           ("packet_version", "attempt", "contract_revision")}
                self._require_step_delivery(content | context, step, stage=True, pre_spawn=pre_spawn)
            # The nested board savepoint commits only with this outer transaction.
            pointer = self.board.board_post(run_id=run_id, sender=sender, summary=summary,
                body_md=body_md, channel="inbox", mentions=[recipient], msg_type="handoff")
            mid = "msg_" + uuid.uuid4().hex
            conn.execute("""INSERT INTO inbox_messages
                (id,board_seq,run_id,sender,recipient,key,kind,summary,body_md,recipient_agent_id,step_id,
                 step_packet_version,step_attempt,step_contract_revision)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (mid,pointer["seq"],run_id,sender,recipient,key,kind,summary,body_md,recipient_agent_id,step_id,
                 context["step_packet_version"],context["step_attempt"],context["step_contract_revision"]))
            return {"message": self.get(mid), "duplicate": False}

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
                    # Historical acknowledgements cannot execute work or revert terminal state.
                    return message
            run = self._require_active_run(message["run_id"])
            worker = self._require_recipient(message["run_id"], recipient)
            if status != "cancelled":
                if run["workflow_version"] and not worker["agent_id"]:
                    raise InboxConflictError("Recipient requires a native agent binding before delivery")
                self._require_binding(message, worker["agent_id"])
            if status in {"accepted", "completed"}:
                if self._linked_execution(run, message):
                    step = self._step_context(conn, message["step_id"])
                    self._require_step_delivery(message, step, stage=status == "accepted")
                if worker["status"] in {"stopped", "killed"}:
                    raise InboxConflictError("Recipient is paused; execution acknowledgement is blocked")
                if conn.execute("SELECT 1 FROM worker_controls WHERE worker_id=? AND status='pending'",
                                (worker["id"],)).fetchone():
                    raise InboxConflictError("Recipient has a pending control; execution acknowledgement is blocked")
            current = message["status"]
            if not ((current == "pending" and status in {"accepted", "cancelled"}) or
                    (current == "accepted" and status in {"completed", "failed", "cancelled"})):
                raise InboxConflictError(f"Invalid inbox transition: {current} -> {status}")
            message["receipts"].append(dict(id="rcpt_"+uuid.uuid4().hex, message_id=message_id,
                recipient=recipient,status=status,evidence=evidence,created_at=time.time()))
            conn.execute("UPDATE inbox_messages SET status=?,receipts_json=? WHERE id=?",
                         (status,json.dumps(message["receipts"],ensure_ascii=False),message_id))
            return self.get(message_id)

    @staticmethod
    def _linked_execution(run: Any, message: dict[str, Any]) -> bool:
        return run["workflow_version"] in {2, 3, 4, 5} and run["mode"] == "swarm" and message["kind"] != "notice"

    @staticmethod
    def _step_context(conn: Any, step_id: str | None) -> Any:
        row = conn.execute(f"SELECT {_STEP_SELECT} FROM workflow_steps s {_STEP_JOINS} WHERE s.id=?",
                           (step_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"Workflow step not found: {step_id}")
        return row

    @staticmethod
    def _require_step_delivery(message: dict[str, Any], step: Any, *, stage: bool, pre_spawn: bool = False) -> None:
        if step["ctx_run_id"] != message["run_id"]:
            raise InboxConflictError("Inbox step must belong to the same run")
        role = {"task": "owner", "rework": "owner", "review": "verifier"}.get(message["kind"])
        recipients = {step["ctx_" + role]} if role else {step["ctx_owner"], step["ctx_verifier"]}
        if message["recipient"] not in recipients:
            raise InboxConflictError("Inbox recipient does not match the workflow step role")
        if step["ctx_workflow_version"] == 5:
            owner_aid = step["ctx_owner_agent_id"]
            ver_aid = step["ctx_verifier_agent_id"]
            cur_owner_aid = step["ctx_current_owner_agent_id"]
            cur_ver_aid = step["ctx_current_verifier_agent_id"]
            if step["ctx_owner"] == step["ctx_verifier"] or (owner_aid and ver_aid and owner_aid == ver_aid):
                raise InboxConflictError("Workflow step owner/verifier native binding changed; reconcile or cancel delivery")
            if owner_aid:
                if owner_aid != cur_owner_aid:
                    raise InboxConflictError("Workflow step owner/verifier native binding changed; reconcile or cancel delivery")
            else:
                if not (
                    pre_spawn
                    and message["kind"] == "task"
                    and cur_owner_aid is None
                    and step["ctx_current_owner_pending_path"]
                ):
                    raise InboxConflictError("Workflow step owner/verifier native binding changed; reconcile or cancel delivery")
            if ver_aid:
                if ver_aid != cur_ver_aid:
                    raise InboxConflictError("Workflow step owner/verifier native binding changed; reconcile or cancel delivery")
            else:
                if message["kind"] in {"task", "rework", "result"}:
                    if cur_ver_aid is not None:
                        raise InboxConflictError("Workflow step owner/verifier native binding changed; reconcile or cancel delivery")
                elif message["kind"] == "review":
                    if not (
                        pre_spawn
                        and cur_ver_aid is None
                        and step["ctx_current_verifier_pending_path"]
                        and owner_aid
                        and owner_aid != step["ctx_current_verifier_pending_path"]
                    ):
                        raise InboxConflictError("Workflow step owner/verifier native binding changed; reconcile or cancel delivery")
                else:
                    raise InboxConflictError("Workflow step owner/verifier native binding changed; reconcile or cancel delivery")
        elif (not step["ctx_owner_agent_id"] or not step["ctx_verifier_agent_id"]
                or step["ctx_owner_agent_id"] == step["ctx_verifier_agent_id"]
                or any(step["ctx_" + name + "_agent_id"] != step["ctx_current_" + name + "_agent_id"]
                       for name in ("owner", "verifier"))):
            raise InboxConflictError("Workflow step owner/verifier native binding changed; reconcile or cancel delivery")
        if (step["ctx_packet_version"] != step["ctx_current_packet_version"]
                or any(message["step_" + name] != step["ctx_" + name]
                       for name in ("packet_version", "attempt", "contract_revision"))):
            raise InboxConflictError("Inbox delivery context is stale; reconcile or cancel the original message")
        if stage:
            state, kind = step["ctx_state"], message["kind"]
            if ((kind == "task" and state != "dispatched")
                    or (kind == "rework" and (state != "implementing" or step["ctx_attempt"] < 1))
                    or (kind == "review" and state not in {"candidate_review", "integrated_review"})):
                raise InboxConflictError(f"Inbox {kind} dispatch is incompatible with workflow stage {state}")

    @staticmethod
    def _require_binding(message: dict[str, Any], current_agent_id: str | None) -> None:
        if message["recipient_agent_id"] != current_agent_id:
            raise InboxConflictError("Recipient native agent binding changed; reconcile or cancel the original message")

    def _require_active_run(self, run_id: str) -> dict[str, Any]:
        run = self.db.get_run(run_id)
        if run is None:
            raise NotFoundError(f"Run not found: {run_id}")
        if run["status"] != "active":
            raise InboxConflictError(f"Run is not active: {run_id}")
        return run

    def _require_recipient(self, run_id: str, recipient: str) -> dict[str, Any]:
        worker = self.db.get_worker_by_name(run_id, recipient)
        if worker is None:
            raise NotFoundError(f"Recipient not registered in run {run_id}: {recipient}")
        return worker

    def _rows(self, run_id: str, recipient: str) -> list[Any]:
        _validate_text(run_id=run_id, recipient=recipient)
        # One SQLite statement observes binding, controls, state and messages together.
        with self.db.read_connection() as conn:
            rows = conn.execute(f"""SELECT r.status AS run_status,r.workflow_version AS workflow_version,r.mode AS mode,
                w.id AS worker_id,w.status AS worker_status,w.agent_id AS current_agent_id,{_STEP_SELECT},
                EXISTS(SELECT 1 FROM worker_controls c WHERE c.worker_id=w.id AND c.status='pending') AS controlled,
                m.* FROM runs r LEFT JOIN workers w ON w.run_id=r.id AND w.name=?
                LEFT JOIN inbox_messages m ON m.run_id=r.id AND m.recipient=w.name
                    AND m.status IN ('pending','accepted')
                LEFT JOIN workflow_steps s ON s.id=m.step_id
                {_STEP_JOINS}
                WHERE r.id=? ORDER BY m.board_seq""", (recipient,run_id)).fetchall()
        if not rows:
            raise NotFoundError(f"Run not found: {run_id}")
        if rows[0]["worker_id"] is None:
            raise NotFoundError(f"Recipient not registered in run {run_id}: {recipient}")
        return rows

    def pending(self, run_id: str, recipient: str) -> list[dict[str, Any]]:
        rows = self._rows(run_id,recipient)
        messages = [_message(row) for row in rows if row["id"] is not None]
        self._require_deliveries(rows)
        return messages

    def poll(self, run_id: str, recipient: str) -> dict[str, Any]:
        rows = self._rows(run_id,recipient)
        state = rows[0]
        if state["run_status"] != "active":
            return {"status":"run_closed", "messages":[]}
        if state["worker_status"] in {"stopped","killed"}:
            return {"status":"worker_paused", "messages":[]}
        if state["controlled"]:
            return {"status":"control_requested", "messages":[]}
        if state["workflow_version"] and not state["current_agent_id"]:
            raise InboxConflictError("Recipient requires a native agent binding before delivery")
        messages = [_message(row) for row in rows if row["id"] is not None]
        self._require_deliveries(rows)
        return {"status":"messages" if messages else "timeout", "messages":messages}

    def _require_deliveries(self, rows: list[Any]) -> None:
        for row in rows:
            if row["id"] is None:
                continue
            message = _message(row)
            self._require_binding(message, row["current_agent_id"])
            if self._linked_execution(row, message):
                self._require_step_delivery(message, row, stage=message["status"] == "pending")


def _snapshot(state: Any, run_id: str, recipient: str) -> dict[str, Any]:
    return state.inbox.poll(run_id,recipient)


async def wait_for_inbox(state: Any, run_id: str, recipient: str, timeout_ms: int = 20000) -> dict[str, Any]:
    """EventBus is a lossy signal; SQLite is checked initially, on wake, and at deadline."""
    for name, value in (("run_id", run_id), ("recipient", recipient)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a nonempty string")
    if type(timeout_ms) is not int or not 0 <= timeout_ms <= 25000:
        raise ValueError("timeout_ms must be an integer from 0 through 25000 (not bool)")
    task = asyncio.current_task()
    state.inbox_wait_tasks.add(task)
    bus = state.event_bus
    subscription, queue = bus.subscribe(run_id=run_id)
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    try:
        while True:
            query = asyncio.create_task(asyncio.to_thread(_snapshot, state, run_id, recipient))
            try:
                result = await asyncio.shield(query)
            except asyncio.CancelledError:
                # Finish the short read before lifespan may close its SQLite connection.
                await query
                raise
            if result["status"] != "timeout":
                return result
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return result
            try:
                await asyncio.wait_for(queue.get(), timeout=remaining)
            except TimeoutError:
                # A lost/overflowed event must not conceal committed work at the deadline.
                continue
    finally:
        bus.unsubscribe(subscription)
        state.inbox_wait_tasks.discard(task)


async def _until_disconnect(request: Request) -> None:
    while True:
        event = await request.receive()
        if event["type"] == "http.disconnect":
            return


async def request_wait(request: Request, arguments: dict[str, Any]) -> dict[str, Any] | None:
    waiting = asyncio.create_task(wait_for_inbox(request.app.state, **arguments))
    disconnected = asyncio.create_task(_until_disconnect(request))
    try:
        done, _ = await asyncio.wait((waiting, disconnected), return_when=asyncio.FIRST_COMPLETED)
        if disconnected in done:
            disconnected.result()
            return None
        return waiting.result()
    finally:
        waiting.cancel()
        disconnected.cancel()
        await asyncio.gather(waiting, disconnected, return_exceptions=True)


INBOX_TOOL_DEFINITIONS = [{'name': 'inbox_send',
  'description': 'Commit one durable addressed message and board post; retry with the same key and '
                 'payload is idempotent.',
  'inputSchema': {'type': 'object',
                  'properties': {'run_id': {'type': 'string'},
                                 'sender': {'type': 'string'},
                                 'recipient': {'type': 'string'},
                                 'key': {'type': 'string'},
                                 'kind': {'type': 'string',
                                          'enum': ['task', 'rework', 'review', 'result', 'notice']},
                                 'summary': {'type': 'string'},
                                 'body_md': {'type': 'string'},
                                 'step_id': {'type': 'string',
                                             'description': 'Same-run step; required for workflow2+ '
                                                            'Swarm task/rework/review/result.'}},
                  'required': ['run_id',
                               'sender',
                               'recipient',
                               'key',
                               'kind',
                               'summary',
                               'body_md'],
                  'additionalProperties': False}},
 {'name': 'inbox_get',
  'description': 'Read a durable message and its receipt history by stable message ID.',
  'inputSchema': {'type': 'object',
                  'properties': {'message_id': {'type': 'string'}},
                  'required': ['message_id'],
                  'additionalProperties': False}},
 {'name': 'inbox_ack',
  'description': 'Record acceptance, completion/failure or explicit cancellation. Cancellation '
                 'never claims execution; receipts are not independent acceptance.',
  'inputSchema': {'type': 'object',
                  'properties': {'message_id': {'type': 'string'},
                                 'recipient': {'type': 'string'},
                                 'status': {'type': 'string',
                                            'enum': ['accepted',
                                                     'completed',
                                                     'failed',
                                                     'cancelled']},
                                 'evidence': {'type': 'string', 'minLength': 1}},
                  'required': ['message_id', 'recipient', 'status', 'evidence'],
                  'additionalProperties': False}},
 {'name': 'inbox_wait',
  'description': 'Wait asynchronously for durable nonterminal messages without acknowledging them. '
                 'Accepted work requires reconciliation, not blind replay.',
  'inputSchema': {'type': 'object',
                  'properties': {'run_id': {'type': 'string'},
                                 'recipient': {'type': 'string'},
                                 'timeout_ms': {'type': 'integer',
                                                'minimum': 0,
                                                'maximum': 25000,
                                                'default': 20000}},
                  'required': ['run_id', 'recipient'],
                  'additionalProperties': False}}]
