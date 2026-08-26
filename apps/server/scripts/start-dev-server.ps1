# Restart the AgentCore API server cleanly on Windows.
#
# Kills every matching `python -m agentcore` / `uv run python -m agentcore`
# process TREE (taskkill /T), waits until PORT has no LISTEN, then starts a
# fresh server. Whitelist is command-line exact — will not touch pytest, node,
# electron/desktop, sidecar (`python -m agentcore.sidecar`), or unrelated python.
# Sidecar is owned by the desktop Electron host (stdio JSON-RPC). Killing it
# here would drop live 本机传统 turns and would not reload the API; desktop
# local chats keep the already-imported runtime/prompt until the app is fully
# quit and reopened.
#
# Live-run guard: before killing trees, reads the last line of repo-root
# logs/dev.jsonl. If its `timestamp` is within -ActiveWindowSeconds (default
# 120), refuses restart (non-zero exit) so a parallel Agent session cannot
# wipe a user's multi-agent turn. Pass -Force to skip the check knowingly.
# Missing/unreadable log = allow (first boot must not be blocked).
#
# Usage (from repo root or anywhere):
#   powershell -File apps/server/scripts/start-dev-server.ps1
#   powershell -File apps/server/scripts/start-dev-server.ps1 -Port 8000
#   powershell -File apps/server/scripts/start-dev-server.ps1 -Force

[CmdletBinding()]
param(
    [int]$Port = 8000,
    [int]$ActiveWindowSeconds = 120,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$serverRoot = (Resolve-Path (Join-Path $scriptDir '..')).Path
$repoRoot = (Resolve-Path (Join-Path $serverRoot (Join-Path '..' '..'))).Path
$devLogPath = Join-Path $repoRoot (Join-Path 'logs' 'dev.jsonl')

function Test-IsAgentcoreServerCommand {
    param([string]$CommandLine)
    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return $false }
    # Never touch test runners even if a weird argv mentions agentcore.
    if ($CommandLine -match '(?i)(?:^|[\s\\/"])pytest(?:\.exe)?(?:\s|"|$)') { return $false }
    if ($CommandLine -match '(?i)(?:^|[\s\\/"])_pytest(?:\s|"|$)') { return $false }
    # Exact module `agentcore` only — not `agentcore.evals` / sidecar / etc.
    if ($CommandLine -match '(?i)(?:^|[\s"''])-m\s+agentcore(?:\s|$|"|'')') { return $true }
    return $false
}

function Get-AgentcoreServerProcesses {
    Get-CimInstance Win32_Process |
        Where-Object { Test-IsAgentcoreServerCommand -CommandLine $_.CommandLine }
}

function Get-ListenPidsOnPort {
    param([int]$LocalPort)
    @(Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
}

function Get-DevLogActivityAgeSeconds {
    <#
    .SYNOPSIS
      Seconds since the last logs/dev.jsonl line, or $null if unreadable.
    #>
    param([string]$LogPath)
    if (-not (Test-Path -LiteralPath $LogPath)) { return $null }
    try {
        $line = Get-Content -LiteralPath $LogPath -Tail 1 -ErrorAction Stop
        if ([string]::IsNullOrWhiteSpace($line)) { return $null }
        $obj = $line | ConvertFrom-Json
        $tsRaw = $obj.timestamp
        if ([string]::IsNullOrWhiteSpace([string]$tsRaw)) { return $null }
        $when = [DateTimeOffset]::Parse(
            [string]$tsRaw,
            [System.Globalization.CultureInfo]::InvariantCulture
        )
        $age = ([DateTimeOffset]::UtcNow - $when.ToUniversalTime()).TotalSeconds
        if ($age -lt 0) { return 0 }
        return [int][Math]::Floor($age)
    } catch {
        return $null
    }
}

function Test-LiveRunGuard {
    param(
        [string]$LogPath,
        [int]$WindowSeconds,
        [switch]$Skip
    )
    if ($Skip) {
        Write-Host "Live-run guard skipped (-Force)."
        return $true
    }
    $age = Get-DevLogActivityAgeSeconds -LogPath $LogPath
    if ($null -eq $age) {
        Write-Host ("Live-run guard: log unreadable or missing ({0}); allowing restart." -f $LogPath)
        return $true
    }
    if ($age -lt $WindowSeconds) {
        Write-Host ""
        Write-Host ("*** BLOCKED: 后端 {0} 秒前仍有活动,疑似用户真跑中;确认要杀请加 -Force ***" -f $age) -ForegroundColor Red
        Write-Host ("*** (threshold={0}s, log={1}) ***" -f $WindowSeconds, $LogPath) -ForegroundColor Red
        Write-Host ""
        return $false
    }
    Write-Host ("Live-run guard: last log activity {0}s ago (>= {1}s); OK to restart." -f $age, $WindowSeconds)
    return $true
}

function Stop-AgentcoreServerTrees {
    $matches = @(Get-AgentcoreServerProcesses)
    if ($matches.Count -eq 0) {
        Write-Host "No agentcore server process trees matched; continuing."
        return
    }

    $ids = @($matches | ForEach-Object { [int]$_.ProcessId })
    $roots = @($matches | Where-Object { $ids -notcontains ([int]$_.ParentProcessId) })
    if ($roots.Count -eq 0) {
        # Degenerate: treat every match as a root.
        $roots = $matches
    }

    Write-Host ("Stopping {0} agentcore server tree root(s): {1}" -f `
        $roots.Count, (($roots | ForEach-Object { $_.ProcessId }) -join ', '))
    foreach ($root in $roots) {
        $procId = [int]$root.ProcessId
        # /T = whole tree (reloader parent + worker). Required on Windows.
        & taskkill.exe /F /T /PID $procId 2>$null | Out-Null
    }
}

function Wait-PortFree {
    param(
        [int]$LocalPort,
        [int]$TimeoutSeconds = 20
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $pids = @(Get-ListenPidsOnPort -LocalPort $LocalPort)
        if ($pids.Count -eq 0) { return }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)

    $left = @(Get-ListenPidsOnPort -LocalPort $LocalPort)
    if ($left.Count -gt 0) {
        throw ("Port {0} still has LISTEN after cleanup (PIDs: {1}). " +
            "Inspect with Get-NetTCPConnection -LocalPort {0} -State Listen; " +
            "kill the owning tree with taskkill /F /T /PID <pid>.") -f `
            $LocalPort, ($left -join ', ')
    }
}

Write-Host "=== AgentCore dev server restart (port $Port) ==="
if (-not (Test-LiveRunGuard -LogPath $devLogPath -WindowSeconds $ActiveWindowSeconds -Skip:$Force)) {
    exit 2
}
Stop-AgentcoreServerTrees
Wait-PortFree -LocalPort $Port

Write-Host "Starting: uv run python -m agentcore  (cwd=$serverRoot)"
Write-Host ""
Write-Host "This restarts the API only. Desktop 本机传统 turns run in sidecar" -ForegroundColor Yellow
Write-Host "(python -m agentcore.sidecar) and keep the already-imported runtime/prompt." -ForegroundColor Yellow
Write-Host "To load code changes there: fully quit and reopen the desktop app." -ForegroundColor Yellow
Write-Host "This script never kills sidecar (Electron owns that stdio)." -ForegroundColor Yellow
Write-Host ""
Set-Location $serverRoot
& uv run python -m agentcore
exit $LASTEXITCODE
