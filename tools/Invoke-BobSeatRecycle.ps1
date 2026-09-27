<#
.SYNOPSIS
  Local bob-seat recycle (FR #197) — tray-equivalent ordered steps.
.DESCRIPTION
  Called when the ear sees Jeeves shop wire:
    RECYCLE machine=<id> by=<nick> scope=local exec=local-bob-seat
  Jeeves never runs this. Scripts only; no LLM.
.PARAMETER MachineId
  Must match this host (BOB_MACHINE_ID / COMPUTERNAME slug).
.PARAMETER WhatIf
  Plan only — no stop/kill/git/restart.
.PARAMETER RepoRoot
  Worker checkout to ff-only (default: agentic_build candidates or cwd).
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$MachineId = $(if ($env:BOB_MACHINE_ID) { $env:BOB_MACHINE_ID } else { $env:COMPUTERNAME }),
    [string]$ExpectedMachine = '',
    [string]$RepoRoot = '',
    [switch]$WhatIf
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'
$stepResults = [System.Collections.Generic.List[object]]::new()

function Write-Step([string]$Id, [string]$Status, [string]$Detail = '') {
    $obj = [pscustomobject]@{ id = $Id; status = $Status; detail = $Detail }
    [void]$stepResults.Add($obj)
    Write-Host ("recycle[{0}]: {1} {2}" -f $Id, $Status, $Detail)
}

function Test-OwnedWorkerProcess {
    param($Proc, [string]$InstallRoot)
    # Only processes clearly tied to this Bob/agentic install — never bare powershell/node/python.
    try {
        $cmd = (Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $Proc.Id) -ErrorAction Stop).CommandLine
    } catch { return $false }
    if (-not $cmd) { return $false }
    $markers = @(
        'Watch-Bob', 'Start-Bob', 'agentic_build', 'agentic-irc', 'BobFleet',
        'Watch-AgentHealth', 'irc_agent', 'bob-seat', 'Invoke-BobSeatRecycle'
    )
    $hit = $false
    foreach ($m in $markers) {
        if ($cmd -like ('*{0}*' -f $m)) { $hit = $true; break }
    }
    if (-not $hit) { return $false }
    if ($InstallRoot -and ($cmd -like ('*{0}*' -f $InstallRoot))) { return $true }
    # Allow known Bob tool names even if path differs (orphans).
    return $true
}

$mid = ([string]$MachineId).Trim().ToLowerInvariant()
if ($ExpectedMachine) {
    $exp = ([string]$ExpectedMachine).Trim().ToLowerInvariant().TrimStart('#')
    if ($exp -and $exp -ne 'fleet' -and $exp -ne 'all' -and $exp -ne $mid) {
        Write-Step 'scope' 'fail' ("expected machine={0} this host={1}" -f $exp, $mid)
        $stepResults | ConvertTo-Json -Depth 4
        exit 2
    }
}
Write-Step 'scope' 'ok' ("machine={0}" -f $mid)

