"""Real formal stdio MCP session across cancellation and backend replacement."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest
import uvicorn

from orchestrator.config import Settings
from orchestrator.daemon import create_app


async def _start_server(data_dir, port=0):
    sock = socket.socket()
    sock.bind(("127.0.0.1", port))
    port = sock.getsockname()[1]
    assert port not in {18790, 19890, 19891, 19892, 19893}
    app = create_app(Settings(port=port, data_dir=data_dir), start_watchdog=False)
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
                    raise AssertionError("Owned test server exited before startup")
                await asyncio.sleep(.01)
    except BaseException:
        server.should_exit = True
        await asyncio.wait_for(serving, 10)
        sock.close()
        raise
    return app, server, serving, sock, port


async def _stop_server(server, serving, sock):
    server.should_exit = True
    try:
        await asyncio.wait_for(serving, 10)
    finally:
        sock.close()


def test_formal_mcp_inflight_loss_preserves_error_and_reconciles_without_replay(tmp_path):
    async def scenario():
        data_dir = tmp_path / "state"
        project = tmp_path / "project"
        project.mkdir()
        app, server, serving, sock, port = await _start_server(data_dir)
        source = Path(__file__).resolve().parents[1] / "plugin" / "src"
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
               "PYTHONPATH": str(source), "TRI_ORCH_HOST": "127.0.0.1",
               "TRI_ORCH_PORT": str(port), "TRI_ORCH_DATA_DIR": str(data_dir)}
        params = StdioServerParameters(command=sys.executable,
            args=["-B", "-m", "orchestrator.mcp_shim"], cwd=str(tmp_path), env=env)
        record = None
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=70) as sdk:
                    initialized = await sdk.initialize()
                    assert initialized.server_info.name == "tri-orchestrator"

                    async def call(tool, **arguments):
                        result = await sdk.call_tool(tool, arguments)
                        assert not result.is_error, result.model_dump(mode="json", by_alias=True)
                        text, = [item.text for item in result.content if item.type == "text"]
                        return json.loads(text)

                    tools = await sdk.list_tools()
                    listed = {tool.name for tool in tools.tools}
                    # Core tier: advanced primitives are unlisted but still callable by name below.
                    assert {"inbox_wait", "workflow_prepare_dispatch", "tool_advanced"} <= listed
                    assert not {"inbox_send", "verification_record"} & listed
                    run = await call("run_create", mode="swarm", title="Formal MCP in-flight loss",
                                     project_root=str(project), is_git=False)
                    # Service-loss evidence concerns foundation delivery, not structured work acceptance.
                    app.state.db.connect().execute("UPDATE runs SET workflow_version=1 WHERE id=?", (run["id"],))
                    for name in ("writer", "watcher"):
                        await call("worker_register", run_id=run["id"], name=name, role="coder",
                                   agent_id=f"/root/formal-loss-{name}")
                    arguments = dict(run_id=run["id"], sender="coordinator", recipient="writer",
                                     key="uncertain-action", kind="task", summary="Reconcile one file effect",
                                     body_md="Inspect the persisted effect before acknowledging completion.")
                    sent = await call("inbox_send", **arguments)
                    mid = sent["message"]["id"]
                    accepted = await call("inbox_ack", message_id=mid, recipient="writer", status="accepted",
                                          evidence="Accepted before one isolated file write")
                    effect = project / "one-action.txt"
                    with effect.open("x", encoding="utf-8") as output:
                        output.write(mid + "\n")
                    effect_hash = hashlib.sha256(effect.read_bytes()).hexdigest()

                    waiting = asyncio.create_task(sdk.call_tool("inbox_wait", {
                        "run_id": run["id"], "recipient": "watcher", "timeout_ms": 25000}))
                    async with asyncio.timeout(3):
                        while app.state.event_bus.subscriber_count() != 1:
                            assert not waiting.done(), "Actual wait ended before service-loss injection"
                            await asyncio.sleep(.005)
                    started = time.monotonic()
                    # Only the server created above is stopped; no external process is terminated.
                    await _stop_server(server, serving, sock)
                    lost = await asyncio.wait_for(waiting, 5)
                    elapsed = time.monotonic() - started
                    assert lost.is_error, "Backend cancellation is an MCP error, not a successful timeout"
                    loss_payload = json.loads(lost.content[0].text)
                    assert loss_payload["status_code"] == 500
                    assert loss_payload["error"] == "Internal Server Error"
                    assert elapsed < 5
                    assert app.state.event_bus.subscriber_count() == 0
                    assert not app.state.inbox_wait_tasks

                    # No new MCP call is made while the backend is absent: the formal shim
                    # may legitimately auto-start an absent singleton on a later explicit call.
                    restarted, replacement, replacing, replacement_sock, rebound = await _start_server(data_dir, port)
                    try:
                        assert rebound == port and restarted is not app
                        recovered = await call("inbox_wait", run_id=run["id"], recipient="writer", timeout_ms=0)
                        message, = recovered["messages"]
                        assert recovered["status"] == "messages"
                        assert message["id"] == mid and message["status"] == "accepted"
                        assert message["requires_reconciliation"]
                        assert message["receipts"] == accepted["receipts"]
                        assert message["recipient_agent_id"] == "/root/formal-loss-writer"
                        assert effect.read_text(encoding="utf-8").splitlines() == [mid]
                        assert hashlib.sha256(effect.read_bytes()).hexdigest() == effect_hash
                        completed = await call("inbox_ack", message_id=mid, recipient="writer", status="completed",
                            evidence=f"Verified existing one-line effect SHA256 {effect_hash}; no second execution")
                        duplicate = await call("inbox_send", **arguments)
                        assert duplicate["duplicate"] and duplicate["message"]["id"] == mid
                        assert duplicate["message"]["status"] == completed["status"] == "completed"
                        assert await call("inbox_wait", run_id=run["id"], recipient="writer", timeout_ms=0) == {
                            "status": "timeout", "messages": []}
                        with restarted.state.db.read_connection() as connection:
                            assert connection.execute("SELECT count(*) FROM inbox_messages").fetchone()[0] == 1
                            assert connection.execute("SELECT count(*) FROM board_messages").fetchone()[0] == 1
                        record = {
                            "verdict": "PASS", "proof_layer": "Formal orchestrator.mcp_shim -> real HTTP create_app -> isolated SQLite; no prototype bridge",
                            "port": port, "server_identity": initialized.server_info.model_dump(mode="json"),
                            "tool_count": len(tools.tools), "same_initialized_stdio_session": True,
                            "pending_wait_before_shutdown": True, "shutdown_to_error_seconds": elapsed,
                            "loss_result": lost.model_dump(mode="json", by_alias=True), "recovered_message": message,
                            "side_effect_count": 1, "side_effect_sha256": effect_hash,
                            "business_action_replayed": False, "terminal_message_status": completed["status"],
                            "old_subscribers": 0, "old_wait_tasks": 0,
                            "run_closure": "Not attempted: this transport-only fixture has no independent business acceptance gate",
                        }
                    finally:
                        await _stop_server(replacement, replacing, replacement_sock)
                    assert restarted.state.event_bus.subscriber_count() == 0
                    assert not restarted.state.inbox_wait_tasks
                    record.update(replacement_subscribers=0, replacement_wait_tasks=0)
            assert record is not None
            print("FORMAL_SERVER_LOSS_EVIDENCE=" + json.dumps(record, ensure_ascii=False))
        finally:
            await _stop_server(server, serving, sock)

    asyncio.run(scenario())


def test_timed_out_dispatch_can_be_looked_up_without_replaying(tmp_path, monkeypatch):
    async def scenario():
        project = tmp_path / "source"
        project.mkdir()
        (project / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        app, server, serving, sock, port = await _start_server(tmp_path / "state")
        entered, release = threading.Event(), threading.Event()
        manager = app.state.dispatcher.workspace_mgr
        create = manager.workspace_create
        created = []

        def delayed_create(*args, **kwargs):
            entered.set()
            assert release.wait(10), "Test did not release the pending dispatch"
            workspace = create(*args, **kwargs)
            created.append(workspace["id"])
            return workspace

        monkeypatch.setattr(manager, "workspace_create", delayed_create)
        request = None
        try:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False) as client:
                async def rpc(tool, *, timeout=5, **arguments):
                    return await client.post("/api/rpc", json={"tool": tool, "arguments": arguments}, timeout=timeout)

                run = (await rpc("run_create", mode="boost", title="Delayed dispatch", project_root=str(project))).raise_for_status().json()["result"]
                lookup = dict(run_id=run["id"], sender="coordinator", message_key="first-author:1")
                dispatch = dict(
                    run_id=run["id"], key=lookup["message_key"], create_workspace=True,
                    owner="author", task_name="author", verifier="reviewer", step_key="edit",
                    instruction="Edit app.py", file_ownership={"author": ["app.py"]}, criteria=["AC-1"],
                )
                request = asyncio.create_task(rpc("workflow_prepare_dispatch", timeout=1, **dispatch))
                assert await asyncio.to_thread(entered.wait, 5)
                with pytest.raises(httpx.ReadTimeout):
                    await request
                pending = (await rpc("inbox_get", **lookup)).raise_for_status().json()["result"]
                assert pending["status"] == "not_visible_yet"
                assert created == []

                release.set()
                async with asyncio.timeout(5):
                    while True:
                        recovered = (await rpc("inbox_get", **lookup)).raise_for_status().json()["result"]
                        if recovered["status"] == "found":
                            break
                        await asyncio.sleep(.01)
                message, action, step, workspace = (recovered[k] for k in ("message", "native_action", "step", "workspace"))
                assert message["status"] == "pending" and not message["recipient_agent_id"]
                assert action["target_agent_path"] == "/root/author"
                assert action["status"] == "invoking" and "claim_token" not in action
                assert action["source_id"] == message["id"]
                assert step["id"] == message["step_id"] and step["state"] == "dispatched"
                assert workspace["id"] == step["workspace_id"] and recovered["workspace_recorded"]
                assert Path(workspace["path"]).is_dir()
                assert created == [workspace["id"]]

                conflict = await rpc("workflow_prepare_dispatch", **dispatch)
                assert conflict.status_code == 409 and "inbox_get" in conflict.json()["detail"]
                assert created == [workspace["id"]]
                assert (await rpc("inbox_get", **(lookup | {"sender": "someone_else"}))).json()["result"]["status"] == "not_visible_yet"
                assert (await rpc("inbox_get", message_id=message["id"])).json()["result"] == message
                assert (await rpc("inbox_get", message_id=message["id"], **lookup)).status_code == 400

                # Expire the lease without sleeping. No worker was spawned in the isolated fixture.
                with app.state.db.transaction() as conn:
                    conn.execute("UPDATE native_actions SET lease_expires_at=0 WHERE action_id=?", (action["action_id"],))
                expired = (await rpc("inbox_get", **lookup)).json()["result"]["native_action"]
                assert expired["status"] == "invoking" and expired["lease_expired"]
                reconcile = dict(
                    run_id=run["id"], action_id=action["action_id"], outcome="not_called",
                    reconcile=True, cancel=True, raw_response={"agents": []},
                )
                stale = await rpc("native_action_record_call", expected_revision=expired["revision"], **reconcile)
                assert stale.status_code == 409 and "Stale native action revision" in stale.json()["detail"]
                fenced = (await rpc("inbox_get", **lookup)).json()["result"]["native_action"]
                assert fenced["status"] == "needs_reconciliation" and fenced["revision"] > expired["revision"]
                cancel = await rpc("native_action_record_call", expected_revision=fenced["revision"], **reconcile)
                assert cancel.raise_for_status().json()["result"]["status"] == "cancelled"
                after = (await rpc("inbox_get", **lookup)).json()["result"]
                assert after["message"]["status"] == after["native_action"]["status"] == "cancelled"
                assert after["step"]["state"] == "planned" and after["workspace"]["id"] == workspace["id"]
                assert (await rpc("workflow_prepare_dispatch", **dispatch)).status_code == 409
                assert (await rpc("workspace_cleanup", workspace_id=workspace["id"], force=True)).status_code == 409
                assert Path(workspace["path"]).is_dir()
        finally:
            release.set()
            if request is not None:
                await asyncio.gather(request, return_exceptions=True)
            await _stop_server(server, serving, sock)
    asyncio.run(scenario())
