#Requires -Version 5.1
# Idempotent plan/install for Windows service BobJeeves (gh-Jeeves FR #17).
# NEVER creates/starts/stops/edits Ergo, BobIrcd, or ircd.yaml.
# Default: -DryRun (JSON plan). Pass -Apply only on machines you administer.
[CmdletBinding()]
param(
    [string]$ServiceName = 'BobJeeves',
    [string]$DisplayName = 'Bobiverse Jeeves (digest chair)',
    [string]$DependsOn = 'BobIrcd',
    [string]$RepoRoot,
    [string]$JeevesHome,
    [string]$DigestHome,
    [string]$Nick = 'Jeeves',
    [string]$PythonPath,
    [string]$NssmPath,
    [string]$ChairEntry,
    [switch]$DryRun,
    [switch]$Apply,
    [switch]$Json
)

$ErrorActionPreference = 'Stop'

if (-not $Apply) { $DryRun = $true }
if ($Apply -and $DryRun) { throw 'Use either -Apply or -DryRun, not both.' }

if (-not $RepoRoot) { $RepoRoot = Split-Path $PSScriptRoot -Parent }
$RepoRoot = [IO.Path]::GetFullPath($RepoRoot)

if (-not $JeevesHome) { $JeevesHome = Join-Path $env:USERPROFILE '.agentic-irc-jeeves' }
if (-not $DigestHome) { $DigestHome = Join-Path $env:USERPROFILE '.agentic-irc-bobiverse' }
$JeevesHome = [IO.Path]::GetFullPath($JeevesHome)
$DigestHome = [IO.Path]::GetFullPath($DigestHome)

