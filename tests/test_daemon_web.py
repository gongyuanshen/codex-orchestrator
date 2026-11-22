"""Integration tests for Daemon HTTP/SSE routes, RPC handlers, Watchdog, and Safety Guards."""

from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import httpx
import pytest
import uvicorn

from orchestrator.board import BoardService
from orchestrator.config import Settings
from orchestrator.daemon import build_markdown_report, create_app
from orchestrator.db import Database
from orchestrator.rpc_handlers import TOOL_DEFINITIONS
from orchestrator.watchdog import StallWatchdog
from orchestrator.workspace_mgr import WorkspaceManager


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _init_git_repo(repo_dir: Path) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Tester"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "tester@example.com"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    (repo_dir / "README.md").write_text("# Demo Repo\nline 1\n", encoding="utf-8")
    (repo_dir / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial commit"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )


class _RunningServer:
    """Runs the Starlette daemon app on a real Uvicorn TCP socket in a background thread."""

    def __init__(
        self,
        settings: Settings,
        *,
        start_watchdog: bool = False,
        keepalive_sec: float = 0.4,
    ) -> None:
        self.settings = settings
        self.app = create_app(
            settings,
            start_watchdog=start_watchdog,
            keepalive_sec=keepalive_sec,
            manage_pid_file=True,
        )
        self.config = uvicorn.Config(
            self.app,
            host=settings.host,
            port=settings.port,
            log_config=None,
            access_log=False,
            log_level="warning",
        )
        self.server = uvicorn.Server(self.config)
        self._thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> _RunningServer:
        self._thread.start()
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            try:
                with httpx.Client(trust_env=False, timeout=0.5) as client:
                    resp = client.get(f"{self.settings.base_url}/api/health")
                    if resp.status_code == 200:
                        return self
            except Exception:
                pass
            time.sleep(0.05)
        raise RuntimeError("Test Uvicorn daemon failed to start within 10s")

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.server.should_exit = True
        self._thread.join(timeout=5.0)


@pytest.fixture()
def daemon_env(tmp_path: Path) -> Iterator[tuple[Settings, _RunningServer, httpx.Client]]:
    port = _free_port()
    data_dir = tmp_path / "data"
    settings = Settings(
        host="127.0.0.1",
        port=port,
        data_dir=data_dir,
        stall_sec=10,
        watchdog_interval_sec=0.2,
    )
    with _RunningServer(settings, start_watchdog=False, keepalive_sec=0.3) as srv:
        with httpx.Client(
            base_url=settings.base_url,
            trust_env=False,
            timeout=10.0,
        ) as client:
            yield settings, srv, client


def test_collector_heartbeat_cannot_reopen_finished_worker(daemon_env, tmp_path, monkeypatch):
    _, server, client = daemon_env
    db = server.app.state.db
    run = db.create_run(project_root=tmp_path, is_git=False, mode='swarm', title='Keepalive')
    worker = db.upsert_worker(run['id'], 'verifier', role='verifier', status='working', heartbeat_at=1)
    url = f"/api/runs/{run['id']}/workers/verifier/heartbeat"
    response = client.post(url, json={'current_task': 'running check'})
    assert response.status_code == 200
    assert response.json()['worker']['current_task'] == 'running check'
    assert response.json()['worker']['last_heartbeat_at'] > worker['last_heartbeat_at']
    write_heartbeat = db.update_worker_heartbeat

    def complete_before_heartbeat_write(**kwargs):
        db.update_worker_status(worker_id=worker['id'], status='waiting', current_task='verified')
        return write_heartbeat(**kwargs)

    monkeypatch.setattr(db, 'update_worker_heartbeat', complete_before_heartbeat_write)
    late = client.post(url, json={'current_task': 'late check'})
    assert late.status_code == 409
    after = db.get_worker_by_name(run['id'], 'verifier')
    assert (after['status'], after['current_task']) == ('waiting', 'verified')
    assert after['last_heartbeat_at'] == response.json()['worker']['last_heartbeat_at']


@pytest.mark.parametrize("workspace_state", ["active", "merged", "cleaned"])
def test_summary_snapshot_does_not_scan_disk(daemon_env, tmp_path, monkeypatch, workspace_state):
    _, server, client = daemon_env
    db = server.app.state.db
    run = db.create_run(project_root=tmp_path, is_git=False, mode="swarm", title="Summary")
    ws = db.create_workspace(run["id"], "coder", path=tmp_path / "absent-workspace",
                             mode="snapshot", status=workspace_state,
                             snapshot_manifest={"files": {"large.txt": "a" * 64}})
    def unexpected_scan(workspace_id):
        raise AssertionError("Summary must not inspect workspace files")
    monkeypatch.setattr(server.app.state.workspace_mgr, "workspace_status", unexpected_scan)
    response = client.get(f"/api/runs/{run['id']}/snapshot")
    assert response.status_code == 200, response.text
    summary = response.json()["workspaces"][0]
    assert summary["id"] == ws["id"]
    assert "unmerged_files_count" not in summary
    assert "snapshot_manifest" not in summary
    assert "snapshot_manifest_json" not in summary


def test_static_web_assets_and_health(
    daemon_env: tuple[Settings, _RunningServer, httpx.Client],
) -> None:
    settings, _, client = daemon_env

    health = client.get("/api/health")
    assert health.status_code == 200
    hdata = health.json()
    assert hdata["status"] == "ok"
    assert hdata["ok"] is True
    assert hdata["port"] == settings.port
    assert settings.pid_path.exists()

    idx = client.get("/")
    assert idx.status_code == 200
    assert "text/html" in idx.headers["content-type"]
    assert re.search(r'<html\b[^>]*\blang="zh-CN"', idx.text)
    assert "/styles.css" in idx.text
    assert "/app.js" in idx.text
    assert "http://" not in idx.text and "https://" not in idx.text

    css = client.get("/styles.css")
    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]

    js = client.get("/app.js")
    assert js.status_code == 200
    assert "javascript" in js.headers["content-type"]

    tools_resp = client.get("/api/tools")
    assert tools_resp.status_code == 200
    tools = tools_resp.json()["tools"]
    assert len(tools) == len(TOOL_DEFINITIONS)
    assert {t["name"] for t in tools} == {t["name"] for t in TOOL_DEFINITIONS}


