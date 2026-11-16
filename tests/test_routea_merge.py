"""Real Git/snapshot merge gates and interrupted apply recovery, using temporary data."""
from pathlib import Path
import subprocess

import pytest

from orchestrator.config import Settings
from orchestrator.db import Database
from orchestrator.workspace_mgr import WorkspaceManager
from orchestrator.workflow import WorkflowService, WorkflowConflictError
import orchestrator.workspace_mgr as workspace_module


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True, encoding="utf-8").stdout.strip()


def _setup(tmp_path, git=False):
    root = tmp_path / "源码 project"
    root.mkdir()
    (root / "app.txt").write_text("before\n", encoding="utf-8")
    if git:
        _git(root, "init", "-b", "main")
        _git(root, "config", "user.name", "Test")
        _git(root, "config", "user.email", "test@example.invalid")
        _git(root, "add", ".")
        _git(root, "commit", "-m", "base")
    cfg = Settings(data_dir=tmp_path / "data", port_range=(23100, 23130))
    db = Database.from_settings(cfg)
    run = db.create_run(mode="swarm", title="strict merge", project_root=root)
    mgr = WorkspaceManager(db, cfg)
    ws = mgr.workspace_create(run["id"], "writer", copy_env=False)
    return root, db, run, mgr, ws


@pytest.mark.parametrize("git", [False, True])
def test_direct_manager_merge_requires_candidate_gate(tmp_path, git):
    root, db, run, mgr, ws = _setup(tmp_path, git)
    try:
        (Path(ws["path"]) / "app.txt").write_text("after\n", encoding="utf-8")
        if git:
            _git(Path(ws["path"]), "add", ".")
            _git(Path(ws["path"]), "commit", "-m", "candidate")
        with pytest.raises((ValueError, RuntimeError), match="(?i)(step|candidate|verif|gate)"):
            mgr.workspace_merge(ws["id"])
        assert (root / "app.txt").read_text(encoding="utf-8") == "before\n"
    finally:
        db.close()


def test_reconcile_cannot_bypass_candidate_gate(tmp_path):
    root, db, run, mgr, ws = _setup(tmp_path)
    try:
        with pytest.raises((ValueError, RuntimeError), match="(?i)(step|merge|checkpoint|gate)"):
            mgr.workspace_merge(ws["id"], reconcile=True)
        assert (root / "app.txt").read_text(encoding="utf-8") == "before\n"
    finally:
        db.close()


def _approve(tmp_path, db, run, ws):
    db.save_task_packet(run["id"], title="Frozen", goal="apply approved source",
                        file_ownership={"writer": ["app.txt", "second.txt"]},
                        acceptance_criteria=["approved bytes"])
    db.upsert_worker(run["id"], "writer", role="coder", agent_id="/root/test-writer")
    db.upsert_worker(run["id"], "verifier", role="verifier", agent_id="/root/test-verifier")
    service = WorkflowService(db)
    step = service.step_save(run["id"], "main", "writer", "verifier", workspace_id=ws["id"])
    for action in ("dispatch", "accept", "submit"):
        step = service.step_advance(step["id"], action, step["revision"])
    artifact = service.capture(step["id"], "candidate")
    evidence = tmp_path / "unit-fixture-evidence.txt"
    evidence.write_text("Evidence bytes for gate identity unit tests; not a real business test claim.", encoding="utf-8")
    service.record(step["id"], artifact["id"], "verifier", "PASS",
                   [{"criterion": "approved bytes", "status": "PASS"}], [str(evidence)], "candidate")
    return service, service.get_step(step["id"]), evidence


def _integrated(service, step, evidence):
    artifact = service.capture(step["id"], "integrated")
    return service.record(step["id"], artifact["id"], "verifier", "PASS",
                          [{"criterion": "approved bytes", "status": "PASS"}], [str(evidence)], "integrated")


@pytest.mark.parametrize("git", [False, True])
def test_approved_merge_then_integrated_gate_before_cleanup(tmp_path, git):
    root, db, run, mgr, ws = _setup(tmp_path, git)
    try:
        source = Path(ws["path"])
        (source / "app.txt").write_text("approved\n", encoding="utf-8")
        if git:
            _git(source, "add", ".")
            _git(source, "commit", "-m", "approved")
        service, step, evidence = _approve(tmp_path, db, run, ws)
        result = mgr.workspace_merge(ws["id"])
        assert result["merged"]
        assert (root / "app.txt").read_text(encoding="utf-8") == "approved\n"
        assert service.get_step(step["id"])["state"] == "integrated_review"
        with pytest.raises(WorkflowConflictError, match="PASS"):
            mgr.workspace_cleanup(ws["id"])
        assert source.exists()
        _integrated(service, step, evidence)
        assert mgr.workspace_cleanup(ws["id"])["cleaned"]
    finally:
        db.close()


