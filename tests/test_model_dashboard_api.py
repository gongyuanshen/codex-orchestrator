"""API and snapshot tests for multi-agent and session model visibility (schema 10)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from contextlib import closing
from itertools import count
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

from orchestrator import codex_bridge, db as db_module
from orchestrator.config import Settings
from orchestrator.daemon import build_markdown_report, create_app
from orchestrator.db import Database

from test_daemon_web import _RunningServer, _free_port


def _init_codex_state(
    codex_home: Path,
    *,
    projects: list[tuple[str, str, str]],
    threads: list[tuple[Any, ...]],
    edges: list[tuple[str, str, str]] = (),
) -> Path:
    codex_home.mkdir(parents=True, exist_ok=True)
    db_path = codex_home / "state_5.sqlite"
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.executescript(
            """
            DROP TABLE IF EXISTS thread_spawn_edges;
            DROP TABLE IF EXISTS threads;
            DROP TABLE IF EXISTS project_roots;
            DROP TABLE IF EXISTS projects;

            CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL);
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
                agent_path TEXT,
                agent_role TEXT,
                agent_nickname TEXT,
                model TEXT,
                reasoning_effort TEXT,
                model_provider TEXT,
                created_at REAL,
                updated_at REAL
            );
            CREATE TABLE thread_spawn_edges (
                parent_thread_id TEXT NOT NULL,
                child_thread_id TEXT NOT NULL,
                status TEXT
            );
            """
        )
        for pid, pname, root_path in projects:
            conn.execute("INSERT INTO projects VALUES (?, ?)", (pid, pname))
            conn.execute("INSERT INTO project_roots VALUES (?, ?, ?)", (pid, 0, root_path))
        conn.executemany(
            "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            threads,
        )
        conn.executemany(
            "INSERT INTO thread_spawn_edges VALUES (?, ?, ?)",
            edges,
        )
    codex_bridge._cache = None
    return db_path


@pytest.fixture()
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    codex_home = tmp_path / "codex_home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    codex_bridge._cache = None
    clock = count(1_800_000_000)
    monkeypatch.setattr(db_module, "time", SimpleNamespace(time=lambda: next(clock), sleep=time.sleep))
    yield tmp_path, codex_home
    codex_bridge._cache = None


def test_health_runs_sessions_and_snapshot_model_enrichment(isolated_env) -> None:
    tmp_path, codex_home = isolated_env
    project_root = tmp_path / "proj"
    project_root.mkdir()

    uuid_verifier = "01950000-0000-7000-8000-000000000002"
    _init_codex_state(
        codex_home,
        projects=[("p_codex", "Codex Proj", str(project_root))],
        threads=[
            (
                "sess-main", str(project_root), "p_codex", "Main Session", None, None,
                "user", "cli", None, None, None,
                "gpt-6.2-sol", "ultra", "openai", 1_799_999_990.0, 1_800_000_010.0,
            ),
            (
                "tid-coder-old", str(project_root), "p_codex", None, None, None,
                "subagent", '{"subagent":{}}', "/root/coder-1", "coder", "OldKestrel",
                "gpt-6.0-old", "low", "openai", 1_799_999_950.0, 1_799_999_960.0,
            ),
            (
                "tid-coder-1", str(project_root), "p_codex", None, None, None,
                "subagent", '{"subagent":{}}', "/root/coder-1", "coder", "Kestrel",
                "gpt-6.2-sol", "high", "openai", 1_800_000_005.0, 1_800_000_009.0,
            ),
            (
                uuid_verifier, str(project_root), "p_codex", None, None, None,
                "subagent", '{"subagent":{}}', "/root/verifier-internal", "verifier", "Hawk",
                "gpt-6.2-sol", "high", "openai", 1_800_000_006.0, 1_800_000_009.0,
            ),
            (
                "tid-l2-helper", str(project_root), "p_codex", None, None, None,
                "subagent", '{"subagent":{}}', "/root/coder-1/explorer", "explorer", "Finch",
                "gpt-5.4-mini", "medium", "openai", 1_800_000_007.0, 1_800_000_008.0,
            ),
            (
                "tid-l3-helper", str(project_root), "p_codex", None, None, None,
                "subagent", '{"subagent":{}}', "/root/coder-1/explorer/deep", "explorer", "Wren",
                "gpt-5.4-mini", "low", "openai", 1_800_000_008.0, 1_800_000_009.0,
            ),
            (
                "tid-adhoc-l1", str(project_root), "p_codex", None, None, None,
                "subagent", '{"subagent":{}}', "/root/adhoc-1", "explorer", "Scout",
                "gpt-5.4-mini", "medium", "openai", 1_800_000_007.5, 1_800_000_008.5,
            ),
            (
                "tid-adhoc-l2", str(project_root), "p_codex", None, None, None,
                "subagent", '{"subagent":{}}', "/root/adhoc-1/nested", "explorer", "Pipit",
                "gpt-5.4-mini", "medium", "openai", 1_800_000_008.2, 1_800_000_009.0,
            ),
        ],
        edges=[
            ("sess-main", "tid-coder-old", "completed"),
            ("sess-main", "tid-coder-1", "running"),
            ("sess-main", uuid_verifier, "running"),
            ("tid-coder-1", "tid-l2-helper", "running"),
            ("tid-l2-helper", "tid-l3-helper", "completed"),
            ("sess-main", "tid-adhoc-l1", "completed"),
            ("tid-adhoc-l1", "tid-adhoc-l2", "completed"),
        ],
    )

    settings = Settings(data_dir=tmp_path / "data")
    with Database(settings=settings) as db:
        proj = db.ensure_project(project_root, name="Proj", is_git=False)
        run = db.create_run(
            project_id=proj["id"],
            mode="swarm",
            title="Model Visibility Run",
            session_id="sess-main",
            turn_id="turn-1",
            trigger_model="gpt-6.1-sol",
            trigger_reasoning_effort="high",
        )
        # Ensure worker created_at is closer to tid-coder-1 than tid-coder-old.
        with db.transaction() as conn:
            conn.execute("UPDATE runs SET created_at = ? WHERE id = ?", (1_800_000_000.0, run["id"]))
        w_root = db.upsert_worker(run["id"], "coordinator", role="coder", agent_id="/root", status="working")
        w_coder = db.upsert_worker(run["id"], "coder-1", role="coder", agent_id="/root/coder-1", status="working")
        w_verifier = db.upsert_worker(run["id"], "verifier-1", role="verifier", agent_id=uuid_verifier, status="working")
        with db.transaction() as conn:
            conn.execute("UPDATE workers SET created_at = ? WHERE id = ?", (1_800_000_004.0, w_coder["id"]))

        app = create_app(settings, db=db, start_watchdog=False)
        with TestClient(app) as client:
            health = client.get("/api/health").json()
            assert "agent_model_tracking_v1" in health["capabilities"]

            runs = client.get("/api/runs", params={"project_id": proj["id"]}).json()["runs"]
            assert len(runs) == 1
            r_row = runs[0]
            assert r_row["codex_model"] == "gpt-6.2-sol"
            assert r_row["codex_reasoning_effort"] == "ultra"
            assert r_row["codex_model_provider"] == "openai"
            assert r_row["trigger_model"] == "gpt-6.1-sol"
            assert r_row["trigger_reasoning_effort"] == "high"

            sessions = client.get("/api/sessions", params={"project_id": proj["id"]}).json()["sessions"]
            assert len(sessions) == 1
            s_group = sessions[0]
            assert s_group["session_id"] == "sess-main"
            assert s_group["codex_model"] == "gpt-6.2-sol"
            assert s_group["codex_reasoning_effort"] == "ultra"
            assert s_group["codex_model_provider"] == "openai"

            snap_resp = client.get(f"/api/runs/{run['id']}/snapshot")
            assert snap_resp.status_code == 200
            snap = snap_resp.json()

            # Snapshot must be strictly read-only: persisted DB rows remain unmutated.
            assert db.get_worker(w_coder["id"])["codex_thread_id"] is None
            assert db.list_worker_subagents(run["id"]) == []

            by_name = {w["name"]: w for w in snap["workers"]}
            assert by_name["coordinator"]["codex_thread_id"] == "sess-main"
            assert by_name["coordinator"]["model"] == "gpt-6.2-sol"
            assert by_name["coordinator"]["reasoning_effort"] == "ultra"
            assert by_name["coordinator"]["model_source"] == "live"
            assert by_name["coordinator"]["subagents"] == []

            # Closest created_at tie-breaker selects tid-coder-1 over tid-coder-old.
            assert by_name["coder-1"]["codex_thread_id"] == "tid-coder-1"
            assert by_name["coder-1"]["model"] == "gpt-6.2-sol"
            assert by_name["coder-1"]["reasoning_effort"] == "high"
            assert by_name["coder-1"]["agent_nickname"] == "Kestrel"
            assert by_name["coder-1"]["model_source"] == "live"
            assert [s["thread_id"] for s in by_name["coder-1"]["subagents"]] == [
                "tid-l2-helper", "tid-l3-helper",
            ]
            assert by_name["coder-1"]["subagents"][0]["depth"] == 2
            assert by_name["coder-1"]["subagents"][0]["model_source"] == "live"
            assert by_name["coder-1"]["subagents"][1]["depth"] == 3

            # UUID worker matched via priority 4 and NOT leaked into session_subagents.
            assert by_name["verifier-1"]["codex_thread_id"] == uuid_verifier
            assert by_name["verifier-1"]["agent_nickname"] == "Hawk"
            assert by_name["verifier-1"]["model_source"] == "live"
            assert by_name["verifier-1"]["subagents"] == []

            assert len(snap["helper_subagents"]) == 2
            assert all(h["parent_worker_id"] == w_coder["id"] for h in snap["helper_subagents"])
            assert all(h["parent_worker_name"] == "coder-1" for h in snap["helper_subagents"])

            assert [s["thread_id"] for s in snap["session_subagents"]] == [
                "tid-adhoc-l1", "tid-adhoc-l2",
            ]
            adhoc1, adhoc2 = snap["session_subagents"]
            assert adhoc1["registered"] is False
            assert adhoc1["parent_worker_id"] is None
            assert adhoc1["parent_worker_name"] == "coordinator"
            assert adhoc1["status"] == "completed"
            assert adhoc1["model_source"] == "live"
            assert adhoc2["parent_worker_name"] == "/root/adhoc-1"
            assert adhoc2["registered"] is False

            # /root coordinator worker is NOT double-counted with root session.
            assert snap["model_summary"] == [
                {
                    "model": "gpt-5.4-mini",
                    "reasoning_effort": "medium",
                    "count": 3,
                    "roles": ["explorer"],
                },
                {
                    "model": "gpt-6.2-sol",
                    "reasoning_effort": "high",
                    "count": 2,
                    "roles": ["coder", "verifier"],
                },
                {
                    "model": "gpt-5.4-mini",
                    "reasoning_effort": "low",
                    "count": 1,
                    "roles": ["explorer"],
                },
                {
                    "model": "gpt-6.2-sol",
                    "reasoning_effort": "ultra",
                    "count": 1,
                    "roles": ["coordinator"],
                },
            ]

            md_resp = client.get(f"/api/runs/{run['id']}/export?format=md")
            assert md_resp.status_code == 200
            md = md_resp.text
            assert "- **主对话模型**：`gpt-6.2-sol (ultra)`" in md
            assert "- **触发时模型快照**：`gpt-6.1-sol (high)`" in md
            assert "| 名称 | 职责角色 | 模型 / 推理强度 |" in md
            assert "## 辅助子代理（L2+ 与未登记原生子代理）" in md
            assert "主会话（未登记）" in md
            assert "`coder-1`" in md
            assert "gpt-5.4-mini (medium)" in md

            json_resp = client.get(f"/api/runs/{run['id']}/export?format=json")
            assert json_resp.status_code == 200
            exported = json_resp.json()
            assert "exported_at_utc" in exported
            assert exported["model_summary"] == snap["model_summary"]
            assert exported["helper_subagents"] == snap["helper_subagents"]
            assert exported["session_subagents"] == snap["session_subagents"]


def test_multi_run_same_session_isolation(isolated_env) -> None:
    tmp_path, codex_home = isolated_env
    project_root = tmp_path / "proj"
    project_root.mkdir()

    # Runs are created only 2 seconds apart (<10s) to verify zero window overlap/leakage.
    _init_codex_state(
        codex_home,
        projects=[("p1", "Proj", str(project_root))],
        threads=[
            ("sess-shared", str(project_root), "p1", "Shared", None, None, "user", "cli",
             None, None, None, "gpt-6.2-sol", "high", "openai", 990.0, 1005.0),
            # Too old (< 1000 - 10 = 990): excluded from both runs.
            ("tid-too-old", str(project_root), "p1", None, None, None, "subagent", '{"subagent":{}}',
             "/root/exp-too-old", "explorer", "Ancient", "gpt-5.4-mini", "low", "openai", 985.0, 986.0),
            # Pre-run1 within 10s grace window of run 1 (990 <= 995 < 1002): belongs only to Run 1.
            ("tid-pre-r1", str(project_root), "p1", None, None, None, "subagent", '{"subagent":{}}',
             "/root/exp-pre-r1", "explorer", "PreR1", "gpt-5.4-mini", "medium", "openai", 995.0, 996.0),
            # Run 1 worker + L2 helper + ad-hoc explorer + L2 child.
            ("tid-w1", str(project_root), "p1", None, None, None, "subagent", '{"subagent":{}}',
             "/root/w1", "coder", "W1", "gpt-6.2-sol", "high", "openai", 1000.2, 1001.5),
            ("tid-w1-l2", str(project_root), "p1", None, None, None, "subagent", '{"subagent":{}}',
             "/root/w1/helper", "explorer", "W1L2", "gpt-5.4-mini", "medium", "openai", 1000.4, 1001.0),
            ("tid-adhoc-r1", str(project_root), "p1", None, None, None, "subagent", '{"subagent":{}}',
             "/root/adhoc-r1", "explorer", "AdhocR1", "gpt-5.4-mini", "medium", "openai", 1000.6, 1001.2),
            ("tid-adhoc-r1-l2", str(project_root), "p1", None, None, None, "subagent", '{"subagent":{}}',
             "/root/adhoc-r1/sub", "explorer", "AdhocR1Sub", "gpt-5.4-mini", "low", "openai", 1000.8, 1001.3),
            # Run 2 worker + L2 helper + ad-hoc explorer (created >= 1002.0).
            ("tid-w2", str(project_root), "p1", None, None, None, "subagent", '{"subagent":{}}',
             "/root/w2", "coder", "W2", "gpt-6.2-sol", "ultra", "openai", 1002.2, 1004.0),
            ("tid-w2-l2", str(project_root), "p1", None, None, None, "subagent", '{"subagent":{}}',
             "/root/w2/helper", "explorer", "W2L2", "gpt-5.4-mini", "high", "openai", 1002.4, 1003.5),
            ("tid-adhoc-r2", str(project_root), "p1", None, None, None, "subagent", '{"subagent":{}}',
             "/root/adhoc-r2", "explorer", "AdhocR2", "gpt-5.4-mini", "high", "openai", 1002.6, 1003.0),
        ],
        edges=[
            ("sess-shared", "tid-too-old", "completed"),
            ("sess-shared", "tid-pre-r1", "completed"),
            ("sess-shared", "tid-w1", "completed"),
            ("tid-w1", "tid-w1-l2", "completed"),
            ("sess-shared", "tid-adhoc-r1", "completed"),
            ("tid-adhoc-r1", "tid-adhoc-r1-l2", "completed"),
            ("sess-shared", "tid-w2", "running"),
            ("tid-w2", "tid-w2-l2", "running"),
            ("sess-shared", "tid-adhoc-r2", "running"),
        ],
    )

    settings = Settings(data_dir=tmp_path / "data")
    with Database(settings=settings) as db:
        proj = db.ensure_project(project_root, name="Proj", is_git=False)
        run1 = db.create_run(project_id=proj["id"], mode="swarm", title="Run 1", session_id="sess-shared")
        run2 = db.create_run(project_id=proj["id"], mode="swarm", title="Run 2", session_id="sess-shared")
        with db.transaction() as conn:
            conn.execute("UPDATE runs SET created_at = ? WHERE id = ?", (1000.0, run1["id"]))
            conn.execute("UPDATE runs SET created_at = ? WHERE id = ?", (1002.0, run2["id"]))
        db.upsert_worker(run1["id"], "w1", role="coder", agent_id="/root/w1", status="stopped")
        db.upsert_worker(run2["id"], "w2", role="coder", agent_id="/root/w2", status="working")

        app = create_app(settings, db=db, start_watchdog=False)
        with TestClient(app) as client:
            snap1 = client.get(f"/api/runs/{run1['id']}/snapshot").json()
            snap2 = client.get(f"/api/runs/{run2['id']}/snapshot").json()

            assert [w["name"] for w in snap1["workers"]] == ["w1"]
            assert [h["thread_id"] for h in snap1["helper_subagents"]] == ["tid-w1-l2"]
            assert [s["thread_id"] for s in snap1["session_subagents"]] == [
                "tid-pre-r1", "tid-adhoc-r1", "tid-adhoc-r1-l2",
            ]

            assert [w["name"] for w in snap2["workers"]] == ["w2"]
            assert [h["thread_id"] for h in snap2["helper_subagents"]] == ["tid-w2-l2"]
            assert [s["thread_id"] for s in snap2["session_subagents"]] == ["tid-adhoc-r2"]


def test_live_vs_snapshot_fallback_when_codex_state_cleared(isolated_env) -> None:
    tmp_path, codex_home = isolated_env
    project_root = tmp_path / "proj"
    project_root.mkdir()

    db_path = _init_codex_state(
        codex_home,
        projects=[("p1", "Proj", str(project_root))],
        threads=[
            ("sess-live", str(project_root), "p1", "Live Thread", None, None, "user", "cli",
             None, None, None, "gpt-6.2-sol", "ultra", "openai", 100.0, 200.0),
            ("tid-w1", str(project_root), "p1", None, None, None, "subagent", '{"subagent":{}}',
             "/root/w1", "coder", "Kestrel", "gpt-6.2-sol", "high", "openai", 105.0, 190.0),
            ("tid-l2", str(project_root), "p1", None, None, None, "subagent", '{"subagent":{}}',
             "/root/w1/sub", "explorer", "Finch", "gpt-5.4-mini", "medium", "openai", 110.0, 180.0),
            ("tid-adhoc", str(project_root), "p1", None, None, None, "subagent", '{"subagent":{}}',
             "/root/adhoc", "explorer", "Scout", "gpt-5.4-mini", "low", "openai", 115.0, 175.0),
        ],
        edges=[
            ("sess-live", "tid-w1", "running"),
            ("tid-w1", "tid-l2", "running"),
            ("sess-live", "tid-adhoc", "completed"),
        ],
    )

    settings = Settings(data_dir=tmp_path / "data")
    with Database(settings=settings) as db:
        proj = db.ensure_project(project_root, name="Proj", is_git=False)
        run = db.create_run(
            project_id=proj["id"],
            mode="teamwork",
            title="Live Then Cleared",
            session_id="sess-live",
            trigger_model="gpt-6.1-sol",
            trigger_reasoning_effort="high",
        )
        with db.transaction() as conn:
            conn.execute("UPDATE runs SET created_at = ? WHERE id = ?", (100.0, run["id"]))
        w_root = db.upsert_worker(run["id"], "coordinator", role="coder", agent_id="/root", status="working")
        w_coder = db.upsert_worker(run["id"], "w1", role="coder", agent_id="/root/w1", status="working")

        # Persist earlier snapshot values (different model/effort on /root, same on w1/l2).
        db.update_worker_model_meta(
            w_root["id"], codex_thread_id="sess-live",
            model="gpt-6.1-sol", reasoning_effort="high", model_provider="openai",
        )
        db.update_worker_model_meta(
            w_coder["id"], codex_thread_id="tid-w1",
            model="gpt-6.2-sol", reasoning_effort="high", model_provider="openai",
        )
        db.upsert_worker_subagents(
            run["id"],
            [
                {
                    "thread_id": "tid-l2",
                    "parent_worker_id": w_coder["id"],
                    "parent_thread_id": "tid-w1",
                    "depth": 2,
                    "agent_path": "/root/w1/sub",
                    "agent_role": "explorer",
                    "agent_nickname": "Finch",
                    "model": "gpt-5.4-mini",
                    "reasoning_effort": "medium",
                    "model_provider": "openai",
                    "edge_status": "running",
                    "created_at": 110.0,
                }
            ],
        )

        app = create_app(settings, db=db, start_watchdog=False)
        with TestClient(app) as client:
            live_snap = client.get(f"/api/runs/{run['id']}/snapshot").json()
            live_workers = {w["name"]: w for w in live_snap["workers"]}
            assert live_snap["run"]["codex_model"] == "gpt-6.2-sol"
            assert live_workers["coordinator"]["model"] == "gpt-6.2-sol"
            assert live_workers["coordinator"]["reasoning_effort"] == "ultra"
            assert live_workers["coordinator"]["model_source"] == "live"
            assert live_workers["w1"]["model_source"] == "live"
            assert live_workers["w1"]["subagents"][0]["model_source"] == "live"
            assert len(live_snap["session_subagents"]) == 1

            # Remove live Codex state_5.sqlite completely -> snapshot fallback must kick in.
            db_path.unlink()
            codex_bridge._cache = None

            fallback_snap = client.get(f"/api/runs/{run['id']}/snapshot").json()
            fb_workers = {w["name"]: w for w in fallback_snap["workers"]}
            assert fallback_snap["run"]["codex_model"] is None
            assert fallback_snap["run"]["trigger_model"] == "gpt-6.1-sol"
            assert fallback_snap["run"]["trigger_reasoning_effort"] == "high"

            assert fb_workers["coordinator"]["codex_thread_id"] == "sess-live"
            assert fb_workers["coordinator"]["model"] == "gpt-6.1-sol"
            assert fb_workers["coordinator"]["reasoning_effort"] == "high"
            assert fb_workers["coordinator"]["agent_nickname"] is None
            assert fb_workers["coordinator"]["model_source"] == "snapshot"
            assert fb_workers["coordinator"]["subagents"] == []

            assert fb_workers["w1"]["codex_thread_id"] == "tid-w1"
            assert fb_workers["w1"]["model"] == "gpt-6.2-sol"
            assert fb_workers["w1"]["agent_nickname"] is None
            assert fb_workers["w1"]["model_source"] == "snapshot"
            assert len(fb_workers["w1"]["subagents"]) == 1
            assert fb_workers["w1"]["subagents"][0]["thread_id"] == "tid-l2"
            assert fb_workers["w1"]["subagents"][0]["model"] == "gpt-5.4-mini"
            assert fb_workers["w1"]["subagents"][0]["model_source"] == "snapshot"

            assert len(fallback_snap["helper_subagents"]) == 1
            assert fallback_snap["helper_subagents"][0]["model_source"] == "snapshot"
            assert fallback_snap["session_subagents"] == []
            assert fallback_snap["model_summary"] == [
                {"model": "gpt-5.4-mini", "reasoning_effort": "medium", "count": 1, "roles": ["explorer"]},
                {"model": "gpt-6.1-sol", "reasoning_effort": "high", "count": 1, "roles": ["coordinator"]},
                {"model": "gpt-6.2-sol", "reasoning_effort": "high", "count": 1, "roles": ["coder"]},
            ]


def test_unbound_legacy_run_has_no_models_or_subagents(isolated_env) -> None:
    tmp_path, codex_home = isolated_env
    project_root = tmp_path / "proj"
    project_root.mkdir()

    # Populate state_5.sqlite for an unrelated session in the SAME project root.
    _init_codex_state(
        codex_home,
        projects=[("p1", "Proj", str(project_root))],
        threads=[
            ("other-sess", str(project_root), "p1", "Other Session", None, None, "user", "cli",
             None, None, None, "gpt-6.2-sol", "ultra", "openai", 100.0, 200.0),
            ("other-sub", str(project_root), "p1", None, None, None, "subagent", '{"subagent":{}}',
             "/root/coder", "coder", "Eagle", "gpt-6.2-sol", "high", "openai", 105.0, 150.0),
        ],
        edges=[("other-sess", "other-sub", "running")],
    )

    settings = Settings(data_dir=tmp_path / "data")
    with Database(settings=settings) as db:
        proj = db.ensure_project(project_root, name="Proj", is_git=False)
        legacy = db.create_run(project_id=proj["id"], mode="teamwork", title="Legacy Unbound")
        db.upsert_worker(legacy["id"], "coder", role="coder", agent_id="/root/coder", status="stopped")

        app = create_app(settings, db=db, start_watchdog=False)
        with TestClient(app) as client:
            [run_row] = client.get("/api/runs", params={"project_id": proj["id"]}).json()["runs"]
            assert run_row["codex_model"] is None
            assert run_row["codex_reasoning_effort"] is None
            assert run_row["codex_model_provider"] is None

            [session_group] = client.get("/api/sessions", params={"project_id": proj["id"]}).json()["sessions"]
            assert session_group["session_id"] == "unbound"
            assert session_group["codex_model"] is None
            assert session_group["codex_reasoning_effort"] is None
            assert session_group["codex_model_provider"] is None

            snap = client.get(f"/api/runs/{legacy['id']}/snapshot").json()
            assert snap["run"]["codex_model"] is None
            assert snap["run"]["trigger_model"] is None
            assert snap["helper_subagents"] == []
            assert snap["session_subagents"] == []
            assert snap["model_summary"] == []
            assert snap["workers"][0]["model"] is None
            assert snap["workers"][0]["model_source"] is None
            assert snap["workers"][0]["subagents"] == []


def test_sse_global_projection_exposes_trigger_model_and_hides_trigger_prompt(isolated_env) -> None:
    tmp_path, _codex_home = isolated_env
    project_root = tmp_path / "proj"
    project_root.mkdir()

    async def scenario(base_url: str) -> None:
        async with httpx.AsyncClient(base_url=base_url, trust_env=False, timeout=5.0) as client:
            async with client.stream("GET", "/api/events") as global_stream:
                global_stream.raise_for_status()
                resp = await client.post(
                    "/api/rpc",
                    json={
                        "tool": "run_create",
                        "arguments": {
                            "mode": "boost",
                            "title": "SSE Model Run",
                            "project_root": str(project_root),
                            "session_id": "sess-sse",
                            "turn_id": "turn-sse",
                            "trigger_prompt": "secret prompt must not leak",
                        },
                    },
                )
                resp.raise_for_status()
                run_id = resp.json()["result"]["id"]
                await client.post(
                    "/api/rpc",
                    json={"tool": "run_update", "arguments": {"run_id": run_id, "title": "SSE Updated"}},
                )

                async def _wait_for_run_updated() -> None:
                    current_event = "message"
                    async for line in global_stream.aiter_lines():
                        if line.startswith("event: "):
                            current_event = line.removeprefix("event: ").strip()
                        elif line.startswith("data: "):
                            data = json.loads(line.removeprefix("data: "))
                            if current_event == "run_updated" and isinstance(data.get("run"), dict):
                                projected = data["run"]
                                if projected.get("id") == run_id and projected.get("title") == "SSE Updated":
                                    assert "trigger_prompt" not in projected
                                    assert "trigger_model" in projected
                                    assert "trigger_reasoning_effort" in projected
                                    assert projected["trigger_model"] == "gpt-6.2-sol"
                                    assert projected["trigger_reasoning_effort"] == "ultra"
                                    return
                    raise AssertionError("Global SSE stream ended without projected run_updated event")

                await asyncio.wait_for(_wait_for_run_updated(), timeout=5.0)

    _init_codex_state(
        isolated_env[1],
        projects=[("p1", "Proj", str(project_root))],
        threads=[
            ("sess-sse", str(project_root), "p1", "SSE Thread", None, None, "user", "cli",
             None, None, None, "gpt-6.2-sol", "ultra", "openai", 100.0, 200.0),
        ],
    )
    settings = Settings(host="127.0.0.1", port=_free_port(), data_dir=tmp_path / "data")
    with _RunningServer(settings):
        asyncio.run(scenario(settings.base_url))


def test_build_markdown_report_subagents_and_pipe_escaping(tmp_path: Path) -> None:
    md = build_markdown_report(
        {
            "run": {
                "id": "run_md_1",
                "mode": "swarm",
                "status": "active",
                "title": "Model Report",
                "codex_model": "gpt-6.2-sol",
                "codex_reasoning_effort": "ultra",
                "trigger_model": "gpt-6.1-sol",
                "trigger_reasoning_effort": "high",
                "created_at": 1700000000,
                "updated_at": 1700000060,
            },
            "project": {"name": "demo", "root_path": str(tmp_path)},
            "task_packets": [],
            "workers": [
                {
                    "name": "coder-1",
                    "role": "coder",
                    "status": "working",
                    "workspace_id": None,
                    "current_task": "Implement feature",
                    "model": "gpt-6.2-sol",
                    "reasoning_effort": "high",
                    "last_heartbeat_at": 1700000050,
                }
            ],
            "helper_subagents": [
                {
                    "parent_worker_name": "coder-1",
                    "depth": 2,
                    "agent_path": "/root/coder-1/exp",
                    "agent_nickname": "Finch|A",
                    "agent_role": "explorer",
                    "model": "gpt-5.4-mini",
                    "reasoning_effort": "medium",
                }
            ],
            "session_subagents": [
                {
                    "registered": False,
                    "parent_worker_name": "coordinator",
                    "depth": 1,
                    "agent_path": "/root/adhoc",
                    "agent_nickname": "Scout",
                    "agent_role": "explorer",
                    "model": "gpt-5.4-mini",
                    "reasoning_effort": None,
                }
            ],
            "workspaces": [],
            "messages": [],
        }
    )
    assert "- **主对话模型**：`gpt-6.2-sol (ultra)`" in md
    assert "- **触发时模型快照**：`gpt-6.1-sol (high)`" in md
    assert "## 辅助子代理（L2+ 与未登记原生子代理）" in md
    assert "`coder-1`" in md
    assert "Finch/A" in md
    assert "主会话（未登记）" in md
    assert "`gpt-5.4-mini`" in md