def test_mode_entry_routes_share_dashboard_and_preserve_unknown_path_404(daemon_env):
    _, _, client = daemon_env
    root = client.get("/")
    assert re.search(r'<a\b[^>]*\bid="brandHomeLink"[^>]*\bhref="/"', root.text)
    for mode in ("boost", "teamwork", "swarm"):
        response = client.get(f"/{mode}", params={"project_id": "p", "run_id": "r"})
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-cache"
        assert "text/html" in response.headers["content-type"]
        assert response.content == root.content
    assert client.get("/unknown-mode").status_code == 404


def test_exact_run_metadata_is_unpaged_and_does_not_load_run_details(daemon_env, tmp_path, monkeypatch):
    _, server, client = daemon_env
    db = server.app.state.db
    oldest = db.create_run(project_root=tmp_path, is_git=False, mode="boost", title="Old explicit link")
    for index in range(101):
        db.create_run(project_id=oldest["project_id"], mode="boost", title=f"Newer {index}")
    recent = client.get("/api/runs", params={"project_id": oldest["project_id"]}).json()["runs"]
    assert len(recent) == 100
    assert oldest["id"] not in {run["id"] for run in recent}

    def unexpected_details(*args, **kwargs):
        raise AssertionError("Exact metadata must not load messages, members, workspaces, or disk state")

    for method in ("list_runs", "query_board_messages", "list_workers", "list_workspaces", "list_task_packets"):
        monkeypatch.setattr(db, method, unexpected_details)
    monkeypatch.setattr(server.app.state.board, "board_status", unexpected_details)
    monkeypatch.setattr(server.app.state.workspace_mgr, "workspace_status", unexpected_details)
    response = client.get(f"/api/runs/{oldest['id']}")
    assert response.status_code == 200, response.text
    data = response.json()
    assert set(data) == {"run", "project"}
    assert data["run"] == oldest
    assert data["project"]["id"] == oldest["project_id"]
    assert data["project"]["root_path"] == str(tmp_path.resolve())

    missing = client.get("/api/runs/run_does_not_exist")
    assert missing.status_code == 404
    assert missing.json()["error_type"] == "NotFoundError"


def test_run_list_mode_and_project_filters_apply_before_limit(daemon_env, tmp_path):
    _, server, client = daemon_env
    db = server.app.state.db
    runs = {}
    for mode in ("boost", "teamwork", "swarm"):
        runs[mode] = db.create_run(project_root=tmp_path / "primary", is_git=False, mode=mode, title=mode)
    for mode in runs:
        db.create_run(project_root=tmp_path / "other", is_git=False, mode=mode, title=f"Other {mode}")
    for mode, expected in runs.items():
        response = client.get("/api/runs", params={"project_id": expected["project_id"], "mode": mode, "limit": 1})
        assert response.status_code == 200, response.text
        [actual] = response.json()["runs"]
        assert {key: actual[key] for key in expected} == expected
        assert set(actual) - expected.keys() == {
            "canonical_project_id", "concurrent_project_active_count", "concurrent_local_write_risk",
            "codex_thread_name", "codex_project_name", "session_display_name",
            "codex_model", "codex_reasoning_effort", "codex_model_provider",
        }
        active = client.get("/api/runs", params={"mode": mode, "status": "active", "limit": 1}).json()["runs"]
        assert len(active) == 1
        assert active[0]["mode"] == mode
        assert active[0]["title"] == f"Other {mode}"


