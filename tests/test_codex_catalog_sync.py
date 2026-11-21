"""Tests for live Codex catalog mirroring, real-time SSE sync, and dashboard UI."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from orchestrator import codex_bridge
from orchestrator.config import Settings
from orchestrator.daemon import create_app
from orchestrator.db import Database

from test_daemon_web import _RunningServer, _free_port
from test_dashboard_behavior import run_dashboard


def _init_catalog_codex_home(codex_home: Path) -> Path:
    codex_home.mkdir(parents=True, exist_ok=True)
    db_path = codex_home / "state_5.sqlite"
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(
            """
            CREATE TABLE projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE project_roots (
                project_id TEXT NOT NULL REFERENCES projects(id),
                position INTEGER NOT NULL,
                path TEXT NOT NULL,
                PRIMARY KEY (project_id, position)
            );
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                cwd TEXT,
                project_id TEXT,
                name TEXT,
                title TEXT,
                first_user_message TEXT,
                thread_source TEXT,
                source TEXT,
                archived INTEGER NOT NULL DEFAULT 0,
                is_pinned INTEGER NOT NULL DEFAULT 0,
                model TEXT,
                reasoning_effort TEXT,
                model_provider TEXT,
                created_at REAL,
                updated_at REAL
            );
            """
        )
    codex_bridge.invalidate_catalog_cache()
    return db_path


def _snapshot_files(directory: Path) -> dict[str, tuple[int, int]]:
    return {p.name: (p.stat().st_mtime_ns, p.stat().st_size) for p in directory.iterdir()}


def test_codex_catalog_ordering_exclusions_and_live_wal_mutations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    codex_home = tmp_path / "codex_home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    db_path = _init_catalog_codex_home(codex_home)

    repo_a = tmp_path / "repo-alpha"
    repo_b = tmp_path / "repo-beta"
    repo_del = tmp_path / "repo-deleted"
    for d in (repo_a, repo_b, repo_del):
        d.mkdir(parents=True)
    (repo_a / ".git").mkdir()

    chatgpt_internal = codex_home / ".chatgpt-projects" / "g-p-internal"
    chatgpt_internal.mkdir(parents=True)

    global_state = {
        "project-order": ["leg-b", "leg-a", "leg-del"],
        "app-server-project-id-by-legacy-project-id-by-host": {
            f"local:{codex_home}": {
                "leg-a": "app-a",
                "leg-b": "app-b",
                "leg-del": "app-del",
            }
        },
        "app-server-pending-project-deletions-by-host": {
            f"local:{codex_home}": {
                "leg-del": {"projectId": "app-del", "deleteAfterMs": 9999999999999},
            }
        },
        "thread-project-assignments": {
            "t-assigned": {"projectKind": "local", "projectId": "leg-a"},
            "t-deleted-assign": {"projectKind": "local", "projectId": "leg-del"},
            "t-cloud": {"projectKind": "cloud", "projectId": "cloud-1"},
        },
    }
    (codex_home / ".codex-global-state.json").write_text(
        json.dumps(global_state, ensure_ascii=False), encoding="utf-8"
    )

    writer = sqlite3.connect(db_path)
    try:
        writer.executemany(
            "INSERT INTO projects (id, name, position) VALUES (?, ?, ?)",
            [
                ("app-a", "Alpha", 0),
                ("app-b", "Beta", 1),
                ("app-del", "DeletedProj", 2),
                ("app-gpt", "InternalGPT", 3),
            ],
        )
        writer.executemany(
            "INSERT INTO project_roots (project_id, position, path) VALUES (?, ?, ?)",
            [
                ("app-a", 0, str(repo_a)),
                ("app-b", 0, str(repo_b)),
                ("app-del", 0, str(repo_del)),
                ("app-gpt", 0, str(chatgpt_internal)),
            ],
        )
        writer.executemany(
            """INSERT INTO threads
               (id, cwd, project_id, name, title, first_user_message, thread_source, source,
                archived, is_pinned, model, reasoning_effort, model_provider, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                ("t-main", str(repo_a), "app-a", "Main Chat", "Title", "First", "user", "vscode",
                 0, 0, "gpt-6.2-sol", "ultra", "openai", 100.0, 200.0),
                ("t-pinned", str(repo_a), "app-a", "Pinned Older", "Title", "First", "user", "vscode",
                 0, 1, "gpt-6.1-sol", "high", "openai", 50.0, 60.0),
                ("t-assigned", str(tmp_path / "other"), None, "Assigned Via GS", "Title", "First", "user", "vscode",
                 0, 0, None, None, None, 90.0, 150.0),
                ("t-archived", str(repo_a), "app-a", "Archived Chat", "Title", "First", "user", "vscode",
                 1, 0, None, None, None, 100.0, 250.0),
                ("t-subagent", str(repo_a), "app-a", "Subagent", "Title", "First", "subagent", "vscode",
                 0, 0, None, None, None, 100.0, 250.0),
                ("t-guardian", str(repo_a), "app-a", "Guardian", "Title", "First", "guardian_review", "vscode",
                 0, 0, None, None, None, 100.0, 250.0),
                ("t-exec", str(repo_a), "app-a", "Headless Exec", "Title", "First", "user", "exec",
                 0, 0, None, None, None, 100.0, 250.0),
                ("t-projectless", str(tmp_path / "scratch"), None, "Projectless", "Title", "First", "user", "vscode",
                 0, 0, None, None, None, 100.0, 250.0),
                ("t-deleted-proj", str(repo_del), "app-del", "On Deleted Proj", "Title", "First", "user", "vscode",
                 0, 0, None, None, None, 100.0, 250.0),
                ("t-deleted-assign", str(repo_a), None, "Assigned Deleted", "Title", "First", "user", "vscode",
                 0, 0, None, None, None, 100.0, 250.0),
                ("t-cloud", str(repo_a), None, "Cloud Assigned", "Title", "First", "user", "vscode",
                 0, 0, None, None, None, 100.0, 250.0),
            ],
        )
        writer.commit()

        before = _snapshot_files(codex_home)
        fp1 = codex_bridge.get_codex_state_fingerprint()
        cat1 = codex_bridge.get_codex_catalog()
        assert fp1 is not None and cat1 is not None
        assert _snapshot_files(codex_home) == before

        assert [(p["name"], p["position"], p["is_git"]) for p in cat1["projects"]] == [
            ("Beta", 0, False),
            ("Alpha", 1, True),
        ]
        assert cat1["projects"][0]["threads"] == []
        assert [t["session_id"] for t in cat1["projects"][1]["threads"]] == [
            "t-pinned", "t-main", "t-assigned",
        ]

        # Uncheckpointed WAL mutations: add new project + thread, rename thread, archive one, reorder.
        repo_c = tmp_path / "repo-gamma"
        repo_c.mkdir()
        writer.execute("INSERT INTO projects VALUES ('app-c', 'Gamma', 4)")
        writer.execute("INSERT INTO project_roots VALUES ('app-c', 0, ?)", (str(repo_c),))
        writer.execute(
            """INSERT INTO threads VALUES
               ('t-new', ?, 'app-c', 'Brand New Chat', NULL, NULL, 'user', 'vscode',
                0, 0, 'gpt-6.2-sol', 'high', 'openai', 300.0, 310.0)""",
            (str(repo_c),),
        )
        writer.execute("UPDATE threads SET archived = 1 WHERE id = 't-assigned'")
        writer.commit()
        wal = codex_home / "state_5.sqlite-wal"
        wal_ns = wal.stat().st_mtime_ns + 1_000_000_000
        os.utime(wal, ns=(wal_ns, wal_ns))

        (codex_home / "session_index.jsonl").write_text(
            json.dumps({"id": "t-main", "thread_name": "Renamed Live Title"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        global_state["project-order"] = ["leg-a", "leg-b"]
        (codex_home / ".codex-global-state.json").write_text(
            json.dumps(global_state, ensure_ascii=False), encoding="utf-8"
        )

        fp2 = codex_bridge.get_codex_state_fingerprint()
        assert fp2 != fp1
        before = _snapshot_files(codex_home)
        cat2 = codex_bridge.get_codex_catalog()
        assert _snapshot_files(codex_home) == before
        assert cat2 is not None
        assert [p["name"] for p in cat2["projects"]] == ["Alpha", "Beta", "Gamma"]
        alpha_threads = cat2["projects"][0]["threads"]
        assert [(t["session_id"], t["title"]) for t in alpha_threads] == [
            ("t-pinned", "Pinned Older"),
            ("t-main", "Renamed Live Title"),
        ]
        assert [t["session_id"] for t in cat2["projects"][2]["threads"]] == ["t-new"]

        # Permanently delete a thread and a project.
        writer.execute("DELETE FROM threads WHERE id = 't-pinned'")
        writer.execute("DELETE FROM project_roots WHERE project_id = 'app-b'")
        writer.execute("DELETE FROM projects WHERE id = 'app-b'")
        writer.commit()
        wal_ns = wal.stat().st_mtime_ns + 1_000_000_000
        os.utime(wal, ns=(wal_ns, wal_ns))

        cat3 = codex_bridge.get_codex_catalog()
        assert cat3 is not None
        assert [p["name"] for p in cat3["projects"]] == ["Alpha", "Gamma"]
        assert [t["session_id"] for t in cat3["projects"][0]["threads"]] == ["t-main"]
    finally:
        writer.close()
        codex_bridge.invalidate_catalog_cache()


def test_daemon_codex_catalog_projection_and_sse_live_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    codex_home = tmp_path / "codex_home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    db_path = _init_catalog_codex_home(codex_home)

    repo_main = tmp_path / "斗罗大陆神界传说"
    repo_git_sub = repo_main / "local-edition"
    repo_empty = tmp_path / "工具"
    repo_xian = tmp_path / "xian"
    for d in (repo_main, repo_git_sub, repo_empty, repo_xian):
        d.mkdir(parents=True)
    (repo_git_sub / ".git").mkdir()

    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.executemany(
            "INSERT INTO projects (id, name, position) VALUES (?, ?, ?)",
            [("p-tools", "工具", 0), ("p-douluo", "斗罗大陆神界传说", 1)],
        )
        conn.executemany(
            "INSERT INTO project_roots VALUES (?, ?, ?)",
            [("p-tools", 0, str(repo_empty)), ("p-douluo", 0, str(repo_main))],
        )
        conn.execute(
            """INSERT INTO threads VALUES
               ('sess-relay', ?, 'p-douluo', '接力开发进度报告', NULL, NULL, 'user', 'vscode',
                0, 0, 'gpt-6.2-sol', 'ultra', 'openai', 100.0, 200.0)""",
            (str(repo_main),),
        )
    codex_bridge.invalidate_catalog_cache()

    settings = Settings(host="127.0.0.1", port=_free_port(), data_dir=tmp_path / "data")
    with Database(settings=settings) as db:
        proj_main = db.ensure_project(repo_main, name="斗罗大陆神界传说")
        proj_sub = db.ensure_project(repo_git_sub, name="local-edition", is_git=True)
        proj_xian = db.ensure_project(repo_xian, name="xian")
        fixture_dir = repo_main / "docs" / "verification" / "s1" / "fixture"
        fixture_dir.mkdir(parents=True)
        proj_fix = db.ensure_project(fixture_dir, name="Fixture")

        r1 = db.create_run(project_id=proj_sub["id"], mode="swarm", title="Subdir bound run", session_id="sess-relay")
        r_boost = db.create_run(project_id=proj_main["id"], mode="boost", title="Boost bound run", session_id="sess-relay")
        db.create_run(project_id=proj_main["id"], mode="swarm", title="Unbound legacy")
        db.create_run(project_id=proj_xian["id"], mode="boost", title="Non-Codex xian")
        db.create_run(project_id=proj_fix["id"], mode="teamwork", title="Fixture run")

        app = create_app(settings, db=db, start_watchdog=False)
        with TestClient(app) as client:
            health = client.get("/api/health").json()
            assert "codex_catalog_sync_v1" in health["capabilities"]

            proj_resp = client.get("/api/projects").json()
            assert "codex_catalog" in proj_resp
            catalog = proj_resp["codex_catalog"]
            assert [p["name"] for p in catalog["projects"]] == ["工具", "斗罗大陆神界传说"]
            tools_proj, douluo_proj = catalog["projects"]
            assert tools_proj["id"].startswith("codex:")
            assert tools_proj["threads"] == []
            assert douluo_proj["id"] == proj_main["id"]
            assert [t["title"] for t in douluo_proj["threads"]] == ["接力开发进度报告"]
            assert {r["id"] for r in douluo_proj["threads"][0]["runs"]} == {r1["id"], r_boost["id"]}
            assert all(r["canonical_project_id"] == proj_main["id"] for r in douluo_proj["threads"][0]["runs"])

            # Mode filtering on /api/codex/catalog and /api/runs?project_id=codex:...
            swarm_cat = client.get("/api/codex/catalog", params={"mode": "swarm"}).json()
            assert [r["id"] for r in swarm_cat["projects"][1]["threads"][0]["runs"]] == [r1["id"]]
            assert client.get("/api/runs", params={"project_id": tools_proj["id"]}).json()["runs"] == []

            # Subdir bound run is also returned under parent DB project query.
            douluo_runs = client.get("/api/runs", params={"project_id": proj_main["id"], "mode": "swarm"}).json()["runs"]
            assert r1["id"] in {r["id"] for r in douluo_runs}

            # Existing /api/sessions still keeps unbound in sessions[] while attaching codex_catalog.
            sess_resp = client.get("/api/sessions").json()
            assert any(s["session_id"] == "unbound" for s in sess_resp["sessions"])
            assert "codex_catalog" in sess_resp

    # Live SSE push when Codex state changes on disk.
    async def sse_scenario(base_url: str) -> None:
        async with httpx.AsyncClient(base_url=base_url, trust_env=False, timeout=5.0) as client:
            async with client.stream("GET", "/api/events") as stream:
                stream.raise_for_status()
                seen_catalog_event = False
                lines = stream.aiter_lines()
                # Wait briefly for initial connect line, then mutate state_5.sqlite.
                first = await asyncio.wait_for(lines.__anext__(), timeout=2.0)
                assert first.startswith("retry:")
                with closing(sqlite3.connect(db_path)) as conn, conn:
                    conn.execute("UPDATE threads SET name = '实时更新后的标题' WHERE id = 'sess-relay'")
                db_ns = db_path.stat().st_mtime_ns + 1_000_000_000
                os.utime(db_path, ns=(db_ns, db_ns))

                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    line = await asyncio.wait_for(lines.__anext__(), timeout=2.5)
                    if line == "event: codex_catalog_updated":
                        seen_catalog_event = True
                        break
                assert seen_catalog_event

                updated = (await client.get("/api/codex/catalog")).json()
                assert updated["projects"][1]["threads"][0]["title"] == "实时更新后的标题"

    live_settings = Settings(host="127.0.0.1", port=_free_port(), data_dir=tmp_path / "live_data")
    live_app = create_app(live_settings, start_watchdog=False, codex_watch_interval_sec=0.05)

    # Verify using a running uvicorn server via _RunningServer-like Thread + app instance
    import threading
    import uvicorn

    config = uvicorn.Config(live_app, host=live_settings.host, port=live_settings.port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(50):
            try:
                httpx.get(f"{live_settings.base_url}/api/health", timeout=0.5).raise_for_status()
                break
            except Exception:
                time.sleep(0.05)
        asyncio.run(sse_scenario(live_settings.base_url))
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        codex_bridge.invalidate_catalog_cache()


def test_catalog_merges_run_bound_cli_and_keeps_standalone_projects(tmp_path, monkeypatch):
    codex_home = tmp_path / "codex_home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    db_path = _init_catalog_codex_home(codex_home)
    desktop = tmp_path / "desktop"
    second_root = tmp_path / "secondary-desktop"
    cli_repo = tmp_path / "codex-swarm-smoke-20261121"
    unregistered_repo = tmp_path / "unregistered"
    for path in (desktop, second_root, cli_repo, unregistered_repo):
        path.mkdir()
    (cli_repo / ".git").mkdir()
    (unregistered_repo / ".git").mkdir()
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("INSERT INTO projects VALUES ('desktop', 'Desktop', 0)")
        conn.executemany("INSERT INTO project_roots VALUES ('desktop', ?, ?)", [
            (0, str(desktop)), (1, str(second_root)),
        ])
        conn.executemany(
            """INSERT INTO threads
               (id, cwd, project_id, title, thread_source, source, archived,
                model, reasoning_effort, model_provider)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                ("desktop-chat", str(desktop), "desktop", "Desktop Chat", "user", "vscode", 0, None, None, None),
                ("secondary-cli", str(second_root / "src"), None, "CLI Chat", "user", "cli", 0, None, None, None),
                ("merged-exec", str(tmp_path / "different-cwd"), None, "Work", "user", "exec", 0,
                 "gpt-6.2-sol", "ultra", "local"),
                ("standalone-cli", str(cli_repo / "src"), None, "Standalone CLI Chat", "user", "cli", 0,
                 None, None, None),
                ("standalone-exec", str(cli_repo / "src"), None, "Exec Title", "user", "exec", 0,
                 "gpt-6.2-sol", "high", "openai"),
                ("challenge-exec", str(cli_repo), None, "Challenge", "user", "exec", 0, None, None, None),
                ("archived-cli", str(cli_repo), None, "Archived", "user", "cli", 1, None, None, None),
                ("internal-cli", str(cli_repo), None, "Internal Run", "guardian_review", "cli", 0, None, None, None),
                ("unregistered-cli", str(unregistered_repo / "src"), None, "Solo CLI", "user", "cli", 0,
                 None, None, None),
            ],
        )

    settings = Settings(host="127.0.0.1", port=_free_port(), data_dir=tmp_path / "data")
    with Database(settings=settings) as db:
        desktop_project = db.ensure_project(desktop)
        child = db.ensure_project(second_root / "src")
        standalone = db.ensure_project(cli_repo)
        merged_run = db.create_run(project_id=child["id"], mode="swarm", title="Registered run", session_id="merged-exec")
        cli_run = db.create_run(project_id=standalone["id"], mode="swarm", title="Run", session_id="standalone-exec")
        missing_run = db.create_run(
            project_id=standalone["id"], mode="boost", title="Missing SQLite row", session_id="missing",
            metadata={"codex_source": "exec"},
        )
        for session_id in ("archived-cli", "internal-cli"):
            db.create_run(project_id=standalone["id"], mode="swarm", title="Hidden", session_id=session_id)

        with TestClient(create_app(settings, db=db, start_watchdog=False)) as client:
            catalog = client.get("/api/codex/catalog").json()
            assert client.get("/api/projects").json()["codex_catalog"] == catalog
            assert catalog["available"] is True
            desktop_node, cli_node, unregistered_node = catalog["projects"]
            assert desktop_node["id"] == desktop_project["id"]
            assert desktop_node["project_origin"] == "desktop"
            assert desktop_node["client_kinds"] == ["cli", "desktop"]
            desktop_threads = {t["session_id"]: t for t in desktop_node["threads"]}
            assert set(desktop_threads) == {"desktop-chat", "secondary-cli", "merged-exec"}
            merged = desktop_threads["merged-exec"]
            assert (merged["codex_source"], merged["client_kind"], merged["cwd"]) == (
                "exec", "cli", str(tmp_path / "different-cwd"),
            )
            assert (merged["title"], merged["model"], merged["reasoning_effort"], merged["model_provider"]) == (
                "Work", "gpt-6.2-sol", "ultra", "local",
            )
            [run] = merged["runs"]
            assert (run["id"], run["project_id"], run["canonical_project_id"]) == (
                merged_run["id"], child["id"], desktop_node["id"],
            )

            assert cli_node["id"] == cli_node["db_project_id"] == standalone["id"]
            assert cli_node["project_origin"] == "cli"
            assert cli_node["client_kinds"] == ["cli"]
            assert cli_node["is_git"] is True
            cli_threads = {t["session_id"]: t for t in cli_node["threads"]}
            assert set(cli_threads) == {"standalone-cli", "standalone-exec", "missing"}
            assert cli_threads["standalone-exec"]["runs"][0]["id"] == cli_run["id"]
            assert cli_threads["missing"]["runs"][0]["id"] == missing_run["id"]
            assert (cli_threads["missing"]["title"], cli_threads["missing"]["codex_source"]) == (
                "Missing SQLite row", "exec",
            )
            assert cli_node["active_run_count"] == cli_node["active_session_count"] == cli_node["total_run_count"] == 2
            assert unregistered_node["id"].startswith("codex:cli:")
            assert unregistered_node["db_project_id"] is None
            assert unregistered_node["root_path"] == str(unregistered_repo)
            assert unregistered_node["is_git"] is True
            assert {r["id"] for r in catalog["global_active_runs"]} == {
                cli_run["id"], missing_run["id"], merged_run["id"],
            }
            assert catalog["concurrent_risks"] == [{
                "canonical_project_id": standalone["id"],
                "concurrent_project_active_count": 2,
                "concurrent_local_write_risk": True,
            }]
            snapshot = client.get(f"/api/runs/{cli_run['id']}/snapshot").json()["run"]
            assert snapshot["project_id"] == cli_node["id"]
            assert snapshot["codex_thread_name"] == "Exec Title"

        # Without a readable Codex database the legacy state.db endpoints remain usable.
        monkeypatch.setenv("CODEX_HOME", str(tmp_path / "missing-codex"))
        with TestClient(create_app(settings, db=db, start_watchdog=False)) as client:
            assert client.get("/api/codex/catalog").json()["available"] is False
            response = client.get("/api/projects").json()
            assert "codex_catalog" not in response
            assert standalone["id"] in {p["id"] for p in response["projects"]}
            assert any(s["session_id"] == "missing" for s in client.get("/api/sessions").json()["sessions"])


@pytest.mark.parametrize("with_thread", [True, False])
def test_cli_only_state_without_desktop_tables(tmp_path, monkeypatch, with_thread):
    codex_home = tmp_path / "codex_home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    db_path = _init_catalog_codex_home(codex_home)
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.executescript("""
            DROP TABLE project_roots;
            DROP TABLE projects;
            DROP TABLE threads;
            CREATE TABLE threads (id TEXT PRIMARY KEY, cwd TEXT, title TEXT, source TEXT, model TEXT);
        """)
        if with_thread:
            conn.execute("INSERT INTO threads VALUES ('cli', ?, 'CLI Title', 'cli', 'model')", (str(tmp_path),))

    settings = Settings(host="127.0.0.1", port=_free_port(), data_dir=tmp_path / "data")
    with Database(settings=settings) as db:
        project = db.ensure_project(tmp_path)
        run = db.create_run(project_id=project["id"], mode="swarm", title="CLI run", session_id="cli")
        with TestClient(create_app(settings, db=db, start_watchdog=False)) as client:
            catalog = client.get("/api/codex/catalog").json()
            assert catalog["available"] is True
            [node] = catalog["projects"]
            assert (node["id"], node["project_origin"], node["client_kinds"]) == (project["id"], "cli", ["cli"])
            [thread] = node["threads"]
            assert (thread["title"], thread["model"], thread["codex_source"]) == (
                ("CLI Title", "model", "cli") if with_thread else ("CLI run", None, None)
            )
            assert [r["id"] for r in thread["runs"]] == [run["id"]]
            if with_thread:
                snapshot = client.get(f"/api/runs/{run['id']}/snapshot").json()["run"]
                assert (snapshot["codex_thread_name"], snapshot["codex_model"]) == ("CLI Title", "model")


def test_ui_mirrors_codex_tree_and_handles_live_sse_updates() -> None:
    run_dashboard(r'''(async()=>{
      const catalog = {
        available: true,
        global_active_runs: [],
        concurrent_risks: [],
        projects: [
          {
            id: 'codex:tools',
            codex_project_id: 'tools',
            name: '工具',
            display_name: '工具',
            root_path: 'G:\\工具',
            position: 0,
            is_git: false,
            project_kind: 'workspace',
            canonical_project_id: null,
            active_run_count: 0,
            active_session_count: 0,
            total_run_count: 0,
            thread_count: 0,
            threads: [],
          },
          {
            id: 'proj_douluo',
            codex_project_id: 'douluo',
            name: '斗罗大陆神界传说',
            display_name: '斗罗大陆神界传说',
            root_path: 'G:\\斗罗大陆神界传说',
            position: 1,
            is_git: false,
            project_kind: 'workspace',
            canonical_project_id: null,
            active_run_count: 0,
            active_session_count: 0,
            total_run_count: 2,
            thread_count: 1,
            threads: [
              {
                session_id: 'sess-relay',
                title: '接力开发进度报告',
                cwd: 'G:\\斗罗大陆神界传说',
                is_pinned: false,
                model: 'gpt-6.2-sol',
                reasoning_effort: 'ultra',
                active_count: 0,
                runs: [
                  {
                    id: 'run_2',
                    project_id: 'proj_douluo',
                    canonical_project_id: 'proj_douluo',
                    mode: 'swarm',
                    status: 'completed',
                    title: '斗罗 Phase 2 F23 收尾',
                    session_id: 'sess-relay',
                    session_seq: 2,
                    codex_thread_name: '接力开发进度报告',
                    codex_project_name: '斗罗大陆神界传说',
                    updated_at: 2000,
                  },
                  {
                    id: 'run_1',
                    project_id: 'proj_douluo',
                    canonical_project_id: 'proj_douluo',
                    mode: 'swarm',
                    status: 'completed',
                    title: 'Phase 2 平板续验',
                    session_id: 'sess-relay',
                    session_seq: 1,
                    codex_thread_name: '接力开发进度报告',
                    codex_project_name: '斗罗大陆神界传说',
                    updated_at: 1000,
                  },
                ],
              },
            ],
          },
          {
            id: 'codex:xwechat',
            codex_project_id: 'xwechat',
            name: '微信二次开发',
            display_name: '微信二次开发',
            root_path: 'G:\\xwechat',
            position: 2,
            is_git: false,
            project_kind: 'workspace',
            canonical_project_id: null,
            active_run_count: 0,
            active_session_count: 0,
            total_run_count: 0,
            thread_count: 1,
            threads: [
              {
                session_id: 'sess-xwechat',
                title: '无法启动，报错了。你可以查看下日志如果有的话',
                cwd: 'G:\\xwechat',
                is_pinned: true,
                model: 'gpt-6.1-sol',
                reasoning_effort: 'ultra',
                active_count: 0,
                runs: [],
              },
            ],
          },
        ],
      };

      const loading = loadProjects();
      assert.equal(requests[0].url, '/api/projects');
      requests[0].resolve({
        projects: [{ id: 'fixture', name: 'Fixture', project_kind: 'fixture' }],
        codex_catalog: catalog,
      });
      await new Promise((r) => setImmediate(r));
      assert.equal(requests[1].url, '/api/runs/run_2/snapshot');
      requests[1].resolve({
        run: catalog.projects[1].threads[0].runs[0],
        workers: [],
        messages: [],
      });
      await loading;

      // Window 1: Project Popover has Codex projects only, zero fixtures.
      renderProjectPopover();
      const popHtml = el.projectPopoverList.innerHTML;
      assert.ok(popHtml.includes('全部项目（Codex 目录）'));
      assert.ok(popHtml.includes('工具') && popHtml.includes('斗罗大陆神界传说') && popHtml.includes('微信二次开发'));
      assert.ok(!popHtml.includes('临时与验收夹具') && !popHtml.includes('Fixture'));

      // Window 2: Sidebar shows Codex project folders, 暂无聊天, full thread titles, 0-run leaf, nested #2/#1, zero unbound.
      const sideHtml = el.runSidebarList.innerHTML;
      assert.ok(sideHtml.includes('data-codex-project="codex:tools"') && sideHtml.includes('暂无聊天'));
      assert.ok(sideHtml.includes('session-group-title-row') && sideHtml.includes('接力开发进度报告'));
      assert.ok(sideHtml.includes('#2') && sideHtml.includes('#1'));
      assert.ok(sideHtml.includes('data-select-thread="sess-xwechat"'));
      assert.ok(sideHtml.includes('无法启动，报错了。你可以查看下日志如果有的话'));
      assert.ok(!sideHtml.includes('未绑定会话任务'));

      // Collapsing a project folder keeps it collapsed even though it contains the selected run.
      state.codexProjectOpenOverrides.set('proj_douluo', false);
      renderRunSidebar();
      assert.ok(!el.runSidebarList.innerHTML.match(/<details[^>]*data-codex-project="proj_douluo"[^>]*\bopen\b/));
      state.codexProjectOpenOverrides.set('proj_douluo', true);

      // Clicking a 0-run Codex thread updates topbar & overview cleanly.
      await selectCodexThread('codex:xwechat', 'sess-xwechat');
      assert.equal(state.selectedRunId, null);
      assert.equal(state.selectedSessionId, 'sess-xwechat');
      assert.equal(el.runTitleHeading.textContent, '无法启动，报错了。你可以查看下日志如果有的话');
      assert.ok(el.overviewContent.innerHTML.includes('无法启动，报错了'));

      // Window 3: Ctrl+K palette includes Codex projects, 0-run threads, and runs, without fixtures/unbound.
      openCommandPalette();
      const cmdHtml = el.cmdPaletteResults.innerHTML;
      assert.ok(cmdHtml.includes('Codex 项目'));
      assert.ok(cmdHtml.includes('无法启动，报错了'));
      assert.ok(cmdHtml.includes('斗罗 Phase 2 F23 收尾'));
      assert.ok(!cmdHtml.includes('Fixture'));

      // Resolve initial background /api/sessions so connectGlobalEvents() opens /api/events.
      const sessReqIdx = requests.findIndex((r) => r.url === '/api/sessions');
      assert.ok(sessReqIdx >= 0);
      requests[sessReqIdx].resolve({ sessions: [], global_active_runs: [], codex_catalog: catalog });
      await new Promise((r) => setImmediate(r));

      // Live SSE codex_catalog_updated deletes selected thread & adds new project.
      const globalStream = streams.find((s) => s.url === '/api/events');
      assert.ok(globalStream && globalStream.events.codex_catalog_updated);
      const updatedCatalog = {
        ...catalog,
        projects: [
          ...catalog.projects.slice(0, 2),
          {
            id: 'codex:ppt',
            codex_project_id: 'ppt',
            name: 'PPT',
            display_name: 'PPT',
            root_path: 'C:\\PPT',
            position: 2,
            is_git: false,
            project_kind: 'workspace',
            canonical_project_id: null,
            active_run_count: 0,
            active_session_count: 0,
            total_run_count: 0,
            thread_count: 0,
            threads: [],
          },
        ],
      };
      globalStream.events.codex_catalog_updated({ data: JSON.stringify({ changed_at: 1 }) });
      await new Promise((r) => setImmediate(r));
      const latestReq = requests[requests.length - 1];
      assert.equal(latestReq.url, '/api/sessions');
      latestReq.resolve({ sessions: [], global_active_runs: [], codex_catalog: updatedCatalog });
      await new Promise((r) => setImmediate(r));

      assert.equal(state.selectedSessionId, null);
      assert.ok(el.runSidebarList.innerHTML.includes('PPT'));
      assert.ok(!el.runSidebarList.innerHTML.includes('微信二次开发'));
      assert.ok(!el.runSidebarList.innerHTML.includes('无法启动，报错了'));
    })()''')
