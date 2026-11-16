<#
.SYNOPSIS
    Cleanly removes the tri-orchestrator Codex plugin, local marketplace,
    and agent role TOMLs, with optional restoration of the latest backup.
#>
[CmdletBinding()]
param(
    [string]$CodexHome = "",
    [switch]$RestoreConfigBackup,
    [switch]$RemoveDataDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = $Utf8NoBom
[Console]::OutputEncoding = $Utf8NoBom

if ([string]::IsNullOrWhiteSpace($CodexHome)) {
    if (-not [string]::IsNullOrWhiteSpace($env:CODEX_HOME)) {
        $CodexHome = $env:CODEX_HOME
    } else {
        $CodexHome = Join-Path $env:USERPROFILE ".codex"
    }
}

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
        [hashtable]$EnvOverrides = @{}
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

function Resolve-CodexExeOptional {
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
    return $null
}

# 1. Stop running daemon if present
$dataDir = if (-not [string]::IsNullOrWhiteSpace($env:TRI_ORCH_DATA_DIR)) {
    $env:TRI_ORCH_DATA_DIR
} else {
    Join-Path $env:LOCALAPPDATA "tri-orchestrator"
}
$pidFile = Join-Path $dataDir "daemon.pid.json"
if (Test-Path $pidFile -PathType Leaf) {
    try {
        $pidInfo = Read-Utf8Text $pidFile | ConvertFrom-Json
        if ($pidInfo.PSObject.Properties['pid'] -and $pidInfo.pid) {
            $proc = Get-Process -Id ([int]$pidInfo.pid) -ErrorAction SilentlyContinue
            if ($proc) {
                Write-Host "[uninstall] Stopping running daemon PID $($proc.Id)..."
                Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
                Wait-Process -Id $proc.Id -Timeout 5 -ErrorAction SilentlyContinue
            }
        }
        Remove-Item -Path $pidFile -Force -ErrorAction SilentlyContinue
    } catch {
        Write-Warning "[uninstall] Could not parse/stop daemon from ${pidFile}: $_"
    }
}

# 2. Remove Codex plugin and marketplace registration
$codexExe = Resolve-CodexExeOptional -ActiveCodexHome $CodexHome
$codexEnv = @{ CODEX_HOME = $CodexHome }
if ($codexExe) {
    $plRes = Invoke-Native -FilePath $codexExe -Arguments @("plugin", "list", "--marketplace", "tri-orchestrator", "--json") -EnvOverrides $codexEnv
    if ($plRes.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($plRes.StdOut)) {
        $plJson = $plRes.StdOut | ConvertFrom-Json
        $installedList = if ($plJson.PSObject.Properties['installed'] -and $plJson.installed) { $plJson.installed } else { @() }
        $installed = $installedList | Where-Object { $_.pluginId -eq "tri-orchestrator@tri-orchestrator" } | Select-Object -First 1
        if ($installed) {
            $rmPl = Invoke-Native -FilePath $codexExe -Arguments @("plugin", "remove", "tri-orchestrator@tri-orchestrator", "--json") -EnvOverrides $codexEnv
            if ($rmPl.ExitCode -eq 0) {
                Write-Host "[uninstall] Removed plugin tri-orchestrator@tri-orchestrator"
            } else {
                Write-Warning "[uninstall] codex plugin remove returned exit $($rmPl.ExitCode): $($rmPl.StdErr)"
            }
        }
    }

    $mpRes = Invoke-Native -FilePath $codexExe -Arguments @("plugin", "marketplace", "list", "--json") -EnvOverrides $codexEnv
    if ($mpRes.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($mpRes.StdOut)) {
        $mpJson = $mpRes.StdOut | ConvertFrom-Json
        $mpList = if ($mpJson.PSObject.Properties['marketplaces'] -and $mpJson.marketplaces) { $mpJson.marketplaces } else { @() }
        $mpMatch = $mpList | Where-Object { $_.name -eq "tri-orchestrator" } | Select-Object -First 1
        if ($mpMatch) {
            $rmMp = Invoke-Native -FilePath $codexExe -Arguments @("plugin", "marketplace", "remove", "tri-orchestrator", "--json") -EnvOverrides $codexEnv
            if ($rmMp.ExitCode -eq 0) {
                Write-Host "[uninstall] Removed marketplace tri-orchestrator"
            } else {
                Write-Warning "[uninstall] codex plugin marketplace remove returned exit $($rmMp.ExitCode): $($rmMp.StdErr)"
            }
        }
    }
} else {
    Write-Warning "[uninstall] codex.exe not found; skipping CLI plugin/marketplace removal."
}

$pluginCacheDir = Join-Path $CodexHome "plugins\cache\tri-orchestrator"
if (Test-Path $pluginCacheDir) {
    Remove-Item -Path $pluginCacheDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "[uninstall] Cleaned leftover plugin cache at $pluginCacheDir"
}

# 3. Locate baseline (pre-tri-orchestrator) and latest backup directory
$backupsRoot = Join-Path $CodexHome "backups"
$baselineDir = Join-Path $backupsRoot "tri-orchestrator-baseline"
$hasBaseline = Test-Path (Join-Path $baselineDir "baseline.json") -PathType Leaf
$baselineMeta = $null
if ($hasBaseline) {
    $baselineMeta = Read-Utf8Text (Join-Path $baselineDir "baseline.json") | ConvertFrom-Json
}

$latestBackup = $null
if (Test-Path $backupsRoot) {
    $latestBackup = Get-ChildItem -Path $backupsRoot -Directory -Filter "tri-orchestrator-2*" -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending |
        Select-Object -First 1
}

# 4. Remove tri-orchestrator agent TOMLs & restore only pre-existing user agent TOMLs
$agentsDstDir = Join-Path $CodexHome "agents"
if (Test-Path $agentsDstDir) {
    $preexistingAgents = if ($hasBaseline -and $baselineMeta.PSObject.Properties['preexisting_agents'] -and $baselineMeta.preexisting_agents) {
        @($baselineMeta.preexisting_agents)
    } else {
        @()
    }
    foreach ($rf in $RoleFiles) {
        $target = Join-Path $agentsDstDir $rf
        if (Test-Path $target -PathType Leaf) {
            Remove-Item -Path $target -Force
            Write-Host "[uninstall] Removed $target"
        }
        if ($preexistingAgents -contains $rf) {
            $bakAgent = Join-Path $baselineDir "agents\$rf"
            if (Test-Path $bakAgent -PathType Leaf) {
                Copy-Item -Path $bakAgent -Destination $target -Force
                Write-Host "[uninstall] Restored pre-existing agent role $target from baseline"
            }
        }
    }
}

# 5. Optional full config.toml restoration from baseline (or latest backup)
$configPath = Join-Path $CodexHome "config.toml"
if ($RestoreConfigBackup) {
    $restoreCfgSrc = $null
    if ($hasBaseline -and (Test-Path (Join-Path $baselineDir "config.toml") -PathType Leaf)) {
        $restoreCfgSrc = Join-Path $baselineDir "config.toml"
    } elseif ($latestBackup -and (Test-Path (Join-Path $latestBackup.FullName "config.toml") -PathType Leaf)) {
        $restoreCfgSrc = Join-Path $latestBackup.FullName "config.toml"
    }
    if ($restoreCfgSrc) {
        if (Test-Path $configPath -PathType Leaf) {
            $preRestoreDir = Join-Path $backupsRoot ("tri-orchestrator-pre-uninstall-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
            New-Item -ItemType Directory -Path $preRestoreDir -Force | Out-Null
            Copy-Item -Path $configPath -Destination (Join-Path $preRestoreDir "config.toml") -Force
        }
        $bakCfg = Read-Utf8Text $restoreCfgSrc
        Write-Utf8NoBom -Path $configPath -Content $bakCfg
        Write-Host "[uninstall] Restored config.toml from $restoreCfgSrc"
        if ($hasBaseline) {
            Remove-Item -Path $baselineDir -Recurse -Force -ErrorAction SilentlyContinue
        }
    } else {
        Write-Warning "[uninstall] -RestoreConfigBackup requested, but no backup config.toml was found."
    }
}

# 6. Optional data directory cleanup
if ($RemoveDataDir -and (Test-Path $dataDir)) {
    Remove-Item -Path $dataDir -Recurse -Force
    Write-Host "[uninstall] Removed data directory $dataDir"
}

Write-Host "[uninstall] Completed."
