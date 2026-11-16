"""Workflow2 Swarm deliveries use one frozen work-step context, not another scheduler."""
import asyncio

import httpx
import pytest

from orchestrator.board import BoardService
from orchestrator.config import Settings
from orchestrator.daemon import create_app
from orchestrator.db import Database, NotFoundError
from orchestrator.inbox import InboxConflictError, InboxStore
from orchestrator.workflow import WorkflowService


@pytest.fixture
def linked(tmp_path):
    (tmp_path / "source.txt").write_text("initial", encoding="utf-8")
    db = Database(settings=Settings(data_dir=tmp_path / "state"))
    board = BoardService(db)
    run = db.create_run(mode="swarm", title="Linked inbox", project_root=tmp_path)
    db.save_task_packet(run["id"], title="Frozen", goal="one work item",
                        file_ownership={"author": ["source.txt"], "other": ["source.txt"]},
                        acceptance_criteria=["output"])
    for name, role in (("author", "coder"), ("verifier", "verifier"), ("other", "coder")):
        board.worker_register(run["id"], name, role, agent_id=f"/root/{name}")
    flow = WorkflowService(db)
    step = flow.step_save(run["id"], "work", "author", "verifier")
    store = InboxStore(db, board)
    store.initialize()
    yield db, flow, store, run, step, tmp_path
    db.close()


def advance(flow, step, action):
    return flow.step_advance(step["id"], action, step["revision"])


def args(run, step=None, **changes):
    return dict(run_id=run["id"], sender="coordinator", recipient="author", key="delivery",
                kind="task", summary="Implement work", body_md="Run actual checks.",
                step_id=step["id"] if step else None) | changes


def fail_delivery(flow, step, root):
    for action in ("accept", "submit"):
        step = advance(flow, step, action)
    evidence = root / "failure.txt"
    evidence.write_text("Actual fixture rejection", encoding="utf-8")
    artifact = flow.capture(step["id"], "candidate", ["source.txt"])
    flow.record(step["id"], artifact["id"], "verifier", "FAIL",
                [{"criterion": "output", "status": "FAIL"}], [str(evidence)], "failed-delivery")
    return flow.get_step(step["id"])


@pytest.mark.parametrize("kind", ["task", "rework", "review", "result"])
def test_swarm_execution_requires_step_and_same_run(linked, kind):
    db, flow, store, run, step, root = linked
    with pytest.raises(InboxConflictError, match="step"):
        store.send(**args(run, kind=kind))
    with pytest.raises(NotFoundError, match="step"):
        store.send(**args(run, step_id="missing", kind=kind))
    other = db.create_run(mode="swarm", title="Other", project_root=root)
    BoardService(db).worker_register(other["id"], "author", "coder", agent_id="/root/other-run")
    with pytest.raises(InboxConflictError, match="same run"):
        store.send(**args(other, step, kind=kind))
    assert db.connect().execute("SELECT count(*) FROM inbox_messages").fetchone()[0] == 0


@pytest.mark.parametrize("kind,recipient", [
    ("task", "verifier"), ("rework", "verifier"), ("review", "author"), ("result", "other")])
def test_swarm_execution_rejects_wrong_step_recipient(linked, kind, recipient):
    db, flow, store, run, step, root = linked
    with pytest.raises(InboxConflictError, match="recipient"):
        store.send(**args(run, step, kind=kind, recipient=recipient))


@pytest.mark.parametrize("kind,recipient", [
    ("task", "author"), ("rework", "author"), ("review", "verifier"),
    ("result", "author"), ("result", "verifier")])
