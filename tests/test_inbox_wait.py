"""Real SQLite/EventBus checks for the isolated async inbox addon."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from orchestrator.config import Settings
from orchestrator.daemon import create_app
from orchestrator.rpc_handlers import EventBus


def server_module():
    assert importlib.util.find_spec("orchestrator.inbox") is not None, "async inbox server is missing"
    return importlib.import_module("orchestrator.inbox")


@asynccontextmanager
async def running_app(tmp_path):
    app = create_app(Settings(port=19891, data_dir=tmp_path / "data"), start_watchdog=False, manage_pid_file=False)
    async with app.router.lifespan_context(app):
        run = app.state.db.create_run(mode="swarm", title="Inbox checks", project_root=tmp_path)
        app.state.db.upsert_worker(run["id"], "writer", role="coder", agent_id="native-writer")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield app, client, run["id"]


def message_args(run_id, key="task-1", recipient="writer"):
    return dict(run_id=run_id, sender="coordinator", recipient=recipient, key=key,
                kind="task", summary="Implement fixture", body_md="Run the fixture checks.")


async def rpc(client, tool, **arguments):
    return await client.post("/api/rpc", json={"tool": tool, "arguments": arguments})


async def subscribers(bus, expected):
    async with asyncio.timeout(3):
        while bus.subscriber_count() != expected:
            await asyncio.sleep(0.001)


def test_catalog_and_original_rpc_remain_available(tmp_path):
    async def scenario():
        async with running_app(tmp_path) as (app, client, run_id):
            catalog = (await client.get("/api/tools")).json()["tools"]
            names = {tool["name"] for tool in catalog}
            assert {"inbox_send", "inbox_get", "inbox_ack", "inbox_wait", "board_post", "run_list"} <= names
            result = await rpc(client, "run_list")
            assert result.status_code == 200
            assert run_id in str(result.json())
            assert app.state.settings.port == 19891
            assert not app.state.settings.pid_path.exists()
    asyncio.run(scenario())


def test_existing_and_notified_messages_remain_unacknowledged(tmp_path):
    async def scenario():
        async with running_app(tmp_path) as (app, client, run_id):
            sent = (await rpc(client, "inbox_send", **message_args(run_id))).json()["result"]
            for _ in range(2):
                response = await rpc(client, "inbox_wait", run_id=run_id, recipient="writer", timeout_ms=0)
                assert response.json()["result"]["status"] == "messages"
                assert response.json()["result"]["messages"][0]["id"] == sent["message"]["id"]
            assert app.state.db.list_read_cursors(run_id) == {}
            assert app.state.inbox.get(sent["message"]["id"])["status"] == "pending"
            app.state.db.upsert_worker(run_id, "reviewer", role="verifier", agent_id="native-reviewer")
            pending = asyncio.create_task(rpc(client, "inbox_wait", run_id=run_id, recipient="reviewer", timeout_ms=2000))
            await subscribers(app.state.event_bus, 1)
            new = (await rpc(client, "inbox_send", **message_args(run_id, "task-2", "reviewer"))).json()["result"]
            response = await asyncio.wait_for(pending, 1)
            assert response.json()["result"]["messages"][0]["id"] == new["message"]["id"]
            assert app.state.event_bus.subscriber_count() == 0
    asyncio.run(scenario())


def test_accepted_recovery_and_completed_exclusion(tmp_path):
    async def scenario():
        async with running_app(tmp_path) as (app, client, run_id):
            sent = (await rpc(client, "inbox_send", **message_args(run_id))).json()["result"]["message"]
            ack = dict(message_id=sent["id"], recipient="writer", status="accepted", evidence="Received; no side effect yet")
            assert (await rpc(client, "inbox_ack", **ack)).status_code == 200
            result = (await rpc(client, "inbox_wait", run_id=run_id, recipient="writer", timeout_ms=0)).json()["result"]
            assert result["messages"][0]["requires_reconciliation"] is True
            ack.update(status="failed", evidence="Fixture tests passed")
            assert (await rpc(client, "inbox_ack", **ack)).status_code == 200
            result = (await rpc(client, "inbox_wait", run_id=run_id, recipient="writer", timeout_ms=0)).json()["result"]
            assert result == {"status": "timeout", "messages": []}
    asyncio.run(scenario())


@pytest.mark.parametrize("timeout_ms", [-1, 25001, True, False, 1.5, "20", None])
def test_wait_rejects_bad_timeout_without_subscribing(tmp_path, timeout_ms):
    async def scenario():
        async with running_app(tmp_path) as (app, client, run_id):
            response = await rpc(client, "inbox_wait", run_id=run_id, recipient="writer", timeout_ms=timeout_ms)
            assert response.status_code == 400
            assert response.json()["ok"] is False
            assert app.state.event_bus.subscriber_count() == 0
    asyncio.run(scenario())


def test_timeout_rechecks_durable_database_without_signal(tmp_path):
    async def scenario():
        async with running_app(tmp_path) as (app, client, run_id):
            start = time.monotonic()
            empty = await rpc(client, "inbox_wait", run_id=run_id, recipient="writer", timeout_ms=40)
            assert empty.json()["result"] == {"status": "timeout", "messages": []}
            assert 0.025 < time.monotonic() - start < 1
            task = asyncio.create_task(rpc(client, "inbox_wait", run_id=run_id, recipient="writer", timeout_ms=100))
            await subscribers(app.state.event_bus, 1)
            await asyncio.sleep(0.025)
            sent = app.state.inbox.send(**message_args(run_id))
            result = (await task).json()["result"]
            assert result["status"] == "messages"
            assert result["messages"][0]["id"] == sent["message"]["id"]
            assert app.state.event_bus.subscriber_count() == 0
    asyncio.run(scenario())


def test_subscribes_before_first_query_and_survives_event_overflow(tmp_path):
    async def scenario():
        async with running_app(tmp_path) as (app, client, run_id):
            class PublishOnSubscribe(EventBus):
                def subscribe(self, run_id=None):
                    subscription = super().subscribe(run_id)
                    app.state.inbox.send(**message_args(run_id))
                    self.publish("inbox_message", {"run_id": run_id}, run_id=run_id)
                    for index in range(80):
                        self.publish("unrelated", {"index": index}, run_id=run_id)
                    return subscription
            app.state.event_bus = PublishOnSubscribe(max_queue_size=16)
            result = (await rpc(client, "inbox_wait", run_id=run_id, recipient="writer", timeout_ms=100)).json()["result"]
            assert result["status"] == "messages"
            assert result["messages"][0]["key"] == "task-1"
            assert app.state.event_bus.subscriber_count() == 0
    asyncio.run(scenario())


def test_many_waiters_do_not_starve_rpc_threads(tmp_path):
    async def scenario():
        async with running_app(tmp_path) as (app, client, run_id):
            tasks = [asyncio.create_task(rpc(client, "inbox_wait", run_id=run_id, recipient="writer", timeout_ms=3000)) for _ in range(80)]
            try:
                await subscribers(app.state.event_bus, 80)
                sent = await asyncio.wait_for(rpc(client, "inbox_send", **message_args(run_id)), 1)
                assert sent.status_code == 200
                responses = await asyncio.wait_for(asyncio.gather(*tasks), 2)
                assert all(r.json()["result"]["status"] == "messages" for r in responses)
                assert app.state.event_bus.subscriber_count() == 0
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(scenario())


def test_cancellation_releases_subscriber_without_receipt(tmp_path):
    async def scenario():
        async with running_app(tmp_path) as (app, client, run_id):
            task = asyncio.create_task(rpc(client, "inbox_wait", run_id=run_id, recipient="writer"))
            await subscribers(app.state.event_bus, 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert app.state.event_bus.subscriber_count() == 0
            sent = (await rpc(client, "inbox_send", **message_args(run_id))).json()["result"]["message"]
            assert sent["status"] == "pending"
    asyncio.run(scenario())


def test_asgi_disconnect_releases_subscription(tmp_path):
    async def scenario():
        async with running_app(tmp_path) as (app, client, run_id):
            incoming = asyncio.Queue()
            await incoming.put({"type": "http.request", "body": json.dumps({"tool": "inbox_wait", "arguments": {"run_id": run_id, "recipient": "writer"}}).encode(), "more_body": False})
            scope = {"type": "http", "asgi": {"version": "3.0"}, "method": "POST", "path": "/api/rpc", "raw_path": b"/api/rpc", "query_string": b"", "headers": [(b"content-type", b"application/json")], "scheme": "http", "server": ("test", 80), "client": ("test", 1234), "http_version": "1.1"}
            output = []
            async def send(event):
                output.append(event)
            task = asyncio.create_task(app(scope, incoming.get, send))
            await subscribers(app.state.event_bus, 1)
            await incoming.put({"type": "http.disconnect"})
            await asyncio.wait_for(task, 1)
            assert app.state.event_bus.subscriber_count() == 0
            assert app.state.db.list_read_cursors(run_id) == {}
    asyncio.run(scenario())


def test_shutdown_cancels_waiters_before_closing_database(tmp_path):
    async def scenario():
        app = create_app(Settings(port=19891, data_dir=tmp_path / "data"), start_watchdog=False, manage_pid_file=False)
        async with app.router.lifespan_context(app):
            run = app.state.db.create_run(mode="swarm", title="shutdown", project_root=tmp_path)
            app.state.db.upsert_worker(run["id"], "writer", role="coder", agent_id="native-writer")
            task = asyncio.create_task(server_module().wait_for_inbox(app.state, run["id"], "writer"))
            await subscribers(app.state.event_bus, 1)
        assert task.cancelled()
        assert app.state.event_bus.subscriber_count() == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("status", ["stopped", "killed"])
def test_paused_workers_do_not_receive_pending_messages(tmp_path, status):
    async def scenario():
        async with running_app(tmp_path) as (app, client, run_id):
            app.state.inbox.send(**message_args(run_id))
            app.state.db.update_worker_status(run_id=run_id, worker_name="writer", status=status)
            result = (await rpc(client, "inbox_wait", run_id=run_id, recipient="writer", timeout_ms=0)).json()["result"]
            assert result == {"status": "worker_paused", "messages": []}
            assert len(app.state.inbox.pending(run_id, "writer")) == 1
    asyncio.run(scenario())


def test_closed_run_and_pending_control_take_priority_over_delivery(tmp_path):
    async def scenario():
        async with running_app(tmp_path) as (app, client, run_id):
            app.state.inbox.send(**message_args(run_id))
            worker = app.state.db.get_worker_by_name(run_id, "writer")
            control = app.state.db.request_worker_control(worker["id"], "stopped")
            result = (await rpc(client, "inbox_wait", run_id=run_id, recipient="writer", timeout_ms=0)).json()["result"]
            assert result == {"status": "control_requested", "messages": []}
            app.state.db.worker_control_ack(control["id"], "failed", "No native operation executed")
            app.state.db.update_run(run_id, status="failed")
            result = (await rpc(client, "inbox_wait", run_id=run_id, recipient="writer", timeout_ms=0)).json()["result"]
            assert result == {"status": "run_closed", "messages": []}
    asyncio.run(scenario())


def test_errors_are_not_timeouts_and_conflicts_are_409(tmp_path):
    async def scenario():
        async with running_app(tmp_path) as (app, client, run_id):
            response = await rpc(client, "inbox_wait", run_id=run_id, recipient="missing", timeout_ms=0)
            assert response.status_code == 404
            sent = await rpc(client, "inbox_send", **message_args(run_id))
            assert sent.status_code == 200
            changed = message_args(run_id)
            changed["summary"] = "Changed payload"
            assert (await rpc(client, "inbox_send", **changed)).status_code == 409
            assert app.state.event_bus.subscriber_count() == 0
    asyncio.run(scenario())


def test_signal_observer_sees_committed_message_and_receipt(tmp_path, monkeypatch):
    async def scenario():
        async with running_app(tmp_path) as (app, client, run_id):
            bus = app.state.event_bus
            publish = bus.publish
            observed = []
            def check_committed(event, data, *, run_id=None):
                message = data["message"]
                with sqlite3.connect(app.state.settings.db_path) as db:
                    durable = db.execute("SELECT status, board_seq FROM inbox_messages WHERE id=?", (message["id"],)).fetchone()
                    assert durable == (message["status"], message["board_seq"])
                    assert db.execute("SELECT seq FROM board_messages WHERE seq=?", (message["board_seq"],)).fetchone()
                observed.append(event)
                return publish(event, data, run_id=run_id)
            monkeypatch.setattr(bus, "publish", check_committed)
            sent = (await rpc(client, "inbox_send", **message_args(run_id))).json()["result"]["message"]
            duplicate = (await rpc(client, "inbox_send", **message_args(run_id))).json()["result"]
            assert duplicate["duplicate"] is True
            assert observed == ["inbox_updated"]
            assert (await rpc(client, "inbox_ack", message_id=sent["id"], recipient="writer", status="accepted", evidence="Receipt observed")).status_code == 200
            assert observed == ["inbox_updated", "inbox_updated"]
            missing = message_args(run_id, "missing-recipient", "missing")
            assert (await rpc(client, "inbox_send", **missing)).status_code == 404
            assert observed == ["inbox_updated", "inbox_updated"]
    asyncio.run(scenario())


@pytest.mark.parametrize("transition,expected", [("stop", "worker_paused"), ("close", "run_closed"), ("control", "control_requested")])
def test_wait_wakes_for_existing_worker_and_run_control_events(tmp_path, transition, expected):
    async def scenario():
        async with running_app(tmp_path) as (app, client, run_id):
            task = asyncio.create_task(rpc(client, "inbox_wait", run_id=run_id, recipient="writer", timeout_ms=2000))
            await subscribers(app.state.event_bus, 1)
            if transition == "stop":
                change = await rpc(client, "worker_status_update", run_id=run_id, worker_name="writer", status="stopped")
            elif transition == "close":
                change = await rpc(client, "run_update", run_id=run_id, status="failed")
            else:
                worker = app.state.db.get_worker_by_name(run_id, "writer")
                change = await client.post(f"/api/workers/{worker['id']}/status", json={"status": "stopped"})
            assert change.status_code in (200, 202)
            response = await asyncio.wait_for(task, 1)
            assert response.json()["result"] == {"status": expected, "messages": []}
            assert app.state.event_bus.subscriber_count() == 0
    asyncio.run(scenario())


def test_real_read_starts_after_subscription_and_finishes_before_cancellation_cleanup(tmp_path, monkeypatch):
    async def scenario():
        async with running_app(tmp_path) as (app, client, run_id):
            module = server_module()
            read = module._snapshot
            entered = threading.Event()
            release = threading.Event()
            def blocked_read(state, run_id, recipient):
                assert state.event_bus.subscriber_count() == 1
                entered.set()
                assert release.wait(timeout=3)
                return read(state, run_id, recipient)
            monkeypatch.setattr(module, "_snapshot", blocked_read)
            task = asyncio.create_task(module.wait_for_inbox(app.state, run_id, "writer"))
            async with asyncio.timeout(3):
                while not entered.is_set():
                    await asyncio.sleep(0.001)
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done()
            assert app.state.event_bus.subscriber_count() == 1
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert app.state.event_bus.subscriber_count() == 0
    asyncio.run(scenario())


def test_wait_reads_never_write_database_and_sql_errors_remain_errors(tmp_path, monkeypatch):
    async def scenario():
        async with running_app(tmp_path) as (app, client, run_id):
            read_connection = app.state.db.read_connection
            from contextlib import contextmanager
            @contextmanager
            def read_only():
                with read_connection() as connection:
                    connection.execute("PRAGMA query_only=ON")
                    try:
                        yield connection
                    finally:
                        connection.execute("PRAGMA query_only=OFF")
            monkeypatch.setattr(app.state.db, "read_connection", read_only)
            response = await rpc(client, "inbox_wait", run_id=run_id, recipient="writer", timeout_ms=5)
            assert response.json()["result"] == {"status": "timeout", "messages": []}
            monkeypatch.setattr(app.state.db, "read_connection", read_connection)
            with app.state.db.transaction() as connection:
                connection.execute("DROP TABLE inbox_messages")
            response = await rpc(client, "inbox_wait", run_id=run_id, recipient="writer", timeout_ms=0)
            assert response.status_code == 500
            assert response.json()["error_type"] == "OperationalError"
            assert app.state.event_bus.subscriber_count() == 0
    asyncio.run(scenario())


def test_wait_rejects_rebound_recipient_and_cleans_subscription(tmp_path):
    async def scenario():
        async with running_app(tmp_path) as (app, client, run_id):
            app.state.inbox.send(**message_args(run_id))
            # Inject stale persisted binding; supported registration correctly forbids rebinding.
            with app.state.db.transaction() as conn:
                conn.execute("UPDATE workers SET agent_id=? WHERE run_id=? AND name='writer'", ("replacement", run_id))
            response = await rpc(client, "inbox_wait", run_id=run_id, recipient="writer", timeout_ms=0)
            assert response.status_code == 409
            assert "binding" in response.json()["error"]
            assert app.state.event_bus.subscriber_count() == 0
    asyncio.run(scenario())



def test_wait_requires_native_binding_before_subscription_can_deliver(tmp_path):
    async def scenario():
        async with running_app(tmp_path) as (app, client, run_id):
            app.state.db.upsert_worker(run_id, "unbound", role="coder")
            response = await rpc(client, "inbox_wait", run_id=run_id, recipient="unbound", timeout_ms=0)
            assert response.status_code == 409
            assert "binding" in response.json()["error"]
            assert app.state.event_bus.subscriber_count() == 0
            app.state.db.upsert_worker(run_id, "unbound", role="coder", agent_id="new-agent")
            response = await rpc(client, "inbox_wait", run_id=run_id, recipient="unbound", timeout_ms=0)
            assert response.json()["result"] == {"status": "timeout", "messages": []}
    asyncio.run(scenario())