@pytest.mark.parametrize("git", [False, True])
def test_changed_candidate_cannot_reuse_pass(tmp_path, git):
    root, db, run, mgr, ws = _setup(tmp_path, git)
    try:
        source = Path(ws["path"])
        (source / "app.txt").write_text("approved\n", encoding="utf-8")
        if git:
            _git(source, "add", ".")
            _git(source, "commit", "-m", "approved")
        _approve(tmp_path, db, run, ws)
        (source / "app.txt").write_text("unapproved\n", encoding="utf-8")
        if git:
            _git(source, "add", ".")
            _git(source, "commit", "-m", "later unapproved")
        with pytest.raises(WorkflowConflictError, match="(?i)(stale|commit|artifact)"):
            mgr.workspace_merge(ws["id"])
        assert (root / "app.txt").read_text(encoding="utf-8") == "before\n"
    finally:
        db.close()


def test_snapshot_staging_uses_approved_content_not_later_workspace(tmp_path, monkeypatch):
    root, db, run, mgr, ws = _setup(tmp_path)
    try:
        source = Path(ws["path"])
        (source / "app.txt").write_text("approved\n", encoding="utf-8")
        service, step, evidence = _approve(tmp_path, db, run, ws)
        original = WorkflowService.begin_merge

        def mutate_after_checkpoint(self, *args):
            result = original(self, *args)
            (source / "app.txt").write_text("unapproved later edit\n", encoding="utf-8")
            return result

        monkeypatch.setattr(WorkflowService, "begin_merge", mutate_after_checkpoint)
        assert mgr.workspace_merge(ws["id"])["merged"]
        assert (root / "app.txt").read_text(encoding="utf-8") == "approved\n"
        assert (source / "app.txt").read_text(encoding="utf-8") == "unapproved later edit\n"
        _integrated(service, step, evidence)
        with pytest.raises(RuntimeError, match="uncommitted"):
            mgr.workspace_cleanup(ws["id"])
    finally:
        db.close()


def test_snapshot_partial_apply_is_retained_and_not_replayed(tmp_path, monkeypatch):
    root, db, run, mgr, ws = _setup(tmp_path)
    try:
        source = Path(ws["path"])
        (source / "app.txt").write_text("approved\n", encoding="utf-8")
        (source / "second.txt").write_text("second\n", encoding="utf-8")
        service, step, _ = _approve(tmp_path, db, run, ws)
        original = mgr._replace_merge_file
        writes = []

        def fail_second(src, target):
            writes.append(target.name)
            if len(writes) == 2:
                raise OSError("injected real apply failure")
            return original(src, target)

        monkeypatch.setattr(mgr, "_replace_merge_file", fail_second)
        with pytest.raises(OSError, match="injected"):
            mgr.workspace_merge(ws["id"])
        pending = service.get_step(step["id"])
        assert pending["state"] == "reconciliation_required"
        staging = Path(pending["checkpoint"]["merge"]["staging_dir"])
        assert (staging / "source/second.txt").read_text(encoding="utf-8") == "second\n"
        assert (staging / "before/app.txt").read_text(encoding="utf-8") == "before\n"
        with pytest.raises(RuntimeError, match="reconcile"):
            mgr.workspace_merge(ws["id"])
        with pytest.raises(RuntimeError, match="partially"):
            mgr.workspace_merge(ws["id"], reconcile=True)
        assert writes == ["app.txt", "second.txt"]
        assert not (root / "second.txt").exists()
        # Explicit external repair is not plugin replay; reconciliation only reads and certifies.
        (root / "second.txt").write_bytes((staging / "source/second.txt").read_bytes())
        assert mgr.workspace_merge(ws["id"], reconcile=True)["reconciled"]
        assert writes == ["app.txt", "second.txt"]
        assert service.get_step(step["id"])["state"] == "integrated_review"
    finally:
        db.close()