def test_swarm_roles_persist_frozen_context_without_advancing_step(linked, kind, recipient):
    db, flow, store, run, step, root = linked
    step = advance(flow, step, "dispatch")
    if kind == "rework":
        step = advance(flow, fail_delivery(flow, step, root), "start_rework")
    elif kind == "review":
        step = advance(flow, advance(flow, step, "accept"), "submit")
    sent = store.send(**args(run, step, kind=kind, recipient=recipient))["message"]
    assert sent["step_packet_version"] == 1
    assert sent["step_attempt"] == (1 if kind == "rework" else 0)
    assert sent["step_contract_revision"] == 0
    assert store.poll(run["id"], recipient)["messages"] == [sent]
    accepted = store.ack(sent["id"], recipient, "accepted", "Read exact step")
    assert accepted["requires_reconciliation"]
    completed = store.ack(sent["id"], recipient, "completed", "Delivery handled, not independent PASS")
    assert completed["status"] == "completed"
    assert flow.get_step(step["id"]) == step
    assert store.poll(run["id"], recipient) == {"status": "timeout", "messages": []}
    db.close()
    reopened = Database(settings=db.settings)
    try:
        assert InboxStore(reopened, BoardService(reopened)).get(sent["id"]) == completed
    finally:
        reopened.close()


def test_task_dispatch_requires_admitted_stage_and_does_not_reset_retry_budget(linked):
    db, flow, store, run, step, root = linked
    with pytest.raises(InboxConflictError, match="stage"):
        store.send(**args(run, step))
    step = advance(flow, step, "dispatch")
    failed = fail_delivery(flow, step, root)
    with pytest.raises(InboxConflictError, match="stage"):
        store.send(**args(run, failed))
    step = advance(flow, failed, "start_rework")
    with pytest.raises(InboxConflictError, match="stage"):
        store.send(**args(run, step))
    step = advance(flow, step, "submit")
    step = flow.step_save(run["id"], "work", "author", "verifier", step_id=step["id"],
                          expected_revision=step["revision"])
    step = advance(flow, step, "dispatch")
    sent = store.send(**args(run, step))["message"]
    assert sent["step_attempt"] == 1
    assert sent["step_contract_revision"] == 1


@pytest.mark.parametrize("stage", ["planned", "dispatched", "implementing", "rework"])
def test_rework_dispatch_requires_started_repair_attempt(linked, stage):
    db, flow, store, run, step, root = linked
    if stage != "planned":
        step = advance(flow, step, "dispatch")
    if stage == "implementing":
        step = advance(flow, step, "accept")
    elif stage == "rework":
        step = fail_delivery(flow, step, root)
    with pytest.raises(InboxConflictError, match="stage"):
        store.send(**args(run, step, kind="rework"))


def test_review_cannot_be_dispatched_before_submission(linked):
    db, flow, store, run, step, root = linked
    step = advance(flow, step, "dispatch")
    with pytest.raises(InboxConflictError, match="stage"):
        store.send(**args(run, step, kind="review", recipient="verifier"))


def test_fresh_task_receipt_cannot_execute_after_stage_consumed(linked):
    db, flow, store, run, step, root = linked
    step = advance(flow, step, "dispatch")
    sent = store.send(**args(run, step))["message"]
    step = advance(flow, step, "accept")
    with pytest.raises(InboxConflictError, match="stage"):
        store.ack(sent["id"], "author", "accepted", "Too late for initial dispatch")
    with pytest.raises(InboxConflictError, match="stage"):
        store.poll(run["id"], "author")
    assert store.ack(sent["id"], "author", "cancelled", "Superseded before receipt")["status"] == "cancelled"


def test_author_can_submit_before_terminal_delivery_receipt_without_fake_pass(linked):
    db, flow, store, run, step, root = linked
    step = advance(flow, step, "dispatch")
    sent = store.send(**args(run, step))["message"]
    accepted = store.ack(sent["id"], "author", "accepted", "Read")
    step = advance(flow, advance(flow, step, "accept"), "submit")
    assert store.poll(run["id"], "author")["messages"] == [accepted]
    assert store.ack(sent["id"], "author", "accepted", "Read") == accepted
    assert store.ack(sent["id"], "author", "completed", "Author submitted")["status"] == "completed"
    assert flow.get_step(step["id"]) == step
    assert step["state"] == "candidate_review"
    assert db.connect().execute("SELECT count(*) FROM verification_records").fetchone()[0] == 0


