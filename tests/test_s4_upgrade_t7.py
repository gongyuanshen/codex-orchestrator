"""T7: rehearse the CLI upgrade on copies; real-machine input is explicitly opt-in."""

from contextlib import closing, contextmanager
from hashlib import file_digest
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import sysconfig
import tempfile
import time
from zipfile import ZipFile

import httpx
import pytest
from starlette.testclient import TestClient

from orchestrator.config import Settings
from orchestrator.daemon import create_app
from test_daemon_web import _free_port
from test_schema9_migration import _schema8_fixture, _upgrade_script


REPO = Path(__file__).resolve().parents[1]
UPGRADE = REPO / "scripts" / "upgrade.py"


@pytest.mark.parametrize("active", [True, False])
def test_cold_start_refuses_schema8_without_writing(tmp_path, active):
    settings = Settings(port=_free_port(), data_dir=tmp_path)
    _schema8_fixture(settings.db_path, tmp_path / "workspace")
    if not active:
        with closing(sqlite3.connect(settings.db_path)) as conn, conn:
            conn.execute("UPDATE runs SET status='completed'")
    upgrade = _upgrade_script()
    before = upgrade.database_snapshot(settings.db_path)
    with pytest.raises(RuntimeError, match=r"scripts/upgrade\.py"):
        with TestClient(create_app(settings, start_watchdog=False)):
            pytest.fail("Schema 8 reached daemon startup")
    assert upgrade.database_snapshot(settings.db_path) == before


def test_shim_reports_upgrade_required_before_spawning(tmp_path, monkeypatch):
    from orchestrator import mcp_shim

    settings = Settings(port=_free_port(), data_dir=tmp_path)
    _schema8_fixture(settings.db_path, tmp_path / "workspace")

    def unexpected_spawn(_settings):
        pytest.fail("Attempted to spawn against a Schema 8 database")

    monkeypatch.setattr(mcp_shim, "_spawn_detached_daemon", unexpected_spawn)
    with pytest.raises(RuntimeError, match=r"scripts/upgrade\.py"):
        mcp_shim.ensure_daemon_running(settings)


def test_cold_start_leaves_old_wal_and_index_unchanged(tmp_path):
    settings = Settings(port=_free_port(), data_dir=tmp_path)
    _schema8_fixture(settings.db_path, tmp_path / "workspace")
    with closing(sqlite3.connect(settings.db_path)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("UPDATE runs SET title='uncheckpointed'")
        writer.commit()
        before = {file.name: file.read_bytes() for file in tmp_path.glob("state.db*")}
        assert set(before) == {"state.db", "state.db-wal", "state.db-shm"}
        with pytest.raises(RuntimeError, match=r"scripts/upgrade\.py"):
            with TestClient(create_app(settings, start_watchdog=False)):
                pytest.fail("Schema 8 reached daemon startup")
        assert {file.name: file.read_bytes() for file in tmp_path.glob("state.db*")} == before

def _snapshot(source: Path, destination: Path) -> None:
    """Run SQLite backup against a stable DB/WAL copy, never a live SHM file."""
    wal = source.with_name(source.name + "-wal")
    destination.parent.mkdir()
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix="capture-") as capture:
        scratch = Path(capture) / source.name
        sources = (source, wal) if wal.is_file() else (source,)
        for path in sources:
            shutil.copyfile(path, scratch.with_name(path.name))
        for path in sources:
            with path.open("rb") as original, scratch.with_name(path.name).open("rb") as copy:
                assert file_digest(original, "sha256").digest() == file_digest(copy, "sha256").digest(), (
                    "Snapshot input changed during capture; no upgrade attempted"
                )
        assert wal.is_file() == (wal in sources), "WAL appeared/disappeared during capture"
        with closing(sqlite3.connect(scratch.as_uri() + "?mode=ro", uri=True)) as src:
            with closing(sqlite3.connect(destination)) as dst:
                src.backup(dst)


@contextmanager
def _rehearse(tmp_path: Path, target: Path, data: Path, codex_home: Path):
    port = _free_port()
    assert port != 18790
    backup = tmp_path / "backup"
    env = dict(
        os.environ,
        CODEX_HOME=str(codex_home),
        PYTHONPATH=str(REPO / "plugin" / "src"),
        PYTHONUTF8="1",
        TRI_ORCH_HOST="127.0.0.1",
        TRI_ORCH_PORT=str(port),
        TRI_ORCH_DATA_DIR=str(data),
    )

    def upgrade(*args):
        result = subprocess.run(
            [sys.executable, "-B", str(UPGRADE), *map(str, args), "--backup-dir", str(backup)],
            cwd=tmp_path, env=env, capture_output=True, encoding="utf-8", timeout=90,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    prepared = upgrade(
        "prepare", "--source-plugin", REPO / "plugin", "--target-plugin", target,
        "--data-dir", data, "--port", port,
    )
    assert prepared["status"] == "prepared"
    assert prepared["target_identity"]["version"] == "0.6.0"
    assert prepared["source_identity"]["version"] == "0.6.12"
    assert prepared["database_before"]["schema_version"] == 8
    applied = upgrade("apply")
    assert applied["status"] == "applied_service_stopped"
    assert applied["database_after"]["schema_version"] == 10

    # The Windows venv launcher has a different PID from the Python it starts.
    daemon_python = sys.executable
    pythonpath = [str(target / "src")]
    if os.name == "nt":
        daemon_python = sys._base_executable
        pythonpath.extend(dict.fromkeys(sysconfig.get_path(key) for key in ("purelib", "platlib")))
    log_path = tmp_path / "daemon.log"
    with log_path.open("w", encoding="utf-8") as log, subprocess.Popen(
        [daemon_python, "-B", "-m", "orchestrator.daemon", "--host", "127.0.0.1",
         "--port", str(port), "--data-dir", str(data)],
        cwd=target, env=dict(env, PYTHONPATH=os.pathsep.join(pythonpath)), stdout=log, stderr=subprocess.STDOUT,
    ) as daemon, httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=2) as client:
        try:
            deadline = time.monotonic() + 10
            while daemon.poll() is None and time.monotonic() < deadline:
                try:
                    health = client.get("/api/health")
                except httpx.TransportError:
                    time.sleep(0.05)
                    continue
                if health.status_code == 200:
                    body = health.json()
                    assert body["pid"] == daemon.pid
                    assert Path(body["data_dir"]).resolve() == data.resolve()
                    break
                time.sleep(0.05)
            else:
                raise AssertionError(log_path.read_text(encoding="utf-8"))
            assert upgrade("verify")["status"] == "backend_verified"
            yield client
        finally:
            if daemon.poll() is None:
                daemon.terminate()