# Resolve worker checkout
$candidates = @(
    $RepoRoot,
    $env:BOB_AGENTIC_BUILD,
    'C:\bob-seat-work\agentic_build',
    'D:\ai\agentic_build',
    'C:\ai\agentic_build'
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
$root = $null
foreach ($c in $candidates) {
    if (Test-Path -LiteralPath (Join-Path $c '.git')) { $root = (Resolve-Path -LiteralPath $c).Path; break }
}
if (-not $root) { $root = (Get-Location).Path }

# --- 1 stop managed workers (owned watchers) ---
$stopped = 0
if ($WhatIf -or -not $PSCmdlet.ShouldProcess('managed workers', 'Stop')) {
    Write-Step 'stop_managed_workers' 'skip' 'WhatIf/no-op plan'
} else {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | ForEach-Object {
        $p = $_
        $name = [string]$p.Name
        if ($name -notmatch '^(powershell|pwsh|node|python)(\.exe)?$') { return }
        # Build a fake proc object with Id
        $fake = [pscustomobject]@{ Id = [int]$p.ProcessId }
        if (-not (Test-OwnedWorkerProcess -Proc $fake -InstallRoot $root)) { return }
        try {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
            $stopped++
        } catch {
            Write-Step 'stop_managed_workers' 'partial' $_.Exception.Message
        }
    }
    Write-Step 'stop_managed_workers' 'ok' ("stopped={0}" -f $stopped)
}

# --- 2 cleanup owned orphans (second pass) ---
$cleaned = 0
if ($WhatIf) {
    Write-Step 'cleanup_owned_orphans' 'skip' 'WhatIf'
} else {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | ForEach-Object {
        $p = $_
        if ([string]$p.Name -notmatch '^(powershell|pwsh|node|python)(\.exe)?$') { return }
        $fake = [pscustomobject]@{ Id = [int]$p.ProcessId }
        if (-not (Test-OwnedWorkerProcess -Proc $fake -InstallRoot $root)) { return }
        try {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
            $cleaned++
        } catch { }
    }
    Write-Step 'cleanup_owned_orphans' 'ok' ("cleaned={0}" -f $cleaned)
}

# --- 3 git ff-only ---
$gitOk = $true
Push-Location $root
try {
    if (-not (Test-Path -LiteralPath (Join-Path $root '.git'))) {
        Write-Step 'git_ff_only' 'skip' 'no .git'
    } else {
        $porcelain = @(git status --porcelain 2>$null)
        if ($porcelain.Count -gt 0) {
            Write-Step 'git_ff_only' 'fail' 'dirty working tree left intact'
            $gitOk = $false
        } elseif ($WhatIf) {
            Write-Step 'git_ff_only' 'skip' 'WhatIf'
        } else {
            git fetch --quiet origin 2>$null | Out-Null
            $ff = git merge --ff-only '@{u}' 2>&1
            if ($LASTEXITCODE -ne 0) {
                Write-Step 'git_ff_only' 'fail' ("non-ff left intact: {0}" -f ($ff | Out-String).Trim())
                $gitOk = $false
            } else {
                Write-Step 'git_ff_only' 'ok' 'fast-forwarded'
            }
        }
    }
} finally { Pop-Location }

# --- 4 reload skills ---
if ($WhatIf) {
    Write-Step 'reload_skills' 'skip' 'WhatIf'
} else {
    $reinstall = Join-Path $root 'tools\Reinstall-AgentSkills.ps1'
    if (Test-Path -LiteralPath $reinstall) {
        try {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $reinstall 2>&1 | Out-Null
            Write-Step 'reload_skills' 'ok' 'Reinstall-AgentSkills'
        } catch {
            Write-Step 'reload_skills' 'fail' $_.Exception.Message
        }
    } else {
        Write-Step 'reload_skills' 'skip' 'Reinstall-AgentSkills.ps1 missing'
    }
}

# --- 5 restart workers ---
if (-not $gitOk -and -not $WhatIf) {
    Write-Step 'restart_workers' 'fail' 'skipped: git step failed (inconsistent tree)'
} elseif ($WhatIf) {
    Write-Step 'restart_workers' 'skip' 'WhatIf'
} else {
    $startTray = Join-Path $root 'tools\Start-BobFleetTray.ps1'
    if (Test-Path -LiteralPath $startTray) {
        Start-Process -FilePath (Get-Command powershell.exe).Source `
            -ArgumentList @('-NoProfile', '-STA', '-WindowStyle', 'Hidden', '-ExecutionPolicy', 'Bypass', '-File', $startTray, '-RepoRoot', $root, '-ForceNew') `
            -WorkingDirectory $root -WindowStyle Hidden | Out-Null
        Write-Step 'restart_workers' 'ok' 'Start-BobFleetTray -ForceNew'
    } else {
        Write-Step 'restart_workers' 'skip' 'Start-BobFleetTray.ps1 missing'
    }
}

$failed = @($stepResults | Where-Object { $_.status -eq 'fail' })
$stepResults | ConvertTo-Json -Depth 4
if ($failed.Count -gt 0) { exit 1 }
exit 0
