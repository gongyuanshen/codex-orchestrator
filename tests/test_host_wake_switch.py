"""v0.6.11 step 4.1: the reserved host-wake switch stays on coordinator relay until the host opens
both wake features, and never changes any gate."""
from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator import host_features
from orchestrator.board import BoardService
from orchestrator.config import Settings
from orchestrator.db import Database
from orchestrator.rpc_handlers import RpcDispatcher
from orchestrator.workspace_mgr import WorkspaceManager

# Shape of `codex features list` on codex-cli 0.166.0-alpha.5.1 (this machine, 2026-11-24).
CURRENT_HOST = """\
agent_message_board                      under development  false
hooks                                    stable             true
multi_agent_v2                           stable             true
wake_on_agent_messages                   under development  false
garbage line without a flag
"""
OPENED_HOST = CURRENT_HOST.replace(
    "agent_message_board                      under development  false",
    "agent_message_board                      stable             true",
).replace(
    "wake_on_agent_messages                   under development  false",
    "wake_on_agent_messages                   stable             true",
)


@pytest.fixture(autouse=True)
def _fresh_cache():
    host_features.reset_cache()
    yield
    host_features.reset_cache()


def _probe(text):
    return lambda: {"features": host_features.parse_feature_list(text), "error": None, "probed_at": 1.0}


def test_parse_feature_list_reads_stage_and_flag():
    parsed = host_features.parse_feature_list(CURRENT_HOST)
    assert parsed["agent_message_board"] == {"stage": "under development", "enabled": False}
    assert parsed["multi_agent_v2"] == {"stage": "stable", "enabled": True}
    assert "garbage" not in parsed


def test_default_mode_is_off_and_never_probes(monkeypatch):
    def explode():
        raise AssertionError("off mode must not probe the host")
    status = host_features.host_wake_status(probe=explode)
    assert status["mode"] == "off" and status["route"] == host_features.ROUTE_RELAY
    monkeypatch.setenv(host_features.HOST_WAKE_ENV, "bogus")
    assert host_features.host_wake_status(probe=explode)["mode"] == "off"


def test_auto_mode_relays_until_every_wake_feature_is_enabled():
    current = host_features.host_wake_status("auto", probe=_probe(CURRENT_HOST))
    assert current["route"] == host_features.ROUTE_RELAY
    assert current["features"] == {"agent_message_board": False, "wake_on_agent_messages": False}
    half = CURRENT_HOST.replace("agent_message_board                      under development  false",
                                "agent_message_board                      stable             true")
    assert host_features.host_wake_status("auto", probe=_probe(half))["route"] == host_features.ROUTE_RELAY
    opened = host_features.host_wake_status("auto", probe=_probe(OPENED_HOST))
    assert opened["route"] == host_features.ROUTE_HOST
    unknown = host_features.host_wake_status("auto", probe=lambda: {"features": None, "error": "no codex"})
    assert unknown["route"] == host_features.ROUTE_RELAY and "unknown" in unknown["reason"]


def test_probe_is_cached_and_missing_binary_is_reported(monkeypatch):
    monkeypatch.setenv("CODEX_BIN", "")
    monkeypatch.setattr(host_features.shutil, "which", lambda name: None)
    first = host_features.probe_features(now=100.0)
    assert first["features"] is None and "not found" in first["error"]
    calls = []
    monkeypatch.setattr(host_features.subprocess, "run", lambda *a, **k: calls.append(a))
    assert host_features.probe_features(now=150.0)["probed_at"] == 100.0
    assert calls == []
    assert host_features.cached_probe()["probed_at"] == 100.0


def test_board_post_reports_route_only_for_mentioned_bound_workers(tmp_path: Path, monkeypatch):
    settings = Settings(data_dir=tmp_path / "data")
    db = Database(settings=settings)
    try:
        dispatcher = RpcDispatcher(db, BoardService(db, settings), WorkspaceManager(db, settings))
        run_id = dispatcher.dispatch("run_create", {"mode": "swarm", "title": "wake", "project_root": str(tmp_path)})["id"]
        dispatcher.dispatch("worker_register", {"run_id": run_id, "name": "verifier", "role": "verifier",
                                                "agent_id": "/root/verifier"})
        dispatcher.dispatch("worker_register", {"run_id": run_id, "name": "pending", "role": "coder"})
        plain = dispatcher.dispatch("board_post", {"run_id": run_id, "sender": "w1", "summary": "no mentions"})
        assert "wake_route" not in plain
        unbound = dispatcher.dispatch("board_post", {"run_id": run_id, "sender": "w1", "summary": "x", "mentions": ["pending"]})
        assert "wake_route" not in unbound
        relayed = dispatcher.dispatch("board_post", {"run_id": run_id, "sender": "w1", "summary": "ready",
                                                     "mentions": ["verifier", "w1"]})
        assert relayed["wake_route"] == "coordinator_relay"
        assert relayed["wake_targets"] == [{"name": "verifier", "agent_id": "/root/verifier"}]
        assert "followup_task" in relayed["wake_hint"]

        monkeypatch.setenv(host_features.HOST_WAKE_ENV, "auto")
        monkeypatch.setattr(host_features, "probe_features", _probe(OPENED_HOST))
        opened = dispatcher.dispatch("board_post", {"run_id": run_id, "sender": "w1", "summary": "ready", "body_md": "@verifier"})
        assert opened["wake_route"] == "host_message_board" and "wake_hint" not in opened
    finally:
        db.close()


def test_health_reports_host_wake_without_probing(tmp_path: Path):
    from starlette.testclient import TestClient
    from orchestrator.daemon import create_app

    app = create_app(Settings(data_dir=tmp_path / "data"), start_watchdog=False)
    with TestClient(app) as client:
        body = client.get("/api/health").json()
    assert body["host_wake"]["mode"] == "off" and body["host_wake"]["route"] == "coordinator_relay"