@pytest.mark.parametrize("mutation", ["packet", "contract", "attempt", "owner_binding", "verifier_binding"])
@pytest.mark.parametrize("accepted", [False, True])
def test_stale_delivery_never_executes_but_remains_inspectable_retryable_and_cancellable(linked, mutation, accepted):
    db, flow, store, run, step, root = linked
    step = advance(flow, step, "dispatch")
    payload = args(run, step)
    sent = store.send(**payload)["message"]
    if accepted:
        sent = store.ack(sent["id"], "author", "accepted", "Original accepted")
    if mutation == "packet":
        db.save_task_packet(run["id"], title="Changed", goal="new packet",
                            file_ownership={"author": ["source.txt"]}, acceptance_criteria=["output"])
    elif mutation == "contract":
        flow.step_save(run["id"], "work", "author", "verifier", step_id=step["id"],
                       expected_revision=step["revision"])
    elif mutation == "attempt":
        # Fault injection checks the persisted receipt guard independently of stage transitions.
        db.connect().execute("UPDATE workflow_steps SET attempt=attempt+1 WHERE id=?", (step["id"],))
    else:
        name = "author" if mutation == "owner_binding" else "verifier"
        db.connect().execute("UPDATE workers SET agent_id='/root/rebound' WHERE run_id=? AND name=?", (run["id"], name))
    assert store.get(sent["id"])["requires_reconciliation"] is accepted
    assert store.send(**payload) == {"message": sent, "duplicate": True}
    for operation in (store.poll, store.pending):
        with pytest.raises(InboxConflictError, match="stale|binding"):
            operation(run["id"], "author")
    with pytest.raises(InboxConflictError, match="stale|binding"):
        store.ack(sent["id"], "author", "completed" if accepted else "accepted", "No stale execution")
    if accepted:
        assert store.ack(sent["id"], "author", "accepted", "Original accepted") == sent
    cancelled = store.ack(sent["id"], "author", "cancelled", "Reconciled obsolete delivery; no replay")
    assert cancelled["status"] == "cancelled"
    assert cancelled["step_packet_version"] == 1
    assert cancelled["step_attempt"] == cancelled["step_contract_revision"] == 0


@pytest.mark.parametrize("worker", ["author", "verifier"])
def test_new_delivery_rejects_either_changed_step_binding(linked, worker):
    db, flow, store, run, step, root = linked
    step = advance(flow, step, "dispatch")
    db.connect().execute("UPDATE workers SET agent_id='/root/rebound' WHERE run_id=? AND name=?", (run["id"], worker))
    with pytest.raises(InboxConflictError, match="binding"):
        store.send(**args(run, step))


def test_unlinked_notice_is_nonblocking_and_other_modes_keep_unlinked_deliveries(linked):
    db, flow, store, run, step, root = linked
    notice = store.send(**args(run, kind="notice"))["message"]
    assert notice["step_id"] is None
    assert notice["step_packet_version"] is notice["step_attempt"] is notice["step_contract_revision"] is None
    assert store.poll(run["id"], "author")["messages"] == [notice]
    assert BoardService(db).board_status(run["id"])["inbox"]["blocking_count"] == 0
    for mode in ("boost", "teamwork"):
        other = db.create_run(mode=mode, title=mode, project_root=root)
        BoardService(db).worker_register(other["id"], "author", "coder", agent_id=f"/root/{mode}")
        assert store.send(**args(other))["message"]["step_id"] is None


