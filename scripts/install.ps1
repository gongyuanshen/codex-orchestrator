<#
.SYNOPSIS
    Idempotent Windows installer for the tri-orchestrator Codex plugin,
    agent role definitions, [agents] config settings, and cached plugin runtime.
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$PluginDir = "",
    [string]$CodexHome = "",
    [switch]$SkipUvSync,
    [switch]$SkipPluginInstall,
    [switch]$TrustPluginHooks,
    [ValidateRange(0, 10000)]
    [int]$KeepBackups = 3
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = $Utf8NoBom
[Console]::OutputEncoding = $Utf8NoBom

$RoleFiles = @(
    "interviewer.toml",
    "planner.toml",
    "investigator.toml",
    "coder.toml",
    "verifier.toml",
    "swarm_worker.toml"
)

function Write-Utf8NoBom {
    param([string]$Path, [string]$Content)
    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $tmp = "$Path.tmp.$([Guid]::NewGuid().ToString('N'))"
    [System.IO.File]::WriteAllText($tmp, $Content, $Utf8NoBom)
    Move-Item -Path $tmp -Destination $Path -Force
}

function Read-Utf8Text {
    param([string]$Path)
    return [System.IO.File]::ReadAllText($Path, $Utf8NoBom)
}

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
        [string]$StdinText = $null,
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
    $psi.RedirectStandardInput = ($null -ne $StdinText)
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
    # When RedirectStandardInput is true, .NET creates a StreamWriter using
    # Console.InputEncoding.  On systems where that encoding includes a BOM
    # preamble (UTF-8 with BOM), the preamble is auto-flushed into the pipe,
    # corrupting binary/text protocols such as TOML parsing.  Temporarily set
    # Console.InputEncoding to our BOM-free encoding so the StreamWriter is
    # constructed without a preamble.
    $savedInputEnc = $null
    if ($null -ne $StdinText) {
        $savedInputEnc = [Console]::InputEncoding
        [Console]::InputEncoding = $Utf8NoBom
    }
    [void]$proc.Start()
    if ($null -ne $savedInputEnc) {
        [Console]::InputEncoding = $savedInputEnc
    }
    if ($null -ne $StdinText) {
        $inBytes = $Utf8NoBom.GetBytes($StdinText)
        $proc.StandardInput.BaseStream.Write($inBytes, 0, $inBytes.Length)
        $proc.StandardInput.BaseStream.Flush()
        $proc.StandardInput.Close()
    }
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
    throw "Could not locate uv.exe. Install uv (https://docs.astral.sh/uv/) and re-run."
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
        [string]$StdinText = $null
    )
    $allArgs = @()
    $allArgs += $PyRunner.PrefixArgs
    $allArgs += @("-c", $Code)
    $allArgs += $ScriptArgs
    return Invoke-Native -FilePath $PyRunner.FilePath -Arguments $allArgs -WorkingDirectory $env:TEMP -StdinText $StdinText -EnvOverrides @{
        PYTHONUTF8       = "1"
        PYTHONIOENCODING = "utf-8"
    } -EnvRemove @("VIRTUAL_ENV", "PYTHONPATH")
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
    $cfgPath = Join-Path $ActiveCodexHome "config.toml"
    if (Test-Path $cfgPath) {
        $cfgText = Read-Utf8Text $cfgPath
        if ($cfgText -match "CODEX_CLI_PATH\s*=\s*['""]([^'""]+)['""]") {
            $candidates += $Matches[1]
        }
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

function Update-AgentsSectionInToml {
    param([string]$TomlText)
    $nl = if ($TomlText -match "`r`n") { "`r`n" } else { "`n" }
    $lines = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrEmpty($TomlText)) {
        foreach ($l in ($TomlText -split "\r?\n")) {
            $lines.Add($l)
        }
        # Trim a single trailing empty entry produced by trailing newline
        if ($lines.Count -gt 0 -and $lines[$lines.Count - 1] -eq "") {
            $lines.RemoveAt($lines.Count - 1)
        }
    }

    $agentsStart = -1
    $agentsEnd = $lines.Count
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $trim = $lines[$i].Trim()
        if ($trim -match '^\[agents\]\s*(#.*)?$') {
            $agentsStart = $i
            continue
        }
        if ($agentsStart -ge 0 -and $trim -match '^\[') {
            $agentsEnd = $i
            break
        }
    }

    $meta = @{
        had_agents_section = ($agentsStart -ge 0)
        added_threads_key  = $false
        added_max_depth    = $false
    }

    if ($agentsStart -lt 0) {
        if ($lines.Count -gt 0 -and $lines[$lines.Count - 1].Trim() -ne "") {
            $lines.Add("")
        }
        $lines.Add("[agents]")
        $lines.Add("max_threads = 6")
        $lines.Add("max_depth = 2")
        $meta.added_threads_key = $true
        $meta.added_max_depth = $true
    } else {
        $hasThreads = $false
        $hasDepth = $false
        for ($i = $agentsStart + 1; $i -lt $agentsEnd; $i++) {
            $line = $lines[$i]
            if ($line -match '^\s*(max_threads|max_concurrent_threads_per_session)\s*=\s*(\d+)') {
                $hasThreads = $true
                $val = [int]$Matches[2]
                if ($val -lt 6) {
                    $keyName = $Matches[1]
                    $lines[$i] = "$keyName = 6"
                }
            } elseif ($line -match '^\s*max_depth\s*=\s*(\d+)') {
                $hasDepth = $true
                $dval = [int]$Matches[1]
                if ($dval -lt 2) {
                    $lines[$i] = "max_depth = 2"
                }
            }
        }

        $insertAt = $agentsStart + 1
        if (-not $hasThreads) {
            $lines.Insert($insertAt, "max_threads = 6")
            $insertAt++
            $meta.added_threads_key = $true
        }
        if (-not $hasDepth) {
            $lines.Insert($insertAt, "max_depth = 2")
            $meta.added_max_depth = $true
        }
    }

    return [PSCustomObject]@{
        Content = (($lines.ToArray() -join $nl) + $nl)
        Meta    = $meta
    }
}

