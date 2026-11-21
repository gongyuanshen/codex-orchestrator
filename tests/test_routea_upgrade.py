"""Controlled package upgrade never impersonates Codex's plugin registry."""
import importlib.util
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sqlite3
import threading

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "upgrade.py"
spec = importlib.util.spec_from_file_location("routea_upgrade", SCRIPT)
upgrade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upgrade)


@pytest.fixture
def fixture(tmp_path):
    source, target, data = (tmp_path / p for p in ("source", "installed", "data"))
    for p, version in ((source, "0.2.0"), (target, "0.1.2")):
        (p / ".codex-plugin").mkdir(parents=True)
        (p / "src" / "orchestrator").mkdir(parents=True)
        (p / ".codex-plugin" / "plugin.json").write_text(json.dumps({"name": "tri-orchestrator", "version": version}), encoding="utf-8")
        (p / "src" / "orchestrator" / "__init__.py").write_text("", encoding="utf-8")
        (p / "src" / "orchestrator" / "db.py").write_text('''SCHEMA_VERSION = 4
import sqlite3
class Database:
    def __init__(self, db_path):
        self.c = sqlite3.connect(db_path)
        self.c.execute('CREATE TABLE IF NOT EXISTS upgraded(value TEXT)')
        self.c.execute('INSERT OR IGNORE INTO schema_migrations VALUES(4,0)')
        self.c.commit()
    def close(self):
        self.c.close()
''', encoding="utf-8")
    (target / "keep.txt").write_text("unrelated", encoding="utf-8")
    (target / ".venv").mkdir()
    (target / ".venv" / "keep").write_text("runtime", encoding="utf-8")
    data.mkdir()
    with closing(sqlite3.connect(data / "state.db")) as db:
        db.executescript("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at REAL); INSERT INTO schema_migrations VALUES(3,0); CREATE TABLE runs(id TEXT, mode TEXT, status TEXT); CREATE TABLE receipts(id TEXT);")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return source, target, data, tmp_path / "backup", port


def prepare(fixture):
    return upgrade.prepare(*fixture[:4], port=fixture[4])


def test_prepare_stage_and_consistent_backup(fixture):
    report = prepare(fixture)
    source, target, data, backup, _ = fixture
    assert report["status"] == "prepared"
    assert (backup / "staged" / ".codex-plugin" / "plugin.json").read_bytes() == (source / ".codex-plugin" / "plugin.json").read_bytes()
    assert not (backup / "package-before" / ".venv").exists()
    assert upgrade.database_snapshot(backup / "state-before.db") == upgrade.database_snapshot(data / "state.db")
    assert json.loads((target / ".codex-plugin" / "plugin.json").read_text())["version"] == "0.1.2"


@pytest.mark.parametrize("mode", ["boost", "teamwork", "swarm"])
def test_prepare_rejects_active_task_in_any_mode(fixture, mode):
    with closing(sqlite3.connect(fixture[2] / "state.db")) as db, db:
        db.execute("INSERT INTO runs VALUES('active',?,'active')", (mode,))
    with pytest.raises(upgrade.UpgradeError, match="active"):
        prepare(fixture)
    assert not fixture[3].exists()


def test_future_schema_rejected_before_backup(fixture):
    with closing(sqlite3.connect(fixture[2] / "state.db")) as db, db:
        db.execute("INSERT INTO schema_migrations VALUES(99,0)")
    with pytest.raises(upgrade.UpgradeError, match="newer"):
        prepare(fixture)
    assert not fixture[3].exists()


def test_apply_migrates_from_staged_package_and_rollback(fixture):
    prepare(fixture)
    report = upgrade.apply(fixture[3])
    assert report["status"] == "applied_service_stopped"
    assert upgrade.database_snapshot(fixture[2] / "state.db")["schema_version"] == 4
    assert (fixture[1] / "keep.txt").read_text() == "unrelated"
    assert (fixture[1] / ".venv" / "keep").read_text() == "runtime"
    assert upgrade.rollback(fixture[3])["status"] == "rolled_back_service_stopped"
    assert upgrade.database_snapshot(fixture[2] / "state.db")["schema_version"] == 3


