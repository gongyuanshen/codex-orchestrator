"""Durable inbox behavior against an isolated, real SQLite database."""

from concurrent.futures import ThreadPoolExecutor
import importlib
import sqlite3
from threading import Barrier

import pytest

from orchestrator.board import BoardService
from orchestrator.config import Settings
from orchestrator.db import Database, NotFoundError


@pytest.fixture
def environment(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", port=19891)
    db = Database(settings=settings)
    board = BoardService(db)
    run = db.create_run(project_root=tmp_path, mode="swarm", title="Inbox tests")
    board.worker_register(run["id"], "writer", "coder", agent_id="native-coder")
    board.worker_register(run["id"], "reviewer", "verifier", agent_id="native-verifier")
    yield db, board, run["id"]
    db.close()


def make_store(db, board):
    store = importlib.import_module("orchestrator.inbox").InboxStore(db, board)
    store.initialize()
    return store


def payload(run_id, **changes):
    return dict(run_id=run_id, sender="coordinator", recipient="writer",
                key="task-1", kind="task", summary="Fix fixture",
                body_md="Change the fixture and run its tests.") | changes


def conflict():
    return importlib.import_module("orchestrator.inbox").InboxConflictError


def board_count(db):
    return db.connect().execute("SELECT count(*) FROM board_messages").fetchone()[0]


def test_send_persists_message_and_board_pointer(environment):
    db, board, run_id = environment
    store = make_store(db, board)
    result = store.send(**payload(run_id))
    message = result["message"]
    assert result["duplicate"] is False
    assert {k: message[k] for k in payload(run_id)} == payload(run_id)
    assert message["status"] == "pending"
    assert message["evidence"] is None
    assert message["receipts"] == []
    assert message["requires_reconciliation"] is False
    assert store.get(message["id"]) == message
    posted = board.get_message(message["board_seq"])
    assert posted["run_id"] == run_id
    assert posted["sender"] == "coordinator"
    assert posted["summary"] == "Fix fixture"
    assert posted["body_md"] == "Change the fixture and run its tests."
    assert "writer" in posted["mentions"]
    assert board_count(db) == 1


@pytest.mark.parametrize("kind", ["task", "rework", "review", "result", "notice"])
def test_each_kind_deduplicates_without_another_board_post(environment, kind):
    db, board, run_id = environment
    store = make_store(db, board)
    original = store.send(**payload(run_id, kind=kind))
    assert store.send(**payload(run_id, kind=kind)) == dict(
        message=original["message"], duplicate=True)
    assert board_count(db) == 1


@pytest.mark.parametrize("change", [
    {"recipient": "reviewer"}, {"kind": "rework"},
    {"summary": "Changed summary"}, {"body_md": "Changed body"},
    {"body_md": "Change the fixture and run its tests. "},
])
def test_same_key_changed_content_conflicts(environment, change):
    db, board, run_id = environment
    store = make_store(db, board)
    original = store.send(**payload(run_id))["message"]
    with pytest.raises(conflict()):
        store.send(**payload(run_id, **change))
    assert store.get(original["id"]) == original
    assert board_count(db) == 1


def test_key_scope_is_run_and_sender(environment, tmp_path):
    db, board, run_id = environment
    store = make_store(db, board)
    other_run = db.create_run(project_root=tmp_path, mode="swarm", title="Other")
    board.worker_register(other_run["id"], "writer", "coder", agent_id="native-coder")
    messages = [store.send(**p)["message"] for p in [
        payload(run_id), payload(run_id, sender="verifier"), payload(other_run["id"]),
    ]]
    assert len({m["id"] for m in messages}) == 3
    assert board_count(db) == 3


def test_parallel_duplicate_sends_use_one_durable_message(environment):
    db, board, run_id = environment
    first = make_store(db, board)
    second_db = Database(settings=db.settings)
    try:
        second = make_store(second_db, BoardService(second_db))
        start = Barrier(8)

        def send(i):
            start.wait(timeout=5)
            return [first, second][i % 2].send(**payload(run_id))

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(send, range(8)))
        assert len({r["message"]["id"] for r in results}) == 1
        assert sum(not r["duplicate"] for r in results) == 1
        assert board_count(db) == 1
    finally:
        second_db.close()


