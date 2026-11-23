"""Tests for storage config, snapshot exclusions/.triignore/size guard, and GC."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from orchestrator.config import Settings, get_settings
from orchestrator.db import Database
from orchestrator.gc import collect_storage_report, main as gc_main, run_gc
from orchestrator.workspace_mgr import (
    SNAPSHOT_EXCLUDE_DIRS,
    WorkspaceCleanupGuardError,
    WorkspaceError,
    WorkspaceManager,
)

try:
    import _winapi
except ImportError:  # pragma: no cover
    _winapi = None  # type: ignore[assignment]


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return proc.stdout.strip()


def _init_git_repo(repo_dir: Path) -> Path:
    repo_dir.mkdir(parents=True, exist_ok=True)
    _git(repo_dir, "init", "-b", "main")
    _git(repo_dir, "config", "user.name", "Storage Test")
    _git(repo_dir, "config", "user.email", "storage@example.invalid")
    (repo_dir / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo_dir, "add", ".")
    _git(repo_dir, "commit", "-m", "initial")
    return repo_dir


def _setup_env(
    tmp_path: Path,
    project_root: Path,
    *,
    workflow_version: int = 0,
    **settings_kwargs: object,
) -> tuple[Settings, Database, WorkspaceManager, str]:
    settings = Settings(
        data_dir=tmp_path / "data",
        port_range=(23400, 23450),
        **settings_kwargs,  # type: ignore[arg-type]
    )
    db = Database.from_settings(settings)
    run = db.create_run(mode="swarm", title="storage gc test", project_root=project_root)
    if workflow_version != 5:
        with db.transaction() as conn:
            conn.execute("UPDATE runs SET workflow_version=? WHERE id=?", (workflow_version, run["id"]))
    return settings, db, WorkspaceManager(db, settings), run["id"]


def test_storage_settings_defaults_env_overrides_and_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for key in (
        "TRI_ORCH_MAX_SNAPSHOT_MB",
        "TRI_ORCH_GC_INTERVAL_SEC",
        "TRI_ORCH_RESULT_TTL_SEC",
        "TRI_ORCH_RESULT_MAX_FILES",
        "TRI_ORCH_WORKSPACE_RETENTION_SEC",
    ):
        monkeypatch.delenv(key, raising=False)

    cfg = get_settings(data_dir=tmp_path / "rt")
    assert cfg.max_snapshot_mb == 500
    assert cfg.gc_interval_sec == 3600.0
    assert cfg.result_ttl_sec == 86400
    assert cfg.result_max_files == 50
    assert cfg.workspace_retention_sec == 86400
    assert cfg.results_dir == cfg.data_dir / "results"

    monkeypatch.setenv("TRI_ORCH_MAX_SNAPSHOT_MB", "0")
    monkeypatch.setenv("TRI_ORCH_GC_INTERVAL_SEC", "120.5")
    monkeypatch.setenv("TRI_ORCH_RESULT_TTL_SEC", "600")
    monkeypatch.setenv("TRI_ORCH_RESULT_MAX_FILES", "12")
    monkeypatch.setenv("TRI_ORCH_WORKSPACE_RETENTION_SEC", "7200")

    overridden = get_settings(data_dir=tmp_path / "rt2")
    assert overridden.max_snapshot_mb == 0
    assert overridden.gc_interval_sec == 120.5
    assert overridden.result_ttl_sec == 600
    assert overridden.result_max_files == 12
    assert overridden.workspace_retention_sec == 7200
    assert overridden.results_dir == overridden.data_dir / "results"

    with pytest.raises(ValueError):
        Settings(data_dir=tmp_path, max_snapshot_mb=-1)
    with pytest.raises(ValueError):
        Settings(data_dir=tmp_path, gc_interval_sec=0)
    with pytest.raises(ValueError):
        Settings(data_dir=tmp_path, result_ttl_sec=-5)
    with pytest.raises(ValueError):
        Settings(data_dir=tmp_path, result_max_files=-1)
    with pytest.raises(ValueError):
        Settings(data_dir=tmp_path, workspace_retention_sec=-10)

    monkeypatch.setenv("TRI_ORCH_GC_INTERVAL_SEC", "-5")
    with pytest.raises(ValueError):
        get_settings(data_dir=tmp_path)


def test_snapshot_excludes_build_dirs_and_triignore_patterns(tmp_path: Path) -> None:
    for expected in (
        "bin",
        "obj",
        "dist",
        "build",
        "out",
        "target",
        ".tox",
        ".nox",
        ".coverage",
        "htmlcov",
        ".gradle",
        ".next",
        ".nuxt",
        ".turbo",
        ".parcel-cache",
    ):
        assert expected in SNAPSHOT_EXCLUDE_DIRS

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "src").mkdir()
    (proj / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (proj / ".triignore").write_text(
        "# comment\n"
        "*.log\n"
        "custom_cache/\n"
        "assets/*.bin\n"
        "/root_only.tmp\n"
        "**/scratch\n",
        encoding="utf-8",
    )
    for excluded_dir in ("dist", "build", "target", ".next", "htmlcov", ".pytest_cache"):
        (proj / excluded_dir).mkdir()
        (proj / excluded_dir / "artifact.txt").write_text("ignored", encoding="utf-8")

    (proj / "app.log").write_text("log", encoding="utf-8")
    (proj / "src" / "debug.log").write_text("nested log", encoding="utf-8")
    (proj / "custom_cache").mkdir()
    (proj / "custom_cache" / "blob.dat").write_text("cached", encoding="utf-8")
    (proj / "assets").mkdir()
    (proj / "assets" / "heavy.bin").write_bytes(b"12345")
    (proj / "assets" / "keep.txt").write_text("keep", encoding="utf-8")
    (proj / "root_only.tmp").write_text("root tmp", encoding="utf-8")
    (proj / "src" / "root_only.tmp").write_text("nested keep", encoding="utf-8")
    (proj / "src" / "scratch").mkdir()
    (proj / "src" / "scratch" / "temp.txt").write_text("scratch", encoding="utf-8")

    _, db, mgr, run_id = _setup_env(tmp_path, proj)
    try:
        ws = mgr.workspace_create(run_id, "worker1", copy_env=False)
        ws_path = Path(ws["path"])

        assert (ws_path / "src" / "main.py").is_file()
        assert (ws_path / "assets" / "keep.txt").is_file()
        assert (ws_path / "src" / "root_only.tmp").is_file()

        for excluded_dir in ("dist", "build", "target", ".next", "htmlcov", ".pytest_cache", "custom_cache"):
            assert not (ws_path / excluded_dir).exists()
        assert not (ws_path / "app.log").exists()
        assert not (ws_path / "src" / "debug.log").exists()
        assert not (ws_path / "assets" / "heavy.bin").exists()
        assert not (ws_path / "root_only.tmp").exists()
        assert not (ws_path / "src" / "scratch").exists()

        expected_files = {".triignore", "assets/keep.txt", "src/main.py", "src/root_only.tmp"}
        assert set(ws["snapshot_manifest"]["files"]) == expected_files
        assert ws["file_count"] == len(expected_files)
        assert ws["snapshot_manifest"]["file_count"] == len(expected_files)
        assert ws["copied_bytes"] > 0
        assert ws["snapshot_manifest"]["copied_bytes"] == ws["copied_bytes"]
        assert mgr.workspace_status(ws["id"])["dirty"] is False
    finally:
        db.close()


def test_historical_excluded_baseline_entries_do_not_trigger_dirty_guard(tmp_path: Path) -> None:
    proj = tmp_path / "legacy_proj"
    proj.mkdir()
    (proj / "app.py").write_text("v1\n", encoding="utf-8")
    (proj / ".triignore").write_text("*.log\n", encoding="utf-8")

    _, db, mgr, run_id = _setup_env(tmp_path, proj)
    try:
        ws = mgr.workspace_create(run_id, "hist", copy_env=False)
        # Simulate an older version that previously recorded .pytest_cache, dist, and *.log in baseline.
        polluted_files = {
            **ws["snapshot_manifest"]["files"],
            ".pytest_cache/v/cache/nodeids": "deadbeef" * 8,
            "dist/bundle.js": "cafebabe" * 8,
            "logs/old.log": "12345678" * 8,
        }
        db.update_workspace(
            ws["id"],
            snapshot_manifest={**ws["snapshot_manifest"], "files": polluted_files},
        )

        st = mgr.workspace_status(ws["id"])
        assert st["dirty"] is False
        assert st["deleted_files"] == []
        assert st["changed_files"] == []

        diff = mgr.workspace_diff(ws["id"])
        assert diff["dirty"] is False
        assert diff["files"] == []

        prev = mgr.workspace_merge_preview(ws["id"])
        assert prev["has_conflicts"] is False
        assert prev["files_changed"] == []

        merged = mgr.workspace_merge(ws["id"])
        assert merged["merged"] is True
        assert merged["files_changed"] == []

        cleaned = mgr.workspace_cleanup(ws["id"])
        assert cleaned["cleaned"] is True
    finally:
        db.close()


def test_snapshot_preflight_size_limit_rejects_without_copying(tmp_path: Path) -> None:
    proj = tmp_path / "big_proj"
    proj.mkdir()
    (proj / "small.txt").write_text("ok\n", encoding="utf-8")
    (proj / "big.bin").write_bytes(b"A" * (2 * 1024 * 1024))

    _, db, mgr, run_id = _setup_env(tmp_path, proj, max_snapshot_mb=1)
    try:
        with pytest.raises(WorkspaceError) as exc_info:
            mgr.workspace_create(run_id, "too-big", copy_env=False)
        msg = str(exc_info.value)
        assert "MB" in msg
        assert "1 MB" in msg
        assert "Git" in msg
        assert "execution_root" in msg
        assert ".triignore" in msg
        assert "TRI_ORCH_MAX_SNAPSHOT_MB" in msg
        assert not (proj / ".swarm-workspaces").exists()
        assert db.list_workspaces(run_id=run_id) == []

        # Adding .triignore to exclude big.bin brings snapshot below 1 MB limit.
        (proj / ".triignore").write_text("big.bin\n", encoding="utf-8")
        ws = mgr.workspace_create(run_id, "filtered-ok", copy_env=False)
        assert ws["file_count"] == 2  # .triignore + small.txt
        assert not (Path(ws["path"]) / "big.bin").exists()
    finally:
        db.close()

    # max_snapshot_mb=0 disables the limit.
    _, db2, mgr2, run_id2 = _setup_env(tmp_path / "unlim", proj, max_snapshot_mb=0)
    try:
        (proj / ".triignore").unlink()
        ws_unlim = mgr2.workspace_create(run_id2, "unlimited", copy_env=False)
        assert ws_unlim["copied_bytes"] >= 2 * 1024 * 1024
        mgr2.workspace_merge(ws_unlim["id"])
        mgr2.workspace_cleanup(ws_unlim["id"])
    finally:
        db2.close()


def test_gc_safety_guards_preserve_dirty_unmerged_and_post_merge_workspaces(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path / "repo")
    settings, db, mgr, run_id = _setup_env(tmp_path, repo)
    try:
        ws_clean = mgr.workspace_create(run_id, "clean-git")
        ws_dirty = mgr.workspace_create(run_id, "dirty-git")
        ws_post = mgr.workspace_create(run_id, "post-commit-git")
        ws_unmerged = mgr.workspace_create(run_id, "unmerged-git")

        # Merge ws_clean and ws_post cleanly.
        (Path(ws_clean["path"]) / "a.txt").write_text("a\n", encoding="utf-8")
        _git(Path(ws_clean["path"]), "add", ".")
        _git(Path(ws_clean["path"]), "commit", "-m", "add a")
        assert mgr.workspace_merge(ws_clean["id"])["merged"] is True

        assert mgr.workspace_merge(ws_post["id"])["merged"] is True
        # Post-merge commit after merge proof -> must NOT be reclaimed!
        (Path(ws_post["path"]) / "extra.py").write_text("extra = 1\n", encoding="utf-8")
        _git(Path(ws_post["path"]), "add", ".")
        _git(Path(ws_post["path"]), "commit", "-m", "post-merge unmerged")

        # ws_dirty: merged then dirtied on disk
        assert mgr.workspace_merge(ws_dirty["id"])["merged"] is True
        (Path(ws_dirty["path"]) / "app.py").write_text("modified\n", encoding="utf-8")
        (Path(ws_dirty["path"]) / "new_untracked.txt").write_text("new\n", encoding="utf-8")

        # ws_unmerged: committed but never merged
        (Path(ws_unmerged["path"]) / "unmerged.txt").write_text("wip\n", encoding="utf-8")
        _git(Path(ws_unmerged["path"]), "add", ".")
        _git(Path(ws_unmerged["path"]), "commit", "-m", "unmerged")

        # Strict workspace_cleanup guard still raises WorkspaceCleanupGuardError.
        with pytest.raises(WorkspaceCleanupGuardError):
            mgr.workspace_cleanup(ws_dirty["id"], force=True)
        with pytest.raises(WorkspaceCleanupGuardError):
            mgr.workspace_cleanup(ws_post["id"], force=True)
        with pytest.raises(WorkspaceCleanupGuardError):
            mgr.workspace_cleanup(ws_unmerged["id"], force=True)

        assert mgr.workspace_gc_safety(ws_clean["id"])["safe_to_clean"] is True
        dirty_safety = mgr.workspace_gc_safety(ws_dirty["id"])
        assert dirty_safety["safe_to_clean"] is False
        assert dirty_safety["added_count"] == 1
        assert dirty_safety["modified_count"] == 1

        post_safety = mgr.workspace_gc_safety(ws_post["id"])
        assert post_safety["safe_to_clean"] is False
        assert post_safety["added_count"] == 1

        unmerged_safety = mgr.workspace_gc_safety(ws_unmerged["id"])
        assert unmerged_safety["safe_to_clean"] is False
        assert unmerged_safety["added_count"] == 1
    finally:
        db.close()


def test_storage_report_and_gc_lifecycle_respects_retention_junctions_and_cli(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "main.py").write_text("print('hello')\n", encoding="utf-8")

    # External large directory that must NOT be counted or deleted through a junction/symlink.
    external_shared = tmp_path / "external_shared"
    external_shared.mkdir()
    (external_shared / "huge_shared.bin").write_bytes(b"Z" * (1024 * 1024))

    settings, db, mgr, completed_run_id = _setup_env(
        tmp_path,
        proj,
        workflow_version=5,
        result_ttl_sec=3600,
        result_max_files=2,
        workspace_retention_sec=3600,
    )
    try:
        active_run = db.create_run(mode="swarm", title="active run", project_root=proj)
        failed_run = db.create_run(mode="swarm", title="failed run", project_root=proj)
        with db.transaction() as conn:
            conn.execute("UPDATE runs SET workflow_version=0 WHERE id IN (?, ?, ?)",
                         (completed_run_id, active_run["id"], failed_run["id"]))

        ws_merged_clean = mgr.workspace_create(completed_run_id, "clean-snap", copy_env=False)
        ws_merged_dirty = mgr.workspace_create(completed_run_id, "dirty-snap", copy_env=False)
        ws_active = mgr.workspace_create(active_run["id"], "active-snap", copy_env=False)
        ws_failed = mgr.workspace_create(failed_run["id"], "failed-snap", copy_env=False)

        # Merge both workspaces in completed_run_id, then dirty one of them.
        assert mgr.workspace_merge(ws_merged_clean["id"])["merged"] is True
        assert mgr.workspace_merge(ws_merged_dirty["id"])["merged"] is True
        (Path(ws_merged_dirty["path"]) / "unsaved.py").write_text("x = 42\n", encoding="utf-8")

        # Also link external_shared via Windows junction (or symlink) inside ws_merged_clean/node_modules
        # to verify size calculation never follows junctions/symlinks!
        link_target = Path(ws_merged_clean["path"]) / "node_modules"
        if os.name == "nt" and _winapi is not None and hasattr(_winapi, "CreateJunction"):
            _winapi.CreateJunction(str(external_shared), str(link_target))
        else:
            os.symlink(external_shared, link_target, target_is_directory=True)

        # Transition runs: completed_run_id -> completed (via direct SQL to emulate completed v5 run),
        # failed_run -> failed.
        with db.transaction() as conn:
            conn.execute("UPDATE runs SET status='completed', workflow_version=5 WHERE id=?", (completed_run_id,))
            conn.execute("UPDATE runs SET status='failed' WHERE id=?", (failed_run["id"],))

        # Populate runtime artifacts: logs, results/*.json, receipts
        settings.daemon_log_path.write_text("daemon log line\n", encoding="utf-8")
        settings.shim_log_path.write_text("shim log line\n", encoding="utf-8")
        receipt_dir = settings.data_dir / "receipts" / completed_run_id
        receipt_dir.mkdir(parents=True, exist_ok=True)
        (receipt_dir / "rcpt.json").write_text('{"ok":true}\n', encoding="utf-8")

        settings.results_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        f_old = settings.results_dir / "old.json"
        f_excess = settings.results_dir / "excess.json"
        f_keep1 = settings.results_dir / "keep1.json"
        f_keep2 = settings.results_dir / "keep2.json"
        for idx, (fp, age) in enumerate(
            [(f_old, 7200), (f_excess, 100), (f_keep1, 50), (f_keep2, 10)]
        ):
            fp.write_text(json.dumps({"idx": idx}) * 10, encoding="utf-8")
            os.utime(fp, (now - age, now - age))

        report = collect_storage_report(db, settings, workspace_mgr=mgr)
        assert report["data_dir"] == str(settings.data_dir)
        assert report["runtime"]["state_db_bytes"] > 0
        assert report["runtime"]["logs_bytes"] > 0
        assert report["runtime"]["results_count"] == 4
        assert report["runtime"]["receipts_bytes"] > 0
        assert report["workspaces"]["cleanable_count"] == 1
        # The merged-but-dirty one needs review; the failed run's untouched snapshot is discardable
        # (never merged, identical to its baseline) but is still never removed by automatic GC.
        assert report["workspaces"]["dirty_review_count"] == 1
        assert report["workspaces"]["discardable_count"] == 1
        failed_item = next(it for it in report["workspaces"]["items"] if it["workspace_id"] == ws_failed["id"])
        assert failed_item["category"] == "discardable"
        assert report["workspaces"]["active_count"] == 1
        # Must NOT include the 1 MB external_shared file via junction!
        clean_item = next(
            it for it in report["workspaces"]["items"] if it["workspace_id"] == ws_merged_clean["id"]
        )
        assert clean_item["bytes"] < 1024 * 1024
        assert clean_item["category"] == "cleanable"

        # 1) Default GC before workspace_retention_sec expires: cleans results, skips young workspace.
        res_young = run_gc(db, mgr, settings, now=now, include_all_completed=False, dry_run=False)
        assert res_young["ok"] is True
        assert set(res_young["cleaned_results"]) == {str(f_old), str(f_excess)}
        assert f_keep1.exists() and f_keep2.exists()
        assert not f_old.exists() and not f_excess.exists()
        assert res_young["cleaned_workspaces"] == []
        assert len(res_young["skipped_dirty_workspaces"]) == 2
        assert Path(ws_merged_clean["path"]).exists()

        # 2) Dry-run with include_all_completed=True previews ws_merged_clean without deleting it.
        res_dry = run_gc(db, mgr, settings, now=now, include_all_completed=True, dry_run=True)
        assert res_dry["dry_run"] is True
        assert res_dry["cleaned_workspaces"] == [ws_merged_clean["id"]]
        assert Path(ws_merged_clean["path"]).exists()
        assert db.get_workspace(ws_merged_clean["id"])["status"] == "merged"

        # 3) Real run via CLI (--all-completed): cleans ws_merged_clean, preserves dirty/failed/active & external_shared!
        db.close()
        rc = gc_main(["--all-completed", "--data-dir", str(settings.data_dir)])
        assert rc == 0

        db2 = Database.from_settings(settings)
        try:
            assert not Path(ws_merged_clean["path"]).exists()
            assert db2.get_workspace(ws_merged_clean["id"])["status"] == "cleaned"
            assert (external_shared / "huge_shared.bin").exists()
            assert Path(ws_merged_dirty["path"]).exists()
            assert Path(ws_active["path"]).exists()
            assert Path(ws_failed["path"]).exists()

            after_report = collect_storage_report(db2, settings)
            # Cleaned workspace without directory on disk is no longer listed.
            listed_ids = {it["workspace_id"] for it in after_report["workspaces"]["items"]}
            assert ws_merged_clean["id"] not in listed_ids
            assert after_report["workspaces"]["cleanable_count"] == 0
        finally:
            db2.close()
    finally:
        if not db._closed:
            db.close()


def test_gc_rmdir_empty_workspaces_parent_and_module_cli_subprocess(tmp_path: Path) -> None:
    proj = tmp_path / "solo_proj"
    proj.mkdir()
    (proj / "file.txt").write_text("hello\n", encoding="utf-8")

    settings, db, mgr, run_id = _setup_env(tmp_path, proj, workspace_retention_sec=100)
    try:
        ws = mgr.workspace_create(run_id, "solo", copy_env=False)
        assert mgr.workspace_merge(ws["id"])["merged"] is True
        with db.transaction() as conn:
            old_ts = time.time() - 500
            conn.execute("UPDATE runs SET status='archived', updated_at=? WHERE id=?", (old_ts, run_id))
            conn.execute("UPDATE workspaces SET updated_at=? WHERE id=?", (old_ts, ws["id"]))
        ws_root = Path(ws["path"]).parent
        assert ws_root.name == ".swarm-workspaces"
        assert ws_root.is_dir()
    finally:
        db.close()

    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "plugin" / "src")
    env["TRI_ORCH_WORKSPACE_RETENTION_SEC"] = "100"
    proc = subprocess.run(
        [sys.executable, "-m", "orchestrator.gc", "--data-dir", str(settings.data_dir)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=True,
    )
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert payload["cleaned_workspaces"] == [ws["id"]]
    assert not ws_root.exists()