def test_rpc_full_workflow_snapshot_diff_safe_actions_and_export(
    daemon_env: tuple[Settings, _RunningServer, httpx.Client],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, legacy_server, client = daemon_env

    repo_dir = tmp_path / "repo_git"
    _init_git_repo(repo_dir)

    # 1. Create run via /api/rpc
    r_create = client.post(
        "/api/rpc",
        json={
            "tool": "run_create",
            "arguments": {
                "mode": "swarm",
                "title": "Daemon Integration Run",
                "project_root": str(repo_dir),
            },
        },
    )
    assert r_create.status_code == 200, r_create.text
    run = r_create.json()["result"]
    run_id = run["id"]
    # This scenario preserves migrated-v0 CRUD/merge behavior; new v1 gates are separate.
    with legacy_server.app.state.db.transaction() as legacy:
        legacy.execute("UPDATE runs SET workflow_version=0 WHERE id=?", (run_id,))
    project_id = run["project_id"]

    # Check /api/projects and /api/runs
    projects = client.get("/api/projects").json()["projects"]
    assert any(p["id"] == project_id and p["is_git"] is True for p in projects)

    runs = client.get(f"/api/runs?project_id={project_id}").json()["runs"]
    assert [r["id"] for r in runs] == [run_id]

    # 2. Save & get task packet
    pkt_save = client.post(
        "/api/rpc",
        json={
            "tool": "task_packet_save",
            "arguments": {
                "run_id": run_id,
                "title": "Build Feature X",
                "goal": "Implement X cleanly with tests",
                "file_ownership": {"coder-1": ["app.py"]},
                "acceptance_criteria": ["VALUE updated", "Tests green"],
            },
        },
    )
    assert pkt_save.status_code == 200
    assert pkt_save.json()["result"]["version"] == 1

    pkt_get = client.post(
        "/api/rpc",
        json={"tool": "task_packet_get", "arguments": {"run_id": run_id}},
    )
    assert pkt_get.status_code == 200
    assert pkt_get.json()["result"]["goal"] == "Implement X cleanly with tests"

    # 3. Register worker + create workspace + heartbeat
    w_reg = client.post(
        "/api/rpc",
        json={
            "tool": "worker_register",
            "arguments": {
                "run_id": run_id,
                "name": "coder-1",
                "role": "coder",
                "status": "working",
                "current_task": "Edit app.py",
                "agent_id": "/root/coder-1",
            },
        },
    )
    assert w_reg.status_code == 200
    worker = w_reg.json()["result"]
    worker_id = worker["id"]

    ws_create = client.post(
        "/api/rpc",
        json={
            "tool": "workspace_create",
            "arguments": {
                "run_id": run_id,
                "worker_name": "coder-1",
                "copy_env": True,
                "dep_mode": "none",
            },
        },
    )
    assert ws_create.status_code == 200
    ws = ws_create.json()["result"]
    ws_id = ws["id"]
    ws_path = Path(ws["path"])
    assert ws_path.is_dir()

    # Edit file in workspace without committing yet (dirty)
    (ws_path / "app.py").write_text("VALUE = 42\n", encoding="utf-8")

    diff_resp = client.get(f"/api/workspaces/{ws_id}/diff")
    assert diff_resp.status_code == 200
    diff_data = diff_resp.json()
    assert "app.py" in diff_data["files"]
    assert "+VALUE = 42" in diff_data["diff"]
    assert diff_data["status_files"] == [{"path": "app.py", "status": "M"}]

    # 4. Human message posting
    bad_human = client.post(
        f"/api/runs/{run_id}/human-message",
        json={"channel": "human", "body_md": "   "},
    )
    assert bad_human.status_code == 400

    good_human = client.post(
        f"/api/runs/{run_id}/human-message",
        json={
            "channel": "human",
            "body_md": "@coder-1 please commit when ready",
            "mentions": ["coder-1"],
        },
    )
    assert good_human.status_code == 200
    hmsg = good_human.json()["message"]
    assert hmsg["sender"] == "human"
    assert hmsg["msg_type"] == "human"
    assert "coder-1" in hmsg["mentions"]

    # 5. Snapshot hydration
    snap_resp = client.get(f"/api/runs/{run_id}/snapshot")
    assert snap_resp.status_code == 200
    snap = snap_resp.json()
    assert snap["run"]["id"] == run_id
    assert snap["task_packet"]["version"] == 1
    assert len(snap["workers"]) == 1
    assert snap["workers"][0]["assigned_port"] == ws["assigned_port"]
    # Summary requests do not inspect disk; the explicit diff above owns that result.
    assert "unmerged_files_count" not in snap["workers"][0]
    assert len(snap["messages"]) == 1

    # 6. Safety guards: cleanup MUST return HTTP 409 when unmerged (even with force=true)
    cleanup_unmerged = client.post(
        f"/api/workspaces/{ws_id}/cleanup",
        json={"force": True},
    )
    assert cleanup_unmerged.status_code == 409
    assert ws_path.exists()

    # Forbidden dangerous routes MUST return HTTP 403
    for method, route in [
        ("POST", "/api/git/push"),
        ("POST", "/api/git/reset"),
        ("POST", f"/api/workspaces/{ws_id}/push"),
        ("POST", f"/api/workspaces/{ws_id}/reset"),
        ("POST", f"/api/workspaces/{ws_id}/force-delete"),
        ("DELETE", f"/api/workspaces/{ws_id}"),
    ]:
        resp = client.request(method, route)
        assert resp.status_code == 403, f"Expected 403 for {method} {route}, got {resp.status_code}"

    # Forbidden worker status override values
    bad_status = client.post(
        f"/api/workers/{worker_id}/status",
        json={"status": "working"},
    )
    assert bad_status.status_code == 400

    # Allowed worker status override ('stopped') preserves workspace files
    ok_status = client.post(
        f"/api/workers/{worker_id}/status",
        json={"status": "stopped"},
    )
    assert ok_status.status_code == 202
    control = ok_status.json()["control_request"]
    assert control["status"] == "pending"
    snapshot = client.get(f"/api/runs/{run_id}/snapshot").json()
    assert snapshot["workers"][0]["status"] == "working"
    assert snapshot["control_requests"][0]["id"] == control["id"]
    acknowledgement = client.post("/api/rpc", json={
        "tool": "worker_control_ack", "arguments": {
            "request_id": control["id"], "outcome": "applied",
            "result": "Test native-tool boundary: interrupt returned idle",
        },
    })
    assert acknowledgement.status_code == 200, acknowledgement.text
    assert client.get(f"/api/runs/{run_id}/snapshot").json()["workers"][0]["status"] == "stopped"
    assert (ws_path / "app.py").exists()

    # Open Explorer validation & mocked launch
    launched_paths: list[Path] = []
    async def launch_explorer(path, _opener_tasks):
        launched_paths.append(path)
        return True

    monkeypatch.setattr("orchestrator.daemon._launch_explorer", launch_explorer)
    exp_resp = client.post(f"/api/workspaces/{ws_id}/open-explorer", json={})
    assert exp_resp.status_code == 200
    assert launched_paths == [ws_path.resolve(strict=False)]

    # 7. Commit in workspace -> preview -> merge -> dirty guard -> clean cleanup
    subprocess.run(["git", "add", "app.py"], cwd=ws_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "update VALUE to 42"],
        cwd=ws_path,
        check=True,
        capture_output=True,
    )

    preview_resp = client.post(f"/api/workspaces/{ws_id}/merge-preview", json={})
    assert preview_resp.status_code == 200
    assert preview_resp.json()["has_conflicts"] is False

    merge_resp = client.post(
        "/api/rpc",
        json={"tool": "workspace_merge", "arguments": {"workspace_id": ws_id}},
    )
    assert merge_resp.status_code == 200
    assert merge_resp.json()["result"]["merged"] is True

    # Create an untracked dirty file in already-merged workspace -> cleanup MUST still return 409!
    dirty_file = ws_path / "scratch.txt"
    dirty_file.write_text("uncommitted", encoding="utf-8")
    cleanup_dirty = client.post(f"/api/workspaces/{ws_id}/cleanup", json={})
    assert cleanup_dirty.status_code == 409
    assert ws_path.exists()

    # Remove dirty file -> cleanup now succeeds with HTTP 200
    dirty_file.unlink()
    cleanup_ok = client.post(f"/api/workspaces/{ws_id}/cleanup", json={})
    assert cleanup_ok.status_code == 200
    assert cleanup_ok.json()["workspace"]["status"] == "cleaned"
    assert not ws_path.exists()

    # 8. Export reports (.md and .json)
    exp_md = client.get(f"/api/runs/{run_id}/export?format=md")
    assert exp_md.status_code == 200
    assert "text/markdown" in exp_md.headers["content-type"]
    assert "# 三重编排器运行报告：Daemon Integration Run" in exp_md.text
    assert "蜂窝模式" in exp_md.text
    assert "## 任务契约" in exp_md.text
    assert "## 成员列表" in exp_md.text
    assert "## 隔离工作区" in exp_md.text
    assert "## 消息板" in exp_md.text
    assert "Implement X cleanly with tests" in exp_md.text
    assert "coder-1" in exp_md.text
    assert "开发实现" in exp_md.text
    assert "已暂停" in exp_md.text
    assert "Git 本地克隆" in exp_md.text
    assert "已清理" in exp_md.text
    assert ws["path"] in exp_md.text
    if ws.get("branch_name"):
        assert ws["branch_name"] in exp_md.text
    assert "@coder-1 please commit when ready" in exp_md.text

    exp_json = client.get(f"/api/runs/{run_id}/export?format=json")
    assert exp_json.status_code == 200
    exported = exp_json.json()
    assert exported["run"]["id"] == run_id
    assert len(exported["messages"]) == 1

    exp_bad = client.get(f"/api/runs/{run_id}/export?format=xml")
    assert exp_bad.status_code == 400


