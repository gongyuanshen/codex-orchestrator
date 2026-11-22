<#
.SYNOPSIS
    End-to-end verification script for tri-orchestrator:
    1. Manifest portability (zero hardcoded local paths), TOML, and SVG checks
    2. Full uv run pytest -v suite at repo root
    3. Detached cache execution: stdio MCP initialize + tools/list from $env:TEMP
    4. Live Daemon /api/health, Web dashboard HTTP 200, and SSE /api/events
    5. Codex CLI plugin & MCP registration checks
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$CodexHome = "",
    [string]$DaemonHost = "",
    [int]$DaemonPort = 0,
    [switch]$SkipPytest,
    [switch]$SkipPluginCheck
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = $Utf8NoBom
[Console]::OutputEncoding = $Utf8NoBom

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
} else {
    $RepoRoot = (Resolve-Path $RepoRoot).Path
}

if ([string]::IsNullOrWhiteSpace($CodexHome)) {
    if (-not [string]::IsNullOrWhiteSpace($env:CODEX_HOME)) {
        $CodexHome = $env:CODEX_HOME
    } else {
        $CodexHome = Join-Path $env:USERPROFILE ".codex"
    }
}

if ([string]::IsNullOrWhiteSpace($DaemonHost)) {
    $DaemonHost = if (-not [string]::IsNullOrWhiteSpace($env:TRI_ORCH_HOST)) { $env:TRI_ORCH_HOST } else { "127.0.0.1" }
}
if ($DaemonPort -le 0) {
    $DaemonPort = if (-not [string]::IsNullOrWhiteSpace($env:TRI_ORCH_PORT)) { [int]$env:TRI_ORCH_PORT } else { 18790 }
}

$ExpectedTools = @(
    "run_create",
    "run_update",
    "run_list",
    "task_packet_save",
    "task_packet_get",
    "board_post",
    "board_read",
    "board_status",
    "board_channel_list",
    "worker_control_ack",
    "worker_register",
    "worker_heartbeat",
    "worker_status_update",
    "workspace_create",
    "workspace_diff",
    "workspace_status",
    "workspace_merge_preview",
    "workspace_merge",
    "workspace_cleanup",
    "native_action_update",
    "inbox_send",
    "inbox_get",
    "inbox_ack",
    "inbox_wait",
    "workflow_step_save",
    "workflow_step_advance",
    "artifact_capture",
    "verification_record",
    "workflow_review",
    "workflow_prepare_dispatch",
    "native_action_record_call",
    "workflow_author_turn",
    "workflow_verifier_turn",
    "workflow_closeout_run",
    "session_bind"
)