function Update-FeaturesSectionInToml {
    param([string]$TomlText)
    $nl = if ($TomlText -match "`r`n") { "`r`n" } else { "`n" }
    $lines = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrEmpty($TomlText)) {
        foreach ($l in ($TomlText -split "\r?\n")) {
            [void]$lines.Add($l)
        }
        if ($lines.Count -gt 0 -and $lines[$lines.Count - 1] -eq "") {
            [void]$lines.RemoveAt($lines.Count - 1)
        }
    }

    $featStart = -1
    $featEnd = $lines.Count
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $trim = $lines[$i].Trim()
        if ($trim -match '^\[features\]\s*(#.*)?$') {
            $featStart = $i
            continue
        }
        if ($featStart -ge 0 -and $trim -match '^\[') {
            $featEnd = $i
            break
        }
    }

    if ($featStart -lt 0) {
        if ($lines.Count -gt 0 -and $lines[$lines.Count - 1].Trim() -ne "") {
            [void]$lines.Add("")
        }
        [void]$lines.Add("[features]")
        [void]$lines.Add("multi_agent = true")
        [void]$lines.Add("multi_agent_v2 = true")
    } else {
        $hasMultiAgent = $false
        $hasMultiAgentV2 = $false
        for ($i = $featStart + 1; $i -lt $featEnd; $i++) {
            $line = $lines[$i]
            if ($line -match '^(\s*multi_agent_v2\s*=\s*)([^\s#]+)(.*)$') {
                $hasMultiAgentV2 = $true
                if ($Matches[2] -cne "true") {
                    $lines[$i] = "$($Matches[1])true$($Matches[3])"
                }
            } elseif ($line -match '^(\s*multi_agent\s*=\s*)([^\s#]+)(.*)$') {
                $hasMultiAgent = $true
                if ($Matches[2] -cne "true") {
                    $lines[$i] = "$($Matches[1])true$($Matches[3])"
                }
            }
        }
        $insertAt = $featStart + 1
        if (-not $hasMultiAgent) {
            [void]$lines.Insert($insertAt, "multi_agent = true")
            $insertAt++
            $featEnd++
        }
        if (-not $hasMultiAgentV2) {
            [void]$lines.Insert($insertAt, "multi_agent_v2 = true")
        }
    }

    return (($lines.ToArray() -join $nl) + $nl)
}

function Test-VenvPointsOutsideDir {
    param([string]$PluginDirPath)
    $venvDir = Join-Path $PluginDirPath ".venv"
    if (-not (Test-Path $venvDir -PathType Container)) {
        return $false
    }
    $normPlugin = [System.IO.Path]::GetFullPath($PluginDirPath).TrimEnd('\')
    $sitePackages = Join-Path $venvDir "Lib\site-packages"
    if (Test-Path $sitePackages -PathType Container) {
        $pthFiles = Get-ChildItem -Path $sitePackages -Filter "*.pth" -File -ErrorAction SilentlyContinue
        foreach ($pf in $pthFiles) {
            foreach ($rawLine in [System.IO.File]::ReadAllLines($pf.FullName, $Utf8NoBom)) {
                $line = $rawLine.Trim()
                if (-not $line -or $line.StartsWith("#") -or $line.StartsWith("import ")) { continue }
                try {
                    $resolved = [System.IO.Path]::GetFullPath($line).TrimEnd('\')
                    if (-not $resolved.StartsWith($normPlugin, [System.StringComparison]::OrdinalIgnoreCase)) {
                        return $true
                    }
                } catch {
                    continue
                }
            }
        }
    }
    return $false
}

# Resolve source layout: supports repo root (<root>/plugin) or direct/cached plugin directory
$HasMarketplaceLayout = $false
if (-not [string]::IsNullOrWhiteSpace($PluginDir)) {
    $PluginDir = (Resolve-Path $PluginDir).Path
    if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
        $parent = Split-Path -Parent $PluginDir
        if ($parent -and (Test-Path (Join-Path $parent ".agents\plugins\marketplace.json") -PathType Leaf)) {
            $RepoRoot = $parent
            $HasMarketplaceLayout = $true
        }
    } else {
        $RepoRoot = (Resolve-Path $RepoRoot).Path
        $HasMarketplaceLayout = (Test-Path (Join-Path $RepoRoot ".agents\plugins\marketplace.json") -PathType Leaf)
    }
} else {
    if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
        $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    } else {
        $RepoRoot = (Resolve-Path $RepoRoot).Path
    }
    $nestedPlugin = Join-Path $RepoRoot "plugin"
    if ((Test-Path (Join-Path $nestedPlugin "agents") -PathType Container) -and
        (Test-Path (Join-Path $nestedPlugin "pyproject.toml") -PathType Leaf)) {
        $PluginDir = $nestedPlugin
        $HasMarketplaceLayout = (Test-Path (Join-Path $RepoRoot ".agents\plugins\marketplace.json") -PathType Leaf)
    } elseif ((Test-Path (Join-Path $RepoRoot "agents") -PathType Container) -and
              (Test-Path (Join-Path $RepoRoot "pyproject.toml") -PathType Leaf)) {
        $PluginDir = $RepoRoot
        $HasMarketplaceLayout = $false
    } else {
        throw "Could not find self-contained plugin layout (expected plugin/agents/*.toml + plugin/pyproject.toml under $RepoRoot)."
    }
}