def test_apply_rejects_new_writes_since_prepare(fixture):
    prepare(fixture)
    with closing(sqlite3.connect(fixture[2] / "state.db")) as db, db:
        db.execute("INSERT INTO receipts VALUES('new')")
    with pytest.raises(upgrade.UpgradeError, match="changed"):
        upgrade.apply(fixture[3])
    assert not (fixture[3] / "applied.json").exists()


def test_apply_rechecks_live_state_after_staged_migration(fixture, monkeypatch):
    prepare(fixture)
    require_old_rows = upgrade.require_old_rows

    def check_then_write(candidate, before):
        require_old_rows(candidate, before)
        with closing(sqlite3.connect(fixture[2] / "state.db")) as db, db:
            db.execute("INSERT INTO receipts VALUES('concurrent write')")

    monkeypatch.setattr(upgrade, "require_old_rows", check_then_write)
    with pytest.raises(upgrade.UpgradeError, match="changed during migration"):
        upgrade.apply(fixture[3], database_only=True)
    with closing(sqlite3.connect(fixture[2] / "state.db")) as db:
        assert db.execute("SELECT id FROM receipts").fetchone() == ("concurrent write",)


def test_rollback_rejects_new_production_writes(fixture):
    prepare(fixture)
    upgrade.apply(fixture[3])
    with closing(sqlite3.connect(fixture[2] / "state.db")) as db, db:
        db.execute("INSERT INTO receipts VALUES('retain')")
    with pytest.raises(upgrade.UpgradeError, match="new writes"):
        upgrade.rollback(fixture[3])
    with closing(sqlite3.connect(fixture[2] / "state.db")) as db, db:
        assert db.execute("SELECT id FROM receipts").fetchone()[0] == "retain"


def test_listener_blocks_apply_without_termination(fixture):
    prepare(fixture)
    with socket.socket() as server:
        server.bind(("127.0.0.1", fixture[4]))
        server.listen()
        with pytest.raises(upgrade.UpgradeError, match="stopped"):
            upgrade.apply(fixture[3])
        assert server.getsockname()[1] == fixture[4]


def test_stage_tamper_rejected(fixture):
    prepare(fixture)
    (fixture[3] / "staged" / "src" / "orchestrator" / "db.py").write_text("tampered")
    with pytest.raises(upgrade.UpgradeError, match="staged"):
        upgrade.apply(fixture[3])


def test_managed_cache_version_mismatch_not_silently_installed(fixture):
    source, target, data, backup, port = fixture
    cached = target.parent / "plugins" / "cache" / "tri-orchestrator" / "tri-orchestrator" / "0.1.2"
    cached.parent.mkdir(parents=True)
    target.rename(cached)
    upgrade.prepare(source, cached, data, backup, port=port)
    with pytest.raises(upgrade.UpgradeError, match="host"):
        upgrade.apply(backup)


def test_database_only_upgrade_and_rollback_leave_managed_plugin_untouched(fixture):
    source, target, data, backup, port = fixture
    cached = target.parent / "plugins" / "cache" / "tri-orchestrator" / "tri-orchestrator" / "0.1.2"
    cached.parent.mkdir(parents=True)
    target.rename(cached)
    prepared = upgrade.prepare(source, cached, data, backup, port=port)

    report = upgrade.apply(backup, database_only=True)

    assert report["status"] == "applied_service_stopped"
    assert report["database_only"] is True
    assert report["database_after"]["schema_version"] == 4
    assert upgrade.package_files(cached) == prepared["target_files"]
    assert upgrade.package_identity(cached)["version"] == "0.1.2"

    # The host may remove the old cache when installing the new version.
    cached.rename(cached.with_name("old-cache-moved-by-host"))
    assert upgrade.rollback(backup)["status"] == "rolled_back_service_stopped"
    assert upgrade.database_snapshot(data / "state.db") == prepared["database_before"]
    assert not cached.exists()