def test_sse_live_events_and_watchdog_stall_alert(tmp_path: Path) -> None:
    async def _run() -> None:
        port = _free_port()
        settings = Settings(
            host="127.0.0.1",
            port=port,
            data_dir=tmp_path / "sse_data",
            stall_sec=5,
            watchdog_interval_sec=0.1,
        )
        proj_a = tmp_path / "proj_a"
        proj_b = tmp_path / "proj_b"
        proj_a.mkdir()
        proj_b.mkdir()

        with _RunningServer(settings, start_watchdog=False, keepalive_sec=0.25) as srv:
            async with httpx.AsyncClient(
                base_url=settings.base_url,
                trust_env=False,
                timeout=10.0,
            ) as client:
                r1 = (
                    await client.post(
                        "/api/rpc",
                        json={
                            "tool": "run_create",
                            "arguments": {
                                "mode": "swarm",
                                "title": "SSE Run 1",
                                "project_root": str(proj_a),
                            },
                        },
                    )
                ).json()["result"]
                r2 = (
                    await client.post(
                        "/api/rpc",
                        json={
                            "tool": "run_create",
                            "arguments": {
                                "mode": "boost",
                                "title": "SSE Run 2",
                                "project_root": str(proj_b),
                            },
                        },
                    )
                ).json()["result"]

                run1_id = r1["id"]
                run2_id = r2["id"]

                # Register a worker whose heartbeat is already stale
                db: Database = srv.app.state.db
                # This test retains the pre-workflow4 heartbeat alert contract.
                db.connect().execute('UPDATE runs SET workflow_version=3 WHERE id IN (?,?)', (run1_id, run2_id))
                board: BoardService = srv.app.state.board
                event_bus = srv.app.state.event_bus
                old_ts = time.time() - 120.0
                worker = board.worker_register(
                    run1_id,
                    "swarm-1",
                    "swarm_worker",
                    status="working",
                    current_task="Long compilation",
                    stall_threshold_sec=10,
                    heartbeat_at=old_ts,
                )

                received: list[tuple[str, dict[str, Any]]] = []

                async def _read_sse() -> None:
                    async with client.stream("GET", f"/api/events?run_id={run1_id}") as resp:
                        assert resp.status_code == 200
                        current_event = "message"
                        async for line in resp.aiter_lines():
                            if line.startswith("event:"):
                                current_event = line.split(":", 1)[1].strip()
                            elif line.startswith("data:"):
                                payload = json.loads(line.split(":", 1)[1].strip())
                                received.append((current_event, payload))
                                if any(evt == "stall_alert" for evt, _ in received) and any(
                                    evt == "board_message"
                                    and p.get("message", {}).get("sender") == "coder-a"
                                    for evt, p in received
                                ):
                                    break

                reader_task = asyncio.create_task(_read_sse())
                await asyncio.sleep(0.15)

                # Event in run2 MUST be filtered out of run1 stream
                await client.post(
                    "/api/rpc",
                    json={
                        "tool": "board_post",
                        "arguments": {
                            "run_id": run2_id,
                            "sender": "other",
                            "body_md": "Should not appear in run1 stream",
                        },
                    },
                )

                # Event in run1 MUST appear
                await client.post(
                    "/api/rpc",
                    json={
                        "tool": "board_post",
                        "arguments": {
                            "run_id": run1_id,
                            "sender": "coder-a",
                            "body_md": "Finished part 1",
                        },
                    },
                )

                # Trigger watchdog scan_once
                watchdog = StallWatchdog(db, board, event_bus, interval_sec=0.1)
                alerts = watchdog.scan_once(now=time.time())
                assert len(alerts) == 1
                assert alerts[0]["worker"]["id"] == worker["id"]
                assert alerts[0]["worker"]["stalled_alerted"] is True

                # Second scan_once must NOT emit duplicate alert
                assert watchdog.scan_once(now=time.time()) == []

                await asyncio.wait_for(reader_task, timeout=5.0)

                assert all(
                    p.get("run_id") == run1_id for _, p in received if p.get("run_id")
                )
                event_names = [evt for evt, _ in received]
                assert "board_message" in event_names
                assert "stall_alert" in event_names

                # Heartbeat clears stalled_alerted flag
                hb_resp = await client.post(
                    "/api/rpc",
                    json={
                        "tool": "worker_heartbeat",
                        "arguments": {"worker_id": worker["id"]},
                    },
                )
                assert hb_resp.status_code == 200
                assert hb_resp.json()["result"]["stalled_alerted"] is False

    asyncio.run(_run())


