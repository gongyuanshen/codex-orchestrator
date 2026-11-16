"""Real in-flight HTTP service loss through an unchanged stdio MCP session."""

import asyncio
import hashlib
import json
import socket
import time

import uvicorn

from orchestrator.config import Settings
from prototypes.inbox.client import call, session
from prototypes.inbox.server import make_app


async def start_server(data_dir, port=0):
    sock = socket.socket()
    sock.bind(("127.0.0.1", port))
    port = sock.getsockname()[1]
    assert port not in {18790, 19891}
    app = make_app(Settings(port=port, data_dir=data_dir))
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="error", access_log=False,
        timeout_graceful_shutdown=0,
    ))
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if serving.done():
                    serving.result()
                    raise AssertionError("Test server stopped before startup")
                await asyncio.sleep(.01)
    except BaseException:
        server.should_exit = True
        await asyncio.wait_for(serving, 10)
        sock.close()
        raise
    return app, server, serving, sock, port


async def stop_server(server, serving, sock):
    server.should_exit = True
    try:
        await asyncio.wait_for(serving, 10)
    finally:
        sock.close()


def test_inflight_service_loss_is_error_and_same_mcp_session_recovers(tmp_path):
    async def scenario():
        data_dir = tmp_path / "data"
        app, server, serving, sock, port = await start_server(data_dir)
        url = f"http://127.0.0.1:{port}"
        try:
            async with session(url, data_dir) as sdk:
                run = await call(sdk, "run_create", dict(
                    mode="swarm", title="In-flight service loss", project_root=str(tmp_path), is_git=False,
                ))
                for name in ("writer", "watcher"):
                    await call(sdk, "worker_register", dict(run_id=run["id"], name=name, role="coder"))
                args = dict(run_id=run["id"], sender="coordinator", recipient="writer",
                            key="uncertain-action", kind="task", summary="Reconcile before replay",
                            body_md="One real isolated file write follows acceptance; receipt remains unfinished.")
                sent = await call(sdk, "inbox_send", args)
                mid = sent["message"]["id"]
                accepted = await call(sdk, "inbox_ack", dict(
                    message_id=mid, recipient="writer", status="accepted", evidence="Preparing one file write",
                ))
                effect = tmp_path / "one-action.txt"
                with effect.open("x", encoding="utf-8") as output:
                    output.write(mid + "\n")
                effect_hash = hashlib.sha256(effect.read_bytes()).hexdigest()

                waiting = asyncio.create_task(sdk.call_tool("inbox_wait", dict(
                    run_id=run["id"], recipient="watcher", timeout_ms=25000,
                )))
                async with asyncio.timeout(3):
                    while app.state.event_bus.subscriber_count() != 1:
                        assert not waiting.done(), "Wait completed before loss injection"
                        await asyncio.sleep(.005)
                started = time.monotonic()
                # Shut down only this test's server; zero drain time cancels the real pending request.
                await stop_server(server, serving, sock)
                lost = await asyncio.wait_for(waiting, 5)
                elapsed = time.monotonic() - started
                assert lost.is_error, "Service loss must not be represented as a successful timeout"
                loss_payload = json.loads(lost.content[0].text)
                assert loss_payload != {"status": "timeout", "messages": []}
                assert loss_payload["status_code"] == 500
                assert loss_payload["response_text"] == "Internal Server Error"
                assert elapsed < 5
                assert app.state.event_bus.subscriber_count() == 0
                assert not app.state.inbox_wait_tasks

                restarted, replacement, replacing, replacement_sock, rebound_port = await start_server(data_dir, port)
                try:
                    assert rebound_port == port and restarted is not app
                    # Keep the same initialized MCP client/bridge; only its HTTP service was restarted.
                    recovered = await call(sdk, "inbox_wait", dict(
                        run_id=run["id"], recipient="writer", timeout_ms=0,
                    ))
                    message, = recovered["messages"]
                    assert message["id"] == mid and message["status"] == "accepted"
                    assert message["requires_reconciliation"]
                    assert message["receipts"] == accepted["receipts"]
                    assert effect.read_text(encoding="utf-8").splitlines() == [mid]
                    assert hashlib.sha256(effect.read_bytes()).hexdigest() == effect_hash
                    completed = await call(sdk, "inbox_ack", dict(
                        message_id=mid, recipient="writer", status="completed",
                        evidence=f"Existing effect verified by stable ID/hash {effect_hash}; not repeated",
                    ))
                    duplicate = await call(sdk, "inbox_send", args)
                    assert duplicate["duplicate"] and duplicate["message"]["id"] == mid
                    assert duplicate["message"]["status"] == completed["status"] == "completed"
                    assert await call(sdk, "inbox_wait", dict(
                        run_id=run["id"], recipient="writer", timeout_ms=0,
                    )) == {"status": "timeout", "messages": []}
                    with restarted.state.db.read_connection() as connection:
                        assert connection.execute("SELECT count(*) FROM inbox_messages").fetchone()[0] == 1
                        assert connection.execute("SELECT count(*) FROM board_messages").fetchone()[0] == 1
                    record = dict(
                        verdict="PASS", port=port, real_pending_wait=True,
                        shutdown_to_error_seconds=elapsed,
                        loss_result=lost.model_dump(mode="json", by_alias=True),
                        same_initialized_mcp_session=True, recovered_message=message,
                        side_effect_count=1, side_effect_sha256=effect_hash,
                        replayed=False, terminal_status=completed["status"],
                        old_subscribers=app.state.event_bus.subscriber_count(),
                        old_wait_tasks=len(app.state.inbox_wait_tasks),
                        proof_layer="Real stdio MCP and HTTP; isolated service lifecycle in one test process",
                    )
                finally:
                    await stop_server(replacement, replacing, replacement_sock)
                assert restarted.state.event_bus.subscriber_count() == 0
                assert not restarted.state.inbox_wait_tasks
                record.update(restarted_subscribers=0, restarted_wait_tasks=0)
                print("SERVER_LOSS_EVIDENCE=" + json.dumps(record, ensure_ascii=False))
        finally:
            await stop_server(server, serving, sock)

    asyncio.run(scenario())
