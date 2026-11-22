"""S3 dashboard APIs expose canonical projects, runs, and session events."""
import asyncio
import json
import time
from itertools import count
from types import SimpleNamespace

import httpx
import pytest
from starlette.testclient import TestClient

from orchestrator import codex_bridge, db as db_module
from orchestrator.config import Settings
from orchestrator.daemon import create_app
from orchestrator.db import Database

from test_daemon_web import _RunningServer, _free_port


@pytest.fixture
def dashboard(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "unused-codex-home"))
    # SQLite compares timestamps across tables; Windows can put successive writes in one tick.
    clock = count(1_800_000_000)
    monkeypatch.setattr(db_module, "time", SimpleNamespace(time=lambda: next(clock), sleep=time.sleep))
    root = tmp_path / "main"
    slice_root = root / "docs" / "verification" / "s3" / "fixture"
    other_root = tmp_path / "other"
    slice_root.mkdir(parents=True)
    settings = Settings(data_dir=tmp_path / "data")
    with Database(settings=settings) as db:
        # Preserve a run that was originally registered against the child.
        child = db.ensure_project(slice_root, name="Fixture")
        with db.transaction() as conn:
            conn.execute("UPDATE projects SET project_kind = 'fixture' WHERE id = ?", (child["id"],))
        old = db.create_run(project_id=child["id"], mode="swarm", title="Old slice", session_id="thread-1")
        parent = db.ensure_project(root, name="Local label", codex_project_name="Codex Main", is_git=True)
        direct = db.create_run(
            project_id=parent["id"], mode="boost", title="Direct run", session_id="thread-1",
            turn_id="trigger", trigger_prompt="actual prompt", execution_root=str(slice_root),
        )
        legacy = db.create_run(project_id=parent["id"], mode="teamwork", title="Unbound history")
        db.update_run(legacy["id"], status="archived")
        other = db.create_run(project_root=other_root, mode="teamwork", title="Other project", session_id="thread-2")
        for run in (old, direct, other):
            db.create_workspace(run["id"], "worker", path=tmp_path / run["id"], mode="snapshot")
        db.save_task_packet(
            direct["id"], title="Write", goal="Write", file_ownership={"coordinator": ["file.py"]},
            acceptance_criteria=["Write"],
        )
        app = create_app(settings, db=db, start_watchdog=False)
        with TestClient(app) as client:
            app.state.board.worker_register(direct["id"], "coordinator", "coder", agent_id="/root")
            app.state.board.worker_register(direct["id"], "reviewer", "verifier", agent_id="/root/reviewer")
            app.state.workflow.step_save(
                direct["id"], step_key="local", owner="coordinator", verifier="reviewer",
                execution_kind="coordinator_local",
            )
            direct = db.get_run(direct["id"])
            monkeypatch.setattr(
                codex_bridge, "get_threads_meta",
                lambda ids: {"thread-1": {"title": "Codex thread", "codex_project_name": "Codex Main"}}
                if "thread-1" in ids else {},
            )
            yield client, db, (parent, child, old, direct, legacy, other)


def test_projects_show_grouped_statistics_and_preserve_merged_rows(dashboard):
    client, db, (parent, child, *_runs) = dashboard
    projects = {row["id"]: row for row in client.get("/api/projects").json()["projects"]}
    assert {key: projects[parent["id"]][key] for key in parent} == parent
    assert projects[child["id"]]["canonical_project_id"] == parent["id"]
    assert projects[child["id"]]["project_kind"] == "fixture"
    assert projects[parent["id"]]["display_name"] == "Codex Main"
    for project_id in (parent["id"], child["id"]):
        counts = projects[project_id]
        assert (counts["active_run_count"], counts["active_session_count"], counts["total_run_count"]) == (2, 1, 3)
    assert client.get("/api/projects?include_merged=1").json()["projects"] == list(projects.values())
    assert child["id"] not in {
        row["id"] for row in client.get("/api/projects?include_merged=0").json()["projects"]
    }