if ([string]::IsNullOrWhiteSpace($CodexHome)) {
    if (-not [string]::IsNullOrWhiteSpace($env:CODEX_HOME)) {
        $CodexHome = $env:CODEX_HOME
    } else {
        $CodexHome = Join-Path $env:USERPROFILE ".codex"
    }
}

$pluginPyproject = Join-Path $PluginDir "pyproject.toml"
if (-not (Test-Path $pluginPyproject -PathType Leaf)) {
    throw "Missing plugin pyproject.toml at $pluginPyproject"
}
$legacyRootManifestPath = Join-Path $PluginDir "plugin.json"
if (Test-Path $legacyRootManifestPath -PathType Leaf) {
    throw "Legacy root plugin.json must not exist at $legacyRootManifestPath (use .codex-plugin/plugin.json)."
}
$pluginManifestPath = Join-Path $PluginDir ".codex-plugin\plugin.json"
if (-not (Test-Path $pluginManifestPath -PathType Leaf)) {
    throw "Missing plugin manifest at $pluginManifestPath"
}
$pluginHooksPath = Join-Path $PluginDir "hooks\hooks.json"
if (-not (Test-Path $pluginHooksPath -PathType Leaf)) {
    throw "Missing plugin hooks config at $pluginHooksPath"
}

Write-Host "[install] PluginDir : $PluginDir"
if ($RepoRoot) {
    Write-Host "[install] RepoRoot  : $RepoRoot"
}
Write-Host "[install] CodexHome : $CodexHome"

$uvExe = Resolve-UvExe
Write-Host "[install] Using uv  : $uvExe"
$pyRunner = Resolve-PythonRunner -UvExe $uvExe

# Read single plugin manifest (.codex-plugin/plugin.json) and validate hooks declaration without mutating any repo files
$pluginMeta = Read-Utf8Text $pluginManifestPath | ConvertFrom-Json
if (-not ($pluginMeta.PSObject.Properties['hooks'] -and $pluginMeta.hooks -eq "./hooks/hooks.json")) {
    throw "Expected .codex-plugin/plugin.json to declare hooks='./hooks/hooks.json' at $pluginManifestPath"
}
$null = Read-Utf8Text $pluginHooksPath | ConvertFrom-Json
$pluginVersion = if ($pluginMeta.PSObject.Properties['version'] -and $pluginMeta.version) { [string]$pluginMeta.version } else { "0.1.0" }

# Validate plugin/agents/*.toml before touching ~/.codex
$agentsSrcDir = Join-Path $PluginDir "agents"
if (-not (Test-Path $agentsSrcDir -PathType Container)) {
    throw "Missing plugin agents directory: $agentsSrcDir"
}

$roleCheckPy = @'
import sys, tomllib
d = tomllib.loads(open(sys.argv[1], "r", encoding="utf-8").read())
assert "model" not in d, "hardcoded model forbidden"
assert "model_reasoning_effort" not in d, "hardcoded model_reasoning_effort forbidden"
assert d.get("name") and d.get("description") and d.get("developer_instructions"), "missing required fields"
'@

foreach ($rf in $RoleFiles) {
    $srcFile = Join-Path $agentsSrcDir $rf
    if (-not (Test-Path $srcFile -PathType Leaf)) {
        throw "Missing required agent role file: $srcFile"
    }
    $chk = Invoke-PythonInline -PyRunner $pyRunner -Code $roleCheckPy -ScriptArgs @($srcFile)
    if ($chk.ExitCode -ne 0) {
        throw "Agent role TOML validation failed for ${srcFile}: $($chk.StdErr) $($chk.StdOut)"
    }
}

# Prepare candidate config.toml and validate before writing anything to CodexHome
$codexExe = Resolve-CodexExe -ActiveCodexHome $CodexHome
Write-Host "[install] Using codex: $codexExe"

$configPath = Join-Path $CodexHome "config.toml"
$configExisted = Test-Path $configPath -PathType Leaf
$existingConfigText = if ($configExisted) { Read-Utf8Text $configPath } else { "" }

$updated = Update-AgentsSectionInToml -TomlText $existingConfigText
$candidateConfig = Update-FeaturesSectionInToml -TomlText $updated.Content

$tomlParseCheck = Invoke-PythonInline -PyRunner $pyRunner -Code "import sys, tomllib; tomllib.loads(sys.stdin.read())" -StdinText $candidateConfig
if ($tomlParseCheck.ExitCode -ne 0) {
    throw "Generated config.toml failed TOML parse check: $($tomlParseCheck.StdErr)"
}

