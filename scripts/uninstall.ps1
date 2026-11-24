<#
.SYNOPSIS
    Cleanly removes the tri-orchestrator Codex plugin, local marketplace,
    and unchanged installer-owned agent roles, with optional configuration restoration.
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

function Get-RoleHash {
    param([string]$Path)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash([System.IO.File]::ReadAllBytes($Path))).Replace("-", "") }
    finally { $sha.Dispose() }
}

function Copy-RoleFile {
    param([string]$Source, [string]$Target)
    $tmp = "$Target.tmp.$([Guid]::NewGuid().ToString('N'))"
    [System.IO.File]::Copy($Source, $tmp)
    if ([System.IO.File]::Exists($Target)) {
        [System.IO.File]::Replace($tmp, $Target, [NullString]::Value)
    } else {
        [System.IO.File]::Move($tmp, $Target)
    }
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

# 1. Stop running daemon if present (never touch global %LOCALAPPDATA%\tri-orchestrator when targeting a custom -CodexHome without explicit TRI_ORCH_DATA_DIR)
$defaultCodexHome = [System.IO.Path]::GetFullPath((Join-Path $env:USERPROFILE ".codex")).TrimEnd('\')
$resolvedTargetHome = [System.IO.Path]::GetFullPath($CodexHome).TrimEnd('\')
$isDefaultCodexHome = ($resolvedTargetHome -ieq $defaultCodexHome)

$dataDir = if (-not [string]::IsNullOrWhiteSpace($env:TRI_ORCH_DATA_DIR)) {
    $env:TRI_ORCH_DATA_DIR
} elseif ($isDefaultCodexHome) {
    Join-Path $env:LOCALAPPDATA "tri-orchestrator"
} else {
    ""
}
function Stop-RecordedDaemon {
    param([string]$PidFile, [string]$DataDir)
    $record = Read-Utf8Text $PidFile | ConvertFrom-Json
    $expectedDir = [IO.Path]::GetFullPath($DataDir).TrimEnd('\')
    if ($record.pid -le 0 -or $record.port -lt 1 -or $record.port -gt 65535 -or $record.started_at -le 0 -or
        -not [IO.Path]::IsPathRooted($record.data_dir) -or
        [IO.Path]::GetFullPath($record.data_dir).TrimEnd('\') -ine $expectedDir) {
        throw "Invalid daemon identity in $PidFile"
    }

    # The handle must be opened before the probe and retained through termination: a PID can be reused.
    try { $process = [Diagnostics.Process]::GetProcessById([int]$record.pid) }
    catch [ArgumentException] {
        $listeners = [Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
        if ($listeners | Where-Object Port -eq $record.port) { throw "PID is absent but port $($record.port) is in use" }
        return # No process and no listener: only the stale record remains.
    }
    try {
        [void]$process.get_Handle()
        $socket = Get-NetTCPConnection -State Listen -LocalPort ([uint16]$record.port) -OwningProcess $process.Id -ErrorAction Stop | Select-Object -First 1
        $address = $socket.LocalAddress
        if ($address -eq '0.0.0.0') { $address = '127.0.0.1' }
        if ($address -eq '::') { $address = '::1' }
        $uri = [UriBuilder]::new('http', $address, [int]$record.port, '/api/health').Uri
        Add-Type -AssemblyName System.Net.Http
        $handler = [Net.Http.HttpClientHandler]::new()
        $handler.UseProxy = $false
        $handler.AllowAutoRedirect = $false
        $client = [Net.Http.HttpClient]::new($handler)
        try {
            $client.Timeout = [TimeSpan]::FromSeconds(5)
            $health = $client.GetStringAsync($uri).GetAwaiter().GetResult() | ConvertFrom-Json
        } finally { $client.Dispose() }
        if ($health.ok -isnot [bool] -or -not $health.ok -or $health.status -cne 'ok' -or
            $health.pid -ne $process.Id -or $health.port -ne $record.port -or
            $health.started_at -ne $record.started_at -or -not [IO.Path]::IsPathRooted($health.data_dir) -or
            [IO.Path]::GetFullPath($health.data_dir).TrimEnd('\') -ine $expectedDir) {
            throw "Daemon health identity mismatch: PID $($record.pid), data $expectedDir, started_at $($record.started_at)"
        }

        Write-Host "[uninstall] Stopping verified daemon PID $($process.Id)..."
        $process.Kill()
        if (-not $process.WaitForExit(5000)) { throw "Timed out stopping daemon PID $($process.Id)" }
    } finally { $process.Dispose() }
}

$pidFile = if ($dataDir) { Join-Path $dataDir "daemon.pid.json" } else { "" }
if ($pidFile -and (Test-Path -LiteralPath $pidFile -PathType Leaf)) {
    # Cooperate with the shim's daemon.lock byte zero; do not unlink a replacement instance's metadata.
    $lock = [IO.File]::Open((Join-Path $dataDir 'daemon.lock'), 'OpenOrCreate', 'ReadWrite', 'ReadWrite')
    try {
        $lock.Lock(0, 1)
        if (Test-Path -LiteralPath $pidFile -PathType Leaf) {
            Stop-RecordedDaemon $pidFile $dataDir
            Remove-Item -LiteralPath $pidFile -Force
        }
    } catch {
        throw "[uninstall] Could not verify/stop daemon; preserving ${pidFile}: $_"
    } finally { $lock.Dispose() }
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

# 4. Remove or restore only unchanged role files owned by the installer.
$ownershipPath = Join-Path $baselineDir "installed-roles.json"
$agentsDstDir = Join-Path $CodexHome "agents"
if ((Test-Path -LiteralPath $ownershipPath -PathType Leaf) -and (Test-Path -LiteralPath $agentsDstDir -PathType Container)) {
    $ownership = Read-Utf8Text $ownershipPath | ConvertFrom-Json
    if ((Get-Item -LiteralPath $agentsDstDir -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        Write-Warning "[uninstall] Preserved roles in linked agents directory: $agentsDstDir"
    } else {
        $currentRoles = @(Get-ChildItem -LiteralPath $agentsDstDir -Force)
        foreach ($rf in $RoleFiles) {
            if (-not $ownership.PSObject.Properties[$rf]) { continue }
            $record = $ownership.$rf
            $target = Join-Path $agentsDstDir $rf
            $entry = $currentRoles | Where-Object { $_.Name -eq $rf } | Select-Object -First 1
            if ($entry -and ($entry.PSIsContainer -or ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -or (Get-RoleHash $target) -ne $record.sha256)) {
                Write-Warning "[uninstall] Preserved linked or modified role: $target"
                continue
            }
            if ($record.restore_from -in @("agents", "roles-before-reinstall")) {
                $backup = Join-Path (Join-Path $baselineDir $record.restore_from) $rf
                Copy-RoleFile -Source $backup -Target $target
                Write-Host "[uninstall] Restored pre-existing agent role $target from $backup"
            } elseif ($record.restore_from -ne "") {
                throw "Unknown restore source for role $rf in $ownershipPath"
            } elseif ($entry) {
                Remove-Item -LiteralPath $target -Force
                Write-Host "[uninstall] Removed $target"
            }
        }
    }
    Write-Utf8NoBom -Path $ownershipPath -Content ("{}" + "`n")
} elseif ($hasBaseline -and -not (Test-Path -LiteralPath $ownershipPath -PathType Leaf)) {
    Write-Warning "[uninstall] Kept existing roles because this legacy installation has no role ownership record."
}

# 5. Optional full config.toml restoration from baseline (or latest backup), plus clean removal of tri-orchestrator [hooks.state] keys
function Remove-TriOrchestratorHooksStateFromToml {
    param([string]$TomlText)
    if ([string]::IsNullOrEmpty($TomlText)) {
        return $TomlText
    }
    $nl = if ($TomlText -match "`r`n") { "`r`n" } else { "`n" }
    $rawLines = $TomlText -split "\r?\n"
    $lines = New-Object System.Collections.Generic.List[string]
    foreach ($l in $rawLines) {
        $lines.Add($l)
    }
    if ($lines.Count -gt 0 -and $lines[$lines.Count - 1] -eq "") {
        $lines.RemoveAt($lines.Count - 1)
    }

    # Remove any standalone subtable [hooks.state."tri-orchestrator@tri-orchestrator:..."]
    $afterSub = New-Object System.Collections.Generic.List[string]
    $skipSub = $false
    foreach ($ln in $lines) {
        if ($ln -match '^\s*\[\s*hooks\.state\s*\.\s*([''"])tri-orchestrator@tri-orchestrator:[^''"]*\1\s*\]\s*(#.*)?$') {
            $skipSub = $true
            continue
        }
        if ($skipSub -and $ln -match '^\s*\[') {
            $skipSub = $false
        }
        if (-not $skipSub) {
            $afterSub.Add($ln)
        }
    }
    $lines = $afterSub

    # Remove "tri-orchestrator@tri-orchestrator:*" keys inside [hooks.state] and drop empty [hooks.state] header
    $secStart = -1
    $secEnd = $lines.Count
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match '^\s*\[\s*hooks\.state\s*\]\s*(#.*)?$') {
            $secStart = $i
            continue
        }
        if ($secStart -ge 0 -and $lines[$i] -match '^\s*\[') {
            $secEnd = $i
            break
        }
    }

    if ($secStart -ge 0) {
        $keptBody = New-Object System.Collections.Generic.List[string]
        $idx = $secStart + 1
        while ($idx -lt $secEnd) {
            $ln = $lines[$idx]
            if ($ln -match '^\s*([''"])tri-orchestrator@tri-orchestrator:[^''"]*\1\s*=') {
                $eqIdx = $ln.IndexOf('=')
                $rhs = if ($eqIdx -ge 0) { $ln.Substring($eqIdx + 1) } else { "" }
                $opens = ([regex]::Matches($rhs, '\{')).Count
                $closes = ([regex]::Matches($rhs, '\}')).Count
                $depth = $opens - $closes
                $idx++
                while ($depth -gt 0 -and $idx -lt $secEnd) {
                    $cur = $lines[$idx]
                    $depth += ([regex]::Matches($cur, '\{')).Count - ([regex]::Matches($cur, '\}')).Count
                    $idx++
                }
                continue
            }
            $keptBody.Add($ln)
            $idx++
        }

        $hasRemainingKeys = $false
        foreach ($b in $keptBody) {
            $t = $b.Trim()
            if ($t -ne "" -and -not $t.StartsWith("#")) {
                $hasRemainingKeys = $true
                break
            }
        }

        $rebuilt = New-Object System.Collections.Generic.List[string]
        for ($i = 0; $i -lt $secStart; $i++) {
            $rebuilt.Add($lines[$i])
        }
        if ($hasRemainingKeys) {
            $rebuilt.Add($lines[$secStart])
            foreach ($b in $keptBody) {
                $rebuilt.Add($b)
            }
        } else {
            while ($rebuilt.Count -gt 0 -and $rebuilt[$rebuilt.Count - 1].Trim() -eq "" -and ($secEnd -ge $lines.Count -or $lines[$secEnd].Trim() -eq "")) {
                $rebuilt.RemoveAt($rebuilt.Count - 1)
            }
        }
        for ($i = $secEnd; $i -lt $lines.Count; $i++) {
            $rebuilt.Add($lines[$i])
        }
        $lines = $rebuilt
    }

    while ($lines.Count -gt 0 -and $lines[$lines.Count - 1].Trim() -eq "") {
        $lines.RemoveAt($lines.Count - 1)
    }
    if ($lines.Count -eq 0) {
        return ""
    }
    return (($lines.ToArray() -join $nl) + $nl)
}

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
    } else {
        Write-Warning "[uninstall] -RestoreConfigBackup requested, but no backup config.toml was found."
    }
}

if (Test-Path $configPath -PathType Leaf) {
    $existingCfg = Read-Utf8Text $configPath
    $cleanedCfg = Remove-TriOrchestratorHooksStateFromToml -TomlText $existingCfg
    if ($cleanedCfg -cne $existingCfg) {
        Write-Utf8NoBom -Path $configPath -Content $cleanedCfg
        Write-Host "[uninstall] Removed tri-orchestrator entries from [hooks.state] in $configPath"
    }
}

# 6. Optional data directory cleanup
if ($RemoveDataDir -and $dataDir -and (Test-Path $dataDir)) {
    Remove-Item -Path $dataDir -Recurse -Force
    Write-Host "[uninstall] Removed data directory $dataDir"
}

Write-Host "[uninstall] Completed."
