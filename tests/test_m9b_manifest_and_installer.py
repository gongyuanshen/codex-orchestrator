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
                    "clientInfo": {"name": "test-m9b-hooks-list", "version": "0.6.0"},
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
    assert manifest["version"] == "0.6.0"
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
        "version": "0.6.0",
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
    (fake_plugin / "plugin.json").write_text('{"name": "tri-orchestrator", "version": "0.6.0"}\n', encoding="utf-8")

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
        / "0.6.0"
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
