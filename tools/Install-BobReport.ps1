#Requires -Version 5.1
# K8 / FR #9: idempotent plan/install for Windows service BobReport (GIT/digest receiver).
# Replaces ad-hoc profile script BobReport-ionos. Owned in gh-Jeeves repo.
# Default -DryRun. NEVER mutates Ergo/BobIrcd. NEVER runs Apply from FR workers.
[CmdletBinding()]
param(
    [string]$ServiceName = 'BobReport',
    [string]$DisplayName = 'Bobiverse GIT/digest receiver',
    [string]$RepoRoot,
    [string]$DigestHome,
    [string]$Bind = '127.0.0.1',
    [int]$Port = 19781,
    [string]$PythonPath,
    [string]$NssmPath,
    [switch]$DryRun,
    [switch]$Apply,
    [switch]$Json
)

$ErrorActionPreference = 'Stop'
if (-not $Apply) { $DryRun = $true }
if ($Apply -and $DryRun) { throw 'Use either -Apply or -DryRun, not both.' }

if (-not $RepoRoot) { $RepoRoot = Split-Path $PSScriptRoot -Parent }
$RepoRoot = [IO.Path]::GetFullPath($RepoRoot)
if (-not $DigestHome) { $DigestHome = Join-Path $env:USERPROFILE '.agentic-irc-bobiverse' }
$DigestHome = [IO.Path]::GetFullPath($DigestHome)

$plan = [ordered]@{
    ok                = $true
    dry_run           = [bool]$DryRun
    apply             = [bool]$Apply
    service_name      = $ServiceName
    display_name      = $DisplayName
    repo_root         = $RepoRoot
    digest_home       = $DigestHome
    bind              = $Bind
    port              = $Port
    entry             = 'python -m jeeves receiver'
    start_script      = (Join-Path $RepoRoot 'tools\Start-BobReport.ps1')
    python            = $null
    nssm              = $null
    existing_service  = $null
    existing_status   = $null
    ad_hoc_task       = $null
    steps             = New-Object System.Collections.ArrayList
    forbidden         = @(
        'Install-BobIrcd.ps1',
        'edit ircd.yaml',
        'sc create/config BobIrcd',
        'Administrator profile Start-BobReport-ionos.ps1 as source of truth'
    )
    never_touch_ircd  = $true
    replaces          = 'BobReport-ionos ad-hoc scheduled task / profile script'
    errors            = New-Object System.Collections.ArrayList
    warnings          = New-Object System.Collections.ArrayList
}

if ($Bind -notmatch '^(127\.0\.0\.1|localhost|::1)$') {
    [void]$plan.warnings.Add("bind $Bind is not loopback; production default is 127.0.0.1 behind IIS")
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

if (-not (Test-Path -LiteralPath $plan.start_script)) {
    $plan.ok = $false
    [void]$plan.errors.Add("missing Start-BobReport.ps1 at $($plan.start_script)")
}

$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc) {
    $plan.existing_service = $true
    $plan.existing_status = [string]$svc.Status
    [void]$plan.steps.Add("sc.exe config $ServiceName (repair idempotent)")
} else {
    $plan.existing_service = $false
    [void]$plan.steps.Add("sc.exe create $ServiceName start= auto DisplayName= $DisplayName")
}

[void]$plan.steps.Add("BOB_DIGEST_HOME=$DigestHome")
[void]$plan.steps.Add("BOB_REPORT_BIND=$Bind BOB_REPORT_PORT=$Port")
[void]$plan.steps.Add('AppParameters -> Start-BobReport.ps1 (repo-owned)')
[void]$plan.steps.Add('Disable ad-hoc task BobReport-ionos after service healthy (Apply only)')
[void]$plan.steps.Add('IIS reverse-proxy to 127.0.0.1:port remains operator-owned')

$task = Get-ScheduledTask -TaskName 'BobReport-ionos' -ErrorAction SilentlyContinue
if ($task) {
    $plan.ad_hoc_task = [string]$task.State
    [void]$plan.warnings.Add('Scheduled task BobReport-ionos still present; service should replace it')
} else {
    $plan.ad_hoc_task = 'absent'
}

# Self-check: never sc BobIrcd
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
        New-Item -ItemType Directory -Force -Path $DigestHome | Out-Null
        $logDir = Join-Path $DigestHome 'logs'
        New-Item -ItemType Directory -Force -Path $logDir | Out-Null
        if (-not $plan.nssm) {
            $plan.ok = $false
            [void]$plan.errors.Add('nssm required for Apply')
        } elseif (-not $plan.python) {
            $plan.ok = $false
            [void]$plan.errors.Add('python required for Apply')
        } else {
            $ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
            $binPath = '"' + $plan.nssm + '"'
            if (-not $svc) {
                & sc.exe create $ServiceName binPath= $binPath start= auto DisplayName= $DisplayName obj= LocalSystem
                if ($LASTEXITCODE -ne 0) {
                    $plan.ok = $false
                    [void]$plan.errors.Add("sc create failed $LASTEXITCODE")
                }
            } else {
                & sc.exe config $ServiceName binPath= $binPath start= auto DisplayName= $DisplayName obj= LocalSystem
                if ($LASTEXITCODE -ne 0) {
                    $plan.ok = $false
                    [void]$plan.errors.Add("sc config failed $LASTEXITCODE")
                }
            }
            if ($plan.ok) {
                & sc.exe description $ServiceName 'gh-Jeeves GIT/digest HTTP receiver. Loopback. Never manages Ergo.' | Out-Null
                $appParams = "-NoProfile -ExecutionPolicy Bypass -File `"$($plan.start_script)`" -DigestHome `"$DigestHome`" -Bind $Bind -Port $Port -Python `"$($plan.python)`" -RepoRoot `"$RepoRoot`""
                $paramKey = "HKLM:\SYSTEM\CurrentControlSet\Services\$ServiceName\Parameters"
                if (-not (Test-Path $paramKey)) { New-Item -Path $paramKey -Force | Out-Null }
                New-ItemProperty -Path $paramKey -Name Application -Value $ps -PropertyType ExpandString -Force | Out-Null
                New-ItemProperty -Path $paramKey -Name AppParameters -Value $appParams -PropertyType ExpandString -Force | Out-Null
                New-ItemProperty -Path $paramKey -Name AppDirectory -Value $RepoRoot -PropertyType ExpandString -Force | Out-Null
                New-ItemProperty -Path $paramKey -Name AppStdout -Value (Join-Path $logDir 'bobreport-service.log') -PropertyType ExpandString -Force | Out-Null
                New-ItemProperty -Path $paramKey -Name AppStderr -Value (Join-Path $logDir 'bobreport-service.log') -PropertyType ExpandString -Force | Out-Null
                [void]$plan.steps.Add('Apply completed (BobReport service registered)')
            }
        }
    }
}

$plan.steps = @($plan.steps)
$plan.errors = @($plan.errors)
$plan.warnings = @($plan.warnings)

if ($Json -or $DryRun) {
    $plan | ConvertTo-Json -Depth 6
}
if (-not $plan.ok) { exit 2 }
exit 0
