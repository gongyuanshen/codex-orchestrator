"""Tests for WorkspaceManager: git clone --local, port pool, merge preview/merge, cleanup guards, and snapshot fallback."""

from __future__ import annotations

import os
import socket
import stat
import subprocess
from pathlib import Path

import pytest

from orchestrator.config import Settings
from orchestrator.db import Database
from orchestrator.workspace_mgr import (
    WorkspaceCleanupGuardError,
    WorkspaceConflictError,
    WorkspaceManager,
)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return proc


def _init_git_repo(repo_dir: Path) -> Path:
    repo_dir.mkdir(parents=True, exist_ok=True)
    _git(repo_dir, "init", "-b", "main")
    _git(repo_dir, "config", "user.name", "Test User")
    _git(repo_dir, "config", "user.email", "test@example.com")
    (repo_dir / "app.py").write_text("line1 = 'base'\nline2 = 'unchanged'\n", encoding="utf-8")
    (repo_dir / "README.md").write_text("# Demo\n", encoding="utf-8")
    _git(repo_dir, "add", ".")
    _git(repo_dir, "commit", "-m", "initial commit")
    return repo_dir


def _setup_mgr(tmp_path: Path, project_root: Path, port_range: tuple[int, int] = (18820, 18840)) -> tuple[Database, WorkspaceManager, str]:
    settings = Settings(data_dir=tmp_path / "data", port_range=port_range)
    db = Database.from_settings(settings)
    run = db.create_run(
        mode="swarm",
        title="workspace test run",
        project_root=project_root,
    )
    # This fixture exercises legacy merge mechanics; strict v1 gates have separate tests.
    db.connect().execute("UPDATE runs SET workflow_version=0 WHERE id=?", (run["id"],))
    return db, WorkspaceManager(db, settings), run["id"]


def test_git_clone_isolation_exclude_and_copy_env(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path / "repo")
    (repo / ".env").write_text("API_KEY=secret123\n", encoding="utf-8")
    (repo / ".env.local").write_text("LOCAL_FLAG=1\n", encoding="utf-8")

    db, mgr, run_id = _setup_mgr(tmp_path, repo)
    try:
        ws = mgr.workspace_create(run_id, "worker-alpha", copy_env=True, dep_mode="none")
        ws_path = Path(ws["path"])

        assert ws["mode"] == "git_clone"
        assert ws["status"] == "active"
        assert ws["branch_name"].startswith("swarm/")
        assert ws["branch_name"].endswith("/worker-alpha")
        assert ws["base_commit"] == _git(repo, "rev-parse", "HEAD").stdout.strip()
        assert ws_path.is_dir()
        assert ".swarm-workspaces" in ws_path.parts

        # .git/info/exclude must contain .swarm-workspaces/, while .gitignore must NOT be created/edited.
        exclude_text = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
        assert ".swarm-workspaces/" in exclude_text
        assert not (repo / ".gitignore").exists()
        main_status = _git(repo, "status", "--porcelain", "-uall").stdout
        assert ".swarm-workspaces" not in main_status

        # .env and .env.local copied without making workspace dirty.
        assert (ws_path / ".env").read_text(encoding="utf-8") == "API_KEY=secret123\n"
        assert (ws_path / ".env.local").read_text(encoding="utf-8") == "LOCAL_FLAG=1\n"
        status = mgr.workspace_status(ws["id"])
        assert status["dirty"] is False
        assert status["commits_ahead"] == 0

        # copy_env=False skips .env files.
        ws_no_env = mgr.workspace_create(run_id, "worker-beta", copy_env=False, dep_mode="none")
        ws2_path = Path(ws_no_env["path"])
        assert not (ws2_path / ".env").exists()
        assert not (ws2_path / ".env.local").exists()
    finally:
        db.close()


def test_port_pool_allocation_skips_bound_socket_and_active_db(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path / "repo")
    # Pick a high ephemeral test range to avoid colliding with real local services.
    base_port = 18910
    db, mgr, run_id = _setup_mgr(tmp_path, repo, port_range=(base_port, base_port + 10))
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("127.0.0.1", base_port))
        sock.listen(1)

        ws1 = mgr.workspace_create(run_id, "w1")
        ws2 = mgr.workspace_create(run_id, "w2")

        assert ws1["assigned_port"] is not None
        assert ws2["assigned_port"] is not None
        assert ws1["assigned_port"] != base_port
        assert ws2["assigned_port"] != base_port
        assert ws1["assigned_port"] != ws2["assigned_port"]
        assert base_port <= ws1["assigned_port"] <= base_port + 10
        assert base_port <= ws2["assigned_port"] <= base_port + 10
    finally:
        sock.close()
        db.close()