def test_schema4_delivery_keeps_nullable_context_and_legacy_receipts_after_migration(tmp_path):
    path = tmp_path / "old.db"
    db = Database(path)
    run = db.create_run(mode="swarm", title="foundation", project_root=tmp_path)
    db.connect().execute("UPDATE runs SET workflow_version=1 WHERE id=?", (run["id"],))
    board = BoardService(db)
    board.worker_register(run["id"], "author", "coder", agent_id="/root/legacy")
    sent = InboxStore(db, board).send(**args(run))["message"]
    db.connect().execute("DELETE FROM schema_migrations WHERE version>4")
    for field in ("step_packet_version", "step_attempt", "step_contract_revision"):
        db.connect().execute(f"ALTER TABLE inbox_messages DROP COLUMN {field}")
    db.close()
    db = Database(path)
    try:
        store = InboxStore(db, BoardService(db))
        store.initialize()
        migrated = store.get(sent["id"])
        assert migrated["step_packet_version"] is migrated["step_attempt"] is migrated["step_contract_revision"] is None
        assert store.poll(run["id"], "author")["messages"] == [migrated]
        store.ack(sent["id"], "author", "accepted", "Migrated legacy delivery")
        assert store.ack(sent["id"], "author", "completed", "Legacy receipt only")["status"] == "completed"
        assert db.get_run(run["id"])["workflow_version"] == 1
    finally:
        db.close()


def test_real_workflow2_rpc_wait_receive_rework_and_stale_wait_cleanup(tmp_path):
    async def scenario():
        (tmp_path / "source.txt").write_text("initial", encoding="utf-8")
        app = create_app(Settings(data_dir=tmp_path / "rpc-state"), start_watchdog=False, manage_pid_file=False)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                async def rpc(tool, **arguments):
                    result = await client.post("/api/rpc", json={"tool": tool, "arguments": arguments})
                    assert result.status_code == 200, result.text
                    return result.json()["result"]
                run = await rpc("run_create", mode="swarm", title="Full2 transport", project_root=str(tmp_path))
                await rpc("task_packet_save", run_id=run["id"], title="frozen", goal="work",
                          file_ownership={"author": ["source.txt"]}, acceptance_criteria=["output"])
                for name, role in (("author", "coder"), ("verifier", "verifier")):
                    await rpc("worker_register", run_id=run["id"], name=name, role=role, agent_id=f"/root/{name}")
                step = await rpc("workflow_step_save", run_id=run["id"], step_key="work", owner="author", verifier="verifier")
                step = await rpc("workflow_step_advance", step_id=step["id"], action="dispatch", expected_revision=step["revision"])
                waiting = asyncio.create_task(rpc("inbox_wait", run_id=run["id"], recipient="author", timeout_ms=2000))
                async with asyncio.timeout(2):
                    while app.state.event_bus.subscriber_count() != 1:
                        await asyncio.sleep(.001)
                sent = await rpc("inbox_send", **args(run, step))
                received = await waiting
                assert received["messages"] == [sent["message"]]
                mid = sent["message"]["id"]
                await rpc("inbox_ack", message_id=mid, recipient="author", status="accepted", evidence="Actual RPC read")
                failed = fail_delivery(app.state.workflow, step, tmp_path)
                await rpc("inbox_ack", message_id=mid, recipient="author", status="completed", evidence="Author submitted; verifier rejected")
                step = await rpc("workflow_step_advance", step_id=step["id"], action="start_rework", expected_revision=failed["revision"])
                repair = await rpc("inbox_send", **args(run, step, kind="rework", key="repair"))
                assert repair["message"]["step_attempt"] == 1
                assert (await rpc("inbox_wait", run_id=run["id"], recipient="author", timeout_ms=0))["messages"] == [repair["message"]]
                await rpc("inbox_ack", message_id=repair["message"]["id"], recipient="author", status="accepted", evidence="Repair read")
                await rpc("task_packet_save", run_id=run["id"], title="revised", goal="new contract",
                          file_ownership={"author": ["source.txt"]}, acceptance_criteria=["output"])
                response = await client.post("/api/rpc", json={"tool": "inbox_wait", "arguments": {
                    "run_id": run["id"], "recipient": "author", "timeout_ms": 0}})
                assert response.status_code == 409
                assert "stale" in response.json()["error"]
                assert app.state.event_bus.subscriber_count() == 0
                assert not app.state.inbox_wait_tasks
                assert (await rpc("inbox_get", message_id=repair["message"]["id"]))["requires_reconciliation"]
                await rpc("inbox_ack", message_id=repair["message"]["id"], recipient="author", status="cancelled", evidence="Old repair reconciled")
    asyncio.run(scenario())
