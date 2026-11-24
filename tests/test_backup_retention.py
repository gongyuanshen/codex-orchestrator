"""Tests for installer backup retention (install.ps1 / install.sh) and upgrade.py backup pruning."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_ROOT / "plugin"
INSTALL_PS1 = REPO_ROOT / "scripts" / "install.ps1"
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"
UPGRADE_PY = REPO_ROOT / "scripts" / "upgrade.py"


def _load_upgrade_mod():
    spec = importlib.util.spec_from_file_location("test_retention_upgrade_mod", UPGRADE_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _resolve_powershell() -> str | None:
    if os.name != "nt":
        return None
    return shutil.which("powershell.exe") or shutil.which("pwsh.exe")


def _resolve_posix_bash() -> str | None:
    if os.name != "nt":
        return shutil.which("bash")
    git = shutil.which("git")
    if git:
        git_root = Path(git).resolve().parent.parent
        for rel in ("bin\\bash.exe", "usr\\bin\\bash.exe"):
            cand = git_root / rel
            if cand.is_file():
                return str(cand)
    return None


def _resolve_codex_exe() -> Path | None:
    env_cli = os.environ.get("CODEX_CLI_PATH", "").strip()
    if env_cli and Path(env_cli).is_file():
        return Path(env_cli)
    local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
    if local_appdata:
        bin_root = Path(local_appdata) / "OpenAI" / "Codex" / "bin"
        if bin_root.is_dir():
            found = sorted(bin_root.rglob("codex.exe"), key=lambda p: p.stat().st_mtime, reverse=True)
            if found:
                return found[0]
    which_codex = shutil.which("codex.exe") or shutil.which("codex")
    if which_codex and Path(which_codex).is_file():
        return Path(which_codex)
    return None


def _create_dir_link(link_path: Path, target_dir: Path) -> bool:
    """Create a Windows NTFS junction or POSIX symlink without requiring elevation."""
    if os.name == "nt":
        res = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link_path), str(target_dir)],
            capture_output=True,
            text=True,
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return res.returncode == 0
    try:
        link_path.symlink_to(target_dir, target_is_directory=True)
        return True
    except OSError:
        return False


def _make_fake_plugin(base_dir: Path, *, with_venv: bool = False) -> Path:
    fake_plugin = base_dir / "plugin"
    (fake_plugin / "agents").mkdir(parents=True)
    (fake_plugin / ".codex-plugin").mkdir(parents=True)
    (fake_plugin / "hooks").mkdir(parents=True)
    shutil.copy2(PLUGIN_DIR / "pyproject.toml", fake_plugin / "pyproject.toml")
    shutil.copy2(PLUGIN_DIR / ".codex-plugin" / "plugin.json", fake_plugin / ".codex-plugin" / "plugin.json")
    shutil.copy2(PLUGIN_DIR / "hooks" / "hooks.json", fake_plugin / "hooks" / "hooks.json")
    for rf in ("interviewer.toml", "planner.toml", "investigator.toml", "coder.toml", "verifier.toml", "swarm_worker.toml"):
        shutil.copy2(PLUGIN_DIR / "agents" / rf, fake_plugin / "agents" / rf)
    if with_venv:
        venv_scripts = fake_plugin / ".venv" / ("Scripts" if os.name == "nt" else "bin")
        venv_scripts.mkdir(parents=True)
        (venv_scripts / ("python.exe" if os.name == "nt" else "python")).write_text("stub", encoding="utf-8")
    return fake_plugin


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_install_sh_lf_and_keep_backups_cli_contract() -> None:
    raw = INSTALL_SH.read_bytes()
    assert b"\r" not in raw, "scripts/install.sh must preserve pure LF line endings"
    text = raw.decode("utf-8")
    assert "--keep-backups" in text
    assert "KEEP_BACKUPS=3" in text
    assert "tri-orchestrator-baseline" in text
    assert "tri-orchestrator-2" in text

    bash = _resolve_posix_bash()
    if bash is None:
        return

    syntax = subprocess.run([bash, "-n", str(INSTALL_SH)], capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert syntax.returncode == 0, f"bash -n install.sh failed: {syntax.stderr}"

    help_res = subprocess.run([bash, str(INSTALL_SH), "--help"], capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert help_res.returncode == 0
    assert "--keep-backups" in help_res.stdout

    for bad_val in ("-1", "abc"):
        bad_res = subprocess.run(
            [bash, str(INSTALL_SH), "--keep-backups", bad_val],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        assert bad_res.returncode == 2
        assert "Invalid --keep-backups" in bad_res.stderr


def test_install_sh_executes_backup_rotation_when_bash_supports_env(tmp_path: Path) -> None:
    bash = _resolve_posix_bash()
    if bash is None:
        pytest.skip("POSIX bash not available")

    fake_plugin = _make_fake_plugin(tmp_path, with_venv=True)
    codex_home = tmp_path / "codex_home"
    backups_root = codex_home / "backups"
    backups_root.mkdir(parents=True)
    (codex_home / "config.toml").write_text("[features]\nmulti_agent = false\n", encoding="utf-8")
    (codex_home / "agents").mkdir(parents=True)
    (codex_home / "agents" / "coder.toml").write_text('name = "coder"\n', encoding="utf-8")

    for i in range(1, 6):
        d = backups_root / f"tri-orchestrator-20250101-00000{i}"
        d.mkdir(parents=True)
        (d / "config.toml").write_text(f"# old {i}\n", encoding="utf-8")
    unrelated = backups_root / "other-backup-20250101"
    unrelated.mkdir()
    (unrelated / "keep.txt").write_text("keep", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    mock_codex = bin_dir / "codex"
    mock_codex.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    mock_codex.chmod(0o755)
    mock_uv = bin_dir / "uv"
    mock_uv.write_text(
        f"#!/usr/bin/env bash\nif [ \"$1\" = \"python\" ] && [ \"$2\" = \"find\" ]; then printf '%s\\n' \"{sys.executable}\"; exit 0; fi\nexit 0\n",
        encoding="utf-8",
    )
    mock_uv.chmod(0o755)

    env = dict(os.environ)
    if os.name == "nt":
        # Convert bin_dir & use env cleanly under Git Bash; Windows Python handles forward slashes.
        bash_bin = "/" + str(bin_dir).replace(":", "").replace("\\", "/")
        env["PATH"] = f"{bash_bin}:{env.get('PATH', '')}"
        env["CODEX_CLI_PATH"] = "/" + str(mock_codex).replace(":", "").replace("\\", "/")
    else:
        env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
        env["CODEX_CLI_PATH"] = str(mock_codex)

    res = subprocess.run(
        [
            bash,
            str(INSTALL_SH),
            "--plugin-dir",
            str(fake_plugin),
            "--codex-home",
            str(codex_home),
            "--skip-plugin-install",
            "--skip-uv-sync",
            "--keep-backups",
            "3",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=30,
    )
    assert res.returncode == 0, f"install.sh failed:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"

    assert (backups_root / "tri-orchestrator-baseline" / "baseline.json").is_file()
    assert (backups_root / "tri-orchestrator-baseline" / "agents" / "coder.toml").is_file()
    assert (unrelated / "keep.txt").is_file()

    ts_dirs = sorted(p.name for p in backups_root.iterdir() if p.is_dir() and p.name.startswith("tri-orchestrator-2"))
    assert len(ts_dirs) == 3, ts_dirs
    assert "tri-orchestrator-20250101-000001" not in ts_dirs
    assert "tri-orchestrator-20250101-000002" not in ts_dirs
    assert "tri-orchestrator-20250101-000003" not in ts_dirs
    assert "tri-orchestrator-20250101-000004" in ts_dirs
    assert "tri-orchestrator-20250101-000005" in ts_dirs
    assert not any(p.name == ".venv" for p in backups_root.rglob("*"))


@pytest.mark.skipif(os.name != "nt", reason="PowerShell installer retention test runs on Windows")
def test_install_ps1_backup_retention_and_no_venv_leak(tmp_path: Path) -> None:
    pwsh = _resolve_powershell()
    codex_exe = _resolve_codex_exe()
    if pwsh is None or codex_exe is None:
        pytest.skip("Requires Windows PowerShell and codex.exe binary")

    fake_plugin = _make_fake_plugin(tmp_path, with_venv=True)
    codex_home = tmp_path / "codex_home"
    backups_root = codex_home / "backups"
    backups_root.mkdir(parents=True)
    (codex_home / "config.toml").write_text("[features]\nmulti_agent = false\n", encoding="utf-8")
    (codex_home / "agents").mkdir(parents=True)
    (codex_home / "agents" / "coder.toml").write_text('name = "coder"\n', encoding="utf-8")

    # Pre-seed 5 timestamped backups, 1 unrelated dir, and protected junction targets.
    protected_target = tmp_path / "protected_target"
    protected_target.mkdir()
    (protected_target / "keep.txt").write_text("must-survive", encoding="utf-8")

    for i in range(1, 6):
        d = backups_root / f"tri-orchestrator-20250101-00000{i}"
        d.mkdir(parents=True)
        (d / "config.toml").write_text(f"# backup {i}\n", encoding="utf-8")
    assert _create_dir_link(backups_root / "tri-orchestrator-20250101-000001" / "nested_junction", protected_target)
    assert _create_dir_link(backups_root / "tri-orchestrator-20250101-000000", protected_target)

    unrelated = backups_root / "other-tool-20250101"
    unrelated.mkdir()
    (unrelated / "note.txt").write_text("untouched", encoding="utf-8")

    env = dict(
        os.environ,
        CODEX_HOME=str(codex_home),
        CODEX_CLI_PATH=str(codex_exe),
        TRI_ORCH_DATA_DIR=str(tmp_path / "data"),
        TRI_ORCH_PORT=str(_free_port()),
    )

    def _run_ps1(*extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                pwsh,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(INSTALL_PS1),
                "-PluginDir",
                str(fake_plugin),
                "-CodexHome",
                str(codex_home),
                "-SkipPluginInstall",
                "-SkipUvSync",
                *extra,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=45,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    # Default -KeepBackups 3
    res1 = _run_ps1()
    assert res1.returncode == 0, f"install.ps1 failed:\n{res1.stdout}\n{res1.stderr}"

    assert (backups_root / "tri-orchestrator-baseline" / "baseline.json").is_file()
    assert (backups_root / "tri-orchestrator-baseline" / "config.toml").is_file()
    assert (backups_root / "tri-orchestrator-baseline" / "agents" / "coder.toml").is_file()
    assert (unrelated / "note.txt").is_file()
    assert (protected_target / "keep.txt").read_text(encoding="utf-8") == "must-survive"
    assert not (backups_root / "tri-orchestrator-20250101-000001").exists()
    assert not (backups_root / "tri-orchestrator-20250101-000002").exists()
    assert not (backups_root / "tri-orchestrator-20250101-000003").exists()
    assert (backups_root / "tri-orchestrator-20250101-000004").is_dir()
    assert (backups_root / "tri-orchestrator-20250101-000005").is_dir()

    real_ts_dirs = sorted(
        p.name
        for p in backups_root.iterdir()
        if p.is_dir() and not p.is_symlink() and not p.is_junction() and p.name.startswith("tri-orchestrator-2")
    )
    assert len(real_ts_dirs) == 3, real_ts_dirs
    assert not any(p.name == ".venv" for p in backups_root.rglob("*"))

    # Override with -KeepBackups 1 (plus same-second collision handling)
    res2 = _run_ps1("-KeepBackups", "1")
    assert res2.returncode == 0, f"install.ps1 -KeepBackups 1 failed:\n{res2.stdout}\n{res2.stderr}"
    assert (backups_root / "tri-orchestrator-baseline" / "baseline.json").is_file()
    real_ts_after = [
        p.name
        for p in backups_root.iterdir()
        if p.is_dir() and not p.is_symlink() and not p.is_junction() and p.name.startswith("tri-orchestrator-2")
    ]
    assert len(real_ts_after) == 1, real_ts_after
    assert (backups_root / real_ts_after[0] / "manifest.json").is_file()

    # Invalid negative -KeepBackups must fail validation
    bad_res = _run_ps1("-KeepBackups", "-1")
    assert bad_res.returncode != 0


def _seed_upgrade_backup(
    path: Path,
    *,
    created_at: float,
    applied: bool = False,
    verified: bool = False,
    failed: bool = False,
    started: bool = False,
    wrapped: bool = False,
) -> Path:
    bundle = (path / "upgrade-bundle") if wrapped else path
    with TemporaryDirectory(dir=path.parent) as staging:
        source, target, data = (Path(staging) / name for name in ("source", "target", "data"))
        for plugin in (source, target):
            (plugin / ".codex-plugin").mkdir(parents=True)
            (plugin / ".codex-plugin" / "plugin.json").write_text(
                '{"name":"tri-orchestrator","version":"0.6.2"}', encoding="utf-8"
            )
            (plugin / "src" / "orchestrator").mkdir(parents=True)
            (plugin / "src" / "orchestrator" / "db.py").write_text("SCHEMA_VERSION = 10\n", encoding="utf-8")
        data.mkdir()
        with closing(sqlite3.connect(data / "state.db")) as db:
            db.executescript(
                "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY); INSERT INTO schema_migrations VALUES(10);"
                "CREATE TABLE runs(id TEXT, mode TEXT, status TEXT);"
            )
        manifest = _load_upgrade_mod().prepare(source, target, data, bundle)
    manifest["created_at"] = created_at
    (bundle / "prepared.json").write_text(json.dumps(manifest), encoding="utf-8")
    if started:
        (bundle / "apply-started.json").write_text("{}", encoding="utf-8")
    if applied:
        (bundle / "applied.json").write_text("{}", encoding="utf-8")
    if verified:
        (bundle / "verified.json").write_text("{}", encoding="utf-8")
    if failed:
        (bundle / "apply-failed.json").write_text("{}", encoding="utf-8")
    return bundle


def test_upgrade_py_prune_rotates_backups_and_preserves_failed_and_active(tmp_path: Path) -> None:
    upgrade = _load_upgrade_mod()
    backup_root = tmp_path / "upgrades"
    backup_root.mkdir()

    b1 = _seed_upgrade_backup(backup_root / "tri-orchestrator-0.4-to-0.5-1", created_at=100.0, applied=True, verified=True)
    b2 = _seed_upgrade_backup(backup_root / "tri-orchestrator-0.5-to-0.6-2", created_at=200.0, applied=True)
    b3 = _seed_upgrade_backup(backup_root / "tri-orchestrator-0.6.0-to-0.6.1-3", created_at=300.0, applied=True, verified=True)
    b4 = _seed_upgrade_backup(backup_root / "tri-orchestrator-0.6.1-to-0.6.2-4", created_at=400.0, applied=True, verified=True, wrapped=True)
    b5 = _seed_upgrade_backup(backup_root / "tri-orchestrator-0.6.2-to-0.6.3-5", created_at=500.0, applied=True, verified=True)
    b_fail = _seed_upgrade_backup(backup_root / "tri-orchestrator-failed", created_at=50.0, started=True, failed=True)
    b_inflight = _seed_upgrade_backup(backup_root / "tri-orchestrator-inflight", created_at=60.0, started=True)
    unrelated = backup_root / "unrelated-folder"
    unrelated.mkdir()
    (unrelated / "keep.txt").write_text("keep", encoding="utf-8")

    # Dry-run reports without deleting
    dry_res = subprocess.run(
        [
            sys.executable,
            str(UPGRADE_PY),
            "prune",
            "--backup-root",
            str(backup_root),
            "--keep-backups",
            "3",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert dry_res.returncode == 0, dry_res.stderr
    dry_report = json.loads(dry_res.stdout)
    assert dry_report["dry_run"] is True
    assert dry_report["freed_bytes"] > 0
    assert {Path(p).name for p in dry_report["pruned_backups"]} == {b1.name, b2.name}
    assert b1.exists() and b2.exists()

    # Verified-only keeps b2 (unverified) untouched and prunes oldest verified b1 (since 4 verified -> keep 3)
    ver_only = upgrade.prune(backup_root=backup_root, keep_backups=3, verified_only=True)
    assert [Path(p).name for p in ver_only["pruned_backups"]] == [b1.name]
    assert not b1.exists()
    assert b2.exists() and b3.exists() and b4.exists() and b5.exists()

    # Regular prune (--keep-backups 3) removes b2 so only b3, b4, b5 remain; failed/inflight/unrelated stay
    res = subprocess.run(
        [
            sys.executable,
            str(UPGRADE_PY),
            "prune",
            "--backup-root",
            str(backup_root),
            "--keep-backups",
            "3",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert res.returncode == 0, res.stderr
    report = json.loads(res.stdout)
    assert [Path(p).name for p in report["pruned_backups"]] == [b2.name]
    assert {Path(p).name for p in report["kept_backups"]} == {b3.name, b4.name, b5.name}
    skipped_reasons = {Path(item["path"]).name: item["reason"] for item in report["skipped_backups"]}
    assert skipped_reasons[b_fail.name] == "apply_failed"
    assert skipped_reasons[b_inflight.name] == "apply_in_progress"
    assert not b2.exists()
    assert b3.exists() and b4.exists() and b5.exists()
    assert b_fail.exists() and b_inflight.exists() and (unrelated / "keep.txt").is_file()


def test_rotation_checks_ownership_before_counting_or_deleting(tmp_path: Path) -> None:
    upgrade = _load_upgrade_mod()
    root = tmp_path / "backups"
    root.mkdir()
    old = _seed_upgrade_backup(root / "wrapped-old", created_at=10, wrapped=True)
    sibling = old.parent / "other-application-data"
    sibling.write_bytes(b"keep me")
    keep = [_seed_upgrade_backup(root / str(i), created_at=i) for i in (20, 30, 40)]

    foreign = root / "foreign"
    shutil.copytree(keep[0], foreign)
    marker = foreign / "prepared.json"
    manifest = json.loads(marker.read_text(encoding="utf-8"))
    manifest["created_at"] = 1
    manifest["source_identity"]["name"] = "another-app"
    marker.write_text(json.dumps(manifest), encoding="utf-8")
    broken = root / "broken"
    broken.mkdir()
    (broken / "prepared.json").write_bytes(b"{")
    incomplete = root / "missing-db"
    shutil.copytree(keep[0], incomplete)
    (incomplete / "state-before.db").unlink()
    alias = root / "foreign-wrapper"
    alias.mkdir()
    assert _create_dir_link(alias / "upgrade-bundle", keep[0])

    for dry_run in (True, False):
        report = upgrade.prune(backup_root=root, keep_backups=3, dry_run=dry_run)
        assert report["pruned_backups"] == [str(old)]
        assert set(report["kept_backups"]) == set(map(str, keep))
        skipped = {Path(item["path"]): item["reason"] for item in report["skipped_backups"]}
        assert all(skipped[path] for path in (foreign, broken, incomplete, alias))
        assert skipped[alias] == "symlink_or_junction"
        assert old.exists() == dry_run
        assert sibling.read_bytes() == b"keep me"
        assert (broken / "prepared.json").read_bytes() == b"{"
        assert all(p.is_dir() for p in (foreign, incomplete, alias, *keep))


def test_upgrade_py_prune_plugin_backup_staging_and_junction_safety(tmp_path: Path) -> None:
    upgrade = _load_upgrade_mod()

    # 1. Without any active plugin dir, plugin-backup-* must NOT be deleted
    orphan_cache = tmp_path / "orphan_cache"
    orphan_cache.mkdir()
    orphan_bak = orphan_cache / "plugin-backup-only"
    orphan_bak.mkdir()
    (orphan_bak / "data.txt").write_text("keep", encoding="utf-8")
    orphan_rep = upgrade.prune(plugin_cache_dir=orphan_cache)
    assert orphan_rep["pruned_plugin_backups"] == []
    assert orphan_rep["skipped_plugin_backups"][0]["reason"] == "no_active_plugin_detected"
    assert orphan_bak.exists()

    # 2. With valid active plugin dir, expired plugin-backup-* staging dirs are cleaned safely without following junctions
    cache_root = tmp_path / "plugins" / "cache" / "tri-orchestrator"
    active_ver = cache_root / "tri-orchestrator" / "0.6.2"
    (active_ver / ".codex-plugin").mkdir(parents=True)
    (active_ver / ".codex-plugin" / "plugin.json").write_text('{"name":"tri-orchestrator","version":"0.6.2"}', encoding="utf-8")

    victim_dir = tmp_path / "victim_dir"
    victim_dir.mkdir()
    (victim_dir / "secret.txt").write_text("safe", encoding="utf-8")

    pb1 = cache_root / "plugin-backup-aaa111"
    pb1.mkdir()
    (pb1 / "old.txt").write_text("stale", encoding="utf-8")
    assert _create_dir_link(pb1 / "inner_link", victim_dir)

    pb_top_link = cache_root / "plugin-backup-toplink"
    assert _create_dir_link(pb_top_link, victim_dir)

    pb_active_like = cache_root / "plugin-backup-active"
    pb_active_like.mkdir()

    rep = upgrade.prune(plugin_cache_dir=cache_root, active_plugin_dir=pb_active_like)
    assert [Path(p).name for p in rep["pruned_plugin_backups"]] == ["plugin-backup-aaa111"]
    assert not pb1.exists()
    assert pb_active_like.exists()
    assert active_ver.exists()
    assert (victim_dir / "secret.txt").read_text(encoding="utf-8") == "safe"
    skipped_pb = {Path(item["path"]).name: item["reason"] for item in rep["skipped_plugin_backups"]}
    assert skipped_pb["plugin-backup-toplink"] == "symlink_or_junction"
    assert skipped_pb["plugin-backup-active"] == "active_plugin_overlap"


def test_upgrade_py_verify_and_prepare_rotation(tmp_path: Path) -> None:
    upgrade = _load_upgrade_mod()

    source = tmp_path / "source"
    (source / ".codex-plugin").mkdir(parents=True)
    (source / "src" / "orchestrator").mkdir(parents=True)
    (source / ".codex-plugin" / "plugin.json").write_text('{"name":"tri-orchestrator","version":"0.6.2"}', encoding="utf-8")
    (source / "src" / "orchestrator" / "__init__.py").write_text("", encoding="utf-8")
    (source / "src" / "orchestrator" / "db.py").write_text(
        "SCHEMA_VERSION = 10\nclass Database:\n    def __init__(self, p): pass\n    def close(self): pass\n",
        encoding="utf-8",
    )

    cache_root = tmp_path / "codex_home" / "plugins" / "cache" / "tri-orchestrator"
    target = cache_root / "tri-orchestrator" / "0.6.2"
    (target / ".codex-plugin").mkdir(parents=True)
    (target / "src" / "orchestrator").mkdir(parents=True)
    (target / ".codex-plugin" / "plugin.json").write_text('{"name":"tri-orchestrator","version":"0.6.2"}', encoding="utf-8")
    (target / "src" / "orchestrator" / "__init__.py").write_text("", encoding="utf-8")
    (target / "src" / "orchestrator" / "db.py").write_text("SCHEMA_VERSION = 10\n", encoding="utf-8")

    stale_pb = cache_root / "plugin-backup-stale99"
    stale_pb.mkdir()
    (stale_pb / "junk.txt").write_text("junk", encoding="utf-8")

    data = tmp_path / "data"
    data.mkdir()
    with closing(sqlite3.connect(data / "state.db")) as db:
        db.executescript(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at REAL);"
            "INSERT INTO schema_migrations VALUES(10, 0);"
            "CREATE TABLE runs(id TEXT, mode TEXT, status TEXT);"
        )

    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    old1 = _seed_upgrade_backup(backup_root / "bak-1", created_at=10.0, applied=True, verified=True)
    old2 = _seed_upgrade_backup(backup_root / "bak-2", created_at=20.0, applied=True, verified=True)
    old3 = _seed_upgrade_backup(backup_root / "bak-3", created_at=30.0, applied=True, verified=True)

    # prepare(..., keep_backups=0) still protects the newly staged backup while pruning older ones
    cur_bak = backup_root / "bak-4"
    prep_res = upgrade.prepare(source, target, data, cur_bak, port=_free_port(), keep_backups=3)
    assert prep_res["status"] == "prepared"
    assert not old1.exists()
    assert old2.exists() and old3.exists() and cur_bak.exists()

    # Run verify against loopback health endpoint -> writes verified.json & prunes stale plugin-backup-* + excess backups
    caps = [
        "durable_inbox_v1", "workflow_gates_v1", "async_inbox_wait_v1", "workflow_modes_v2",
        "native_actions_v1", "verification_receipts_v1", "plan_admission_v1", "compact_handoff_v1",
        "recipient_groups_v1", "progress_classification_v1", "workflow_reviews_v1",
        "macro_rpc_v1", "compact_board_v1", "workflow5_direct_spawn_v1", "session_hooks_v1",
        "agent_model_tracking_v1",
    ]
    health = {
        "status": "ok",
        "ok": True,
        "pid": os.getpid(),
        "data_dir": str(data.resolve()),
        "software_version": "0.6.2",
        "schema_version": 10,
        "capabilities": caps,
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(health).encode("utf-8"))

        def log_message(self, *_args):
            return

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as srv:
        health["port"] = srv.server_port
        prepared_data = json.loads((cur_bak / "prepared.json").read_text(encoding="utf-8"))
        prepared_data["port"] = srv.server_port
        (cur_bak / "prepared.json").write_text(json.dumps(prepared_data), encoding="utf-8")

        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            ver_res = upgrade.verify(cur_bak, keep_backups=2)
        finally:
            srv.shutdown()
            th.join(timeout=3)

    assert ver_res["status"] == "backend_verified"
    assert (cur_bak / "verified.json").is_file()
    assert not old2.exists()
    assert old3.exists() and cur_bak.exists()
    assert not stale_pb.exists()
    assert target.exists()