def test_prepare_refuses_overlapping_paths(fixture):
    source, target, data, _, port = fixture
    with pytest.raises(upgrade.UpgradeError, match="overlap"):
        upgrade.prepare(source, target, data, target / "backup", port=port)


def test_failed_migration_retains_explicit_checkpoint(fixture):
    dbmodule = fixture[0] / "src" / "orchestrator" / "db.py"
    dbmodule.write_text("SCHEMA_VERSION = 4\nraise RuntimeError('deliberate migration failure')\n", encoding="utf-8")
    prepare(fixture)
    with pytest.raises(upgrade.UpgradeError, match="migration"):
        upgrade.apply(fixture[3])
    assert (fixture[3] / "apply-failed.json").is_file()
    assert not (fixture[3] / "applied.json").exists()


def test_repeat_apply_rejected(fixture):
    prepare(fixture)
    upgrade.apply(fixture[3])
    with pytest.raises(upgrade.UpgradeError, match="already"):
        upgrade.apply(fixture[3])


def test_read_snapshot_releases_sqlite_file_handle(fixture):
    path = fixture[2] / "state.db"
    upgrade.database_snapshot(path)
    # SQLite connection context managers commit/rollback but do not close handles.
    moved = path.with_name("released.db")
    path.rename(moved)
    moved.rename(path)


def test_live_pid_blocks_even_without_listener(fixture):
    prepare(fixture)
    (fixture[2] / "daemon.pid.json").write_text(json.dumps({"pid": os.getpid()}))
    with pytest.raises(upgrade.UpgradeError, match="stopped manually"):
        upgrade.apply(fixture[3])


def test_changed_installed_package_rejected(fixture):
    prepare(fixture)
    (fixture[1] / "keep.txt").write_text("concurrent edit")
    with pytest.raises(upgrade.UpgradeError, match="changed"):
        upgrade.apply(fixture[3])
    assert (fixture[1] / "keep.txt").read_text() == "concurrent edit"


def test_migration_cannot_erase_preexisting_receipts(fixture):
    with closing(sqlite3.connect(fixture[2] / "state.db")) as db, db:
        db.execute("INSERT INTO receipts VALUES('preserve')")
    module = fixture[0] / "src" / "orchestrator" / "db.py"
    code = module.read_text(encoding="utf-8")
    module.write_text(code.replace("self.c.commit()", "self.c.execute('DELETE FROM receipts')\n        self.c.commit()"), encoding="utf-8")
    prepare(fixture)
    with pytest.raises(upgrade.UpgradeError, match="preexisting"):
        upgrade.apply(fixture[3])
    with closing(sqlite3.connect(fixture[2] / "state.db")) as db:
        assert db.execute("SELECT id FROM receipts").fetchone()[0] == "preserve"
    assert json.loads((fixture[3] / "apply-failed.json").read_text())["status"] == "reconciliation_required"


@pytest.mark.parametrize("matching", [True, False])
def test_verify_actual_health_identity(fixture, matching):
    health = {
        "status": "ok", "ok": True, "pid": os.getpid(), "data_dir": str(fixture[2]),
        "software_version": "0.2.0", "schema_version": 4,
        "capabilities": ["durable_inbox_v1", "workflow_gates_v1", "async_inbox_wait_v1"] if matching else [],
    }
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == "/api/health"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(health).encode())
        def log_message(self, *_args):
            return
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        health["port"] = server.server_port
        upgrade.prepare(*fixture[:4], port=server.server_port)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            if matching:
                assert upgrade.verify(fixture[3])["status"] == "backend_verified"
            else:
                with pytest.raises(upgrade.UpgradeError, match="mismatch"):
                    upgrade.verify(fixture[3])
        finally:
            server.shutdown()
            thread.join()
