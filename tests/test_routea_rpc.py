"""Formal backend routes exercise the same catalog and nonblocking inbox contract."""

import asyncio
import time

import httpx

from orchestrator.config import Settings
from orchestrator.daemon import create_app
from orchestrator.rpc_handlers import get_tool_definitions


def test_formal_catalog_and_http_boundary(tmp_path):
    async def check():
        app = create_app(Settings(data_dir=tmp_path / "state"), start_watchdog=False)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                advertised = (await client.get("/api/tools")).json()["tools"]
                names = {x["name"] for x in advertised}
                assert names == {x["name"] for x in get_tool_definitions()}
                assert {"inbox_send", "inbox_get", "inbox_ack", "inbox_wait", "workflow_step_save",
                        "workflow_step_advance", "artifact_capture", "verification_record"} <= names
                rejected = await client.post("/api/rpc", json={"tool": "run_list", "arguments": {"bypass": True}})
                assert rejected.status_code == 400
                assert "Unknown" in rejected.json()["detail"]
                for bad in ([], True, "bad"):
                    rejected = await client.post("/api/rpc", json={"tool": "inbox_wait", "arguments": bad})
                    assert rejected.status_code == 400
                rejected = await client.post("/api/rpc", json={"tool": "inbox_wait", "arguments": {"run_id": "missing"}})
                assert rejected.status_code == 400

    asyncio.run(check())


def test_async_wait_does_not_serialize_send_and_conflicts_are_409(tmp_path):
    async def check():
        app = create_app(Settings(data_dir=tmp_path / "state"), start_watchdog=False)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                async def rpc(tool, **args):
                    response = await client.post("/api/rpc", json={"tool": tool, "arguments": args})
                    assert response.status_code == 200, response.text
                    return response.json()["result"]

                run = await rpc("run_create", mode="swarm", title="formal inbox", project_root=str(tmp_path))
                # Foundation transport fixture; workflow2 business gates have their own full-stack test.
                app.state.db.connect().execute("UPDATE runs SET workflow_version=1 WHERE id=?", (run["id"],))
                await rpc("worker_register", run_id=run["id"], name="worker", role="coder", agent_id="/root/formal-worker")
                waiting = asyncio.create_task(rpc("inbox_wait", run_id=run["id"], recipient="worker", timeout_ms=25000))
                try:
                    async with asyncio.timeout(2):
                        while app.state.event_bus.subscriber_count() == 0:
                            assert not waiting.done()
                            await asyncio.sleep(.005)
                    args = dict(run_id=run["id"], sender="coordinator", recipient="worker", key="formal-delivery",
                                kind="task", summary="actual route", body_md="No external side effect")
                    sent = await rpc("inbox_send", **args)
                    received = await asyncio.wait_for(waiting, 2)
                    assert received["status"] == "messages"
                    assert received["messages"][0]["id"] == sent["message"]["id"]
                    repeat = await rpc("inbox_send", **args)
                    assert repeat["duplicate"] is True
                    conflict = await client.post("/api/rpc", json={"tool": "inbox_send", "arguments": {**args, "body_md": "Changed"}})
                    assert conflict.status_code == 409
                    mid = sent["message"]["id"]
                    await rpc("inbox_ack", message_id=mid, recipient="worker", status="accepted", evidence="Read actual message")
                    done = await rpc("inbox_ack", message_id=mid, recipient="worker", status="completed", evidence="Transport checked")
                    assert done["status"] == "completed"
                    assert app.state.event_bus.subscriber_count() == 0
                    assert not app.state.inbox_wait_tasks
                finally:
                    waiting.cancel()
                    await asyncio.gather(waiting, return_exceptions=True)

    asyncio.run(check())


def test_workflow_tools_preserve_gates_and_emit_after_commit(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "code.txt").write_text("actual artifact", encoding="utf-8")
    evidence = tmp_path / "verification.txt"

    async def check():
        app = create_app(Settings(data_dir=tmp_path / "state"), start_watchdog=False)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                async def rpc(tool, **args):
                    response = await client.post("/api/rpc", json={"tool": tool, "arguments": args})
                    assert response.status_code == 200, response.text
                    return response.json()["result"]

                run = await rpc("run_create", mode="swarm", title="formal gates", project_root=str(project))
                rid = run["id"]
                await rpc("task_packet_save", run_id=rid, title="content", goal="Verify actual file content",
                          acceptance_criteria=["content matches"], file_ownership={"writer": ["code.txt"]})
                await rpc("worker_register", run_id=rid, name="writer", role="coder", agent_id="/root/writer")
                await rpc("worker_register", run_id=rid, name="gate", role="verifier", agent_id="/root/gate")
                subscription, events = app.state.event_bus.subscribe(run_id=rid)
                try:
                    step = await rpc("workflow_step_save", run_id=rid, step_key="content", owner="writer", verifier="gate")
                    assert step["revision"] == 0
                    first = events.get_nowait()
                    assert first["event"] == "workflow_updated"
                    assert app.state.workflow.get_step(step["id"]) == step
                    rejected = await client.post("/api/rpc", json={"tool": "workflow_step_advance", "arguments": {
                        "step_id": step["id"], "action": "dispatch", "expected_revision": 999}})
                    assert rejected.status_code == 409
                    observation = {"agent_id": "/root/writer", "state": "idle", "observed_at": time.time(), "evidence": "Actual unit-test fixture observation"}
                    step = await rpc("workflow_step_advance", step_id=step["id"], action="observe", expected_revision=0, observation=observation)
                    assert step["checkpoint"]["native_observation"] == observation
                    for action in ("dispatch", "accept", "submit"):
                        step = await rpc("workflow_step_advance", step_id=step["id"], action=action, expected_revision=step["revision"])
                    candidate = await rpc("artifact_capture", step_id=step["id"], scope="candidate")
                    assert (project / "code.txt").read_text(encoding="utf-8") == "actual artifact"
                    evidence.write_text("Asserted code.txt equals actual artifact", encoding="utf-8")
                    candidate_gate = await rpc("verification_record", step_id=step["id"], artifact_id=candidate["id"], verifier="gate", verdict="PASS",
                                              criteria_results=[{"criterion": "content matches", "status": "PASS"}], evidence_files=[str(evidence)], key="candidate-1")
                    assert candidate_gate["verdict"] == "PASS"
                    step = app.state.workflow.get_step(step["id"])
                    step = await rpc("workflow_step_advance", step_id=step["id"], action="begin_integrated_review", expected_revision=step["revision"])
                    artifact = await rpc("artifact_capture", step_id=step["id"], scope="integrated")
                    assert artifact["scope"] == "integrated"
                    await rpc("verification_record", step_id=step["id"], artifact_id=artifact["id"], verifier="gate", verdict="PASS",
                              criteria_results=[{"criterion": "content matches", "status": "PASS"}], evidence_files=[str(evidence)], key="integrated-1")
                    step = app.state.workflow.get_step(step["id"])
                    step = await rpc("workflow_step_advance", step_id=step["id"], action="complete", expected_revision=step["revision"])
                    assert step["state"] == "done"
                    done = await rpc("run_update", run_id=rid, status="completed")
                    assert done["status"] == "completed"
                    snapshot = (await client.get(f"/api/runs/{rid}/snapshot")).json()
                    assert snapshot["workflow"]["steps"][0]["state"] == "done"
                    assert snapshot["inbox"]["blocking_count"] == 0
                    exported = await client.get(f"/api/runs/{rid}/export")
                    assert exported.status_code == 200
                    assert "结构化验收门禁" in exported.text
                finally:
                    app.state.event_bus.unsubscribe(subscription)

    asyncio.run(check())