def test_merge_preview_conflict_and_watchdog_background_loop(tmp_path: Path) -> None:
    port = _free_port()
    settings = Settings(
        host="127.0.0.1",
        port=port,
        data_dir=tmp_path / "conflict_data",
        stall_sec=1,
        watchdog_interval_sec=0.08,
    )
    repo_dir = tmp_path / "repo_conflict"
    _init_git_repo(repo_dir)

    with _RunningServer(settings, start_watchdog=True, keepalive_sec=0.3) as srv:
        with httpx.Client(
            base_url=settings.base_url, trust_env=False, timeout=10.0
        ) as client:
            run = client.post(
                "/api/rpc",
                json={
                    "tool": "run_create",
                    "arguments": {
                        "mode": "teamwork",
                        "title": "Conflict & Watchdog Loop",
                        "project_root": str(repo_dir),
                    },
                },
            ).json()["result"]
            run_id = run["id"]

            ws = client.post(
                "/api/rpc",
                json={
                    "tool": "workspace_create",
                    "arguments": {"run_id": run_id, "worker_name": "coder-c"},
                },
            ).json()["result"]
            ws_id = ws["id"]
            ws_path = Path(ws["path"])

            # Diverging edits on main and workspace branch -> conflict on preview
            (repo_dir / "app.py").write_text("VALUE = 'main'\n", encoding="utf-8")
            subprocess.run(["git", "add", "app.py"], cwd=repo_dir, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "main edit"], cwd=repo_dir, check=True, capture_output=True)

            (ws_path / "app.py").write_text("VALUE = 'worker'\n", encoding="utf-8")
            subprocess.run(["git", "add", "app.py"], cwd=ws_path, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "worker edit"], cwd=ws_path, check=True, capture_output=True)

            preview = client.post(
                f"/api/workspaces/{ws_id}/merge-preview", json={}
            ).json()
            assert preview["has_conflicts"] is True
            assert "app.py" in preview["conflict_files"]
            # Main working tree remains clean and untouched
            assert (repo_dir / "app.py").read_text(encoding="utf-8") == "VALUE = 'main'\n"

            # Register stale worker and let the real background watchdog loop detect it
            srv.app.state.db.connect().execute('UPDATE runs SET workflow_version=3 WHERE id=?', (run_id,))
            board: BoardService = srv.app.state.board
            board.worker_register(
                run_id,
                "stalled-loop-worker",
                "coder",
                status="working",
                stall_threshold_sec=1,
                heartbeat_at=time.time() - 30.0,
            )
            deadline = time.monotonic() + 3.0
            alert_found = False
            while time.monotonic() < deadline:
                msgs = client.post(
                    "/api/rpc",
                    json={"tool": "board_read", "arguments": {"run_id": run_id}},
                ).json()["result"]["messages"]
                if any(m["msg_type"] == "alert" and m["sender"] == "watchdog" for m in msgs):
                    alert_found = True
                    break
                time.sleep(0.08)
            assert alert_found is True