def test_run_list_and_snapshot_enrich_only_dashboard_routes(dashboard, monkeypatch):
    client, db, (parent, child, old, direct, legacy, other) = dashboard
    rows = client.get("/api/runs", params={"project_id": parent["id"], "mode": "all"}).json()["runs"]
    assert {row["id"] for row in rows} == {old["id"], direct["id"], legacy["id"]}
    [boost] = client.get(
        "/api/runs", params={"project_id": parent["id"], "mode": "boost", "limit": 1},
    ).json()["runs"]
    assert boost["id"] == direct["id"]
    assert {key: boost[key] for key in direct} == direct
    assert (boost["codex_thread_name"], boost["codex_project_name"], boost["session_display_name"]) == (
        "Codex thread", "Codex Main", "Codex thread",
    )
    assert [row["id"] for row in client.get(f"/api/runs?project_id={child['id']}").json()["runs"]] == [old["id"]]
    for row in rows:
        assert row["canonical_project_id"] == parent["id"]
        assert row["concurrent_project_active_count"] == 2
        assert row["concurrent_local_write_risk"] is True
    assert client.get(f"/api/runs/{direct['id']}").json()["run"] == direct

    trigger = db.upsert_session_turn("thread-1", "trigger", cwd=boost["execution_root"],
                                     prompt_preview="actual prompt", active_run_id=old["id"])
    follow = db.upsert_session_turn("thread-1", "follow", cwd=boost["execution_root"],
                                    prompt_preview="continue", active_run_id=direct["id"])
    continuation = db.upsert_session_turn("thread-1", "window", cwd=boost["execution_root"],
                                          prompt_preview="continue within the run", active_run_id=direct["id"])
    db.upsert_session_turn("thread-1", "earlier-run", cwd=str(parent["root_path"]),
                           prompt_preview="old scope", active_run_id=old["id"])
    db.upsert_session_turn("thread-2", "outside-session", cwd=str(parent["root_path"]),
                           prompt_preview="private")
    next_run = db.create_run(project_id=parent["id"], mode="swarm", title="Next run", session_id="thread-1")
    with monkeypatch.context() as fixed_tick:
        fixed_tick.setattr(db_module.time, "time", lambda: next_run["created_at"])
        next_turn = db.upsert_session_turn("thread-1", "next-window", cwd=boost["execution_root"],
                                           prompt_preview="exactly at the next run boundary")
    snap = client.get(f"/api/runs/{direct['id']}/snapshot").json()
    assert snap["run"]["concurrent_project_active_count"] == 3
    assert {**snap["run"], "concurrent_project_active_count": 2} == boost
    assert snap["session_turns"] == [trigger, follow, continuation]
    assert db.list_run_session_turns(next_run) == []
    assert client.get(f"/api/runs/{legacy['id']}/snapshot").json()["session_turns"] == []


def test_sessions_filter_groups_but_keep_global_activity_and_risk(dashboard):
    client, db, (parent, child, old, direct, legacy, other) = dashboard
    response = client.get("/api/sessions")
    assert response.status_code == 200
    summary = response.json()
    groups = {(row["canonical_project_id"], row["session_id"]): row for row in summary["sessions"]}
    group = groups[parent["id"], "thread-1"]
    assert group["project_id"] == parent["id"]
    assert group["active_count"] == 2
    assert group["session_display_name"] == group["codex_thread_name"] == "Codex thread"
    assert [row["id"] for row in group["runs"]] == [direct["id"], old["id"]]
    assert groups[other["project_id"], "thread-2"]["session_display_name"] == other["title"]
    [archived] = groups[parent["id"], "unbound"]["runs"]
    assert (archived["id"], archived["session_id"]) == (legacy["id"], None)
    assert group["runs"][1]["project_id"] == child["id"]

    filtered = client.get(
        "/api/sessions", params={"project_id": parent["id"], "mode": "boost", "status": "active"},
    ).json()
    [only] = filtered["sessions"]
    assert [row["id"] for row in only["runs"]] == [direct["id"]]
    assert only["active_count"] == 1
    assert {row["id"] for row in filtered["global_active_runs"]} == {old["id"], direct["id"], other["id"]}
    assert filtered["global_active_runs"] == summary["global_active_runs"]
    [risk] = filtered["concurrent_risks"]
    assert risk["canonical_project_id"] == parent["id"]
    assert risk["concurrent_project_active_count"] == 2
    assert risk["concurrent_local_write_risk"] is True
    assert client.get("/api/sessions?project_id=all&status=all&mode=all").json() == summary


def test_sessions_order_by_activity_and_recency_then_run_sequence(dashboard):
    client, db, (_parent, _child, old, direct, legacy, other) = dashboard
    # An older sequence can have the latest activity; it moves the group up, not the run.
    db.update_run(old["id"], title="Touched older run")
    db.update_run(legacy["id"], title="Most recently edited, but not active")
    groups = client.get("/api/sessions").json()["sessions"]
    assert [group["session_id"] for group in groups] == ["thread-1", "thread-2", "unbound"]
    assert [run["id"] for run in groups[0]["runs"]] == [direct["id"], old["id"]]
    db.update_run(other["id"], title="New most recent active group")
    assert [group["session_id"] for group in client.get("/api/sessions").json()["sessions"]] == [
        "thread-2", "thread-1", "unbound",
    ]


def test_isolated_concurrency_and_runs_without_isolation(dashboard):
    client, db, (*_, other) = dashboard
    sibling = db.create_run(project_id=other["project_id"], mode="swarm", title="More work", session_id="thread-3")
    run_id = sibling["id"]
    rows = client.get("/api/runs", params={"project_id": other["project_id"], "mode": "all"}).json()["runs"]
    assert len(rows) == 2
    assert all(row["concurrent_local_write_risk"] is True for row in rows)
    db.create_workspace(run_id, "worker", path=db.settings.data_dir / run_id, mode="snapshot")
    rows = client.get("/api/runs", params={"project_id": other["project_id"], "mode": "all"}).json()["runs"]
    assert all(row["concurrent_local_write_risk"] is False for row in rows)