$plan = [ordered]@{
    ok                   = $true
    dry_run              = [bool]$DryRun
    apply                = [bool]$Apply
    service_name         = $ServiceName
    display_name         = $DisplayName
    depends_on           = $DependsOn
    depends_on_action    = 'declare-only'
    never_touch_ircd     = $true
    repo_root            = $RepoRoot
    jeeves_home          = $JeevesHome
    digest_home          = $DigestHome
    nick                 = $Nick
    homes_distinct       = ($JeevesHome.TrimEnd('\') -ne $DigestHome.TrimEnd('\'))
    chair_entry          = $null
    python               = $null
    nssm                 = $null
    existing_service     = $null
    existing_status      = $null
    task_BobJeeves_chair = $null
    steps                = New-Object System.Collections.ArrayList
    forbidden            = @(
        'Install-BobIrcd.ps1',
        'edit ircd.yaml',
        'sc create/config/start/stop BobIrcd',
        'nssm install BobIrcd'
    )
    errors               = New-Object System.Collections.ArrayList
    warnings             = New-Object System.Collections.ArrayList
}

if (-not $plan.homes_distinct) {
    $plan.ok = $false
    [void]$plan.errors.Add('JeevesHome must differ from DigestHome (BOB_DIGEST_HOME)')
}

$candidates = @(
    (Join-Path $RepoRoot 'src\jeeves\__main__.py'),
    (Join-Path $RepoRoot 'tools\Start-BobJeeves.ps1')
)
if ($ChairEntry) {
    $plan.chair_entry = $ChairEntry
} else {
    foreach ($c in $candidates) {
        if (Test-Path -LiteralPath $c) {
            $plan.chair_entry = $c
            break
        }
    }
}
if (-not $plan.chair_entry) {
    [void]$plan.warnings.Add('No local chair entry yet; production may use agentic_irc until cut-over')
    $plan.chair_entry = 'PENDING: tools/Start-BobJeeves.ps1 or python -m jeeves'
}

if ($PythonPath) {
    $plan.python = $PythonPath
} else {
    foreach ($c in @(
            (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
            (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'),
            (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe')
        )) {
        if (Test-Path -LiteralPath $c) { $plan.python = $c; break }
    }
    if (-not $plan.python) {
        $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -notmatch 'WindowsApps') { $plan.python = $cmd.Source }
    }
}
if (-not $plan.python) {
    [void]$plan.warnings.Add('python.exe not found (required for Apply)')
}

if ($NssmPath) {
    $plan.nssm = $NssmPath
} else {
    foreach ($c in @('C:\ai\ergo\nssm.exe', 'C:\Tools\nssm\nssm.exe')) {
        if (Test-Path -LiteralPath $c) { $plan.nssm = $c; break }
    }
}
if (-not $plan.nssm) {
    [void]$plan.warnings.Add('nssm.exe not found (Apply reads existing path only; never installs Ergo)')
}

$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc) {
    $plan.existing_service = $true
    $plan.existing_status = [string]$svc.Status
    [void]$plan.steps.Add("sc.exe config $ServiceName (repair idempotent)")
} else {
    $plan.existing_service = $false
    [void]$plan.steps.Add("sc.exe create $ServiceName start= auto depend= $DependsOn")
}
[void]$plan.steps.Add("set BOB_DIGEST_HOME=$DigestHome")
[void]$plan.steps.Add("set chair home=$JeevesHome")
[void]$plan.steps.Add('sc.exe failure reset/restart actions')
[void]$plan.steps.Add('Disable scheduled task BobJeeves-chair after service healthy (Apply only)')

$task = Get-ScheduledTask -TaskName 'BobJeeves-chair' -ErrorAction SilentlyContinue
if ($task) {
    $plan.task_BobJeeves_chair = [string]$task.State
    [void]$plan.warnings.Add('Scheduled task BobJeeves-chair still present')
} else {
    $plan.task_BobJeeves_chair = 'absent'
}

# Self-check: this file must not invoke sc against BobIrcd
$self = $MyInvocation.MyCommand.Path
if ($self -and (Test-Path -LiteralPath $self)) {
    $raw = Get-Content -LiteralPath $self -Raw -Encoding UTF8
    if ($raw -match '(?i)&\s*sc\.exe\s+\w+\s+BobIrcd') {
        $plan.ok = $false
        [void]$plan.errors.Add('installer source must not mutate BobIrcd')
    }
}

if ($Apply) {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) {
        $plan.ok = $false
        [void]$plan.errors.Add('Apply requires elevated administrator')
    }
    if ($plan.ok -and $plan.errors.Count -eq 0) {
        $startHelper = Join-Path $RepoRoot 'tools\Start-BobJeeves.ps1'
        if (-not (Test-Path -LiteralPath $startHelper)) {
            [void]$plan.warnings.Add("missing $startHelper - create before production Apply")
        }
        New-Item -ItemType Directory -Force -Path $JeevesHome, $DigestHome | Out-Null
        if (-not $plan.nssm) {
            $plan.ok = $false
            [void]$plan.errors.Add('nssm required for Apply')
        } else {
            $binPath = '"' + $plan.nssm + '"'
            if (-not $svc) {
                & sc.exe create $ServiceName binPath= $binPath start= auto depend= $DependsOn DisplayName= $DisplayName obj= LocalSystem
                if ($LASTEXITCODE -ne 0) {
                    $plan.ok = $false
                    [void]$plan.errors.Add("sc create failed $LASTEXITCODE")
                }
            } else {
                & sc.exe config $ServiceName binPath= $binPath start= auto depend= $DependsOn DisplayName= $DisplayName obj= LocalSystem
                if ($LASTEXITCODE -ne 0) {
                    $plan.ok = $false
                    [void]$plan.errors.Add("sc config failed $LASTEXITCODE")
                }
            }
            if ($plan.ok) {
                & sc.exe description $ServiceName 'gh-Jeeves chair. Depends on BobIrcd. Never manages Ergo.' | Out-Null
                [void]$plan.steps.Add('Apply completed (service registered)')
            }
        }
    }
}

# Convert ArrayLists for JSON
$plan.steps = @($plan.steps)
$plan.errors = @($plan.errors)
$plan.warnings = @($plan.warnings)

if ($Json -or $DryRun) {
    $plan | ConvertTo-Json -Depth 6
}

if (-not $plan.ok) { exit 2 }
exit 0