def test_build_markdown_report_localization_and_empty_states(tmp_path: Path) -> None:
    demo_root = str(tmp_path / "demo-proj")
    rich_root = str(tmp_path / "proj-b")
    ws_path = str(tmp_path / "proj-b" / ".swarm-workspaces" / "ws_1")
    empty_md = build_markdown_report(
        {
            "run": {
                "id": "run_empty_1",
                "mode": "boost",
                "status": "completed",
                "title": "Raw Title Alpha",
                "created_at": 1700000000,
                "updated_at": 1700000060,
            },
            "project": {"name": "demo-proj", "root_path": demo_root},
            "task_packets": [
                {
                    "version": 2,
                    "title": "Packet Title",
                    "status": "frozen",
                    "goal": "Raw Goal Text",
                    "file_ownership": {"coder-a": ["a.py"]},
                    "acceptance_criteria": [],
                }
            ],
            "workers": [],
            "workspaces": [],
            "messages": [],
        }
    )
    assert "# 三重编排器运行报告：Raw Title Alpha" in empty_md
    assert "`极速模式`" in empty_md
    assert "`已完成`" in empty_md
    assert "`已冻结`" in empty_md
    assert "Raw Goal Text" in empty_md
    assert "- （无）" in empty_md
    assert "_暂无已注册成员。_" in empty_md
    assert "_暂无隔离工作区。_" in empty_md
    assert "_暂无消息记录。_" in empty_md

    rich_md = build_markdown_report(
        {
            "run": {
                "id": "run_rich_1",
                "mode": "teamwork",
                "status": "active",
                "title": "Teamwork Run Raw",
                "created_at": 1700000000,
                "updated_at": 1700000120,
            },
            "project": {"name": "proj-b", "root_path": rich_root},
            "task_packets": [],
            "workers": [
                {
                    "name": "verifier-1",
                    "role": "verifier",
                    "status": "working",
                    "workspace_id": "ws_1",
                    "current_task": "Verify API",
                    "last_heartbeat_at": 1700000100,
                }
            ],
            "workspaces": [
                {
                    "id": "ws_1",
                    "worker_name": "verifier-1",
                    "mode": "snapshot",
                    "status": "verified",
                    "branch_name": "swarm/verifier-1",
                    "assigned_port": 18801,
                    "path": ws_path,
                }
            ],
            "messages": [
                {
                    "seq": 7,
                    "channel": "main",
                    "sender": "verifier-1",
                    "sender_role": "verifier",
                    "mentions": ["coordinator"],
                    "msg_type": "test",
                    "summary": "All tests green",
                    "body_md": "Exact raw body_md line",
                    "created_at": 1700000110,
                }
            ],
        }
    )
    assert "# 三重编排器运行报告：Teamwork Run Raw" in rich_md
    assert "`协同模式`" in rich_md
    assert "`进行中`" in rich_md
    assert "`verifier-1`" in rich_md
    assert "`独立验收`" in rich_md
    assert "`执行中`" in rich_md
    assert "`目录快照`" in rich_md
    assert "`已验证`" in rich_md
    assert "`swarm/verifier-1`" in rich_md
    assert f"`{ws_path}`" in rich_md
    assert "### #7 [主频道] verifier-1（独立验收）— `测试`" in rich_md
    assert "Exact raw body_md line" in rich_md