def test_trigger_moves_from_provisional_run_without_adopting_unlinked_turns(dashboard, tmp_path):
    client, db, (parent, *_runs) = dashboard
    root = parent["root_path"]

    def rpc(tool, **arguments):
        return client.post("/api/rpc", json={"tool": tool, "arguments": arguments}).raise_for_status().json()["result"]

    def create(**arguments):
        return rpc("run_create", mode="swarm", title="Run", project_id=parent["id"], session_id="same", **arguments)

    def hook(turn_id, cwd=root, invoked=False):
        rpc("session_hook_check", event_name="UserPromptSubmit", session_id="same", turn_id=turn_id,
            cwd=cwd, prompt_preview=f"prompt {turn_id}", has_mode_invocation=invoked)

    def turns(run):
        data = client.get(f"/api/runs/{run['id']}/snapshot").raise_for_status().json()
        return [row["turn_id"] for row in data["session_turns"]]

    old = create()
    hook("unlinked", str(tmp_path / "unrelated"))
    hook("invoke", invoked=True)
    hook("continue")
    new = create(turn_id="invoke")
    assert new["trigger_prompt"] == "prompt invoke"
    assert turns(old) == ["continue"]
    assert turns(new) == ["invoke"]
    later = create(turn_id="unknown")
    assert later["trigger_prompt"] == ""
    hook("invoke", invoked=True)
    hook("continue")
    assert turns(old) == ["continue"]
    assert turns(later) == []
    assert db.list_run_session_turns(new)[0]["active_run_id"] == new["id"]


def test_sse_global_and_run_scoped_lifecycle_events(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "unused-codex-home"))

    async def scenario(base_url):
        async with httpx.AsyncClient(base_url=base_url, trust_env=False, timeout=5) as client:
            async def rpc(tool, **arguments):
                response = await client.post("/api/rpc", json={"tool": tool, "arguments": arguments})
                response.raise_for_status()
                return response.json()["result"]

            async def read_lifecycle(lines, *, event_count):
                seen = []
                async for line in lines:
                    if line.startswith("event: "):
                        event = line.removeprefix("event: ")
                    elif line.startswith("data: "):
                        data = json.loads(line.removeprefix("data: "))
                        if event == "run_updated" and "run" in data:
                            seen.append(("run_updated", data["run"]["title"], "trigger_prompt" in data["run"]))
                        if event == "session_updated":
                            assert set(data) <= {"run_id", "session_id", "turn_id"}
                            seen.append(("session_updated", data["session_id"], data.get("turn_id")))
                        if len(seen) == event_count:
                            return seen
                raise AssertionError("SSE stream ended before lifecycle events arrived")

            async with client.stream("GET", "/api/events") as global_stream:
                global_stream.raise_for_status()
                created = await rpc("run_create", title="Initial", mode="boost", project_root=str(tmp_path),
                                    trigger_prompt="private prompt", execution_root=str(tmp_path))
                rid = created["id"]
                async with client.stream("GET", "/api/events", params={"run_id": rid}) as scoped_stream:
                    scoped_stream.raise_for_status()
                    await rpc("run_update", run_id=rid, title="Renamed")
                    await rpc("session_bind", run_id=rid, session_id="bound")
                    await rpc("session_hook_check", session_id="new-thread", event_name="UserPromptSubmit",
                              cwd=str(tmp_path), turn_id="unscoped", prompt_preview="start", has_mode_invocation=True)
                    await rpc("session_hook_check", session_id="bound", event_name="UserPromptSubmit",
                              cwd=str(tmp_path), turn_id="turn-1", prompt_preview="hi", has_mode_invocation=False)
                    await rpc("task_packet_save", run_id=rid, title="Write", goal="Write",
                              file_ownership={"coordinator": ["target.py"]}, acceptance_criteria=["Write"])
                    await rpc("worker_register", run_id=rid, name="coordinator", role="coder", agent_id="/root")
                    await rpc("worker_register", run_id=rid, name="reviewer", role="verifier", agent_id="/root/reviewer")
                    await rpc("workflow_step_save", run_id=rid, step_key="local", owner="coordinator",
                              verifier="reviewer", execution_kind="coordinator_local")
                    unrelated = ("session_updated", "new-thread", "unscoped")
                    expected = [
                        ("run_updated", "Renamed", True), ("session_updated", "bound", None),
                        ("session_updated", "bound", "turn-1"), ("session_updated", "bound", None),
                    ]
                    assert await asyncio.wait_for(read_lifecycle(scoped_stream.aiter_lines(), event_count=4), 2) == expected
                    assert await asyncio.wait_for(read_lifecycle(global_stream.aiter_lines(), event_count=6), 2) == [
                        ("run_updated", "Initial", False), ("run_updated", "Renamed", False),
                        expected[1], unrelated, *expected[2:],
                    ]

    settings = Settings(host="127.0.0.1", port=_free_port(), data_dir=tmp_path / "data")
    with _RunningServer(settings):
        asyncio.run(scenario(settings.base_url))