function Format-Win32Arg {
    param([string]$Arg)
    if ($Arg -eq "") { return '""' }
    if ($Arg -notmatch '[\s"]') { return $Arg }
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.Append('"')
    $backslashes = 0
    foreach ($ch in $Arg.ToCharArray()) {
        if ($ch -eq '\') {
            $backslashes++
        } elseif ($ch -eq '"') {
            [void]$sb.Append(('\' * ($backslashes * 2 + 1)) + '"')
            $backslashes = 0
        } else {
            if ($backslashes -gt 0) {
                [void]$sb.Append(('\' * $backslashes))
                $backslashes = 0
            }
            [void]$sb.Append($ch)
        }
    }
    if ($backslashes -gt 0) {
        [void]$sb.Append(('\' * ($backslashes * 2)))
    }
    [void]$sb.Append('"')
    return $sb.ToString()
}

function Invoke-Native {
    param(
        [string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = "",
        [hashtable]$EnvOverrides = @{},
        [string[]]$EnvRemove = @()
    )
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FilePath
    $psi.Arguments = (($Arguments | ForEach-Object { Format-Win32Arg $_ }) -join " ")
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = $Utf8NoBom
    $psi.StandardErrorEncoding = $Utf8NoBom
    if (-not [string]::IsNullOrWhiteSpace($WorkingDirectory)) {
        $psi.WorkingDirectory = $WorkingDirectory
    }
    foreach ($r in $EnvRemove) {
        if ($psi.EnvironmentVariables.ContainsKey($r)) {
            [void]$psi.EnvironmentVariables.Remove($r)
        }
    }
    foreach ($k in $EnvOverrides.Keys) {
        $psi.EnvironmentVariables[$k] = [string]$EnvOverrides[$k]
    }
    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    [void]$proc.Start()
    $stdoutTask = $proc.StandardOutput.ReadToEndAsync()
    $stderrTask = $proc.StandardError.ReadToEndAsync()
    $proc.WaitForExit()
    return [PSCustomObject]@{
        ExitCode = $proc.ExitCode
        StdOut   = $stdoutTask.Result
        StdErr   = $stderrTask.Result
    }
}

function Resolve-UvExe {
    $candidates = @()
    $cmd = Get-Command "uv.exe" -ErrorAction SilentlyContinue
    if ($cmd) { $candidates += $cmd.Source }
    $cmd2 = Get-Command "uv" -ErrorAction SilentlyContinue
    if ($cmd2 -and $cmd2.Source -like "*.exe") { $candidates += $cmd2.Source }
    $candidates += (Join-Path $env:USERPROFILE ".local\bin\uv.exe")
    $candidates += (Join-Path $env:LOCALAPPDATA "uv\uv.exe")
    if (-not [string]::IsNullOrWhiteSpace($env:CARGO_HOME)) {
        $candidates += (Join-Path $env:CARGO_HOME "bin\uv.exe")
    }
    foreach ($c in ($candidates | Select-Object -Unique)) {
        if ($c -and (Test-Path $c -PathType Leaf)) {
            return $c
        }
    }
    throw "Could not locate uv.exe"
}

function Resolve-PythonRunner {
    param([string]$UvExe)
    $findRes = Invoke-Native -FilePath $UvExe -Arguments @("python", "find", ">=3.11")
    if ($findRes.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($findRes.StdOut)) {
        $pyPath = ($findRes.StdOut -split "\r?\n" | Select-Object -First 1).Trim()
        if ($pyPath -and (Test-Path $pyPath -PathType Leaf)) {
            return [PSCustomObject]@{
                FilePath   = $pyPath
                PrefixArgs = @()
            }
        }
    }
    return [PSCustomObject]@{
        FilePath   = $UvExe
        PrefixArgs = @("run", "--quiet", "--no-project", "--python", ">=3.11", "python")
    }
}

function Invoke-PythonInline {
    param(
        [PSCustomObject]$PyRunner,
        [string]$Code,
        [string[]]$ScriptArgs = @(),
        [string]$WorkingDirectory = "",
        [hashtable]$EnvOverrides = @{},
        [string[]]$EnvRemove = @("VIRTUAL_ENV", "PYTHONPATH")
    )
    $allArgs = @()
    $allArgs += $PyRunner.PrefixArgs
    $allArgs += @("-c", $Code)
    $allArgs += $ScriptArgs
    $wd = if ($WorkingDirectory) { $WorkingDirectory } else { $env:TEMP }
    $mergedEnv = @{
        PYTHONUTF8       = "1"
        PYTHONIOENCODING = "utf-8"
    }
    foreach ($k in $EnvOverrides.Keys) { $mergedEnv[$k] = $EnvOverrides[$k] }
    return Invoke-Native -FilePath $PyRunner.FilePath -Arguments $allArgs -WorkingDirectory $wd -EnvOverrides $mergedEnv -EnvRemove $EnvRemove
}

function Resolve-CodexExe {
    param([string]$ActiveCodexHome)
    $candidates = @()
    if (-not [string]::IsNullOrWhiteSpace($env:CODEX_CLI_PATH) -and (Test-Path $env:CODEX_CLI_PATH -PathType Leaf)) {
        $candidates += $env:CODEX_CLI_PATH
    }
    $binRoot = Join-Path $env:LOCALAPPDATA "OpenAI\Codex\bin"
    if (Test-Path $binRoot) {
        $found = Get-ChildItem -Path $binRoot -Filter "codex.exe" -Recurse -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending
        foreach ($f in $found) { $candidates += $f.FullName }
    }
    $cmdExe = Get-Command "codex.exe" -ErrorAction SilentlyContinue
    if ($cmdExe) { $candidates += $cmdExe.Source }
    $cmdAny = Get-Command "codex" -ErrorAction SilentlyContinue
    if ($cmdAny) { $candidates += $cmdAny.Source }
    $appServerCodex = Join-Path $ActiveCodexHome "plugins\.plugin-appserver\codex.exe"
    if (Test-Path $appServerCodex -PathType Leaf) { $candidates += $appServerCodex }

    foreach ($c in ($candidates | Select-Object -Unique)) {
        if ($c -and (Test-Path $c -PathType Leaf)) {
            try {
                $res = Invoke-Native -FilePath $c -Arguments @("--version")
                if ($res.ExitCode -eq 0 -and $res.StdOut -match "codex-cli") {
                    return $c
                }
            } catch {
                continue
            }
        }
    }
    throw "Could not locate a working codex.exe binary."
}

$uvExe = Resolve-UvExe
$pyRunner = Resolve-PythonRunner -UvExe $uvExe

# 1. Static Manifest, Portability, TOML, and SVG Validation
Write-Host "[verify] 1/5 Validating portable manifests, self-contained layout, SVG assets, and plugin/agents/*.toml..."
$staticCheckPy = @'
import json, pathlib, re, sys, tomllib, xml.etree.ElementTree as ET

root = pathlib.Path(sys.argv[1])
assert not (root / "src").exists(), "Legacy root src/ must be removed"
assert not (root / "agents").exists(), "Legacy root agents/ must be removed"

for req_file in [
    root / "plugin" / "pyproject.toml",
    root / "plugin" / "uv.lock",
    root / "plugin" / "src" / "orchestrator" / "__init__.py",
    root / "plugin" / "src" / "orchestrator" / "mcp_shim.py",
]:
    assert req_file.is_file(), f"Missing self-contained plugin file: {req_file}"

json_files = sorted(
    list((root / ".agents").rglob("*.json")) +
    [p for p in (root / "plugin").rglob("*.json") if ".venv" not in p.parts]
)
assert not (root / "plugin" / "plugin.json").exists(), "Legacy root plugin/plugin.json must be removed"
assert not (root / "plugin" / "mcp.json").exists(), "Legacy plugin/mcp.json must be removed; use plugin/.mcp.json"
expected_core_json = {
    root / ".agents" / "plugins" / "marketplace.json",
    root / "plugin" / ".codex-plugin" / "plugin.json",
    root / "plugin" / "hooks" / "hooks.json",
    root / "plugin" / ".mcp.json",
}
for jf in expected_core_json:
    assert jf.is_file(), f"Missing JSON file: {jf}"

bad_path_re = re.compile(r'(?i)(?:(?<![A-Za-z])[A-Z]:(?:\\|/(?!/))|/Users/|/home/)')
for jf in json_files:
    raw = jf.read_text(encoding="utf-8")
    json.loads(raw)
    m = bad_path_re.search(raw)
    assert not m, f"Hardcoded local path {m.group(0)!r} found in {jf}"

mcp_cfg = json.loads((root / "plugin" / ".mcp.json").read_text(encoding="utf-8"))
srv = mcp_cfg["mcpServers"]["tri_orchestrator"]
assert srv.get("command") == "uv", f"plugin/.mcp.json must use command='uv', got {srv.get('command')!r}"
args_str = " ".join(srv.get("args", []))
assert "--offline" not in args_str and "--no-sync" not in args_str, f"plugin/.mcp.json must not contain --offline or --no-sync: {args_str}"
assert srv.get("cwd") == ".", f"plugin/.mcp.json cwd must be '.', got {srv.get('cwd')!r}"

p2 = json.loads((root / "plugin" / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
assert p2["name"] == "tri-orchestrator"
assert p2["version"] == "0.6.8"
assert p2.get("hooks") == "./hooks/hooks.json"
assert p2.get("mcpServers") == "./.mcp.json"
hooks_cfg = json.loads((root / "plugin" / "hooks" / "hooks.json").read_text(encoding="utf-8"))
assert set(hooks_cfg.get("hooks", {}).keys()) == {"SessionStart", "PostCompact", "UserPromptSubmit", "Stop"}
short_desc = p2["interface"]["shortDescription"]
assert len(short_desc) <= 30, f"shortDescription too long ({len(short_desc)}): {short_desc}"

for svg in [root / "plugin" / "assets" / "logo.svg", root / "plugin" / "assets" / "icon.svg"]:
    assert svg.is_file(), f"Missing SVG: {svg}"
    ET.fromstring(svg.read_text(encoding="utf-8"))

expected_roles = {"interviewer", "planner", "investigator", "coder", "verifier", "swarm_worker"}
actual_roles = set()
for tf in sorted((root / "plugin" / "agents").glob("*.toml")):
    data = tomllib.loads(tf.read_text(encoding="utf-8"))
    assert "model" not in data, f"Hardcoded model forbidden in {tf.name}"
    assert "model_reasoning_effort" not in data, f"Hardcoded model_reasoning_effort forbidden in {tf.name}"
    assert data.get("name") and data.get("description") and data.get("developer_instructions"), f"Missing required fields in {tf.name}"
    actual_roles.add(tf.stem)
assert actual_roles == expected_roles, f"Role mismatch: {actual_roles} != {expected_roles}"
print(f"Portable manifest/TOML/SVG checks OK ({len(json_files)} JSON, {len(actual_roles)} TOML, 2 SVG, 0 local paths)")
'@
$staticRes = Invoke-PythonInline -PyRunner $pyRunner -Code $staticCheckPy -ScriptArgs @($RepoRoot)
if ($staticRes.ExitCode -ne 0) {
    throw "[FAIL] Static validation failed:`n$($staticRes.StdOut)`n$($staticRes.StdErr)"
}
Write-Host "[PASS] $($staticRes.StdOut.Trim())"

# 2. Full pytest run at repo root
if (-not $SkipPytest) {
    Write-Host "[verify] 2/5 Running uv run pytest -v at repo root..."
    $pytestRes = Invoke-Native -FilePath $uvExe -Arguments @("run", "pytest", "-v") -WorkingDirectory $RepoRoot -EnvRemove @("VIRTUAL_ENV", "PYTHONPATH")
    if ($pytestRes.ExitCode -ne 0) {
        throw "[FAIL] pytest failed (exit $($pytestRes.ExitCode)):`n$($pytestRes.StdOut)`n$($pytestRes.StdErr)"
    }
    $summaryLine = ($pytestRes.StdOut -split "\r?\n" | Where-Object { $_ -match "passed" } | Select-Object -Last 1)
    Write-Host "[PASS] pytest: $summaryLine"
} else {
    Write-Host "[SKIP] 2/5 pytest skipped via -SkipPytest"
}

# Resolve cached plugin directory & Codex CLI details
$codexExe = $null
$cachedPluginDir = ""
if (-not $SkipPluginCheck) {
    $codexExe = Resolve-CodexExe -ActiveCodexHome $CodexHome
    $codexEnv = @{ CODEX_HOME = $CodexHome }
    $mcpProbeList = Invoke-Native -FilePath $codexExe -Arguments @("mcp", "list", "--json") -EnvOverrides $codexEnv
    if ($mcpProbeList.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($mcpProbeList.StdOut)) {
        $mcpItems = $mcpProbeList.StdOut | ConvertFrom-Json
        $mcpEntry = $mcpItems | Where-Object { $_.name -eq "tri_orchestrator" } | Select-Object -First 1
        if ($mcpEntry -and $mcpEntry.transport) {
            if ($mcpEntry.transport.PSObject.Properties['env'] -and $mcpEntry.transport.env -and $mcpEntry.transport.env.PSObject.Properties['PLUGIN_ROOT'] -and $mcpEntry.transport.env.PLUGIN_ROOT) {
                $cachedPluginDir = [string]$mcpEntry.transport.env.PLUGIN_ROOT
            } elseif ($mcpEntry.transport.PSObject.Properties['cwd'] -and $mcpEntry.transport.cwd) {
                $cachedPluginDir = [string]$mcpEntry.transport.cwd
            }
        }
    }
}
if ([string]::IsNullOrWhiteSpace($cachedPluginDir)) {
    $pluginMeta = [System.IO.File]::ReadAllText((Join-Path $RepoRoot "plugin\.codex-plugin\plugin.json"), $Utf8NoBom) | ConvertFrom-Json
    $pluginVersion = if ($pluginMeta.PSObject.Properties['version'] -and $pluginMeta.version) { [string]$pluginMeta.version } else { "0.1.0" }
    $cachedPluginDir = Join-Path $CodexHome "plugins\cache\tri-orchestrator\tri-orchestrator\$pluginVersion"
}

if (-not (Test-Path $cachedPluginDir -PathType Container)) {
    throw "[FAIL] Cached plugin directory not found at '$cachedPluginDir'. Run scripts\install.ps1 or 'codex plugin add tri-orchestrator@tri-orchestrator' first."
}
$cachedPluginDir = (Resolve-Path $cachedPluginDir).Path
$normRepo = [System.IO.Path]::GetFullPath($RepoRoot).TrimEnd('\')
$normCache = [System.IO.Path]::GetFullPath($cachedPluginDir).TrimEnd('\')
if ($normCache.StartsWith($normRepo, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "[FAIL] Cached plugin directory '$cachedPluginDir' is inside RepoRoot '$RepoRoot'!"
}

# 3. Detached cache execution: stdio MCP initialize + tools/list from $env:TEMP
Write-Host "[verify] 3/5 Testing detached cache execution from $($env:TEMP) against $cachedPluginDir..."
$detachedProbePy = @'
import json, os, pathlib, queue, subprocess, sys, tempfile, threading, time

cached_dir = pathlib.Path(sys.argv[1]).resolve()
uv_exe = sys.argv[2]
expected = set(sys.argv[3].split(","))
repo_root = pathlib.Path(sys.argv[4]).resolve()
temp_cwd = tempfile.gettempdir()

for sub in ["pyproject.toml", "uv.lock", "src/orchestrator/__init__.py", "agents", "skills"]:
    assert (cached_dir / sub).exists(), f"Missing {sub} inside cached plugin {cached_dir}"

env = os.environ.copy()
env["PYTHONUTF8"] = "1"
env["PYTHONIOENCODING"] = "utf-8"
env.pop("PYTHONPATH", None)
env.pop("VIRTUAL_ENV", None)

# Confirm orchestrator resolves strictly inside cached_dir (not repo_root)
imp = subprocess.run(
    [uv_exe, "run", "--quiet", "--project", str(cached_dir), "python", "-c",
     "import orchestrator, pathlib; print(pathlib.Path(orchestrator.__file__).resolve())"],
    cwd=temp_cwd,
    env=env,
    capture_output=True,
    text=True,
    encoding="utf-8",
    check=True,
)
mod_path = pathlib.Path(imp.stdout.strip().splitlines()[-1]).resolve()
assert mod_path.is_relative_to(cached_dir), f"Imported orchestrator from {mod_path}, expected inside {cached_dir}"
assert not mod_path.is_relative_to(repo_root), f"Imported orchestrator from source repo {repo_root}!"

proc = subprocess.Popen(
    [uv_exe, "run", "--quiet", "--project", str(cached_dir), "python", "-m", "orchestrator.mcp_shim"],
    cwd=temp_cwd,
    env=env,
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    encoding="utf-8",
    bufsize=1,
)

q = queue.Queue()
def _reader():
    assert proc.stdout is not None
    for line in proc.stdout:
        q.put(line)
    q.put(None)

t = threading.Thread(target=_reader, daemon=True)
t.start()

def send(obj):
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()

try:
    send({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "verify_e2e_detached", "version": "0.1.0"},
        },
    })

    responses = {}
    sent_tools_list = False
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        try:
            line = q.get(timeout=0.5)
        except queue.Empty:
            continue
        if line is None:
            break
        stripped = line.strip()
        if not stripped:
            continue
        try:
            msg = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise AssertionError(f"Non-JSON-RPC line on MCP stdout (purity failure): {stripped!r} ({exc})")
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            raise AssertionError(f"Invalid JSON-RPC frame on stdout: {msg!r}")
        if "id" in msg:
            responses[msg["id"]] = msg
            if msg["id"] == 1 and not sent_tools_list:
                assert "result" in msg, f"initialize error: {msg}"
                send({"jsonrpc": "2.0", "method": "notifications/initialized"})
                send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
                sent_tools_list = True
            if 2 in responses:
                break

    assert 1 in responses, "Did not receive initialize response"
    assert 2 in responses, "Did not receive tools/list response"
    tools_resp = responses[2]
    assert "result" in tools_resp and "tools" in tools_resp["result"], f"Bad tools/list: {tools_resp}"
    tool_names = {t["name"] for t in tools_resp["result"]["tools"]}
    missing = sorted(expected - tool_names)
    extra = sorted(tool_names - expected)
    assert not missing, f"Missing MCP tools: {missing}"
    assert len(tool_names) == len(expected), f"Expected {len(expected)} tools, got {len(tool_names)} (extra={extra})"
    print(f"Detached cache MCP OK (cwd={temp_cwd}, module={mod_path}, {len(tool_names)}/{len(expected)} tools, stdout pure JSON-RPC)")
finally:
    try:
        if proc.stdin:
            proc.stdin.close()
    except Exception:
        pass
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.terminate()
        proc.wait(timeout=3)
'@
$mcpEnv = @{
    TRI_ORCH_HOST = $DaemonHost
    TRI_ORCH_PORT = [string]$DaemonPort
}
$mcpRes = Invoke-PythonInline -PyRunner $pyRunner -Code $detachedProbePy -ScriptArgs @($cachedPluginDir, $uvExe, ($ExpectedTools -join ","), $RepoRoot) -WorkingDirectory $env:TEMP -EnvOverrides $mcpEnv
if ($mcpRes.ExitCode -ne 0) {
    throw "[FAIL] Detached cache MCP handshake failed:`n$($mcpRes.StdOut)`n$($mcpRes.StdErr)"
}
Write-Host "[PASS] $($mcpRes.StdOut.Trim())"

# 4. Daemon /api/health, Web Dashboard HTTP 200, and SSE stream check
Write-Host "[verify] 4/5 Checking Daemon HTTP /api/health, Web UI, APIs, and SSE..."
$webProbePy = @'
import json, re, sys, urllib.request

base = sys.argv[1]

def fetch(path, timeout=5):
    with urllib.request.urlopen(base + path, timeout=timeout) as resp:
        return resp.status, dict(resp.headers.items()), resp.read()

st, hdrs, body = fetch("/api/health")
assert st == 200, f"/api/health status={st}"
health = json.loads(body.decode("utf-8"))
assert health.get("status") in ("ok", "healthy") or health.get("ok") is True, f"Unexpected health payload: {health}"

st_html, _, html_bytes = fetch("/")
assert st_html == 200, f"/ status={st_html}"
html = html_bytes.decode("utf-8")
assert not re.search(r'(?i)<(script|link)\b[^>]+https?://', html), "External CDN link found in index.html"

st_css, _, css_bytes = fetch("/styles.css")
assert st_css == 200 and len(css_bytes) > 50, f"/styles.css invalid: {st_css}"

st_js, _, js_bytes = fetch("/app.js")
assert st_js == 200 and len(js_bytes) > 50, f"/app.js invalid: {st_js}"

for api_path in ["/api/projects", "/api/runs"]:
    s, _, b = fetch(api_path)
    assert s == 200, f"{api_path} status={s}"
    json.loads(b.decode("utf-8"))

req = urllib.request.Request(base + "/api/events", headers={"Accept": "text/event-stream"})
with urllib.request.urlopen(req, timeout=5) as sse_resp:
    assert sse_resp.status == 200, f"/api/events status={sse_resp.status}"
    ctype = sse_resp.headers.get("Content-Type", "")
    assert "text/event-stream" in ctype.lower(), f"Bad SSE content-type: {ctype}"

print(f"Daemon & Web UI OK at {base} (health={health.get('status', health.get('ok'))}, pid={health.get('pid')}, SSE=text/event-stream)")
'@
$baseUrl = "http://${DaemonHost}:${DaemonPort}"
$webRes = Invoke-PythonInline -PyRunner $pyRunner -Code $webProbePy -ScriptArgs @($baseUrl)
if ($webRes.ExitCode -ne 0) {
    throw "[FAIL] Daemon/Web UI verification failed:`n$($webRes.StdOut)`n$($webRes.StdErr)"
}
Write-Host "[PASS] $($webRes.StdOut.Trim())"

# 5. Codex plugin list & mcp list check
if (-not $SkipPluginCheck) {
    Write-Host "[verify] 5/5 Checking Codex CLI plugin & MCP registration..."
    $codexEnv = @{ CODEX_HOME = $CodexHome }

    $plRes = Invoke-Native -FilePath $codexExe -Arguments @("plugin", "list", "--marketplace", "tri-orchestrator", "--json") -EnvOverrides $codexEnv
    if ($plRes.ExitCode -ne 0) {
        throw "[FAIL] codex plugin list failed: $($plRes.StdErr)"
    }
    $plJson = $plRes.StdOut | ConvertFrom-Json
    $installedList = if ($plJson.PSObject.Properties['installed'] -and $plJson.installed) { $plJson.installed } else { @() }
    $match = $installedList | Where-Object { $_.pluginId -eq "tri-orchestrator@tri-orchestrator" -and $_.installed -and $_.enabled } | Select-Object -First 1
    if (-not $match) {
        throw "[FAIL] tri-orchestrator@tri-orchestrator is not installed+enabled in codex plugin list:`n$($plRes.StdOut)"
    }

    $mcpListRes = Invoke-Native -FilePath $codexExe -Arguments @("mcp", "list", "--json") -EnvOverrides $codexEnv
    if ($mcpListRes.ExitCode -ne 0) {
        throw "[FAIL] codex mcp list failed: $($mcpListRes.StdErr)"
    }
    $mcpJson = $mcpListRes.StdOut | ConvertFrom-Json
    $mcpMatch = $mcpJson | Where-Object { $_.name -eq "tri_orchestrator" -and $_.enabled } | Select-Object -First 1
    if (-not $mcpMatch) {
        throw "[FAIL] tri_orchestrator MCP server not enabled in codex mcp list:`n$($mcpListRes.StdOut)"
    }
    if ($mcpMatch.transport.command -ne "uv") {
        throw "[FAIL] Expected tri_orchestrator MCP command='uv', got '$($mcpMatch.transport.command)'"
    }
    $mcpCwd = [System.IO.Path]::GetFullPath([string]$mcpMatch.transport.cwd).TrimEnd('\')
    if ($mcpCwd -ine $normCache) {
        throw "[FAIL] Expected tri_orchestrator MCP cwd='$normCache', got '$mcpCwd'"
    }

    Write-Host "[PASS] codex plugin list: $($match.pluginId) (v$($match.version), installed=$($match.installed), enabled=$($match.enabled))"
    Write-Host "[PASS] codex mcp list   : $($mcpMatch.name) (enabled=$($mcpMatch.enabled), command=$($mcpMatch.transport.command), cwd=$mcpCwd)"
} else {
    Write-Host "[SKIP] 5/5 Codex CLI check skipped via -SkipPluginCheck"
}

Write-Host "[verify] ALL CHECKS PASSED."