$stageDir = Join-Path ([System.IO.Path]::GetTempPath()) ("tri-orch-cfg-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $stageDir -Force | Out-Null
try {
    Write-Utf8NoBom -Path (Join-Path $stageDir "config.toml") -Content $candidateConfig
    $strictRes = Invoke-Native -FilePath $codexExe -Arguments @("--strict-config", "exec", "--skip-git-repo-check") -StdinText "" -EnvOverrides @{ CODEX_HOME = $stageDir }
    if ($strictRes.StdErr -match "Error loading config\.toml") {
        throw "codex --strict-config rejected updated config.toml:`n$($strictRes.StdErr)"
    }
} finally {
    Remove-Item -Path $stageDir -Recurse -Force -ErrorAction SilentlyContinue
}

# Ensure CodexHome & backup directories exist (back up only config.toml + role TOMLs; never copy .venv into backups)
New-Item -ItemType Directory -Path $CodexHome -Force | Out-Null
$agentsDstDir = Join-Path $CodexHome "agents"
New-Item -ItemType Directory -Path $agentsDstDir -Force | Out-Null

$backupsRoot = Join-Path $CodexHome "backups"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupDir = Join-Path $backupsRoot "tri-orchestrator-$timestamp"
$suffixIdx = 1
while (Test-Path $backupDir) {
    $backupDir = Join-Path $backupsRoot ("tri-orchestrator-{0}-{1:D2}" -f $timestamp, $suffixIdx)
    $suffixIdx++
}
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null

$baselineDir = Join-Path $backupsRoot "tri-orchestrator-baseline"
$baselineExists = Test-Path $baselineDir
if (-not $baselineExists) {
    New-Item -ItemType Directory -Path $baselineDir -Force | Out-Null
}

if ($configExisted) {
    Write-Utf8NoBom -Path (Join-Path $backupDir "config.toml") -Content $existingConfigText
    if (-not $baselineExists) {
        Write-Utf8NoBom -Path (Join-Path $baselineDir "config.toml") -Content $existingConfigText
    }
    Write-Host "[install] Backed up config.toml -> $backupDir\config.toml"
}

$agentsBackupDir = Join-Path $backupDir "agents"
$baselineAgentsDir = Join-Path $baselineDir "agents"
$backedUpAgents = @()
$baselinePreexistingAgents = @()
$installedAgents = @()

foreach ($rf in $RoleFiles) {
    $srcFile = Join-Path $agentsSrcDir $rf
    $dstFile = Join-Path $agentsDstDir $rf
    if (Test-Path $dstFile -PathType Leaf) {
        New-Item -ItemType Directory -Path $agentsBackupDir -Force | Out-Null
        Copy-Item -Path $dstFile -Destination (Join-Path $agentsBackupDir $rf) -Force
        $backedUpAgents += $rf
        if (-not $baselineExists) {
            New-Item -ItemType Directory -Path $baselineAgentsDir -Force | Out-Null
            Copy-Item -Path $dstFile -Destination (Join-Path $baselineAgentsDir $rf) -Force
            $baselinePreexistingAgents += $rf
        }
    }
    Copy-Item -Path $srcFile -Destination $dstFile -Force
    $installedAgents += $rf
}

if (-not $baselineExists) {
    $baselineObj = [ordered]@{
        created_at         = $timestamp
        config_existed     = $configExisted
        preexisting_agents = $baselinePreexistingAgents
    }
    Write-Utf8NoBom -Path (Join-Path $baselineDir "baseline.json") -Content (($baselineObj | ConvertTo-Json -Depth 4) + "`n")
}
Write-Host "[install] Installed $($installedAgents.Count) agent role TOMLs into $agentsDstDir"

Write-Utf8NoBom -Path $configPath -Content $candidateConfig
Write-Host "[install] Updated [agents] and [features] in $configPath (strict-config verified)"

$manifestObj = [ordered]@{
    installed_at     = $timestamp
    repo_root        = $RepoRoot
    plugin_dir       = $PluginDir
    codex_home       = $CodexHome
    config_existed   = $configExisted
    backed_up_agents = $backedUpAgents
    installed_agents = $installedAgents
    agents_meta      = $updated.Meta
}
Write-Utf8NoBom -Path (Join-Path $backupDir "manifest.json") -Content (($manifestObj | ConvertTo-Json -Depth 6) + "`n")

$pruneBackupsPy = @'
import os, pathlib, stat, sys

backups_root = pathlib.Path(sys.argv[1])
keep_count = max(0, int(sys.argv[2]))

def is_reparse_or_symlink(p: pathlib.Path) -> bool:
    try:
        if p.is_symlink():
            return True
        if hasattr(p, "is_junction") and p.is_junction():
            return True
        st = os.lstat(p)
        attrs = getattr(st, "st_file_attributes", 0)
        return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    except OSError:
        return False

def rmtree_safe(root: pathlib.Path) -> None:
    if is_reparse_or_symlink(root):
        try:
            os.rmdir(root)
        except OSError:
            root.unlink(missing_ok=True)
        return
    with os.scandir(root) as it:
        for entry in it:
            ep = pathlib.Path(entry.path)
            if is_reparse_or_symlink(ep):
                try:
                    os.rmdir(ep)
                except OSError:
                    ep.unlink(missing_ok=True)
            elif entry.is_dir(follow_symlinks=False):
                rmtree_safe(ep)
            else:
                try:
                    ep.unlink()
                except PermissionError:
                    os.chmod(ep, stat.S_IWRITE | stat.S_IREAD)
                    ep.unlink()
    try:
        os.rmdir(root)
    except PermissionError:
        os.chmod(root, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        os.rmdir(root)

if not backups_root.is_dir():
    sys.exit(0)

candidates = []
for entry in backups_root.iterdir():
    name = entry.name
    if name == "tri-orchestrator-baseline":
        continue
    if not name.startswith("tri-orchestrator-2"):
        continue
    if is_reparse_or_symlink(entry):
        continue
    if entry.is_dir():
        candidates.append(entry)

candidates.sort(key=lambda p: p.name, reverse=True)
pruned = []
for old_dir in candidates[keep_count:]:
    rmtree_safe(old_dir)
    pruned.append(old_dir.name)

if pruned:
    print(f"[install] Pruned {len(pruned)} old backup(s) under {backups_root} (kept baseline + latest {keep_count}): {', '.join(pruned)}")
'@

$pruneRes = Invoke-PythonInline -PyRunner $pyRunner -Code $pruneBackupsPy -ScriptArgs @($backupsRoot, [string]$KeepBackups)
if ($pruneRes.ExitCode -ne 0) {
    throw "Failed to prune old config backups under ${backupsRoot}: $($pruneRes.StdErr) $($pruneRes.StdOut)"
}
if (-not [string]::IsNullOrWhiteSpace($pruneRes.StdOut)) {
    Write-Host ($pruneRes.StdOut.TrimEnd())
}

$cachedPluginDir = ""

if (-not $SkipPluginInstall) {
    $codexEnv = @{ CODEX_HOME = $CodexHome }

    if ($HasMarketplaceLayout) {
        # Register/refresh local marketplace
        $mpListRes = Invoke-Native -FilePath $codexExe -Arguments @("plugin", "marketplace", "list", "--json") -EnvOverrides $codexEnv
        if ($mpListRes.ExitCode -ne 0) {
            throw "codex plugin marketplace list failed: $($mpListRes.StdErr)"
        }
        $mpData = $mpListRes.StdOut | ConvertFrom-Json
        $marketplaces = if ($mpData.PSObject.Properties['marketplaces'] -and $mpData.marketplaces) { $mpData.marketplaces } else { @() }
        $existingMp = $marketplaces | Where-Object { $_.name -eq "tri-orchestrator" } | Select-Object -First 1
        if ($existingMp) {
            $existingRoot = if ($existingMp.PSObject.Properties['root'] -and $existingMp.root) { [string]$existingMp.root } else { "" }
            $normExisting = if ($existingRoot) { [System.IO.Path]::GetFullPath(($existingRoot -replace '^\\\\\?\\', '')) } else { "" }
            $normRepo = [System.IO.Path]::GetFullPath($RepoRoot)
            if ($normExisting -ine $normRepo) {
                Write-Host "[install] Re-pointing marketplace 'tri-orchestrator' from '$normExisting' -> '$normRepo'"
                [void](Invoke-Native -FilePath $codexExe -Arguments @("plugin", "marketplace", "remove", "tri-orchestrator", "--json") -EnvOverrides $codexEnv)
                $mpAddRes = Invoke-Native -FilePath $codexExe -Arguments @("plugin", "marketplace", "add", $RepoRoot, "--json") -EnvOverrides $codexEnv
                if ($mpAddRes.ExitCode -ne 0) {
                    throw "codex plugin marketplace add failed: $($mpAddRes.StdOut) $($mpAddRes.StdErr)"
                }
            } else {
                Write-Host "[install] Marketplace 'tri-orchestrator' already points to $normRepo"
            }
        } else {
            $mpAddRes = Invoke-Native -FilePath $codexExe -Arguments @("plugin", "marketplace", "add", $RepoRoot, "--json") -EnvOverrides $codexEnv
            if ($mpAddRes.ExitCode -ne 0) {
                throw "codex plugin marketplace add failed: $($mpAddRes.StdOut) $($mpAddRes.StdErr)"
            }
            Write-Host "[install] Registered marketplace 'tri-orchestrator'"
        }

        # Force-refresh plugin cache so updated plugin/ is copied cleanly into ~/.codex/plugins/cache/...
        $plListRes = Invoke-Native -FilePath $codexExe -Arguments @("plugin", "list", "--marketplace", "tri-orchestrator", "--json") -EnvOverrides $codexEnv
        if ($plListRes.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($plListRes.StdOut)) {
            $plData = $plListRes.StdOut | ConvertFrom-Json
            $installedItems = if ($plData.PSObject.Properties['installed'] -and $plData.installed) { $plData.installed } else { @() }
            $installedItem = $installedItems | Where-Object { $_.pluginId -eq "tri-orchestrator@tri-orchestrator" } | Select-Object -First 1
            if ($installedItem) {
                Write-Host "[install] Removing existing plugin cache before fresh install..."
                $rmRes = Invoke-Native -FilePath $codexExe -Arguments @("plugin", "remove", "tri-orchestrator@tri-orchestrator", "--json") -EnvOverrides $codexEnv
                if ($rmRes.ExitCode -ne 0) {
                    throw "codex plugin remove failed: $($rmRes.StdOut) $($rmRes.StdErr)"
                }
            }
        }

        $addRes = Invoke-Native -FilePath $codexExe -Arguments @("plugin", "add", "tri-orchestrator@tri-orchestrator", "--json") -EnvOverrides $codexEnv
        if ($addRes.ExitCode -ne 0) {
            throw "codex plugin add failed: $($addRes.StdOut) $($addRes.StdErr)"
        }
    } else {
        # Direct/cached plugin mode: install plugin from existing configured marketplace only if missing
        $plListRes = Invoke-Native -FilePath $codexExe -Arguments @("plugin", "list", "--marketplace", "tri-orchestrator", "--json") -EnvOverrides $codexEnv
        $isInstalled = $false
        if ($plListRes.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($plListRes.StdOut)) {
            $plData = $plListRes.StdOut | ConvertFrom-Json
            $installedItems = if ($plData.PSObject.Properties['installed'] -and $plData.installed) { $plData.installed } else { @() }
            $installedItem = $installedItems | Where-Object { $_.pluginId -eq "tri-orchestrator@tri-orchestrator" -and $_.installed } | Select-Object -First 1
            $isInstalled = ($null -ne $installedItem)
        }
        if (-not $isInstalled) {
            $addRes = Invoke-Native -FilePath $codexExe -Arguments @("plugin", "add", "tri-orchestrator@tri-orchestrator", "--json") -EnvOverrides $codexEnv
            if ($addRes.ExitCode -ne 0) {
                throw "Plugin tri-orchestrator@tri-orchestrator is not installed and could not be added from marketplace: $($addRes.StdOut) $($addRes.StdErr)"
            }
        }
    }

    # Verify via codex plugin list & codex mcp list
    $verifyPl = Invoke-Native -FilePath $codexExe -Arguments @("plugin", "list", "--marketplace", "tri-orchestrator", "--json") -EnvOverrides $codexEnv
    if ($verifyPl.ExitCode -ne 0) {
        throw "codex plugin list verification failed: $($verifyPl.StdErr)"
    }
    $verifyPlJson = $verifyPl.StdOut | ConvertFrom-Json
    $match = $verifyPlJson.installed | Where-Object { $_.pluginId -eq "tri-orchestrator@tri-orchestrator" -and $_.installed -and $_.enabled } | Select-Object -First 1
    if (-not $match) {
        throw "tri-orchestrator@tri-orchestrator not reported as installed+enabled:`n$($verifyPl.StdOut)"
    }
    if ($match.PSObject.Properties['version'] -and $match.version) {
        $pluginVersion = [string]$match.version
    }

    $verifyMcp = Invoke-Native -FilePath $codexExe -Arguments @("mcp", "list", "--json") -EnvOverrides $codexEnv
    if ($verifyMcp.ExitCode -ne 0) {
        throw "codex mcp list verification failed: $($verifyMcp.StdErr)"
    }
    $mcpItems = $verifyMcp.StdOut | ConvertFrom-Json
    $mcpEntry = $mcpItems | Where-Object { $_.name -eq "tri_orchestrator" -and $_.enabled } | Select-Object -First 1
    if (-not $mcpEntry) {
        throw "MCP server 'tri_orchestrator' not reported enabled in codex mcp list:`n$($verifyMcp.StdOut)"
    }

    if ($mcpEntry.transport.PSObject.Properties['env'] -and $mcpEntry.transport.env -and $mcpEntry.transport.env.PSObject.Properties['PLUGIN_ROOT'] -and $mcpEntry.transport.env.PLUGIN_ROOT) {
        $cachedPluginDir = [string]$mcpEntry.transport.env.PLUGIN_ROOT
    } elseif ($mcpEntry.transport.PSObject.Properties['cwd'] -and $mcpEntry.transport.cwd) {
        $cachedPluginDir = [string]$mcpEntry.transport.cwd
    }

    Write-Host "[install] Verified plugin: $($match.pluginId) (v$pluginVersion, installed=$($match.installed), enabled=$($match.enabled))"
    Write-Host "[install] Verified MCP   : $($mcpEntry.name) (command=$($mcpEntry.transport.command), cwd=$($mcpEntry.transport.cwd))"
}

if ([string]::IsNullOrWhiteSpace($cachedPluginDir)) {
    $expectedCache = Join-Path $CodexHome "plugins\cache\tri-orchestrator\tri-orchestrator\$pluginVersion"
    if (Test-Path $expectedCache -PathType Container) {
        $cachedPluginDir = $expectedCache
    } elseif (-not $HasMarketplaceLayout) {
        $cachedPluginDir = $PluginDir
    }
}

if (-not $SkipUvSync) {
    if ([string]::IsNullOrWhiteSpace($cachedPluginDir) -or -not (Test-Path (Join-Path $cachedPluginDir "pyproject.toml") -PathType Leaf)) {
        if ($SkipPluginInstall) {
            Write-Host "[install] Cached plugin directory not present (-SkipPluginInstall); skipping cache uv sync."
        } else {
            throw "Cached plugin directory missing pyproject.toml: '$cachedPluginDir'"
        }
    } else {
        $cachedPluginDir = (Resolve-Path $cachedPluginDir).Path
        # Remove any .venv copied from source repo or pointing outside the cache
        $cachedVenv = Join-Path $cachedPluginDir ".venv"
        if ((Test-Path $cachedVenv -PathType Container) -and ($HasMarketplaceLayout -or (Test-VenvPointsOutsideDir -PluginDirPath $cachedPluginDir))) {
            Write-Host "[install] Removing stale copied .venv inside $cachedPluginDir..."
            Remove-Item -Path $cachedVenv -Recurse -Force
        }

        Write-Host "[install] Pre-warming cached plugin runtime: uv sync --project $cachedPluginDir --no-dev"
        $syncRes = Invoke-Native -FilePath $uvExe -Arguments @("sync", "--project", $cachedPluginDir, "--no-dev") -WorkingDirectory $env:TEMP -EnvRemove @("VIRTUAL_ENV", "PYTHONPATH")
        if ($syncRes.ExitCode -ne 0) {
            throw "uv sync --project $cachedPluginDir --no-dev failed (exit $($syncRes.ExitCode)):`n$($syncRes.StdOut)`n$($syncRes.StdErr)"
        }
        $cachedPy = Join-Path $cachedPluginDir ".venv\Scripts\python.exe"
        if (-not (Test-Path $cachedPy -PathType Leaf)) {
            throw "Expected cached plugin python missing at $cachedPy"
        }
        Write-Host "[install] Pre-warmed cached runtime -> $cachedPluginDir\.venv"
    }
}

function Invoke-HooksListProbe {
    param(
        [PSCustomObject]$PyRunner,
        [string]$CodexExePath,
        [string]$TargetCodexHome,
        [string]$ProbeCwd,
        [string]$VersionStr
    )
    $hooksProbePy = @'
import json, os, queue, subprocess, sys, threading, time

codex_exe, codex_home, probe_cwd, plugin_version = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
env = os.environ.copy()
env["CODEX_HOME"] = codex_home
env["PYTHONUTF8"] = "1"
env["PYTHONIOENCODING"] = "utf-8"

creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
proc = subprocess.Popen(
    [codex_exe, "app-server", "--stdio"],
    cwd=probe_cwd,
    env=env,
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    encoding="utf-8",
    bufsize=1,
    creationflags=creationflags,
)

stdout_q = queue.Queue()
stderr_chunks = []

def _read_out():
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            stdout_q.put(line)
    finally:
        stdout_q.put(None)

def _read_err():
    assert proc.stderr is not None
    try:
        for line in proc.stderr:
            stderr_chunks.append(line)
    except Exception:
        pass

t_out = threading.Thread(target=_read_out, daemon=True)
t_err = threading.Thread(target=_read_err, daemon=True)
t_out.start()
t_err.start()

def _send(obj):
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()

hooks_resp = None
try:
    _send({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "clientInfo": {"name": "tri-orchestrator-installer", "version": plugin_version},
            "capabilities": {"experimentalApi": True},
        },
    })
    sent_list = False
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        try:
            line = stdout_q.get(timeout=0.4)
        except queue.Empty:
            if proc.poll() is not None and stdout_q.empty():
                break
            continue
        if line is None:
            break
        raw = line.strip()
        if not raw:
            continue
        msg = json.loads(raw)
        if not isinstance(msg, dict):
            continue
        msg_id = msg.get("id")
        if msg_id == 1 and not sent_list:
            if "error" in msg:
                raise RuntimeError(f"app-server initialize error: {msg['error']}")
            _send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
            _send({"jsonrpc": "2.0", "id": 2, "method": "hooks/list", "params": {}})
            sent_list = True
        elif msg_id == 2:
            if "error" in msg:
                raise RuntimeError(f"app-server hooks/list error: {msg['error']}")
            hooks_resp = msg.get("result") or {}
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
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2.0)
    t_out.join(timeout=1.0)
    t_err.join(timeout=1.0)
    try:
        if proc.stdout:
            proc.stdout.close()
    except Exception:
        pass
    try:
        if proc.stderr:
            proc.stderr.close()
    except Exception:
        pass

if hooks_resp is None:
    err_tail = "".join(stderr_chunks[-20:]).strip()
    raise RuntimeError(f"Timed out or exited before receiving hooks/list response. stderr={err_tail}")

all_hooks = []
warnings = []
errors = []
if isinstance(hooks_resp.get("data"), list):
    for bucket in hooks_resp["data"]:
        if isinstance(bucket, dict):
            if isinstance(bucket.get("hooks"), list):
                all_hooks.extend(bucket["hooks"])
            if isinstance(bucket.get("warnings"), list):
                warnings.extend(bucket["warnings"])
            if isinstance(bucket.get("errors"), list):
                errors.extend(bucket["errors"])
elif isinstance(hooks_resp.get("hooks"), list):
    all_hooks.extend(hooks_resp["hooks"])

plugin_hooks_by_key = {}
for h in all_hooks:
    if isinstance(h, dict) and h.get("pluginId") == "tri-orchestrator@tri-orchestrator":
        k = h.get("key") or f"{h.get('eventName')}:{len(plugin_hooks_by_key)}"
        plugin_hooks_by_key[k] = h

print(json.dumps({
    "hooks": list(plugin_hooks_by_key.values()),
    "warnings": warnings,
    "errors": errors,
}, ensure_ascii=False))
'@
    $probeRes = Invoke-PythonInline -PyRunner $PyRunner -Code $hooksProbePy -ScriptArgs @($CodexExePath, $TargetCodexHome, $ProbeCwd, $VersionStr)
    if ($probeRes.ExitCode -ne 0) {
        throw "codex app-server --stdio hooks/list self-check failed:`n$($probeRes.StdOut)`n$($probeRes.StdErr)"
    }
    return ($probeRes.StdOut | ConvertFrom-Json)
}