def test_parallel_different_payload_cannot_overwrite_key(environment):
    db, board, run_id = environment
    store = make_store(db, board)
    start = Barrier(2)

    def send(body):
        start.wait(timeout=5)
        try:
            return store.send(**payload(run_id, body_md=body))
        except conflict() as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(send, ["first", "second"]))
    assert sum(isinstance(r, conflict()) for r in results) == 1
    winner = next(r["message"] for r in results if isinstance(r, dict))
    assert store.get(winner["id"]) == winner
    assert board_count(db) == 1


@pytest.mark.parametrize("table", ["inbox_messages", "board_messages"])
def test_failed_insert_rolls_back_message_board_and_run_update(environment, table):
    db, board, run_id = environment
    store = make_store(db, board)
    before = db.get_run(run_id)
    with db.transaction() as conn:
        conn.execute(f"CREATE TRIGGER fail_insert BEFORE INSERT ON {table} "
                     "BEGIN SELECT RAISE(ABORT, 'injected write failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="injected write failure"):
        store.send(**payload(run_id))
    assert store.pending(run_id, "writer") == []
    assert board_count(db) == 0
    assert db.get_run(run_id) == before


def test_outer_transaction_rollback_has_no_orphan_board(environment):
    db, board, run_id = environment
    store = make_store(db, board)
    with pytest.raises(RuntimeError, match="caller aborted"):
        with db.transaction():
            store.send(**payload(run_id))
            raise RuntimeError("caller aborted")
    assert store.pending(run_id, "writer") == []
    assert board_count(db) == 0


def test_board_must_share_the_store_database(environment, tmp_path):
    db, _, _ = environment
    other_db = Database(settings=Settings(data_dir=tmp_path / "other"))
    try:
        with pytest.raises(ValueError, match="same Database"):
            make_store(db, BoardService(other_db))
    finally:
        other_db.close()


def test_send_requires_existing_active_run_and_registered_recipient(environment, tmp_path):
    db, board, run_id = environment
    store = make_store(db, board)
    other_run = db.create_run(project_root=tmp_path, mode="swarm", title="Other")
    board.worker_register(other_run["id"], "other-only", "coder", agent_id="native-coder")
    with pytest.raises(NotFoundError):
        store.send(**payload("missing-run"))
    with pytest.raises(NotFoundError):
        store.send(**payload(run_id, recipient="other-only"))
    db.update_run(run_id, status="failed")
    with pytest.raises(conflict()):
        store.send(**payload(run_id))
    assert board_count(db) == 0


def test_existing_message_is_retryable_after_run_closes(environment):
    db, board, run_id = environment
    store = make_store(db, board)
    original = store.send(**payload(run_id))["message"]
    db.update_run(run_id, status="failed")
    assert store.send(**payload(run_id)) == dict(message=original, duplicate=True)
    assert board_count(db) == 1


def test_pending_is_recipient_and_run_scoped(environment, tmp_path):
    db, board, run_id = environment
    store = make_store(db, board)
    other_run = db.create_run(project_root=tmp_path, mode="swarm", title="Other")
    board.worker_register(other_run["id"], "writer", "coder", agent_id="native-coder")
    mine = store.send(**payload(run_id))["message"]
    store.send(**payload(run_id, key="review-1", recipient="reviewer"))
    store.send(**payload(other_run["id"]))
    assert store.pending(run_id, "writer") == [mine]
    with pytest.raises(NotFoundError):
        store.pending(run_id, "unregistered")
    with pytest.raises(NotFoundError):
        store.pending("missing-run", "writer")


@pytest.mark.parametrize("terminal", ["completed", "failed"])
def test_receipts_transition_and_retry_without_reverting_or_redelivery(environment, terminal):
    db, board, run_id = environment
    store = make_store(db, board)
    message = store.send(**payload(run_id))["message"]
    accepted = store.ack(message["id"], "writer", "accepted", "Read task")
    assert accepted["status"] == "accepted"
    assert accepted["evidence"] == "Read task"
    assert accepted["requires_reconciliation"] is True
    assert len(accepted["receipts"]) == 1
    assert store.ack(message["id"], "writer", "accepted", "Read task") == accepted
    assert store.pending(run_id, "writer") == [accepted]
    finished = store.ack(message["id"], "writer", terminal, "pytest report.txt")
    assert finished["status"] == terminal
    assert finished["evidence"] == "pytest report.txt"
    assert finished["requires_reconciliation"] is False
    assert len(finished["receipts"]) == 2
    assert finished["receipts"][0] == accepted["receipts"][0]
    assert finished["receipts"][0]["id"] != finished["receipts"][1]["id"]
    assert store.ack(message["id"], "writer", terminal, "pytest report.txt") == finished
    assert store.ack(message["id"], "writer", "accepted", "Read task") == finished
    assert store.send(**payload(run_id)) == dict(message=finished, duplicate=True)
    assert store.pending(run_id, "writer") == []
    assert board_count(db) == 1


@pytest.mark.parametrize("attempts", [
    [("completed", "premature")], [("failed", "premature")],
    [("accepted", "first"), ("accepted", "changed")],
    [("accepted", "first"), ("completed", "done"), ("completed", "changed")],
    [("accepted", "first"), ("completed", "done"), ("failed", "changed")],
    [("accepted", "first"), ("failed", "failed"), ("completed", "changed")],
])
def test_incompatible_receipts_are_explicit_conflicts(environment, attempts):
    db, board, run_id = environment
    store = make_store(db, board)
    message = store.send(**payload(run_id))["message"]
    for status, evidence in attempts[:-1]:
        message = store.ack(message["id"], "writer", status, evidence)
    with pytest.raises(conflict()):
        store.ack(message["id"], "writer", *attempts[-1])
    assert store.get(message["id"]) == message


def test_wrong_recipient_cannot_ack_or_replay_someone_elses_receipt(environment):
    db, board, run_id = environment
    store = make_store(db, board)
    message = store.send(**payload(run_id))["message"]
    for status in ["accepted", "completed"]:
        with pytest.raises(conflict()):
            store.ack(message["id"], "reviewer", status, "claim")
    accepted = store.ack(message["id"], "writer", "accepted", "claim")
    with pytest.raises(conflict()):
        store.ack(message["id"], "reviewer", "accepted", "claim")
    assert store.get(message["id"]) == accepted


def test_parallel_exact_ack_has_one_receipt(environment):
    db, board, run_id = environment
    store = make_store(db, board)
    message = store.send(**payload(run_id))["message"]
    start = Barrier(6)

    def ack(_):
        start.wait(timeout=5)
        return store.ack(message["id"], "writer", "accepted", "Read task")

    with ThreadPoolExecutor(max_workers=6) as pool:
        replies = list(pool.map(ack, range(6)))
    assert all(reply == replies[0] for reply in replies)
    assert len(replies[0]["receipts"]) == 1


def test_reopen_preserves_accepted_for_reconciliation_and_terminal_receipts(environment):
    db, board, run_id = environment
    store = make_store(db, board)
    message = store.send(**payload(run_id))["message"]
    accepted = store.ack(message["id"], "writer", "accepted", "Side effect uncertain")
    terminal_id = store.send(**payload(run_id, key="other-task"))["message"]["id"]
    store.ack(terminal_id, "writer", "accepted", "Read task")
    terminal = store.ack(terminal_id, "writer", "completed", "Result saved")
    db.close()
    reopened_db = Database(settings=db.settings)
    try:
        reopened = make_store(reopened_db, BoardService(reopened_db))
        reopened.initialize()
        assert reopened.get(message["id"]) == accepted
        assert reopened.pending(run_id, "writer") == [accepted]
        assert reopened.get(terminal_id) == terminal
        assert reopened.ack(terminal_id, "writer", "completed", "Result saved") == terminal
    finally:
        reopened_db.close()


@pytest.mark.parametrize("field", ["run_id", "sender", "recipient", "key", "kind", "summary", "body_md"])
@pytest.mark.parametrize("value", [None, 1, True, [], "", " \t\n"])
def test_send_rejects_blank_and_wrong_types_at_boundary(environment, field, value):
    db, board, run_id = environment
    store = make_store(db, board)
    with pytest.raises(ValueError, match=field):
        store.send(**(payload(run_id) | {field: value}))
    assert board_count(db) == 0


def test_send_rejects_unknown_kind(environment):
    db, board, run_id = environment
    store = make_store(db, board)
    with pytest.raises(ValueError, match="kind"):
        store.send(**payload(run_id, kind="TASK"))
    assert board_count(db) == 0


@pytest.mark.parametrize("field", ["message_id", "recipient", "status", "evidence"])
@pytest.mark.parametrize("value", [None, False, [], "", " \n"])
def test_ack_validates_before_mutating(environment, field, value):
    db, board, run_id = environment
    store = make_store(db, board)
    message = store.send(**payload(run_id))["message"]
    args = dict(message_id=message["id"], recipient="writer", status="accepted", evidence="Read task")
    with pytest.raises(ValueError, match=field):
        store.ack(**(args | {field: value}))
    assert store.get(message["id"]) == message


@pytest.mark.parametrize("status", ["pending", "unknown"])
def test_ack_rejects_invalid_target_status(environment, status):
    db, board, run_id = environment
    store = make_store(db, board)
    message = store.send(**payload(run_id))["message"]
    with pytest.raises(ValueError, match="status"):
        store.ack(message["id"], "writer", status, "claim")
    assert store.get(message["id"]) == message


def test_missing_message_is_not_an_empty_success(environment):
    db, board, _ = environment
    store = make_store(db, board)
    with pytest.raises(NotFoundError):
        store.get("missing-message")
    with pytest.raises(NotFoundError):
        store.ack("missing-message", "writer", "accepted", "Read task")


@pytest.mark.parametrize("value", [None, False, [], "", " \n"])
def test_read_operations_validate_identifiers(environment, value):
    db, board, run_id = environment
    store = make_store(db, board)
    with pytest.raises(ValueError, match="message_id"):
        store.get(value)
    with pytest.raises(ValueError, match="run_id"):
        store.pending(value, "writer")
    with pytest.raises(ValueError, match="recipient"):
        store.pending(run_id, value)


def test_agent_binding_snapshot_blocks_replacement_delivery(environment):
    db, board, run_id = environment
    store = make_store(db, board)
    original = store.send(**payload(run_id))["message"]
    assert original["recipient_agent_id"] == "native-coder"
    with pytest.raises(ValueError, match="immutable"):
        board.worker_register(run_id, "writer", "coder", agent_id="replacement-coder")
    # Fault injection represents a stale/tampered persisted binding, not an allowed API rebind.
    with db.transaction() as conn:
        conn.execute("UPDATE workers SET agent_id=? WHERE run_id=? AND name='writer'", ("replacement-coder", run_id))
    with pytest.raises(conflict(), match="binding"):
        store.pending(run_id, "writer")
    with pytest.raises(conflict(), match="binding"):
        store.ack(original["id"], "writer", "accepted", "replacement must not claim")
    assert store.send(**payload(run_id))["message"] == original
    cancelled = store.ack(original["id"], "writer", "cancelled", "Original agent ended; explicit cancellation")
    assert cancelled["status"] == "cancelled"
    assert cancelled["recipient_agent_id"] == "native-coder"


def test_new_workflow_requires_bound_agent(environment):
    db, board, run_id = environment
    board.worker_register(run_id, "unbound", "coder")
    with pytest.raises(conflict(), match="binding"):
        make_store(db, board).send(**payload(run_id, recipient="unbound"))


@pytest.mark.parametrize("status", ["stopped", "killed"])
def test_paused_pending_can_cancel_without_fake_accept(environment, status):
    db, board, run_id = environment
    store = make_store(db, board)
    original = store.send(**payload(run_id))["message"]
    db.update_worker_status(run_id=run_id, worker_name="writer", status=status)
    with pytest.raises(conflict(), match="paused"):
        store.ack(original["id"], "writer", "accepted", "Not executed")
    final = store.ack(original["id"], "writer", "cancelled", "User stopped worker before receipt")
    assert [r["status"] for r in final["receipts"]] == ["cancelled"]
    assert store.pending(run_id, "writer") == []


def test_accepted_failure_disposition_allowed_while_control_pending(environment):
    db, board, run_id = environment
    store = make_store(db, board)
    mid = store.send(**payload(run_id))["message"]["id"]
    store.ack(mid, "writer", "accepted", "Read original task")
    worker = db.get_worker_by_name(run_id, "writer")
    db.request_worker_control(worker["id"], "stopped")
    with pytest.raises(conflict(), match="control"):
        store.ack(mid, "writer", "completed", "Must not claim completion")
    failed = store.ack(mid, "writer", "failed", "Interrupted action requires explicit reconciliation")
    assert failed["status"] == "failed"


def test_historical_ack_does_not_execute_after_pause_or_rebind(environment):
    db, board, run_id = environment
    store = make_store(db, board)
    mid = store.send(**payload(run_id))["message"]["id"]
    store.ack(mid, "writer", "accepted", "Read")
    done = store.ack(mid, "writer", "completed", "Checked")
    db.update_worker_status(run_id=run_id, worker_name="writer", status="stopped")
    with db.transaction() as conn:
        conn.execute("UPDATE workers SET agent_id=? WHERE run_id=? AND name='writer'", ("replacement", run_id))
    assert store.ack(mid, "writer", "accepted", "Read") == done


def test_step_reference_cannot_cross_run_or_be_missing(environment):
    db, board, run_id = environment
    with pytest.raises(NotFoundError, match="step"):
        make_store(db, board).send(**payload(run_id), step_id="missing-step")


def test_legacy_unbound_recipient_remains_legacy_not_authenticated(environment):
    db, board, run_id = environment
    with db.transaction() as conn:
        conn.execute("UPDATE runs SET workflow_version=0 WHERE id=?", (run_id,))
    board.worker_register(run_id, "legacy", "coder")
    store = make_store(db, board)
    message = store.send(**payload(run_id, recipient="legacy"))["message"]
    assert message["recipient_agent_id"] is None
    assert store.pending(run_id, "legacy") == [message]


def test_new_ack_after_run_failure_is_not_an_execution_success(environment):
    db, board, run_id = environment
    store = make_store(db, board)
    message = store.send(**payload(run_id))["message"]
    db.update_run(run_id, status="failed")
    with pytest.raises(conflict(), match="not active"):
        store.ack(message["id"], "writer", "accepted", "Too late")
    assert store.get(message["id"]) == message


def test_cancelled_retry_is_idempotent_and_never_accepts_work(environment):
    db, board, run_id = environment
    store = make_store(db, board)
    message = store.send(**payload(run_id))["message"]
    final = store.ack(message["id"], "writer", "cancelled", "Superseded before execution")
    assert store.ack(message["id"], "writer", "cancelled", "Superseded before execution") == final
    with pytest.raises(conflict(), match="transition"):
        store.ack(message["id"], "writer", "accepted", "Must not restart cancelled work")
    assert store.pending(run_id, "writer") == []


def test_explicit_cancel_unblocks_run_gate_without_fake_receipt(environment, tmp_path):
    from orchestrator.workflow import WorkflowConflictError, WorkflowService
    db, board, run_id = environment
    (tmp_path / "target.txt").write_text("reviewed", encoding="utf-8")
    evidence = tmp_path / "real-check.txt"
    evidence.write_text("test fixture evidence", encoding="utf-8")
    db.save_task_packet(run_id, title="Frozen", goal="Validate gate semantics",
                        file_ownership={"writer": ["target.txt"]}, acceptance_criteria=["reviewed target"])
    flow = WorkflowService(db)
    step = flow.step_save(run_id, "gate", "writer", "reviewer")
    store = make_store(db, board)
    message = store.send(**payload(run_id), step_id=step["id"])["message"]
    for action in ("dispatch", "accept", "submit"):
        step = flow.step_advance(step["id"], action, step["revision"])
    for scope in ("candidate", "integrated"):
        artifact = flow.capture(step["id"], scope, paths=["target.txt"])
        flow.record(step["id"], artifact["id"], "reviewer", "PASS",
                    [{"criterion": "reviewed target", "status": "PASS"}], [str(evidence)], scope)
        step = flow.get_step(step["id"])
        action = "begin_integrated_review" if scope == "candidate" else "complete"
        step = flow.step_advance(step["id"], action, step["revision"])
    assert step["state"] == "done"
    with pytest.raises(WorkflowConflictError, match="inbox"):
        db.update_run(run_id, status="completed")
    db.update_worker_status(run_id=run_id, worker_name="writer", status="stopped")
    final = store.ack(message["id"], "writer", "cancelled", "User stopped original task; no execution claimed")
    assert [receipt["status"] for receipt in final["receipts"]] == ["cancelled"]
    assert db.update_run(run_id, status="completed")["status"] == "completed"
