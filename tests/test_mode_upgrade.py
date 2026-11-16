"""Mode2 package activation cannot pass against a foundation-only daemon."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading

import pytest

from test_routea_upgrade import fixture, upgrade


def test_migration_failure_preserves_chinese_stderr_under_legacy_parent_encoding(fixture, monkeypatch):
    monkeypatch.setenv("PYTHONIOENCODING", "gbk")
    db_module = fixture[0] / "src/orchestrator/db.py"
    db_module.write_text("SCHEMA_VERSION = 4\nraise RuntimeError('迁移失败：中文路径')\n", encoding="utf-8")
    upgrade.prepare(*fixture[:4], port=fixture[4])
    with pytest.raises(upgrade.UpgradeError, match="迁移失败：中文路径"):
        upgrade.apply(fixture[3])
    migration = json.loads((fixture[3] / "migration.json").read_text(encoding="utf-8"))
    assert migration["returncode"] != 0
    assert "迁移失败：中文路径" in migration["stderr"]
    assert (fixture[3] / "apply-failed.json").is_file()
    assert not (fixture[3] / "applied.json").exists()


@pytest.mark.parametrize("mode_capability", [False, True])
def test_mode2_upgrade_verifies_its_mode_capability(fixture, mode_capability):
    source, target, data, backup, _ = fixture
    manifest = source / ".codex-plugin/plugin.json"
    manifest.write_text(json.dumps({"name": "tri-orchestrator", "version": "0.3.0"}), encoding="utf-8")
    db_module = source / "src/orchestrator/db.py"
    db_module.write_text(db_module.read_text(encoding="utf-8").replace("SCHEMA_VERSION = 4", "SCHEMA_VERSION = 5"), encoding="utf-8")
    health = {"status": "ok", "ok": True, "pid": os.getpid(), "data_dir": str(data),
              "software_version": "0.3.0", "schema_version": 5,
              "capabilities": ["durable_inbox_v1", "workflow_gates_v1", "async_inbox_wait_v1"]}
    if mode_capability:
        health["capabilities"].append("workflow_modes_v2")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(health).encode())

        def log_message(self, *_args):
            return

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        health["port"] = server.server_port
        upgrade.prepare(source, target, data, backup, port=server.server_port)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            if mode_capability:
                assert upgrade.verify(backup)["status"] == "backend_verified"
            else:
                with pytest.raises(upgrade.UpgradeError, match="mismatch"):
                    upgrade.verify(backup)
        finally:
            server.shutdown()
            thread.join()