def test_dep_modes_link_ro_cache_copy_and_fresh_venv(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path / "repo")
    src_venv = repo / ".venv"
    (src_venv / "Scripts").mkdir(parents=True)
    (src_venv / "Scripts" / "marker.txt").write_text("keep-me", encoding="utf-8")

    db, mgr, run_id = _setup_mgr(tmp_path, repo)
    try:
        # link_ro_cache shares writable dependency files; cleanup preserves the source.
        ws_link = mgr.workspace_create(run_id, "w-link", dep_mode="link_ro_cache")
        ws_link_path = Path(ws_link["path"])
        assert (ws_link_path / ".venv" / "Scripts" / "marker.txt").read_text(encoding="utf-8") == "keep-me"
        assert mgr.workspace_status(ws_link["id"])["dirty"] is False
        (ws_link_path / ".venv" / "Scripts" / "marker.txt").write_text("shared-write", encoding="utf-8")
        assert (src_venv / "Scripts" / "marker.txt").read_text(encoding="utf-8") == "shared-write"

        # Merge empty branch cleanly and cleanup; source .venv must survive completely intact!
        merge_res = mgr.workspace_merge(ws_link["id"])
        assert merge_res["merged"] is True
        mgr.workspace_cleanup(ws_link["id"])
        assert (src_venv / "Scripts" / "marker.txt").read_text(encoding="utf-8") == "shared-write"

        # copy mode
        ws_copy = mgr.workspace_create(run_id, "w-copy", dep_mode="copy")
        copy_marker = Path(ws_copy["path"]) / ".venv" / "Scripts" / "marker.txt"
        assert copy_marker.read_text(encoding="utf-8") == "shared-write"
        copy_marker.write_text("isolated-write", encoding="utf-8")
        assert (src_venv / "Scripts" / "marker.txt").read_text(encoding="utf-8") == "shared-write"

        # fresh_venv_uv mode
        ws_fresh = mgr.workspace_create(run_id, "w-fresh", dep_mode="fresh_venv_uv")
        fresh_venv = Path(ws_fresh["path"]) / ".venv"
        assert fresh_venv.is_dir()
        assert (fresh_venv / "pyvenv.cfg").is_file()
        assert not (fresh_venv / "Scripts" / "marker.txt").exists()
    finally:
        db.close()


def test_status_diff_and_utf8_paths(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path / "repo")
    db, mgr, run_id = _setup_mgr(tmp_path, repo)
    try:
        ws = mgr.workspace_create(run_id, "w-diff")
        ws_path = Path(ws["path"])

        # Modify tracked file and create untracked UTF-8 file.
        (ws_path / "app.py").write_text("line1 = 'updated'\nline2 = 'unchanged'\n", encoding="utf-8")
        (ws_path / "说明_测试.txt").write_text("你好世界\n", encoding="utf-8")

        st1 = mgr.workspace_status(ws["id"])
        assert st1["dirty"] is True
        assert st1["has_uncommitted_changes"] is True
        assert set(st1["uncommitted_files"]) == {"app.py", "说明_测试.txt"}

        diff1 = mgr.workspace_diff(ws["id"])
        assert set(diff1["files"]) == {"app.py", "说明_测试.txt"}
        assert "+line1 = 'updated'" in diff1["diff"]
        assert "+你好世界" in diff1["diff"]

        # Commit changes and verify clean committed status & diff vs base_commit.
        _git(ws_path, "add", ".")
        _git(ws_path, "commit", "-m", "feat: update app and add utf8 doc")
        st2 = mgr.workspace_status(ws["id"])
        assert st2["dirty"] is False
        assert st2["commits_ahead"] == 1
        assert set(st2["committed_files"]) == {"app.py", "说明_测试.txt"}

        diff2 = mgr.workspace_diff(ws["id"])
        assert "+line1 = 'updated'" in diff2["diff"]
        assert "+你好世界" in diff2["diff"]
    finally:
        db.close()