if ((-not $SkipPluginInstall) -or $TrustPluginHooks) {
    $probeWorkDir = if ($RepoRoot -and (Test-Path $RepoRoot -PathType Container)) { $RepoRoot } else { $env:TEMP }
    $hooksReport = Invoke-HooksListProbe -PyRunner $pyRunner -CodexExePath $codexExe -TargetCodexHome $CodexHome -ProbeCwd $probeWorkDir -VersionStr $pluginVersion
    $discoveredHooks = @()
    if ($hooksReport.PSObject.Properties['hooks'] -and $hooksReport.hooks) {
        $discoveredHooks = @($hooksReport.hooks)
    }
    $requiredHookEvents = @("sessionStart", "postCompact", "userPromptSubmit", "stop")
    $presentEvents = @($discoveredHooks | ForEach-Object { [string]$_.eventName })
    $missingEvents = @($requiredHookEvents | Where-Object { $presentEvents -notcontains $_ })
    if ($missingEvents.Count -gt 0) {
        $rawSummary = ($discoveredHooks | ConvertTo-Json -Compress -Depth 5)
        throw "Plugin hooks/list verification failed: missing lifecycle hook(s) [$($missingEvents -join ', ')] for 'tri-orchestrator@tri-orchestrator'. Discovered: $rawSummary"
    }

    if ($TrustPluginHooks) {
        $mergeHooksPy = @'
import json, re, sys, tomllib

raw_payload = json.loads(sys.stdin.read())
toml_text = raw_payload["toml_text"]
hooks = raw_payload["hooks"]

entries = []
for h in sorted(hooks, key=lambda item: (item.get("displayOrder", 999), item.get("key", ""))):
    key = h.get("key")
    cur_hash = h.get("currentHash") or ""
    if not key or not isinstance(key, str):
        raise ValueError(f"Hook missing valid key: {h}")
    parts = cur_hash.split("/", 1)
    if len(parts) != 2 or not parts[0].startswith("sha256:") or not parts[1].startswith("sha256:"):
        raise ValueError(f"Unexpected composite currentHash for {key}: {cur_hash!r}")
    cmd_hash, content_hash = parts[0], parts[1]
    line = f"{json.dumps(key)} = {{ enabled = true, trusted_hash = {json.dumps(cmd_hash)}, trusted_content_hash = {json.dumps(content_hash)} }}"
    entries.append((key, cmd_hash, content_hash, line))

nl = "\r\n" if "\r\n" in toml_text else "\n"
lines = toml_text.splitlines() if toml_text else []

cleaned_lines = []
skip_subtable = False
subtable_re = re.compile(r'^\s*\[\s*hooks\.state\s*\.\s*(["\'])tri-orchestrator@tri-orchestrator:[^"\']*\1\s*\]\s*(?:#.*)?$')
any_header_re = re.compile(r'^\s*\[')
for ln in lines:
    if subtable_re.match(ln):
        skip_subtable = True
        continue
    if skip_subtable and any_header_re.match(ln):
        skip_subtable = False
    if not skip_subtable:
        cleaned_lines.append(ln)
lines = cleaned_lines

sec_start = -1
sec_end = len(lines)
hooks_state_re = re.compile(r'^\s*\[\s*hooks\.state\s*\]\s*(?:#.*)?$')
for idx, ln in enumerate(lines):
    if hooks_state_re.match(ln):
        sec_start = idx
        continue
    if sec_start >= 0 and any_header_re.match(ln):
        sec_end = idx
        break

tri_key_re = re.compile(r'^\s*(["\'])tri-orchestrator@tri-orchestrator:[^"\']*\1\s*=')
if sec_start < 0:
    if lines and lines[-1].strip() != "":
        lines.append("")
    lines.append("[hooks.state]")
    for _, _, _, entry_line in entries:
        lines.append(entry_line)
else:
    kept_body = []
    i = sec_start + 1
    while i < sec_end:
        ln = lines[i]
        if tri_key_re.match(ln):
            rhs = ln.split("=", 1)[1]
            depth = rhs.count("{") - rhs.count("}")
            i += 1
            while depth > 0 and i < sec_end:
                depth += lines[i].count("{") - lines[i].count("}")
                i += 1
            continue
        kept_body.append(ln)
        i += 1
    had_trailing_blank = bool(kept_body and kept_body[-1].strip() == "")
    while kept_body and kept_body[-1].strip() == "":
        kept_body.pop()
    for _, _, _, entry_line in entries:
        kept_body.append(entry_line)
    if sec_end < len(lines) and (had_trailing_blank or lines[sec_end].strip() != ""):
        kept_body.append("")
    lines = lines[: sec_start + 1] + kept_body + lines[sec_end:]

updated_toml = (nl.join(lines) + nl) if lines else ""
parsed = tomllib.loads(updated_toml)
state_tbl = parsed.get("hooks", {}).get("state", {})
for key, cmd_hash, content_hash, _ in entries:
    actual = state_tbl.get(key)
    expected = {"enabled": True, "trusted_hash": cmd_hash, "trusted_content_hash": content_hash}
    if actual != expected:
        raise AssertionError(f"TOML roundtrip mismatch for {key}: {actual} != {expected}")

sys.stdout.buffer.write(updated_toml.encode("utf-8"))
'@
        $currentCfgText = if (Test-Path $configPath -PathType Leaf) { Read-Utf8Text $configPath } else { "" }
        $mergePayload = (@{
            toml_text = $currentCfgText
            hooks     = $discoveredHooks
        } | ConvertTo-Json -Depth 8 -Compress)
        $mergeRes = Invoke-PythonInline -PyRunner $pyRunner -Code $mergeHooksPy -StdinText $mergePayload
        if ($mergeRes.ExitCode -ne 0) {
            throw "Failed to generate [hooks.state] entries for config.toml:`n$($mergeRes.StdErr)`n$($mergeRes.StdOut)"
        }
        $trustedCandidateConfig = $mergeRes.StdOut

        $hooksStageDir = Join-Path ([System.IO.Path]::GetTempPath()) ("tri-orch-hooks-cfg-" + [Guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $hooksStageDir -Force | Out-Null
        try {
            Write-Utf8NoBom -Path (Join-Path $hooksStageDir "config.toml") -Content $trustedCandidateConfig
            $strictHooksRes = Invoke-Native -FilePath $codexExe -Arguments @("app-server", "--strict-config", "--print-startup-config") -EnvOverrides @{ CODEX_HOME = $hooksStageDir }
            if ($strictHooksRes.ExitCode -ne 0 -or $strictHooksRes.StdErr -match "Error loading config\.toml") {
                throw "codex --strict-config rejected [hooks.state] in config.toml:`n$($strictHooksRes.StdOut)`n$($strictHooksRes.StdErr)"
            }
        } finally {
            Remove-Item -Path $hooksStageDir -Recurse -Force -ErrorAction SilentlyContinue
        }

        Write-Utf8NoBom -Path $configPath -Content $trustedCandidateConfig
        $recheckReport = Invoke-HooksListProbe -PyRunner $pyRunner -CodexExePath $codexExe -TargetCodexHome $CodexHome -ProbeCwd $probeWorkDir -VersionStr $pluginVersion
        $recheckHooks = @($recheckReport.hooks)
        $stillUntrusted = @($recheckHooks | Where-Object { $_.trustStatus -ne "trusted" })
        if ($recheckHooks.Count -lt 4 -or $stillUntrusted.Count -gt 0) {
            $recheckSummary = ($recheckHooks | ConvertTo-Json -Compress -Depth 5)
            throw "-TrustPluginHooks failed to transition all 4 hooks to trustStatus='trusted': $recheckSummary"
        }
        Write-Host "[install] Trusted all 4 plugin hooks in [hooks.state] ($configPath) and verified trustStatus=trusted via hooks/list."
    } else {
        $untrustedHooks = @($discoveredHooks | Where-Object { $_.trustStatus -ne "trusted" })
        if ($untrustedHooks.Count -gt 0) {
            Write-Host "[install] Verified 4 plugin hooks via hooks/list (trustStatus=untrusted; [hooks.state] left untouched by default)."
            Write-Host "[提示] 已发现 tri-orchestrator 的全部 4 个生命周期钩子（sessionStart / postCompact / userPromptSubmit / stop），当前默认处于未信任（untrusted）状态。"
            Write-Host "[提示] 打开 Codex Desktop 后，请在弹出的插件钩子信任提示框中点击「信任 / Trust」（或使用 scripts\install.ps1 -TrustPluginHooks 重新安装），即可激活 Stop 防提前退出门禁与 PostCompact 上下文自动恢复。"
        } else {
            Write-Host "[install] Verified 4 plugin hooks via hooks/list (all trustStatus=trusted)."
        }
    }
}

Write-Host "[install] Completed successfully."
