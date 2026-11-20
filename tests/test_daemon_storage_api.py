"""Tests for daemon storage API routes, background GC lifecycle, CLI --gc, mcp_shim TTL/cap, and RPC stats."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

from orchestrator import gc as storage_gc
from orchestrator.board import BoardService
from orchestrator.config import Settings
from orchestrator.daemon import create_app, main as daemon_main
from orchestrator.db import Database
from orchestrator.mcp_shim import _deliver_tool_result
from orchestrator.rpc_handlers import RpcDispatcher
from orchestrator.verify_runner import collect as collect_verify_receipt
from orchestrator.workspace_mgr import WORKSPACES_DIRNAME, WorkspaceManager


def _create_merged_snapshot_workspace(
    db: Database,
    ws_mgr: WorkspaceManager,
    project: Path,
    *,
    worker_name: str,
    run_status: str = "completed",
    age_sec: float = 0.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    run = db.create_run(
        mode="swarm",
        title=f"Run {worker_name}",
        project_root=project,
        is_git=False,
    )
    with db.transaction() as conn:
        conn.execute("UPDATE runs SET workflow_version = 0 WHERE id = ?", (run["id"],))
    ws = ws_mgr.workspace_create(run["id"], worker_name, copy_env=False)
    merge_res = ws_mgr.workspace_merge(ws["id"])
    assert merge_res["merged"] is True
    db.update_run(run["id"], status=run_status)
    if age_sec > 0:
        old_ts = time.time() - age_sec
        with db.transaction() as conn:
            conn.execute("UPDATE runs SET updated_at = ? WHERE id = ?", (old_ts, run["id"]))
            conn.execute("UPDATE workspaces SET updated_at = ? WHERE id = ?", (old_ts, ws["id"]))
    return db.get_run(run["id"]), db.get_workspace(ws["id"])  # type: ignore[return-value]


def _make_old_result_file(settings: Settings, name: str, content: str, *, age_sec: float) -> Path:
    settings.results_dir.mkdir(parents=True, exist_ok=True)
    path = settings.results_dir / name
    path.write_text(content, encoding="utf-8")
    ts = time.time() - age_sec
    os.utime(path, (ts, ts))
    return path


def test_get_storage_and_post_storage_gc_routes_and_events(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "app.py").write_text("VALUE = 1\n", encoding="utf-8")

    settings = Settings(
        data_dir=tmp_path / "data",
        port_range=(29200, 29250),
        result_ttl_sec=60,
        result_max_files=5,
        workspace_retention_sec=3600,
    )
    db = Database.from_settings(settings)
    ws_mgr = WorkspaceManager(db, settings)
    try:
        # 1) Cleanable workspace (completed & merged, recent so only include_all_completed=True removes it immediately)
        clean_run, clean_ws = _create_merged_snapshot_workspace(
            db, ws_mgr, project, worker_name="clean-worker", run_status="completed", age_sec=0.0
        )
        # 2) Dirty workspace (unmerged addition -> must never be removed by GC)
        _, dirty_ws = _create_merged_snapshot_workspace(
            db, ws_mgr, project, worker_name="dirty-worker", run_status="completed", age_sec=7200.0
        )
        (Path(dirty_ws["path"]) / "unmerged.txt").write_text("keep me\n", encoding="utf-8")
        # 3) Active workspace
        active_run = db.create_run(mode="swarm", title="Active", project_root=project, is_git=False)
        active_ws = ws_mgr.workspace_create(active_run["id"], "active-worker", copy_env=False)

        expired_res = _make_old_result_file(settings, "expired.json", '{"old": true}\n', age_sec=120.0)
        fresh_res = _make_old_result_file(settings, "fresh.json", '{"fresh": true}\n', age_sec=0.0)
    finally:
        db.close()

    async def scenario() -> None:
        app = create_app(settings, start_watchdog=False, start_gc=False, manage_pid_file=False)
        async with app.router.lifespan_context(app):
            sub_id, queue = app.state.event_bus.subscribe()
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    # GET /api/storage
                    resp = await client.get("/api/storage")
                    assert resp.status_code == 200, resp.text
                    report = resp.json()
                    assert report["data_dir"] == str(settings.data_dir)
                    assert report["runtime"]["results_count"] == 2
                    assert report["workspaces"]["active_count"] == 1
                    assert report["workspaces"]["dirty_review_count"] == 1
                    assert report["workspaces"]["cleanable_count"] == 1

                    # Optional flags: include_all_completed=False skips recent clean_ws; clean_workspaces=False skips workspaces
                    no_ws_resp = await client.post(
                        "/api/storage/gc",
                        json={"clean_results": False, "clean_workspaces": False},
                    )
                    assert no_ws_resp.status_code == 200
                    assert no_ws_resp.json()["cleaned_workspaces"] == []
                    assert no_ws_resp.json()["cleaned_results"] == []

                    ret_resp = await client.post(
                        "/api/storage/gc",
                        json={"clean_results": False, "clean_workspaces": True, "include_all_completed": False},
                    )
                    assert ret_resp.status_code == 200
                    assert ret_resp.json()["cleaned_workspaces"] == []
                    assert Path(clean_ws["path"]).is_dir()

                    # Invalid JSON / non-bool validation -> HTTP 400
                    bad_type = await client.post("/api/storage/gc", json={"dry_run": "false"})
                    assert bad_type.status_code == 400
                    assert bad_type.json()["error_type"] == "ValueError"

                    bad_json = await client.post(
                        "/api/storage/gc",
                        content=b"{not-json",
                        headers={"Content-Type": "application/json"},
                    )
                    assert bad_json.status_code == 400

                    # Dry run: must NOT delete files or emit SSE events
                    dry_resp = await client.post("/api/storage/gc", json={"dry_run": True})
                    assert dry_resp.status_code == 200, dry_resp.text
                    dry_data = dry_resp.json()
                    assert dry_data["ok"] is True
                    assert dry_data["dry_run"] is True
                    assert clean_ws["id"] in dry_data["cleaned_workspaces"]
                    assert str(expired_res) in dry_data["cleaned_results"]
                    assert Path(clean_ws["path"]).is_dir()
                    assert expired_res.is_file()
                    assert queue.empty()

                    # Real GC with default optional body (include_all_completed=True)
                    gc_resp = await client.post("/api/storage/gc")
                    assert gc_resp.status_code == 200, gc_resp.text
                    gc_data = gc_resp.json()
                    assert gc_data["ok"] is True
                    assert gc_data["dry_run"] is False
                    assert gc_data["freed_bytes"] > 0
                    assert gc_data["cleaned_workspaces"] == [clean_ws["id"]]
                    assert gc_data["cleaned_results"] == [str(expired_res)]
                    assert any(item["workspace_id"] == dirty_ws["id"] for item in gc_data["skipped_dirty_workspaces"])

                    assert not Path(clean_ws["path"]).exists()
                    assert not expired_res.exists()
                    assert fresh_res.is_file()
                    assert Path(dirty_ws["path"]).is_dir()
                    assert Path(active_ws["path"]).is_dir()
                    assert app.state.db.get_workspace(clean_ws["id"])["status"] == "cleaned"

                    events: list[dict[str, Any]] = []
                    while not queue.empty():
                        events.append(queue.get_nowait())
                    ws_events = [e for e in events if e["event"] == "workspace_updated"]
                    storage_events = [e for e in events if e["event"] == "storage_updated"]
                    assert len(ws_events) == 1
                    assert ws_events[0]["data"]["run_id"] == clean_run["id"]
                    assert ws_events[0]["data"]["workspace"]["id"] == clean_ws["id"]
                    assert ws_events[0]["data"]["workspace"]["status"] == "cleaned"
                    assert "snapshot_manifest" not in ws_events[0]["data"]["workspace"]
                    assert len(storage_events) == 1
                    assert storage_events[0]["data"]["freed_bytes"] == gc_data["freed_bytes"]
                    assert storage_events[0]["data"]["report"] == gc_data["report_after"]

                    # Second GC pass is a no-op -> must NOT emit duplicate storage_updated events
                    noop_resp = await client.post("/api/storage/gc", json={})
                    assert noop_resp.status_code == 200
                    assert noop_resp.json()["freed_bytes"] == 0
                    assert noop_resp.json()["cleaned_workspaces"] == []
                    assert queue.empty()
            finally:
                app.state.event_bus.unsubscribe(sub_id)

    asyncio.run(scenario())


def test_background_gc_task_lifecycle_resilience_and_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    project = tmp_path / "bg_proj"
    project.mkdir()
    (project / "main.py").write_text("x = 1\n", encoding="utf-8")

    settings = Settings(
        data_dir=tmp_path / "bg_data",
        port_range=(29251, 29299),
        result_ttl_sec=30,
        workspace_retention_sec=60,
        gc_interval_sec=3600.0,
    )
    db = Database.from_settings(settings)
    ws_mgr = WorkspaceManager(db, settings)
    try:
        _, old_clean_ws = _create_merged_snapshot_workspace(
            db, ws_mgr, project, worker_name="old-clean", run_status="completed", age_sec=120.0
        )
        _, recent_clean_ws = _create_merged_snapshot_workspace(
            db, ws_mgr, project, worker_name="recent-clean", run_status="completed", age_sec=0.0
        )
        expired_res = _make_old_result_file(settings, "stale.json", '{"a": 1}\n', age_sec=120.0)
    finally:
        db.close()

    # 1) Initial GC pass runs safely with include_all_completed=False, leaving recent_clean_ws intact.
    async def check_initial_pass_and_shutdown() -> None:
        app = create_app(settings, start_watchdog=False, start_gc=True, gc_interval_sec=0.05)
        async with app.router.lifespan_context(app):
            gc_task = app.state.gc_task
            assert gc_task is not None
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if not expired_res.exists() and not Path(old_clean_ws["path"]).exists():
                    break
                await asyncio.sleep(0.02)
            assert not expired_res.exists()
            assert not Path(old_clean_ws["path"]).exists()
            assert Path(recent_clean_ws["path"]).is_dir()
            assert not gc_task.done()
        assert gc_task.done()
        assert gc_task.cancelled()

    asyncio.run(check_initial_pass_and_shutdown())

    # 2) Exception in run_gc is logged and does not kill periodic loop; shutdown cancels cleanly.
    calls: list[bool] = []
    real_run_gc = storage_gc.run_gc

    def flaky_run_gc(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(bool(kwargs.get("include_all_completed")))
        if len(calls) == 1:
            raise RuntimeError("Simulated transient GC error")
        return real_run_gc(*args, **kwargs)

    monkeypatch.setattr(storage_gc, "run_gc", flaky_run_gc)

    async def check_error_recovery() -> None:
        app = create_app(
            Settings(data_dir=tmp_path / "bg_err_data"),
            start_watchdog=False,
            start_gc=True,
            gc_interval_sec=0.03,
        )
        with caplog.at_level("ERROR"):
            async with app.router.lifespan_context(app):
                gc_task = app.state.gc_task
                assert gc_task is not None
                deadline = time.monotonic() + 3.0
                while len(calls) < 2 and time.monotonic() < deadline:
                    await asyncio.sleep(0.02)
                assert len(calls) >= 2
                assert all(flag is False for flag in calls)
                assert not gc_task.done()
        assert gc_task.done()

    asyncio.run(check_error_recovery())
    assert "Background GC pass failed" in caplog.text

    # 3) start_gc=False skips creating gc_task
    async def check_disabled() -> None:
        app = create_app(
            Settings(data_dir=tmp_path / "bg_off_data"),
            start_watchdog=False,
            start_gc=False,
            gc_interval_sec=0,
        )
        async with app.router.lifespan_context(app):
            assert app.state.gc_task is None

    asyncio.run(check_disabled())


def test_daemon_cli_gc_offline_and_online_wal_and_preserves_settings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Dry-run on non-existent data_dir must not start daemon or create state.db
    empty_data = tmp_path / "empty_data"
    rc = daemon_main(["--data-dir", str(empty_data), "--gc", "--gc-dry-run"])
    assert rc == 0
    assert not (empty_data / "state.db").exists()
    empty_report = json.loads(capsys.readouterr().out)
    assert empty_report["ok"] is True
    assert empty_report["dry_run"] is True
    assert empty_report["cleaned_workspaces"] == []

    # Invalid flag combination without --gc exits with SystemExit(2)
    with pytest.raises(SystemExit):
        daemon_main(["--data-dir", str(empty_data), "--gc-dry-run"])

    # Online SQLite WAL test: daemon holds WAL open while subprocess `python -m orchestrator.daemon --gc` runs
    project = tmp_path / "wal_proj"
    project.mkdir()
    (project / "mod.py").write_text("M = 1\n", encoding="utf-8")
    wal_data = tmp_path / "wal_data"
    settings = Settings(
        data_dir=wal_data,
        port_range=(29300, 29350),
        result_ttl_sec=30,
        workspace_retention_sec=3600,
    )
    app = create_app(settings, start_watchdog=False, start_gc=False, manage_pid_file=False)
    with TestClient(app):
        ws_mgr = app.state.workspace_mgr
        _, ws = _create_merged_snapshot_workspace(
            app.state.db, ws_mgr, project, worker_name="wal-worker", run_status="completed", age_sec=0.0
        )
        old_result = _make_old_result_file(settings, "wal-old.json", '{"x": 1}\n', age_sec=120.0)

        env = dict(
            os.environ,
            PYTHONPATH=str(Path(__file__).resolve().parents[1] / "plugin" / "src"),
            PYTHONUTF8="1",
            TRI_ORCH_RESULT_TTL_SEC="30",
            TRI_ORCH_WORKSPACE_RETENTION_SEC="3600",
        )
        dry_proc = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "orchestrator.daemon",
                "--data-dir",
                str(wal_data),
                "--gc",
                "--gc-dry-run",
                "--gc-all-completed",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=20,
        )
        assert dry_proc.returncode == 0, dry_proc.stderr
        dry_out = json.loads(dry_proc.stdout)
        assert dry_out["dry_run"] is True
        assert ws["id"] in dry_out["cleaned_workspaces"]
        assert str(old_result) in dry_out["cleaned_results"]
        assert Path(ws["path"]).is_dir()
        assert old_result.is_file()

        real_proc = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "orchestrator.daemon",
                "--data-dir",
                str(wal_data),
                "--gc",
                "--gc-all-completed",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=20,
        )
        assert real_proc.returncode == 0, real_proc.stderr
        real_out = json.loads(real_proc.stdout)
        assert real_out["dry_run"] is False
        assert real_out["cleaned_workspaces"] == [ws["id"]]
        assert real_out["cleaned_results"] == [str(old_result)]
        assert not Path(ws["path"]).exists()
        assert not old_result.exists()
        assert app.state.db.get_workspace(ws["id"])["status"] == "cleaned"

    # Verify daemon.main preserves extended Settings env fields even with --host/--port overrides
    captured: list[Settings] = []
    monkeypatch.setenv("TRI_ORCH_GC_INTERVAL_SEC", "123.5")
    monkeypatch.setenv("TRI_ORCH_MAX_SNAPSHOT_MB", "42")
    monkeypatch.setattr("orchestrator.daemon.run_daemon", captured.append)
    assert daemon_main(["--host", "127.0.0.1", "--port", "19999", "--data-dir", str(empty_data)]) == 0
    assert len(captured) == 1
    assert captured[0].gc_interval_sec == 123.5
    assert captured[0].max_snapshot_mb == 42
    assert captured[0].port == 19999


def test_mcp_shim_deliver_tool_result_uses_settings_ttl_and_max_files(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "shim_data",
        result_ttl_sec=60,
        result_max_files=2,
    )
    settings.results_dir.mkdir(parents=True, exist_ok=True)
    expired = _make_old_result_file(settings, "expired.json", "{}", age_sec=120.0)
    older_valid = _make_old_result_file(settings, "older_valid.json", "{}", age_sec=20.0)
    recent_valid = _make_old_result_file(settings, "recent_valid.json", "{}", age_sec=5.0)

    large_payload = {"diff": "A" * (1200 * 1024)}
    res = _deliver_tool_result(settings, "workspace_diff", large_payload)
    assert res.is_error is False
    delivery = json.loads(res.content[0].text)
    created = Path(delivery["result_file"])

    assert created.parent == settings.results_dir.resolve()
    assert created.is_file()
    assert not expired.exists()
    assert not older_valid.exists()
    assert recent_valid.is_file()
    assert sorted(p.name for p in settings.results_dir.glob("*.json")) == sorted(
        [recent_valid.name, created.name]
    )

    # Even with result_max_files=0 and result_ttl_sec=0, the newly written file itself must survive!
    zero_settings = Settings(
        data_dir=settings.data_dir,
        result_ttl_sec=0,
        result_max_files=0,
    )
    res2 = _deliver_tool_result(zero_settings, "workspace_diff", large_payload)
    delivery2 = json.loads(res2.content[0].text)
    created2 = Path(delivery2["result_file"])
    assert created2.is_file()
    assert list(zero_settings.results_dir.glob("*.json")) == [created2]


def test_rpc_handlers_preserve_copy_stats_and_closeout_freed_bytes_with_excluded_cache(
    tmp_path: Path,
) -> None:
    project = tmp_path / "rpc_proj"
    project.mkdir()
    (project / "app.py").write_bytes(b"VALUE = 1\n")
    (project / ".pytest_cache").mkdir()
    (project / ".pytest_cache" / "cached.txt").write_bytes(b"cache\n")
    (project / "build").mkdir()
    (project / "build" / "out.bin").write_bytes(b"0" * 4096)

    settings = Settings(data_dir=tmp_path / "rpc_data", port_range=(29351, 29399))
    db = Database.from_settings(settings)
    board = BoardService(db, settings)
    ws_mgr = WorkspaceManager(db, settings)
    dispatcher = RpcDispatcher(db, board, ws_mgr)

    try:
        run = dispatcher.dispatch(
            "run_create",
            {"mode": "swarm", "title": "Copy Stats & Closeout", "project_root": str(project)},
        )
        run_id = run["id"]
        dispatcher.dispatch(
            "task_packet_save",
            {
                "run_id": run_id,
                "title": "Packet",
                "goal": "Check copy stats and closeout freed_bytes",
                "file_ownership": {"author_1": ["app.py"]},
                "acceptance_criteria": ["AC-1"],
            },
        )

        # Direct workspace_create exposes copied_bytes and file_count
        project_direct = tmp_path / "rpc_direct_proj"
        project_direct.mkdir()
        (project_direct / "app.py").write_bytes(b"VALUE = 1\n")
        run_direct = dispatcher.dispatch(
            "run_create",
            {"mode": "swarm", "title": "Direct WS Copy Stats", "project_root": str(project_direct)},
        )
        direct_ws = dispatcher.dispatch(
            "workspace_create",
            {"run_id": run_direct["id"], "worker_name": "direct", "copy_env": False},
        )
        assert direct_ws["copied_bytes"] == len("VALUE = 1\n".encode("utf-8"))
        assert direct_ws["file_count"] == 1
        assert direct_ws["snapshot_manifest"]["copied_bytes"] == direct_ws["copied_bytes"]
        assert direct_ws["snapshot_manifest"]["file_count"] == 1

        # workflow_prepare_dispatch (including custom workspace_id rename!) preserves copied_bytes & file_count
        prep = dispatcher.dispatch(
            "workflow_prepare_dispatch",
            {
                "run_id": run_id,
                "create_workspace": True,
                "workspace_id": "ws_custom_stats_1",
                "copy_env": False,
                "owner": "author_1",
                "task_name": "author_1",
                "verifier": "verifier_1",
                "step_key": "step-1",
                "instruction": "Update app.py",
            },
        )
        assert prep["workspace"]["id"] == "ws_custom_stats_1"
        assert prep["workspace"]["copied_bytes"] == len("VALUE = 1\n".encode("utf-8"))
        assert prep["workspace"]["file_count"] == 1

        step_id = prep["step"]["id"]
        dispatcher.dispatch(
            "native_action_record_call",
            {
                "run_id": run_id,
                "action_id": prep["action_id"],
                "claim_token": prep["claim_token"],
                "expected_revision": prep["expected_revision"],
                "outcome": "confirmed",
                "raw_response": {"task_name": "/root/author_1"},
            },
        )
        dispatcher.dispatch(
            "workflow_author_turn",
            {
                "run_id": run_id,
                "step_id": step_id,
                "message_id": prep["message_id"],
                "phase": "accept",
            },
        )
        ws_path = Path(prep["workspace"]["path"])
        (ws_path / "app.py").write_text("VALUE = 200\n", encoding="utf-8")
        # Create excluded caches inside workspace before submit/merge/closeout
        (ws_path / ".pytest_cache").mkdir()
        (ws_path / ".pytest_cache" / "ws_cache.bin").write_bytes(b"c" * 1024)
        (ws_path / "build").mkdir()
        (ws_path / "build" / "artifact.o").write_bytes(b"b" * 2048)

        dispatcher.dispatch(
            "workflow_author_turn",
            {
                "run_id": run_id,
                "step_id": step_id,
                "message_id": prep["message_id"],
                "phase": "submit",
            },
        )

        env_profile = "windows_minimal_py" if os.name == "nt" else "posix_minimal_py"
        for scope, target_cwd in (("candidate", ws_path), ("integrated", project)):
            v_prep = dispatcher.dispatch(
                "workflow_prepare_dispatch",
                {
                    "run_id": run_id,
                    "dispatch_action": "request_verify",
                    "step_id": step_id,
                    "verifier": "verifier_1",
                    "task_name": "verifier_1",
                    "instruction": f"Verify {scope}",
                },
            )
            dispatcher.dispatch(
                "native_action_record_call",
                {
                    "run_id": run_id,
                    "action_id": v_prep["action_id"],
                    "claim_token": v_prep["claim_token"],
                    "expected_revision": v_prep["expected_revision"],
                    "outcome": "confirmed",
                    "raw_response": {"task_name": "/root/verifier_1"},
                },
            )
            turn = dispatcher.dispatch(
                "workflow_verifier_turn",
                {
                    "run_id": run_id,
                    "step_id": step_id,
                    "phase": "prepare",
                    "scope": scope,
                    "env_profile": env_profile,
                    "verification_commands": [
                        {
                            "id": f"chk-{scope}",
                            "argv": [sys.executable, "-c", "print('ok')"],
                            "cwd": str(target_cwd),
                            "timeout_sec": 15,
                            "criteria": ["AC-1"],
                        }
                    ],
                },
            )
            out_dir = tmp_path / f"receipts_{scope}"
            collect_verify_receipt(Path(turn["artifact"]["manifest_path"]), out_dir)
            dispatcher.dispatch(
                "workflow_verifier_turn",
                {
                    "run_id": run_id,
                    "step_id": step_id,
                    "phase": "record",
                    "artifact_id": turn["artifact"]["id"],
                    "verdict": "PASS",
                    "receipt_files": [str(out_dir / "receipt.json")],
                },
            )
            if scope == "candidate":
                dispatcher.dispatch("workspace_merge", {"workspace_id": "ws_custom_stats_1"})

        expected_ws_bytes = storage_gc._dir_size_bytes(ws_path)
        assert expected_ws_bytes >= len("VALUE = 200\n") + 1024 + 2048

        closeout = dispatcher.dispatch("workflow_closeout_run", {"run_id": run_id})
        assert closeout["run"]["status"] == "completed"
        assert closeout["cleaned_workspaces"] == ["ws_custom_stats_1"]
        assert closeout["freed_bytes"] == expected_ws_bytes
        assert not ws_path.exists()
        assert not (project / WORKSPACES_DIRNAME).exists()
    finally:
        db.close()