def test_merge_preview_sequential_merge_and_conflict_abort(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path / "repo")
    db, mgr, run_id = _setup_mgr(tmp_path, repo)
    try:
        ws1 = mgr.workspace_create(run_id, "w1")
        ws2 = mgr.workspace_create(run_id, "w2")
        ws3 = mgr.workspace_create(run_id, "w3-conflict")

        p1, p2, p3 = Path(ws1["path"]), Path(ws2["path"]), Path(ws3["path"])

        # Worker 1 adds module_a.py
        (p1 / "module_a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
        _git(p1, "add", ".")
        _git(p1, "commit", "-m", "add module_a")

        # Worker 2 modifies README.md (non-overlapping)
        (p2 / "README.md").write_text("# Demo\n\nUpdated by worker 2.\n", encoding="utf-8")
        _git(p2, "add", ".")
        _git(p2, "commit", "-m", "update readme")

        # Worker 3 modifies module_a.py differently (will conflict after ws1 merges!)
        (p3 / "module_a.py").write_text("def a():\n    return 999\n", encoding="utf-8")
        _git(p3, "add", ".")
        _git(p3, "commit", "-m", "conflicting module_a")

        # Preview ws1 -> clean, main repo unchanged
        prev1 = mgr.workspace_merge_preview(ws1["id"])
        assert prev1["has_conflicts"] is False
        assert prev1["conflict_files"] == []
        assert prev1["files_changed"] == ["module_a.py"]
        assert not (repo / "module_a.py").exists()

        # Sequential merge ws1 -> clean
        m1 = mgr.workspace_merge(ws1["id"], strategy="merge")
        assert m1["merged"] is True
        assert m1["status"] == "merged"
        assert (repo / "module_a.py").read_text(encoding="utf-8") == "def a():\n    return 1\n"

        # Preview ws2 after ws1 merged -> clean
        prev2 = mgr.workspace_merge_preview(ws2["id"])
        assert prev2["has_conflicts"] is False
        assert prev2["files_changed"] == ["README.md"]

        # Sequential cherry-pick ws2 -> clean
        m2 = mgr.workspace_merge(ws2["id"], strategy="cherry-pick")
        assert m2["merged"] is True
        assert m2["status"] == "merged"
        assert "Updated by worker 2." in (repo / "README.md").read_text(encoding="utf-8")

        # Preview ws3 -> detects conflict on module_a.py without mutating repo!
        prev3 = mgr.workspace_merge_preview(ws3["id"])
        assert prev3["has_conflicts"] is True
        assert "module_a.py" in prev3["conflict_files"]
        assert _git(repo, "status", "--porcelain").stdout == ""
        assert (repo / "module_a.py").read_text(encoding="utf-8") == "def a():\n    return 1\n"

        # Merge ws3 -> aborts cleanly, preserves main repo & workspace, marks status='conflict'
        m3 = mgr.workspace_merge(ws3["id"], strategy="merge")
        assert m3["merged"] is False
        assert m3["has_conflicts"] is True
        assert m3["status"] == "conflict"
        assert "module_a.py" in m3["conflict_files"]
        assert db.get_workspace(ws3["id"])["status"] == "conflict"
        assert _git(repo, "status", "--porcelain").stdout == ""
        assert not (repo / ".git" / "MERGE_HEAD").exists()
        assert (p3 / "module_a.py").exists()

        # Cherry-pick ws3 -> also aborts cleanly without leaving CHERRY_PICK_HEAD
        m3_cp = mgr.workspace_merge(ws3["id"], strategy="cherry-pick")
        assert m3_cp["merged"] is False
        assert m3_cp["has_conflicts"] is True
        assert m3_cp["status"] == "conflict"
        assert "module_a.py" in m3_cp["conflict_files"]
        assert not (repo / ".git" / "CHERRY_PICK_HEAD").exists()
        assert _git(repo, "status", "--porcelain").stdout == ""
    finally:
        db.close()


def test_strict_cleanup_guard_and_windows_readonly_git_objects(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path / "repo")
    db, mgr, run_id = _setup_mgr(tmp_path, repo)
    try:
        ws = mgr.workspace_create(run_id, "w-guard")
        ws_path = Path(ws["path"])

        # Dirty & unmerged -> cleanup must refuse even with force=True
        (ws_path / "wip.txt").write_text("unsaved work\n", encoding="utf-8")
        with pytest.raises(WorkspaceCleanupGuardError):
            mgr.workspace_cleanup(ws["id"], force=True)
        assert ws_path.exists()

        # Commit wip.txt, now clean working tree, still unmerged -> cleanup must refuse even with force=True
        _git(ws_path, "add", ".")
        _git(ws_path, "commit", "-m", "wip commit")
        with pytest.raises(WorkspaceConflictError):
            # Also verify dirty-check on another uncommitted edit before merge:
            (ws_path / "extra_dirty.txt").write_text("dirty\n", encoding="utf-8")
            mgr.workspace_merge(ws["id"])
        (ws_path / "extra_dirty.txt").unlink()

        with pytest.raises(WorkspaceCleanupGuardError):
            mgr.workspace_cleanup(ws["id"], force=True)
        assert ws_path.exists()

        # Merge cleanly -> status becomes 'merged'
        merge_res = mgr.workspace_merge(ws["id"])
        assert merge_res["merged"] is True

        # Make workspace dirty AFTER merge -> cleanup must still refuse!
        (ws_path / "post_merge_edit.txt").write_text("dirty after merge\n", encoding="utf-8")
        with pytest.raises(WorkspaceCleanupGuardError):
            mgr.workspace_cleanup(ws["id"], force=True)
        assert ws_path.exists()
        (ws_path / "post_merge_edit.txt").unlink()

        # Force read-only attributes on .git/objects files (Windows PermissionError trap)
        ro_file = ws_path / ".git" / "objects" / "ro_test_obj"
        ro_file.parent.mkdir(parents=True, exist_ok=True)
        ro_file.write_bytes(b"readonly-object")
        os.chmod(ro_file, stat.S_IREAD)

        # Now merged and clean -> cleanup succeeds cleanly!
        clean_res = mgr.workspace_cleanup(ws["id"])
        assert clean_res["cleaned"] is True
        assert clean_res["status"] == "cleaned"
        assert not ws_path.exists()
        assert db.get_workspace(ws["id"])["status"] == "cleaned"
    finally:
        db.close()


def test_nongit_snapshot_fallback_diff_merge_conflict_and_cleanup(tmp_path: Path) -> None:
    proj = tmp_path / "nongit_proj"
    proj.mkdir()
    (proj / "main.py").write_text("print('v1')\n", encoding="utf-8")
    (proj / "config.json").write_text('{"port": 80}\n', encoding="utf-8")
    (proj / ".env").write_text("SECRET=abc\n", encoding="utf-8")
    # Excluded directories that must NOT be copied into workspace snapshot
    (proj / "node_modules" / "pkg").mkdir(parents=True)
    (proj / "node_modules" / "pkg" / "index.js").write_text("module.exports = 1;\n", encoding="utf-8")
    (proj / "__pycache__").mkdir()
    (proj / "__pycache__" / "main.cpython-314.pyc").write_bytes(b"\x00\x01\x02")

    db, mgr, run_id = _setup_mgr(tmp_path, proj)
    try:
        ws1 = mgr.workspace_create(run_id, "snap-1", copy_env=True, dep_mode="none")
        ws2 = mgr.workspace_create(run_id, "snap-2", copy_env=True, dep_mode="none")
        p1, p2 = Path(ws1["path"]), Path(ws2["path"])

        assert ws1["mode"] == "snapshot"
        assert (p1 / "main.py").is_file()
        assert (p1 / ".env").read_text(encoding="utf-8") == "SECRET=abc\n"
        assert not (p1 / "node_modules").exists()
        assert not (p1 / "__pycache__").exists()
        assert "main.py" in ws1["snapshot_manifest"]["files"]
        assert ".env" not in ws1["snapshot_manifest"]["files"]

        # Worker 1 modifies main.py and adds helper.py
        (p1 / "main.py").write_text("print('v2')\n", encoding="utf-8")
        (p1 / "helper.py").write_text("VALUE = 42\n", encoding="utf-8")

        st1 = mgr.workspace_status(ws1["id"])
        assert st1["dirty"] is True
        assert set(st1["changed_files"]) == {"helper.py", "main.py"}

        diff1 = mgr.workspace_diff(ws1["id"])
        assert set(diff1["files"]) == {"helper.py", "main.py"}
        assert "+print('v2')" in diff1["diff"]
        assert "+VALUE = 42" in diff1["diff"]

        # Worker 2 also modifies main.py differently and modifies config.json
        (p2 / "main.py").write_text("print('conflict-v2')\n", encoding="utf-8")
        (p2 / "config.json").write_text('{"port": 8080}\n', encoding="utf-8")

        # Unmerged snapshot workspace must reject cleanup
        with pytest.raises(WorkspaceCleanupGuardError):
            mgr.workspace_cleanup(ws1["id"], force=True)

        # Merge ws1 cleanly
        prev1 = mgr.workspace_merge_preview(ws1["id"])
        assert prev1["has_conflicts"] is False
        m1 = mgr.workspace_merge(ws1["id"])
        assert m1["merged"] is True
        assert (proj / "main.py").read_text(encoding="utf-8") == "print('v2')\n"
        assert (proj / "helper.py").read_text(encoding="utf-8") == "VALUE = 42\n"

        # Preview and merge ws2 -> must detect overlapping edit on main.py and NOT touch config.json!
        prev2 = mgr.workspace_merge_preview(ws2["id"])
        assert prev2["has_conflicts"] is True
        assert prev2["conflict_files"] == ["main.py"]
        assert (proj / "config.json").read_text(encoding="utf-8") == '{"port": 80}\n'

        m2 = mgr.workspace_merge(ws2["id"])
        assert m2["merged"] is False
        assert m2["has_conflicts"] is True
        assert m2["status"] == "conflict"
        assert m2["conflict_files"] == ["main.py"]
        # Atomic abort: non-conflicting config.json was NOT partially overwritten on conflict!
        assert (proj / "config.json").read_text(encoding="utf-8") == '{"port": 80}\n'
        assert (proj / "main.py").read_text(encoding="utf-8") == "print('v2')\n"

        # ws1 is merged & clean -> cleanup succeeds (and is idempotent); ws2 is conflict -> cleanup refused!
        assert mgr.workspace_cleanup(ws1["id"])["cleaned"] is True
        assert not p1.exists()
        assert mgr.workspace_cleanup(ws1["id"])["already_cleaned"] is True
        with pytest.raises(WorkspaceCleanupGuardError):
            mgr.workspace_cleanup(ws2["id"], force=True)
        assert p2.exists()

        # Verify snapshot deletion diff & merge
        (proj / "obsolete.txt").write_text("remove me\n", encoding="utf-8")
        ws3 = mgr.workspace_create(run_id, "snap-del", copy_env=False, dep_mode="none")
        p3 = Path(ws3["path"])
        (p3 / "obsolete.txt").unlink()
        diff3 = mgr.workspace_diff(ws3["id"])
        assert "obsolete.txt" in diff3["files"]
        assert "-remove me" in diff3["diff"]
        m3 = mgr.workspace_merge(ws3["id"])
        assert m3["merged"] is True
        assert not (proj / "obsolete.txt").exists()
        assert mgr.workspace_cleanup(ws3["id"])["cleaned"] is True
    finally:
        db.close()


@pytest.mark.parametrize("strategy", ["merge", "cherry-pick"])
def test_cleanup_rejects_post_merge_commit_after_restart(tmp_path: Path, strategy: str) -> None:
    repo = _init_git_repo(tmp_path / "repo")
    db, mgr, run_id = _setup_mgr(tmp_path, repo)
    try:
        ws = mgr.workspace_create(run_id, "post-merge")
        ws_path = Path(ws["path"])
        (ws_path / "first.txt").write_text("first\n", encoding="utf-8")
        _git(ws_path, "add", ".")
        _git(ws_path, "commit", "-m", "first worker commit")
        first_commit = _git(ws_path, "rev-parse", "HEAD").stdout.strip()
        # Divergence makes cherry-pick produce a different target commit ID.
        (repo / "main-only.txt").write_text("main\n", encoding="utf-8")
        _git(repo, "add", "main-only.txt")
        _git(repo, "commit", "-m", "main advances")
        assert mgr.workspace_merge(ws["id"], strategy=strategy)["merged"] is True

        settings = db.settings
        db.close()
        db = Database.from_settings(settings)
        mgr = WorkspaceManager(db, settings)
        (ws_path / "second.txt").write_text("not merged yet\n", encoding="utf-8")
        _git(ws_path, "add", ".")
        _git(ws_path, "commit", "-m", "post-merge worker commit")
        second_commit = _git(ws_path, "rev-parse", "HEAD").stdout.strip()
        assert mgr.workspace_status(ws["id"])["dirty"] is False
        assert not (repo / "second.txt").exists()

        with pytest.raises(WorkspaceCleanupGuardError, match="HEAD.*merged commit"):
            mgr.workspace_cleanup(ws["id"], force=True)
        assert ws_path.is_dir()
        assert _git(ws_path, "rev-parse", "HEAD").stdout.strip() == second_commit
        assert db.get_workspace(ws["id"])["merged_commit"] == first_commit

        assert mgr.workspace_merge(ws["id"], strategy=strategy)["merged"] is True
        assert (repo / "second.txt").read_text(encoding="utf-8") == "not merged yet\n"
        assert db.get_workspace(ws["id"])["merged_commit"] == second_commit
        target_head = _git(repo, "rev-parse", "HEAD").stdout.strip()
        assert mgr.workspace_merge(ws["id"], strategy=strategy)["commits_merged"] == []
        assert _git(repo, "rev-parse", "HEAD").stdout.strip() == target_head
        assert mgr.workspace_cleanup(ws["id"])["cleaned"] is True
        assert not ws_path.exists()
    finally:
        db.close()


@pytest.mark.parametrize("strategy", ["merge", "cherry-pick"])
def test_noop_merge_persists_cleanup_proof(tmp_path: Path, strategy: str) -> None:
    repo = _init_git_repo(tmp_path / "repo")
    db, mgr, run_id = _setup_mgr(tmp_path, repo)
    try:
        ws = mgr.workspace_create(run_id, "noop")
        assert mgr.workspace_merge(ws["id"], strategy=strategy)["merged"] is True
        settings = db.settings
        db.close()
        db = Database.from_settings(settings)
        mgr = WorkspaceManager(db, settings)
        assert db.get_workspace(ws["id"])["merged_commit"] == ws["base_commit"]
        assert mgr.workspace_cleanup(ws["id"])["cleaned"] is True
    finally:
        db.close()


def test_cleanup_rejects_legacy_merged_workspace_without_proof(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path / "repo")
    db, mgr, run_id = _setup_mgr(tmp_path, repo)
    try:
        ws = mgr.workspace_create(run_id, "legacy")
        db.update_workspace(ws["id"], status="merged")
        with pytest.raises(WorkspaceCleanupGuardError, match="missing merged commit proof"):
            mgr.workspace_cleanup(ws["id"], force=True)
        assert Path(ws["path"]).is_dir()
        assert mgr.workspace_merge(ws["id"])["merged"] is True
        assert mgr.workspace_cleanup(ws["id"])["cleaned"] is True
    finally:
        db.close()


@pytest.mark.parametrize("checkout", [("checkout", "-b", "other"), ("checkout", "--detach")])
def test_merge_and_cleanup_reject_changed_workspace_branch(tmp_path: Path, checkout: tuple[str, ...]) -> None:
    repo = _init_git_repo(tmp_path / "repo")
    db, mgr, run_id = _setup_mgr(tmp_path, repo)
    try:
        ws = mgr.workspace_create(run_id, "branch")
        ws_path = Path(ws["path"])
        assert mgr.workspace_merge(ws["id"])["merged"] is True
        _git(ws_path, *checkout)
        with pytest.raises(WorkspaceCleanupGuardError, match="expected branch"):
            mgr.workspace_cleanup(ws["id"])
        with pytest.raises(WorkspaceConflictError, match="expected branch"):
            mgr.workspace_merge(ws["id"])
        assert ws_path.is_dir()
        # HEAD can still match old proof while the recorded branch has new work.
        _git(ws_path, "checkout", ws["branch_name"])
        (ws_path / "new.txt").write_text("new branch work\n", encoding="utf-8")
        _git(ws_path, "add", ".")
        _git(ws_path, "commit", "-m", "new work on recorded branch")
        _git(ws_path, "checkout", "--detach", ws["base_commit"])
        with pytest.raises(WorkspaceCleanupGuardError):
            mgr.workspace_cleanup(ws["id"], force=True)
        assert _git(ws_path, "show", f"{ws['branch_name']}:new.txt").stdout == "new branch work\n"
    finally:
        db.close()


@pytest.mark.parametrize("strategy", ["merge", "cherry-pick"])
def test_merge_only_commit_is_not_false_noop(tmp_path: Path, strategy: str) -> None:
    repo = _init_git_repo(tmp_path / "repo")
    _git(repo, "checkout", "-b", "side")
    (repo / "side.txt").write_text("side\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "side commit")
    _git(repo, "checkout", "main")
    db, mgr, run_id = _setup_mgr(tmp_path, repo)
    try:
        ws = mgr.workspace_create(run_id, "merge-only")
        ws_path = Path(ws["path"])
        _git(ws_path, "merge", "--no-ff", "origin/side", "-m", "merge side")
        (ws_path / "resolution.txt").write_text("merge-only content\n", encoding="utf-8")
        _git(ws_path, "add", ".")
        _git(ws_path, "commit", "--amend", "--no-edit")
        _git(repo, "merge", "--no-ff", "side", "-m", "main merge side")
        if strategy == "cherry-pick":
            with pytest.raises(WorkspaceConflictError, match="merge commits"):
                mgr.workspace_merge(ws["id"], strategy=strategy)
            assert db.get_workspace(ws["id"])["merged_commit"] is None
            assert ws_path.is_dir()
            assert not (repo / "resolution.txt").exists()
        else:
            assert mgr.workspace_merge(ws["id"], strategy=strategy)["merged"] is True
            assert (repo / "resolution.txt").read_text(encoding="utf-8") == "merge-only content\n"
            assert mgr.workspace_cleanup(ws["id"])["cleaned"] is True
    finally:
        db.close()



def test_cherry_pick_does_not_certify_whitespace_equivalent_changes(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path / "repo")
    db, mgr, run_id = _setup_mgr(tmp_path, repo)
    try:
        ws = mgr.workspace_create(run_id, "indentation")
        ws_path = Path(ws["path"])
        source = "def f():\n    if False:\n        return 1\n    return 2\n"
        target = "def f():\n    if False:\n        return 1\n        return 2\n"
        (ws_path / "meaning.py").write_text(source, encoding="utf-8")
        _git(ws_path, "add", ".")
        _git(ws_path, "commit", "-m", "source returns 2")
        (repo / "meaning.py").write_text(target, encoding="utf-8")
        _git(repo, "add", "meaning.py")
        _git(repo, "commit", "-m", "target returns None")
        assert mgr.workspace_merge(ws["id"], strategy="cherry-pick")["merged"] is False
        assert (repo / "meaning.py").read_text(encoding="utf-8") == target
        with pytest.raises(WorkspaceCleanupGuardError):
            mgr.workspace_cleanup(ws["id"])
        assert (ws_path / "meaning.py").read_text(encoding="utf-8") == source
    finally:
        db.close()


def test_repeat_cherry_pick_proof_is_scoped_to_target_history(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path / "repo")
    db, mgr, run_id = _setup_mgr(tmp_path, repo)
    try:
        ws = mgr.workspace_create(run_id, "targets")
        ws_path = Path(ws["path"])
        (ws_path / "first.txt").write_text("first\n", encoding="utf-8")
        _git(ws_path, "add", ".")
        _git(ws_path, "commit", "-m", "first")
        assert mgr.workspace_merge(ws["id"], strategy="cherry-pick")["merged"] is True
        first_target = _git(repo, "rev-parse", "HEAD").stdout.strip()
        assert db.get_workspace(ws["id"])["merged_target_commit"] == first_target
        (ws_path / "second.txt").write_text("second\n", encoding="utf-8")
        _git(ws_path, "add", ".")
        _git(ws_path, "commit", "-m", "second")
        _git(repo, "checkout", "-b", "alternate", ws["base_commit"])
        assert mgr.workspace_merge(ws["id"], strategy="cherry-pick")["merged"] is True
        assert (repo / "first.txt").read_text(encoding="utf-8") == "first\n"
        assert (repo / "second.txt").read_text(encoding="utf-8") == "second\n"
        assert mgr.workspace_cleanup(ws["id"])["cleaned"] is True
    finally:
        db.close()