def test_duplicate_daemon_bind_preserves_existing_pid(daemon_env, tmp_path):
    import os
    import sys
    settings, _, client = daemon_env
    original = settings.pid_path.read_bytes()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "plugin" / "src")
    failed = subprocess.run(
        [sys.executable, "-B", "-m", "orchestrator.daemon", "--host", settings.host,
         "--port", str(settings.port), "--data-dir", str(settings.data_dir)],
        env=env, capture_output=True, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert failed.returncode != 0
    assert settings.pid_path.exists(), "Failed second daemon erased live owner's PID file"
    assert settings.pid_path.read_bytes() == original
    assert client.get("/api/health").status_code == 200


def test_failed_control_keeps_actual_state(daemon_env, tmp_path):
    _, _, client = daemon_env
    project = tmp_path / "control-project"
    project.mkdir()
    def rpc(tool, **arguments):
        response = client.post("/api/rpc", json={"tool": tool, "arguments": arguments})
        assert response.status_code == 200, response.text
        return response.json()["result"]
    run = rpc("run_create", mode="boost", title="control", project_root=str(project))
    worker = rpc("worker_register", run_id=run["id"], name="verify", role="verifier",
                 status="working", agent_id="/root/verify")
    response = client.post(f"/api/workers/{worker['id']}/status", json={"status": "killed"})
    assert response.status_code == 202
    request = response.json()["control_request"]
    rpc("worker_control_ack", request_id=request["id"], outcome="failed", result="Native interrupt: target not found")
    snapshot = client.get(f"/api/runs/{run['id']}/snapshot").json()
    assert snapshot["workers"][0]["status"] == "working"
    assert snapshot["control_requests"][0]["status"] == "failed"
    assert snapshot["control_requests"][0]["result"] == "Native interrupt: target not found"


@pytest.fixture
def explorer_route(daemon_env, tmp_path):
    _, server, client = daemon_env
    target = tmp_path / ".swarm-workspaces" / "workspace with spaces"
    target.mkdir(parents=True)
    db = server.app.state.db
    run = db.create_run(project_root=tmp_path, is_git=False, mode="swarm", title="Opener")
    workspace = db.create_workspace(run["id"], "coder", path=target, mode="snapshot")
    return client, f"/api/workspaces/{workspace['id']}/open-explorer", target.resolve()


@pytest.mark.parametrize(
    ("platform", "expected_command", "code", "expected_status"),
    [
        ("linux", "xdg-open", 4, 500),
        ("linux", "xdg-open", None, 500),
        ("linux", "xdg-open", 0, 200),
        ("darwin", "open", 0, 200),
    ],
)
def test_open_explorer_reports_child_result(explorer_route, monkeypatch, platform, expected_command, code, expected_status):
    import orchestrator.daemon as daemon_mod

    client, url, target = explorer_route
    monkeypatch.setattr(daemon_mod, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(daemon_mod, "sys", SimpleNamespace(platform=platform))
    def spawn(args, **kwargs):
        assert args == [expected_command, str(target)]
        assert kwargs.get("shell", False) is False
        assert kwargs["start_new_session"] is True
        assert kwargs["close_fds"] is True
        if code is None:
            raise FileNotFoundError("test opener not on PATH")
        return subprocess.Popen([sys.executable, "-c", f"raise SystemExit({code})", args[1]], **kwargs)

    monkeypatch.setattr(daemon_mod, "subprocess", SimpleNamespace(Popen=spawn, DEVNULL=subprocess.DEVNULL))
    response = client.post(url, json={})
    assert response.status_code == expected_status, response.text
    payload = response.json()
    assert payload["ok"] is (code == 0)
    if code == 0:
        assert payload["status"] == "accepted"
        assert payload["path"] == str(target)
    elif code is None:
        assert "Failed to start xdg-open" in payload["detail"]
        assert "test opener not on PATH" in payload["detail"]
    else:
        assert "xdg-open exited with status 4" in payload["detail"]


@pytest.mark.parametrize("shell_error", [False, True])
def test_open_explorer_uses_windows_shell_result(explorer_route, monkeypatch, shell_error):
    import orchestrator.daemon as daemon_mod

    client, url, target = explorer_route
    calls = []

    def startfile(path, operation):
        calls.append((path, operation))
        if shell_error:
            raise PermissionError("Explorer refused test directory")

    monkeypatch.setattr(daemon_mod, "os", SimpleNamespace(name="nt", startfile=startfile))
    response = client.post(url, json={})
    assert calls == [(str(target), "explore")]
    assert response.status_code == (500 if shell_error else 200), response.text
    assert response.json()["ok"] is not shell_error
    if shell_error:
        assert "Explorer refused test directory" in response.json()["detail"]
    else:
        assert response.json()["status"] == "accepted"


def test_open_explorer_checks_exit_at_wait_deadline(explorer_route, monkeypatch):
    import orchestrator.daemon as daemon_mod

    client, url, _ = explorer_route
    monkeypatch.setattr(daemon_mod, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(daemon_mod, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(daemon_mod, "_DIRECTORY_OPENER_WAIT_SECONDS", 0.01)
    polls = iter([None])
    process = SimpleNamespace(poll=lambda: next(polls, 4))
    monkeypatch.setattr(daemon_mod, "subprocess", SimpleNamespace(Popen=lambda *a, **kw: process, DEVNULL=subprocess.DEVNULL))

    response = client.post(url, json={})

    assert response.status_code == 500, response.text
    assert "xdg-open exited with status 4" in response.json()["detail"]


def test_open_explorer_returns_pending_without_killing_the_child(explorer_route, tmp_path, monkeypatch, caplog, capfd):
    import orchestrator.daemon as daemon_mod

    client, url, target = explorer_route
    monkeypatch.setattr(daemon_mod, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(daemon_mod, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(daemon_mod, "_DIRECTORY_OPENER_WAIT_SECONDS", 0.05)
    release = tmp_path / "release-opener"
    script = (
        "from pathlib import Path\nimport sys, time\n"
        "release = Path(sys.argv[1])\ndeadline = time.monotonic() + 5\n"
        "while not release.exists() and time.monotonic() < deadline:\n    time.sleep(0.01)\n"
        "print('test opener diagnostics', file=sys.stderr)\n"
        "raise SystemExit(7 if release.exists() else 9)\n"
    )
    processes = []

    def spawn(args, **kwargs):
        assert args == ["xdg-open", str(target)]
        process = subprocess.Popen([sys.executable, "-c", script, str(release)], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(daemon_mod, "subprocess", SimpleNamespace(Popen=spawn, DEVNULL=subprocess.DEVNULL))
    try:
        response = client.post(url, json={})
        assert response.status_code == 202, response.text
        assert response.json() == {
            "ok": False, "status": "pending", "workspace_id": url.split("/")[-2], "path": str(target),
        }
        assert processes[0].returncode is None
    finally:
        release.touch()
    deadline = time.monotonic() + 5
    while "xdg-open exited with status 7" not in caplog.text and time.monotonic() < deadline:
        time.sleep(0.01)
    assert processes[0].returncode == 7
    assert "xdg-open exited with status 7" in caplog.text
    assert "test opener diagnostics" in capfd.readouterr().err


def test_detached_directory_opener_survives_daemon_shutdown(tmp_path):
    release = tmp_path / "release"
    receipt = tmp_path / "finished"
    child = tmp_path / "file_manager.py"
    child.write_text(
        "from pathlib import Path\nimport sys, time\n"
        "release, receipt = map(Path, sys.argv[1:])\ndeadline = time.monotonic() + 8\n"
        "while not release.exists() and time.monotonic() < deadline:\n    time.sleep(0.01)\n"
        "if release.exists():\n    receipt.write_text('survived', encoding='utf-8')\n",
        encoding="utf-8",
    )
    runner = """
import asyncio, gc, subprocess, sys
from pathlib import Path
from types import SimpleNamespace
from orchestrator import daemon

daemon.os = SimpleNamespace(name="posix")
daemon.sys = SimpleNamespace(platform="linux")
daemon._DIRECTORY_OPENER_WAIT_SECONDS = 0.05
child_args = [sys.executable, *sys.argv[1:]]
real_create = asyncio.create_subprocess_exec
async def create_async(*args, **kwargs):
    return await real_create(*child_args, **kwargs)
asyncio.create_subprocess_exec = create_async
daemon.subprocess = SimpleNamespace(
    Popen=lambda args, **kwargs: subprocess.Popen(child_args, **kwargs),
    DEVNULL=subprocess.DEVNULL,
)

async def main():
    assert not await daemon._launch_explorer(Path(sys.argv[1]).parent, set())

asyncio.run(main(), debug=True)
gc.collect()
"""
    env = os.environ | {"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "plugin" / "src")}
    with (tmp_path / "daemon.log").open("wb") as log, subprocess.Popen(
        [sys.executable, "-c", runner, str(child), str(release), str(receipt)],
        env=env,
        stdout=log,
        stderr=log,
    ) as daemon_process:
        try:
            assert daemon_process.wait(timeout=5) == 0
        finally:
            release.touch()
    deadline = time.monotonic() + 2
    while not receipt.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert receipt.exists(), (tmp_path / "daemon.log").read_text(encoding="utf-8")