def test_schema8_copy_upgrade_preserves_rows_and_session_evidence(tmp_path):
    source = tmp_path / "original.db"
    _schema8_fixture(source, tmp_path / "workspace")
    data = tmp_path / "data"
    with closing(sqlite3.connect(source)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("UPDATE runs SET status='completed' WHERE id='slice-run'")
        writer.commit()
        _snapshot(source, data / "state.db")
        assert writer.execute("SELECT MAX(version) FROM schema_migrations").fetchone() == (8,)

    target = tmp_path / "old-package"
    (target / ".codex-plugin").mkdir(parents=True)
    (target / ".codex-plugin" / "plugin.json").write_text(
        '{"name": "tri-orchestrator", "version": "0.6.0"}', encoding="utf-8",
    )
    with _rehearse(tmp_path, target, data, tmp_path / "empty-codex") as client:
        response = client.get("/api/sessions?project_id=main")
        response.raise_for_status()
        groups = {row["session_id"]: row["runs"] for row in response.json()["sessions"]}
        assert {(run["id"], run["session_seq"]) for run in groups["a-session"]} == {("slice-run", 1)}
        assert {(run["id"], run["session_seq"]) for run in groups["z-session"]} == {("old-run", 1)}
        assert [(run["id"], run["session_id"], run["session_seq"]) for run in groups["unbound"]] == [
            ("unbound", None, 0),
        ]
        assert groups["a-session"][0]["project_id"] == "slice"
        assert groups["a-session"][0]["canonical_project_id"] == "main"
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
        assert original.execute("SELECT MAX(version) FROM schema_migrations").fetchone() == (8,)


def test_real_machine_snapshot_upgrade(tmp_path):
    source, codex_home, baseline, expectations = (
        os.getenv(key) for key in (
            "TRI_ORCH_T7_STATE_DB", "TRI_ORCH_T7_CODEX_HOME", "TRI_ORCH_T7_BASELINE_REF",
            "TRI_ORCH_T7_EXPECTATIONS",
        )
    )
    if not all((source, codex_home, baseline, expectations)):
        pytest.skip("T7 real-machine rehearsal requires explicit paths, baseline ref, and scenario JSON")

    expected = json.loads(Path(expectations).read_text(encoding="utf-8"))
    run_ids = expected["runs"]
    parent_id = expected["project_id"]
    thread_id = expected["session_id"]
    data = tmp_path / "data"
    _snapshot(Path(source).resolve(strict=True), data / "state.db")
    with closing(sqlite3.connect((data / "state.db").as_uri() + "?mode=ro", uri=True)) as conn:
        original_bindings = {run_id: session_id for run_id, session_id in conn.execute(
            "SELECT run_id, session_id FROM session_run_bindings WHERE session_id = ?", (thread_id,),
        ) if run_id in run_ids}
    assert original_bindings, "Expected at least one preserved schema8 binding for the target session"

    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", "--end-of-options", f"{baseline}^{{commit}}"], cwd=REPO, text=True,
    ).strip()
    archive = subprocess.check_output(["git", "archive", "--format=zip", commit, "plugin"], cwd=REPO)
    with ZipFile(BytesIO(archive)) as files:
        files.extractall(tmp_path / "baseline")

    with _rehearse(tmp_path, tmp_path / "baseline" / "plugin", data, Path(codex_home).resolve(strict=True)) as client:
        projects = {row["id"]: row for row in client.get("/api/projects").raise_for_status().json()["projects"]}
        assert projects[parent_id]["display_name"] == expected["project_name"]
        assert all(projects[child]["canonical_project_id"] == parent_id for child in run_ids.values())

        groups = client.get("/api/sessions", params={"project_id": parent_id}).raise_for_status().json()["sessions"]
        runs = {run["id"]: (group, run) for group in groups for run in group["runs"]}
        [thread] = [group for group in groups if group["session_id"] == thread_id]
        assert thread["session_display_name"] == expected["session_name"]
        for run_id, child in run_ids.items():
            group, run = runs[run_id]
            assert run["project_id"] == child
            assert run["canonical_project_id"] == parent_id
            assert run["session_id"] == original_bindings.get(run_id)
            if run_id not in original_bindings:
                assert group["session_id"] == "unbound" and run["session_seq"] == 0
        sequences = [run["session_seq"] for run in thread["runs"] if run["id"] in run_ids]
        assert sequences == list(range(len(original_bindings), 0, -1))
        print(f"T7: {len(run_ids)} slices merged; bound: {len(sequences)}, unbound: {len(run_ids) - len(sequences)}")
