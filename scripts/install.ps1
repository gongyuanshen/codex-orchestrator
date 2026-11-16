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
    [switch]$SkipPluginInstall
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
    [void]$proc.Start()
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
$pluginManifestPath = Join-Path $PluginDir "plugin.json"
if (-not (Test-Path $pluginManifestPath -PathType Leaf)) {
    throw "Missing plugin.json at $pluginManifestPath"
}

Write-Host "[install] PluginDir : $PluginDir"
if ($RepoRoot) {
    Write-Host "[install] RepoRoot  : $RepoRoot"
}
Write-Host "[install] CodexHome : $CodexHome"

$uvExe = Resolve-UvExe
Write-Host "[install] Using uv  : $uvExe"
$pyRunner = Resolve-PythonRunner -UvExe $uvExe

# Read plugin version without mutating any repo files
$pluginMeta = Read-Utf8Text $pluginManifestPath | ConvertFrom-Json
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
$candidateConfig = $updated.Content

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

# Ensure CodexHome & backup directories exist
New-Item -ItemType Directory -Path $CodexHome -Force | Out-Null
$agentsDstDir = Join-Path $CodexHome "agents"
New-Item -ItemType Directory -Path $agentsDstDir -Force | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupDir = Join-Path $CodexHome "backups\tri-orchestrator-$timestamp"
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null

$baselineDir = Join-Path $CodexHome "backups\tri-orchestrator-baseline"
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
Write-Host "[install] Updated [agents] in $configPath (strict-config verified)"

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

Write-Host "[install] Completed successfully."
