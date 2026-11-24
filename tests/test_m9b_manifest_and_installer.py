"""M9b single-manifest convergence, native `hooks/list` self-check, `-TrustPluginHooks`, and uninstall tests."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
import tomllib
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_ROOT / "plugin"
INSTALL_PS1 = REPO_ROOT / "scripts" / "install.ps1"
UNINSTALL_PS1 = REPO_ROOT / "scripts" / "uninstall.ps1"
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"
VERIFY_E2E_PS1 = REPO_ROOT / "scripts" / "verify_e2e.ps1"
UPGRADE_PY = REPO_ROOT / "scripts" / "upgrade.py"


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _resolve_powershell() -> str | None:
    if os.name != "nt":
        return None
    return shutil.which("powershell.exe") or shutil.which("pwsh.exe")


def _resolve_bash() -> str | Path | None:
    if os.name == "nt":
        git = shutil.which("git")
        return Path(git).resolve().parents[1] / "bin" / "bash.exe" if git else None
    return shutil.which("bash")


def _resolve_codex_exe() -> Path | None:
    env_cli = os.environ.get("CODEX_CLI_PATH", "").strip()
    if env_cli and Path(env_cli).is_file():
        return Path(env_cli)
    local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
    if local_appdata:
        bin_root = Path(local_appdata) / "OpenAI" / "Codex" / "bin"
        if bin_root.is_dir():
            found = sorted(
                bin_root.rglob("codex.exe"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if found:
                return found[0]
    which_codex = shutil.which("codex.exe") or shutil.which("codex")
    if which_codex and Path(which_codex).is_file():
        return Path(which_codex)
    return None


def _query_appserver_hooks_list(codex_exe: Path, codex_home: Path, cwd: Path) -> list[dict[str, Any]]:
    """Run short-lived read-only `codex app-server --stdio` handshake to query `hooks/list`."""
    env = dict(os.environ)
    env["CODEX_HOME"] = str(codex_home)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.Popen(
        [str(codex_exe), "app-server", "--stdio"],
        cwd=str(cwd),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
        creationflags=creationflags,
    )

    out_q: queue.Queue[str | None] = queue.Queue()
    err_lines: list[str] = []

    def _read_stdout() -> None:
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                out_q.put(line)
        finally:
            out_q.put(None)

    def _read_stderr() -> None:
        assert proc.stderr is not None
        try:
            for line in proc.stderr:
                err_lines.append(line)
        except Exception:
            return

    t_out = threading.Thread(target=_read_stdout, daemon=True)
    t_err = threading.Thread(target=_read_stderr, daemon=True)
    t_out.start()
    t_err.start()

    def _send(payload: dict[str, Any]) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(payload) + "\n")
        proc.stdin.flush()

    result_obj: dict[str, Any] | None = None
    try:
        _send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "test-m9b-hooks-list", "version": "0.6.10"},
                    "capabilities": {"experimentalApi": True},
                },
            }
        )
        sent_hooks_list = False
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            try:
                line = out_q.get(timeout=0.4)
            except queue.Empty:
                if proc.poll() is not None and out_q.empty():
                    break
                continue
            if line is None:
                break
            stripped = line.strip()
            if not stripped:
                continue
            msg = json.loads(stripped)
            if not isinstance(msg, dict):
                continue
            if msg.get("id") == 1 and not sent_hooks_list:
                assert "error" not in msg, f"initialize failed: {msg}"
                _send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
                _send({"jsonrpc": "2.0", "id": 2, "method": "hooks/list", "params": {}})
                sent_hooks_list = True
            elif msg.get("id") == 2:
                assert "error" not in msg, f"hooks/list failed: {msg}"
                result_obj = msg.get("result") or {}
                break
    finally:
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(timeout=3.0)
        t_out.join(timeout=1.0)
        t_err.join(timeout=1.0)
        if proc.stdout:
            proc.stdout.close()
        if proc.stderr:
            proc.stderr.close()

    assert result_obj is not None, f"Did not receive hooks/list response; stderr={''.join(err_lines)}"
    collected: list[dict[str, Any]] = []
    if isinstance(result_obj.get("data"), list):
        for bucket in result_obj["data"]:
            if isinstance(bucket, dict) and isinstance(bucket.get("hooks"), list):
                collected.extend(bucket["hooks"])
    elif isinstance(result_obj.get("hooks"), list):
        collected.extend(result_obj["hooks"])

    deduped: dict[str, dict[str, Any]] = {}
    for item in collected:
        if isinstance(item, dict) and item.get("pluginId") == "tri-orchestrator@tri-orchestrator":
            key = str(item.get("key") or f"{item.get('eventName')}:{len(deduped)}")
            deduped[key] = item
    return list(deduped.values())


def test_single_manifest_convergence_and_sixth_cli_entrypoint() -> None:
    # 1. Root plugin/plugin.json must be deleted; .codex-plugin/plugin.json is the sole manifest
    assert not (PLUGIN_DIR / "plugin.json").exists()
    manifest_path = PLUGIN_DIR / ".codex-plugin" / "plugin.json"
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["name"] == "tri-orchestrator"
    assert manifest["version"] == "0.6.13"
    assert manifest["hooks"] == "./hooks/hooks.json"
    assert manifest["skills"] == "./skills/"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert len(manifest["interface"]["shortDescription"]) <= 30

    # Verify all 3 skills mentioned in description/longDescription and pipeline removed
    desc = manifest.get("description", "")
    long_desc = manifest["interface"].get("longDescription", "")
    for skill_name in ("$boost", "$teamwork", "$swarm"):
        assert skill_name in desc or skill_name in long_desc, f"{skill_name} must be mentioned in plugin.json description"
    assert "$pipeline" not in desc and "$pipeline" not in long_desc
    assert "pipeline" not in manifest.get("keywords", [])

    marketplace = json.loads(
        (REPO_ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8")
    )
    assert marketplace["name"] == "tri-orchestrator"
    assert marketplace["plugins"][0]["source"]["path"] == "./plugin"

    hooks_path = PLUGIN_DIR / "hooks" / "hooks.json"
    assert hooks_path.is_file()
    hooks_cfg = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert set(hooks_cfg["hooks"].keys()) == {
        "SessionStart",
        "PostCompact",
        "UserPromptSubmit",
        "Stop",
    }

    # 2. All 5 CLI scripts registered in plugin/pyproject.toml and installed into repo .venv
    pyproject = tomllib.loads((PLUGIN_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject["project"]["scripts"]
    assert len(scripts) == 5
    assert "tri-orchestrator-pipeline" not in scripts
    if os.name == "nt":
        venv_script = REPO_ROOT / ".venv" / "Scripts" / "tri-orchestrator-challenge.exe"
    else:
        venv_script = REPO_ROOT / ".venv" / "bin" / "tri-orchestrator-challenge"
    assert venv_script.is_file(), f"Expected console script installed at {venv_script}"

    # 3. Scripts reference .codex-plugin/plugin.json and upgrade.py omits removed pipeline capability
    for script_path in (INSTALL_PS1, INSTALL_SH, VERIFY_E2E_PS1, UPGRADE_PY):
        txt = script_path.read_text(encoding="utf-8")
        assert ".codex-plugin" in txt, f"{script_path} must reference .codex-plugin/plugin.json"

    assert b"\r" not in INSTALL_SH.read_bytes(), "scripts/install.sh must preserve LF line endings"

    spec = importlib.util.spec_from_file_location("m9b_upgrade_mod", UPGRADE_PY)
    assert spec is not None and spec.loader is not None
    upgrade_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(upgrade_mod)
    assert upgrade_mod.package_identity(PLUGIN_DIR) == {
        "name": "tri-orchestrator",
        "version": "0.6.13",
    }
    assert "pipeline_runner_v1" not in UPGRADE_PY.read_text(encoding="utf-8")


@pytest.mark.skipif(os.name != "nt", reason="PowerShell installer tests run on Windows")
def test_install_ps1_rejects_legacy_dual_manifest(tmp_path: Path) -> None:
    pwsh = _resolve_powershell()
    assert pwsh is not None
    fake_plugin = tmp_path / "plugin"
    (fake_plugin / "agents").mkdir(parents=True)
    (fake_plugin / ".codex-plugin").mkdir(parents=True)
    (fake_plugin / "hooks").mkdir(parents=True)
    shutil.copy2(PLUGIN_DIR / "pyproject.toml", fake_plugin / "pyproject.toml")
    shutil.copy2(
        PLUGIN_DIR / ".codex-plugin" / "plugin.json",
        fake_plugin / ".codex-plugin" / "plugin.json",
    )
    shutil.copy2(PLUGIN_DIR / "hooks" / "hooks.json", fake_plugin / "hooks" / "hooks.json")
    # Re-introduce a stale root plugin.json to confirm fail-closed detection
    (fake_plugin / "plugin.json").write_text('{"name": "tri-orchestrator", "version": "0.6.10"}\n', encoding="utf-8")

    codex_home = tmp_path / "codex_home"
    env = dict(
        os.environ,
        CODEX_HOME=str(codex_home),
        TRI_ORCH_DATA_DIR=str(tmp_path / "data"),
        TRI_ORCH_PORT=str(_free_loopback_port()),
    )
    res = subprocess.run(
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
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=30,
    )
    assert res.returncode != 0
    combined = f"{res.stdout}\n{res.stderr}"
    assert "Legacy root plugin.json must not exist" in combined


@pytest.mark.skipif(os.name != "nt", reason="PowerShell installer E2E runs on Windows")
def test_real_install_hooks_list_trust_and_uninstall_e2e(tmp_path: Path) -> None:
    pwsh = _resolve_powershell()
    codex_exe = _resolve_codex_exe()
    if pwsh is None or codex_exe is None:
        pytest.skip("Requires Windows PowerShell and codex.exe binary")

    codex_home = tmp_path / "codex_home"
    data_dir = tmp_path / "tri_orch_data"
    probe_cwd = tmp_path / "workspace"
    probe_cwd.mkdir(parents=True)
    ephemeral_port = _free_loopback_port()

    env = dict(
        os.environ,
        CODEX_HOME=str(codex_home),
        TRI_ORCH_DATA_DIR=str(data_dir),
        TRI_ORCH_PORT=str(ephemeral_port),
    )

    def _run_ps1(script: Path, *extra_args: str, timeout: float = 180.0) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                pwsh,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                *extra_args,
            ],
            cwd=str(probe_cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    # Phase 1: Default conservative install (no -TrustPluginHooks)
    install_default = _run_ps1(
        INSTALL_PS1,
        "-RepoRoot",
        str(REPO_ROOT),
        "-CodexHome",
        str(codex_home),
    )
    assert install_default.returncode == 0, (
        f"Default install.ps1 failed:\nSTDOUT:\n{install_default.stdout}\nSTDERR:\n{install_default.stderr}"
    )
    assert "untrusted" in install_default.stdout
    assert "[提示]" in install_default.stdout
    assert "PostCompact" in install_default.stdout and "Stop" in install_default.stdout

    cfg_path = codex_home / "config.toml"
    assert cfg_path.is_file()
    cfg_default = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    assert "hooks" not in cfg_default, "Default install must NOT write [hooks.state] into config.toml"

    # Verify [features] multi_agent_v2 = true is set by install.ps1
    features = cfg_default.get("features", {})
    assert features.get("multi_agent") is True, "install.ps1 must set multi_agent = true"
    assert features.get("multi_agent_v2") is True, "install.ps1 must set multi_agent_v2 = true"

    cached_venv_scripts = (
        codex_home
        / "plugins"
        / "cache"
        / "tri-orchestrator"
        / "tri-orchestrator"
        / "0.6.13"
        / ".venv"
        / "Scripts"
    )
    assert (cached_venv_scripts / "tri-orchestrator-hook.exe").is_file()
    assert not (cached_venv_scripts / "tri-orchestrator-pipeline.exe").exists()

    hooks_after_default = _query_appserver_hooks_list(codex_exe, codex_home, probe_cwd)
    assert {h["eventName"] for h in hooks_after_default} == {
        "sessionStart",
        "postCompact",
        "userPromptSubmit",
        "stop",
    }
    assert all(h["trustStatus"] == "untrusted" for h in hooks_after_default)

    # Phase 2: Explicit -TrustPluginHooks transitions all 4 hooks to trusted (and is idempotent)
    install_trusted = _run_ps1(
        INSTALL_PS1,
        "-RepoRoot",
        str(REPO_ROOT),
        "-CodexHome",
        str(codex_home),
        "-SkipUvSync",
        "-TrustPluginHooks",
    )
    assert install_trusted.returncode == 0, (
        f"-TrustPluginHooks install.ps1 failed:\nSTDOUT:\n{install_trusted.stdout}\nSTDERR:\n{install_trusted.stderr}"
    )
    # Note: without -SkipPluginInstall, install.ps1 refreshes the plugin cache; re-run idempotently with -SkipPluginInstall -SkipUvSync -TrustPluginHooks too
    install_trusted_idem = _run_ps1(
        INSTALL_PS1,
        "-RepoRoot",
        str(REPO_ROOT),
        "-CodexHome",
        str(codex_home),
        "-SkipPluginInstall",
        "-SkipUvSync",
        "-TrustPluginHooks",
    )
    assert install_trusted_idem.returncode == 0, (
        f"Idempotent -TrustPluginHooks failed:\n{install_trusted_idem.stdout}\n{install_trusted_idem.stderr}"
    )

    cfg_trusted = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    hooks_state = cfg_trusted.get("hooks", {}).get("state", {})
    tri_keys = [k for k in hooks_state if k.startswith("tri-orchestrator@tri-orchestrator:")]
    assert len(tri_keys) == 4
    for k in tri_keys:
        entry = hooks_state[k]
        assert entry["enabled"] is True
        assert entry["trusted_hash"].startswith("sha256:")
        assert entry["trusted_content_hash"].startswith("sha256:")

    hooks_after_trust = _query_appserver_hooks_list(codex_exe, codex_home, probe_cwd)
    assert {h["eventName"] for h in hooks_after_trust} == {
        "sessionStart",
        "postCompact",
        "userPromptSubmit",
        "stop",
    }
    assert all(h["trustStatus"] == "trusted" for h in hooks_after_trust)

    # Phase 3a: Verify uninstall preserves non-tri-orchestrator [hooks.state] entries when present
    codex_home_mixed = tmp_path / "codex_home_mixed"
    codex_home_mixed.mkdir(parents=True)
    other_key = "other-plugin@other-mp:hooks/hooks.json:stop:0:0"
    mixed_cfg_path = codex_home_mixed / "config.toml"
    mixed_cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8")
        + f'"{other_key}" = {{ enabled = true, trusted_hash = "sha256:1111", trusted_content_hash = "sha256:2222" }}\n',
        encoding="utf-8",
    )
    uninstall_mixed = _run_ps1(UNINSTALL_PS1, "-CodexHome", str(codex_home_mixed))
    assert uninstall_mixed.returncode == 0
    parsed_mixed = tomllib.loads(mixed_cfg_path.read_text(encoding="utf-8"))
    assert list(parsed_mixed["hooks"]["state"].keys()) == [other_key]

    # Phase 3b: Full uninstall on `codex_home` removes plugin cache, marketplace, role TOMLs, and empty [hooks.state] header
    uninstall_res = _run_ps1(UNINSTALL_PS1, "-CodexHome", str(codex_home))
    assert uninstall_res.returncode == 0, (
        f"uninstall.ps1 failed:\nSTDOUT:\n{uninstall_res.stdout}\nSTDERR:\n{uninstall_res.stderr}"
    )
    assert not (codex_home / "plugins" / "cache" / "tri-orchestrator").exists()
    for role_name in (
        "interviewer.toml",
        "planner.toml",
        "investigator.toml",
        "coder.toml",
        "verifier.toml",
        "swarm_worker.toml",
    ):
        assert not (codex_home / "agents" / role_name).exists()

    raw_after_uninstall = cfg_path.read_text(encoding="utf-8")
    assert "tri-orchestrator@tri-orchestrator:" not in raw_after_uninstall
    assert "[hooks.state]" not in raw_after_uninstall
    parsed_after_uninstall = tomllib.loads(raw_after_uninstall)
    assert "hooks" not in parsed_after_uninstall

    mp_list = subprocess.run(
        [str(codex_exe), "plugin", "marketplace", "list", "--json"],
        cwd=str(probe_cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert mp_list.returncode == 0
    mp_names = [m.get("name") for m in json.loads(mp_list.stdout).get("marketplaces", [])]
    assert "tri-orchestrator" not in mp_names

    hooks_after_uninstall = _query_appserver_hooks_list(codex_exe, codex_home, probe_cwd)
    assert hooks_after_uninstall == []



def test_hooks_json_windows_apps_filter_and_pythonutf8() -> None:
    """hooks.json commandWindows filters Microsoft Store stub python.exe and sets PYTHONUTF8."""
    hooks_path = PLUGIN_DIR / "hooks" / "hooks.json"
    hooks_cfg = json.loads(hooks_path.read_text(encoding="utf-8"))

    for event_name, groups in hooks_cfg["hooks"].items():
        for group in groups:
            for handler in group["hooks"]:
                cmd_win = handler["commandWindows"]
                # Must filter WindowsApps stub
                assert "WindowsApps" in cmd_win, (
                    f"{event_name}: commandWindows must filter WindowsApps python.exe stub"
                )
                assert "-notlike" in cmd_win, (
                    f"{event_name}: commandWindows must use -notlike filter for WindowsApps"
                )
                # Must set PYTHONUTF8 on the python.exe fallback branch
                assert "PYTHONUTF8" in cmd_win, (
                    f"{event_name}: commandWindows must set $env:PYTHONUTF8 = '1' on python.exe fallback"
                )


def test_install_sh_lf_line_endings_and_features_section() -> None:
    """install.sh must have pure LF line endings and merge [features] section."""
    raw = INSTALL_SH.read_bytes()
    assert b"\r" not in raw, "scripts/install.sh must preserve LF line endings"

    text = raw.decode("utf-8")
    # Must merge [features] section with multi_agent_v2
    assert "multi_agent_v2" in text, "install.sh must set multi_agent_v2 = true"
    assert "multi_agent = true" in text, "install.sh must set multi_agent = true"
    # Must have --trust-plugin-hooks flag
    assert "--trust-plugin-hooks" in text, "install.sh must support --trust-plugin-hooks flag"
    # Must have hooks/list self-check
    assert "hooks/list" in text, "install.sh must have hooks/list self-check"


@pytest.mark.parametrize("trust", [False, True])
def test_install_sh_registers_plugin_and_verifies_hook_trust(tmp_path: Path, trust: bool) -> None:
    codex = _resolve_codex_exe()
    bash = _resolve_bash()
    if not bash or not Path(bash).is_file() or codex is None or not shutil.which("uv"):
        pytest.skip("Requires Bash, Codex CLI and uv")

    repo = tmp_path / "source"
    marketplace = Path(".agents/plugins/marketplace.json")
    (repo / marketplace.parent).mkdir(parents=True)
    shutil.copyfile(REPO_ROOT / marketplace, repo / marketplace)
    shutil.copytree(PLUGIN_DIR, repo / "plugin", ignore=shutil.ignore_patterns(".venv", "__pycache__"))
    codex_home = tmp_path / "codex"
    config_path = codex_home / "config.toml"
    original_config = b'model = "gpt-5.2"\n'
    if trust:
        codex_home.mkdir()
        config_path.write_bytes(original_config)
    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    data_dir = tmp_path / "data"
    env = dict(
        os.environ, CODEX_CLI_PATH=codex.as_posix(), CODEX_HOME=str(codex_home),
        HOME=str(isolated_home), USERPROFILE=str(isolated_home), LOCALAPPDATA=str(isolated_home),
        TRI_ORCH_DATA_DIR=str(data_dir), TRI_ORCH_PORT=str(_free_loopback_port()),
    )
    cmd = [str(bash), INSTALL_SH.as_posix(), "--repo-root", repo.as_posix(), "--skip-uv-sync"]
    if trust:
        cmd.append("--trust-plugin-hooks")
    result = subprocess.run(cmd, env=env, cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=90)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Verified plugin: tri-orchestrator@tri-orchestrator" in result.stdout
    assert "Verified MCP   : tri_orchestrator" in result.stdout
    installed_config = config_path.read_bytes()
    config = tomllib.loads(installed_config.decode("utf-8"))
    assert len(config.get("hooks", {}).get("state", {})) == (4 if trust else 0)
    if trust:
        assert config["model"] == "gpt-5.2"
        baseline = codex_home / "backups" / "tri-orchestrator-baseline" / "config.toml"
        assert baseline.read_bytes() == original_config
        reinstall = subprocess.run(
            [*cmd, "--skip-plugin-install"],
            env=env, cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=90,
        )
        assert reinstall.returncode == 0, reinstall.stdout + reinstall.stderr
        assert config_path.read_bytes() == installed_config
        assert baseline.read_bytes() == original_config
    assert not data_dir.exists(), "Registry and hooks/list checks must not start the daemon"


@pytest.mark.parametrize("layout", ["agents-symlink", "role-symlink", "role-directory", "role-hardlink"])
def test_install_sh_does_not_write_through_role_links(tmp_path: Path, layout: str) -> None:
    bash, codex = _resolve_bash(), _resolve_codex_exe()
    if not bash or not Path(bash).is_file() or codex is None or not shutil.which("uv"):
        pytest.skip("Requires Bash, Codex CLI and uv")

    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    agents = codex_home / "agents"
    outside = tmp_path / "custom-roles"
    outside.mkdir()
    original = outside / "coder.toml"
    original.write_bytes(b'name = "user-coder"\n')
    role = agents / "coder.toml"
    try:
        if layout == "agents-symlink":
            agents.symlink_to(outside, target_is_directory=True)
        else:
            agents.mkdir()
            if layout == "role-symlink":
                role.symlink_to(original)
            elif layout == "role-directory":
                role.mkdir()
            else:
                os.link(original, role)
    except OSError as exc:
        pytest.skip(f"Cannot create {layout}: {exc}")

    result = subprocess.run(
        [str(bash), INSTALL_SH.as_posix(), "--skip-plugin-install", "--skip-uv-sync"],
        env=dict(os.environ, CODEX_CLI_PATH=codex.as_posix(), CODEX_HOME=str(codex_home)),
        cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=45,
    )
    assert original.read_bytes() == b'name = "user-coder"\n'
    if layout == "role-hardlink":
        assert result.returncode == 0, result.stdout + result.stderr
        assert role.read_bytes() == (PLUGIN_DIR / "agents" / role.name).read_bytes()
    else:
        assert result.returncode != 0, result.stdout + result.stderr
        assert not (codex_home / "config.toml").exists()
        assert not (codex_home / "backups").exists()


@pytest.mark.parametrize("layout", ["directory", "symlink", "dangling-link", "junction", "fifo"])
def test_install_sh_rejects_nonregular_config_before_writing(tmp_path: Path, layout: str) -> None:
    bash = _resolve_bash()
    if not bash or not Path(bash).is_file() or not shutil.which("uv"):
        pytest.skip("Requires Bash and uv")

    codex = tmp_path / "codex-stub"
    codex.write_bytes(b"#!/usr/bin/env bash\nexit 0\n")
    codex.chmod(0o755)
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    config = codex_home / "config.toml"
    target = tmp_path / "original"
    if layout == "directory":
        config.mkdir()
    elif layout in {"symlink", "dangling-link"}:
        if layout == "symlink":
            target.write_bytes(b'model = "user-model"\n')
        try:
            config.symlink_to(target)
        except OSError as exc:
            pytest.skip(f"Cannot create symlink: {exc}")
    elif layout == "junction":
        if os.name != "nt":
            pytest.skip("Junctions require Windows")
        target.mkdir()
        subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(config), str(target)], check=True, capture_output=True)
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("Named pipes require POSIX")
        os.mkfifo(config)

    initial_config = config.lstat()
    result = subprocess.run(
        [str(bash), INSTALL_SH.as_posix(), "--skip-plugin-install", "--skip-uv-sync"],
        env=dict(os.environ, CODEX_CLI_PATH=codex.as_posix(), CODEX_HOME=codex_home.as_posix()),
        cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=45,
    )

    assert result.returncode != 0, result.stdout + result.stderr
    assert "config.toml" in result.stderr and "regular" in result.stderr, result.stderr
    assert "Completed successfully" not in result.stdout
    assert list(codex_home.iterdir()) == [config]
    assert config.lstat() == initial_config
    if layout == "directory":
        assert not list(config.iterdir())
    elif layout == "symlink":
        assert target.read_bytes() == b'model = "user-model"\n'
    elif layout == "dangling-link":
        assert not target.exists()
    elif layout == "junction":
        assert not list(target.iterdir())


@pytest.mark.parametrize("installation", [
    "cli_only", "script_no_rotating_backups", "legacy_script", "symlink_replaced", "symlink_preexisting", "hardlinks",
])
def test_uninstaller_preserves_unowned_or_edited_role_files(tmp_path: Path, installation: str) -> None:
    pwsh = _resolve_powershell()
    codex = _resolve_codex_exe()
    if not pwsh or codex is None or not shutil.which("uv"):
        pytest.skip("Requires Windows PowerShell, Codex CLI and uv")
    codex_home = tmp_path / "codex"
    agents = codex_home / "agents"
    agents.mkdir(parents=True)
    coder = agents / "coder.toml"
    coder.write_text('name = "user-coder"\n', encoding="utf-8")
    investigator = agents / "investigator.toml"
    investigator.write_text('name = "original-investigator"\n', encoding="utf-8")
    config = codex_home / "config.toml"
    if installation == "legacy_script":
        config.write_bytes(b"[features]\nmulti_agent = false\n")
    data_dir = tmp_path / "data"
    env = dict(
        os.environ, CODEX_HOME=str(codex_home), TRI_ORCH_DATA_DIR=str(data_dir),
        TRI_ORCH_PORT=str(_free_loopback_port()), CODEX_CLI_PATH=str(codex),
    )

    def run(script: Path, *args: str, succeeds: bool = True) -> str:
        result = subprocess.run(
            [pwsh, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script),
             "-CodexHome", str(codex_home), *args],
            env=env, cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=45,
        )
        output = result.stdout + result.stderr
        assert (result.returncode == 0) == succeeds, output
        return output

    if installation == "symlink_preexisting":
        outside = tmp_path / "user-role.toml"
        expected = coder.read_bytes()
        outside.write_bytes(expected)
        coder.unlink()
        try:
            coder.symlink_to(outside)
        except OSError as exc:
            pytest.skip(f"Cannot create the test symlink: {exc}")
        output = run(INSTALL_PS1, "-SkipPluginInstall", "-SkipUvSync", succeeds=False)
        assert "reparse point" in output
        assert coder.is_symlink() and outside.read_bytes() == expected
        assert not (codex_home / "config.toml").exists()
        return

    if installation == "hardlinks":
        original_coder = coder.read_bytes()
        linked_original = tmp_path / "original-user-role.toml"
        coder.rename(linked_original)
        try:
            os.link(linked_original, coder)
        except OSError as exc:
            pytest.skip(f"Cannot create the test hard link: {exc}")

    if installation != "cli_only":
        keep = "3" if installation == "legacy_script" else "0"
        run(INSTALL_PS1, "-SkipPluginInstall", "-SkipUvSync", "-KeepBackups", keep)
        if installation == "legacy_script":
            (codex_home / "backups" / "tri-orchestrator-baseline" / "installed-roles.json").unlink()
        assert (agents / "planner.toml").is_file()
        if installation != "hardlinks":
            coder.write_text('name = "user-edited-coder"\n', encoding="utf-8")
    if installation == "symlink_replaced":
        outside = tmp_path / "user-role.toml"
        outside.write_bytes((PLUGIN_DIR / "agents" / "coder.toml").read_bytes())
        coder.unlink()
        try:
            coder.symlink_to(outside)
        except OSError as exc:
            pytest.skip(f"Cannot create the test symlink: {exc}")
    if installation == "hardlinks":
        assert linked_original.read_bytes() == original_coder
        linked_stock = tmp_path / "other-stock-role.toml"
        stock_role = coder.read_bytes()
        linked_stock.write_bytes(stock_role)
        coder.unlink()
        os.link(linked_stock, coder)
        expected_coder = original_coder
    else:
        expected_coder = coder.read_bytes()
    expected_investigator = investigator.read_text(encoding="utf-8") if installation == "legacy_script" else 'name = "original-investigator"\n'

    if installation == "legacy_script":
        run(UNINSTALL_PS1, "-RestoreConfigBackup")
        assert config.read_bytes() == b"[features]\nmulti_agent = false\n"
        original = codex_home / "backups" / "tri-orchestrator-baseline" / "agents" / "investigator.toml"
        assert original.read_text(encoding="utf-8") == 'name = "original-investigator"\n'
    else:
        run(UNINSTALL_PS1)
    run(UNINSTALL_PS1)
    assert coder.read_bytes() == expected_coder
    assert investigator.read_text(encoding="utf-8") == expected_investigator
    assert (agents / "planner.toml").exists() == (installation == "legacy_script")
    assert not data_dir.exists(), "Installer and uninstaller tests must leave the daemon stopped"
    if installation == "symlink_replaced":
        assert coder.is_symlink() and outside.read_bytes() == expected_coder
    if installation == "hardlinks":
        assert linked_stock.read_bytes() == stock_role
    if installation == "script_no_rotating_backups":
        run(INSTALL_PS1, "-SkipPluginInstall", "-SkipUvSync", "-KeepBackups", "0")
        run(UNINSTALL_PS1)
        assert coder.read_bytes() == expected_coder


@pytest.mark.skipif(os.name != "nt", reason="PowerShell features section test on Windows")
def test_install_ps1_features_section_idempotent(tmp_path: Path) -> None:
    """install.ps1 idempotently sets [features] multi_agent = true and multi_agent_v2 = true."""
    pwsh = _resolve_powershell()
    if pwsh is None:
        pytest.skip("Requires Windows PowerShell")

    # Create a fake minimal plugin layout
    fake_plugin = tmp_path / "plugin"
    (fake_plugin / "agents").mkdir(parents=True)
    (fake_plugin / ".codex-plugin").mkdir(parents=True)
    (fake_plugin / "hooks").mkdir(parents=True)
    shutil.copy2(PLUGIN_DIR / "pyproject.toml", fake_plugin / "pyproject.toml")
    shutil.copy2(PLUGIN_DIR / ".codex-plugin" / "plugin.json", fake_plugin / ".codex-plugin" / "plugin.json")
    shutil.copy2(PLUGIN_DIR / "hooks" / "hooks.json", fake_plugin / "hooks" / "hooks.json")
    for rf in ("interviewer.toml", "planner.toml", "investigator.toml", "coder.toml", "verifier.toml", "swarm_worker.toml"):
        src = PLUGIN_DIR / "agents" / rf
        if src.is_file():
            shutil.copy2(src, fake_plugin / "agents" / rf)

    codex_home = tmp_path / "codex_home"
    # Pre-seed a config.toml with multi_agent=false (no space) + comment and multi_agent_v2 = false
    codex_home.mkdir(parents=True)
    (codex_home / "config.toml").write_text(
        "[features]\nmulti_agent=false # previously = true\nmulti_agent_v2 = false\n",
        encoding="utf-8",
    )

    env = dict(
        os.environ,
        CODEX_HOME=str(codex_home),
        TRI_ORCH_DATA_DIR=str(tmp_path / "data"),
        TRI_ORCH_PORT=str(_free_loopback_port()),
    )
    cmd = [
        pwsh,
        "-NoLogo", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass",
        "-File", str(INSTALL_PS1),
        "-PluginDir", str(fake_plugin),
        "-CodexHome", str(codex_home),
        "-SkipPluginInstall", "-SkipUvSync",
    ]
    res = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=30,
    )
    assert res.returncode == 0, f"install.ps1 failed:\n{res.stdout}\n{res.stderr}"

    # Re-run idempotently and confirm config.toml remains valid without duplicate keys
    res2 = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=30,
    )
    assert res2.returncode == 0, f"idempotent install.ps1 failed:\n{res2.stdout}\n{res2.stderr}"

    cfg = tomllib.loads((codex_home / "config.toml").read_text(encoding="utf-8"))
    features = cfg.get("features", {})
    assert features.get("multi_agent") is True
    assert features.get("multi_agent_v2") is True

    # Live codex.exe verification in isolated CODEX_HOME: V2 `collaboration` tools & custom roles exposed!
    codex_exe = _resolve_codex_exe()
    if codex_exe is not None:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import gzip

        feat_res = subprocess.run(
            [str(codex_exe), "features", "list"],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert feat_res.returncode == 0
        feat_lines = {
            parts[0]: parts[-1]
            for ln in feat_res.stdout.splitlines()
            if (parts := ln.split()) and len(parts) >= 2
        }
        assert feat_lines.get("multi_agent") == "true"
        assert feat_lines.get("multi_agent_v2") == "true"

        captured_tools: list[dict[str, Any]] = []
        got_tools = threading.Event()

        class _LoopbackResponsesCapture(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:
                body = b"not found"
                self.send_response(404)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                te = self.headers.get("Transfer-Encoding", "").lower()
                ce = self.headers.get("Content-Encoding", "").lower()
                if "chunked" in te:
                    chunks: list[bytes] = []
                    while True:
                        hdr = self.rfile.readline().strip()
                        sz = int(hdr.split(b";")[0], 16)
                        if sz == 0:
                            self.rfile.readline()
                            break
                        chunks.append(self.rfile.read(sz))
                        self.rfile.read(2)
                    raw = b"".join(chunks)
                else:
                    raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if ce == "zstd":
                    import compression.zstd as zstd  # type: ignore[import-not-found]
                    raw = zstd.decompress(raw)
                elif ce == "gzip":
                    raw = gzip.decompress(raw)
                req_json = json.loads(raw.decode("utf-8"))
                if isinstance(req_json.get("tools"), list) and req_json["tools"]:
                    captured_tools.extend(req_json["tools"])
                    got_tools.set()
                resp_body = b'{"error":{"message":"probe_done","type":"invalid_request_error"}}'
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp_body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(resp_body)

            def log_message(self, *_args: Any) -> None:
                return

        with ThreadingHTTPServer(("127.0.0.1", 0), _LoopbackResponsesCapture) as srv:
            th = threading.Thread(target=srv.serve_forever, daemon=True)
            th.start()
            probe_env = dict(env, NO_PROXY="127.0.0.1,localhost")
            proc = subprocess.Popen(
                [
                    str(codex_exe),
                    "exec",
                    "--skip-git-repo-check",
                    "-m",
                    "gpt-5.4",
                    "--dangerously-bypass-approvals-and-sandbox",
                    "-c",
                    'model_provider="mock"',
                    "-c",
                    'model_providers.mock.name="mock"',
                    "-c",
                    f'model_providers.mock.base_url="http://127.0.0.1:{srv.server_port}/v1"',
                    "-c",
                    'model_providers.mock.wire_api="responses"',
                    "ping",
                ],
                cwd=str(tmp_path),
                env=probe_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            try:
                assert got_tools.wait(timeout=10.0), "Timed out waiting for codex exec tool definitions"
            finally:
                proc.terminate()
                try:
                    proc.communicate(timeout=3.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()
                srv.shutdown()
                th.join(timeout=2.0)

        collab_tool = next((t for t in captured_tools if t.get("name") == "collaboration"), None)
        assert collab_tool is not None, f"Expected V2 collaboration namespace tool, got {[t.get('name') for t in captured_tools]}"
        subtool_names = {st.get("name") for st in collab_tool.get("tools", []) if isinstance(st, dict)}
        for expected_v2 in ("spawn_agent", "followup_task", "list_agents", "send_message", "interrupt_agent", "wait_agent"):
            assert expected_v2 in subtool_names, f"Missing V2 collaboration subtool {expected_v2}: {subtool_names}"
        spawn_def = next(st for st in collab_tool["tools"] if st.get("name") == "spawn_agent")
        spawn_props = spawn_def.get("parameters", {}).get("properties", {})
        assert "task_name" in spawn_props and "fork_turns" in spawn_props and "agent_type" in spawn_props


@pytest.mark.skipif(os.name != "nt", reason="Windows daemon identity and process handles")
def test_uninstall_checks_live_daemon_identity_before_stopping_or_removing_data(tmp_path):
    from orchestrator.config import Settings
    from orchestrator.mcp_shim import ensure_daemon_running
    from orchestrator.windows_daemon import WindowsDaemonProcess

    ps = _resolve_powershell()
    assert ps is not None
    cfg = Settings(data_dir=tmp_path / "中文 data", port=_free_loopback_port())
    codex_home = tmp_path / "codex-home"
    env = dict(os.environ, TRI_ORCH_DATA_DIR=str(cfg.data_dir), CODEX_HOME=str(codex_home),
               LOCALAPPDATA=str(tmp_path), CODEX_CLI_PATH="", PATH=os.environ["SystemRoot"] + r"\System32")
    args = [ps, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(UNINSTALL_PS1), "-CodexHome", str(codex_home), "-RemoveDataDir"]

    live = ensure_daemon_running(cfg)
    assert live["spawned"]
    daemon = WindowsDaemonProcess(live["pid"], [])
    try:
        original = cfg.pid_path.read_bytes()
        info = json.loads(original)
        with subprocess.Popen([sys._base_executable, "-c", "import time; time.sleep(120)"],
                              creationflags=subprocess.CREATE_NO_WINDOW) as other:
            try:
                for wrong in ({"pid": other.pid}, {"pid": 2**31 - 1}, {"started_at": info["started_at"] - 1},
                              {"data_dir": str(tmp_path / "wrong")}):
                    evidence = json.dumps(info | wrong).encode()
                    cfg.pid_path.write_bytes(evidence)
                    refused = subprocess.run(args, env=env, capture_output=True, timeout=30)
                    assert refused.returncode != 0 and b"preserving" in refused.stderr, refused
                    assert cfg.pid_path.read_bytes() == evidence
                    assert other.poll() is None and daemon.poll() is None
                cfg.pid_path.write_bytes(original)
                stopped = subprocess.run(args, env=env, capture_output=True, timeout=30)
                assert stopped.returncode == 0, stopped.stderr
                assert daemon.poll() is not None and other.poll() is None
                assert not cfg.data_dir.exists()
            finally:
                if other.poll() is None:
                    other.terminate()
    finally:
        if daemon.poll() is None:
            daemon.terminate()
            daemon.wait(timeout=5)