@pytest.mark.parametrize("git", [False, True])
def test_apply_completed_before_database_receipt_recovers_without_replay(tmp_path, monkeypatch, git):
    root, db, run, mgr, ws = _setup(tmp_path, git)
    try:
        source = Path(ws["path"])
        (source / "app.txt").write_text("approved\n", encoding="utf-8")
        if git:
            _git(source, "add", ".")
            _git(source, "commit", "-m", "approved")
        service, step, _ = _approve(tmp_path, db, run, ws)
        original = WorkflowService.finish_merge

        def fail_receipt(*args):
            raise OSError("injected before database receipt")

        monkeypatch.setattr(WorkflowService, "finish_merge", fail_receipt)
        with pytest.raises(OSError, match="receipt"):
            mgr.workspace_merge(ws["id"])
        assert (root / "app.txt").read_text(encoding="utf-8") == "approved\n"
        assert db.get_workspace(ws["id"])["status"] == "active"
        assert service.get_step(step["id"])["state"] == "reconciliation_required"
        head = _git(root, "rev-parse", "HEAD") if git else None
        monkeypatch.setattr(WorkflowService, "finish_merge", original)
        result = WorkspaceManager(db).workspace_merge(ws["id"], reconcile=True)
        assert result["reconciled"]
        assert db.get_workspace(ws["id"])["status"] == "merged"
        if git:
            assert _git(root, "rev-parse", "HEAD") == head
    finally:
        db.close()


def test_snapshot_add_delete_and_environment_exclusion(tmp_path):
    root, db, run, mgr, ws = _setup(tmp_path)
    try:
        source = Path(ws["path"])
        (source / "app.txt").unlink()
        (source / "second.txt").write_text("addition", encoding="utf-8")
        (source / ".env.local").write_text("must-not-merge", encoding="utf-8")
        _approve(tmp_path, db, run, ws)
        assert mgr.workspace_merge(ws["id"])["merged"]
        assert not (root / "app.txt").exists()
        assert (root / "second.txt").read_text(encoding="utf-8") == "addition"
        assert not (root / ".env.local").exists()
    finally:
        db.close()


def test_gate_compares_fetched_source_not_a_later_mutable_head(tmp_path, monkeypatch):
    root, db, run, mgr, ws = _setup(tmp_path, True)
    try:
        source = Path(ws["path"])
        (source / "app.txt").write_text("approved\n", encoding="utf-8")
        _git(source, "add", ".")
        _git(source, "commit", "-m", "approved")
        approved_commit = _git(source, "rev-parse", "HEAD")
        _approve(tmp_path, db, run, ws)
        (source / "app.txt").write_text("unapproved fetched content\n", encoding="utf-8")
        _git(source, "add", ".")
        _git(source, "commit", "-m", "unapproved")
        original = workspace_module._run_git

        def rewind_after_fetch(repo, *args, **kwargs):
            result = original(repo, *args, **kwargs)
            if args[0] == "fetch":
                _git(source, "reset", "--hard", approved_commit)
            return result

        monkeypatch.setattr(workspace_module, "_run_git", rewind_after_fetch)
        with pytest.raises(WorkflowConflictError, match="Fetched source commit"):
            mgr.workspace_merge(ws["id"])
        assert (root / "app.txt").read_text(encoding="utf-8") == "before\n"
        assert _git(source, "rev-parse", "HEAD") == approved_commit
    finally:
        db.close()


def test_strict_cherry_pick_checks_result_tree_and_source_proof(tmp_path):
    root, db, run, mgr, ws = _setup(tmp_path, True)
    try:
        source = Path(ws["path"])
        for text in ("candidate one\n", "candidate two\n"):
            (source / "app.txt").write_text(text, encoding="utf-8")
            _git(source, "add", ".")
            _git(source, "commit", "-m", text.strip())
        approved = _git(source, "rev-parse", "HEAD")
        service, step, evidence = _approve(tmp_path, db, run, ws)
        result = mgr.workspace_merge(ws["id"], strategy="cherry-pick")
        assert result["merged"] and len(result["commits_merged"]) == 2
        assert db.get_workspace(ws["id"])["merged_commit"] == approved
        assert (root / "app.txt").read_text(encoding="utf-8") == "candidate two\n"
        _integrated(service, step, evidence)
        assert mgr.workspace_cleanup(ws["id"])["cleaned"]
    finally:
        db.close()
