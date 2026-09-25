#Requires -Version 5.1
# FR #48 / #17: idempotent plan/install for Windows service BobJeeves.
# Full IRC cmdline from config (host/port/TLS/nick/SASL file/receiver 19781).
# NO service dependency on BobIrcd. NEVER mutates Ergo/BobIrcd/ircd.yaml.
# Default -DryRun. -Apply only on machines you administer.
[CmdletBinding()]
param(
    [string]$ServiceName = 'BobJeeves',
    [string]$DisplayName = 'Bobiverse Jeeves (chair+receiver)',
    [string]$RepoRoot,
    [string]$ConfigPath,
    [string]$JeevesHome,
    [string]$DigestHome,
    [string]$Nick,
    [string]$IrcHost,
    [int]$IrcPort = 0,
    [string]$ReceiverPort,
    [string]$PythonPath,
    [string]$NssmPath,
    [switch]$Tls,
    [switch]$NoTls,
    [switch]$DryRun,
    [switch]$Apply,
    [switch]$Json,
    [switch]$Production
)

$ErrorActionPreference = 'Stop'
if (-not $Apply) { $DryRun = $true }
if ($Apply -and $DryRun) { throw 'Use either -Apply or -DryRun, not both.' }

if (-not $RepoRoot) { $RepoRoot = Split-Path $PSScriptRoot -Parent }
$RepoRoot = [IO.Path]::GetFullPath($RepoRoot)

function Expand-EnvPath([string]$p) {
    if ([string]::IsNullOrWhiteSpace($p)) { return $p }
    return [Environment]::ExpandEnvironmentVariables($p.Trim())
}

function Read-BobJeevesConfig([string]$path) {
    if (-not $path -or -not (Test-Path -LiteralPath $path)) { return $null }
    $raw = Get-Content -LiteralPath $path -Raw -Encoding UTF8
    return $raw | ConvertFrom-Json
}

# Load config (example shipped; operator copies to bobjeeves.json)
if (-not $ConfigPath) {
    $cand = @(
        (Join-Path $RepoRoot 'config\bobjeeves.json'),
        (Join-Path $env:USERPROFILE '.agentic-irc-jeeves\bobjeeves.json'),
        (Join-Path $RepoRoot 'config\bobjeeves.example.json')
    )
    foreach ($c in $cand) {
        if (Test-Path -LiteralPath $c) { $ConfigPath = $c; break }
    }
}
$cfg = Read-BobJeevesConfig $ConfigPath

# Defaults from config + production profile
if ($Production -or ($cfg -and $cfg.tls -eq $true)) {
    if (-not $NoTls) { $Tls = $true }
}
if (-not $Nick) {
    if ($cfg -and $cfg.nick) { $Nick = [string]$cfg.nick } else { $Nick = 'Jeeves' }
}
if (-not $IrcHost) {
    if ($cfg -and $cfg.irc_host) { $IrcHost = [string]$cfg.irc_host }
    elseif ($Tls -or $Production) { $IrcHost = 'irc.ntsa.uk' }
    else { $IrcHost = '127.0.0.1' }
}
if ($IrcPort -le 0) {
    if ($cfg -and $cfg.irc_port) { $IrcPort = [int]$cfg.irc_port }
    elseif ($Tls -or $Production) { $IrcPort = 6697 }
    else { $IrcPort = 0 }
}
if (-not $ReceiverPort) {
    if ($cfg -and $cfg.receiver_port) { $ReceiverPort = [string]$cfg.receiver_port }
    else { $ReceiverPort = '19781' }
}
$ReceiverBind = '127.0.0.1'
if ($cfg -and $cfg.receiver_bind) { $ReceiverBind = [string]$cfg.receiver_bind }

if (-not $JeevesHome) {
    if ($cfg -and $cfg.jeeves_home) { $JeevesHome = Expand-EnvPath ([string]$cfg.jeeves_home) }
    else { $JeevesHome = Join-Path $env:USERPROFILE '.agentic-irc-jeeves' }
}
if (-not $DigestHome) {
    if ($cfg -and $cfg.digest_home) { $DigestHome = Expand-EnvPath ([string]$cfg.digest_home) }
    else { $DigestHome = Join-Path $env:USERPROFILE '.agentic-irc-bobiverse' }
}
$JeevesHome = [IO.Path]::GetFullPath((Expand-EnvPath $JeevesHome))
$DigestHome = [IO.Path]::GetFullPath((Expand-EnvPath $DigestHome))

$SaslUser = ''
$SaslPasswordFile = ''
$PasswordFile = ''
if ($cfg) {
    if ($cfg.sasl_user) { $SaslUser = [string]$cfg.sasl_user }
    if ($cfg.sasl_password_file) { $SaslPasswordFile = Expand-EnvPath ([string]$cfg.sasl_password_file) }
    if ($cfg.password_file) { $PasswordFile = Expand-EnvPath ([string]$cfg.password_file) }
    if ($cfg.tls -eq $true -and -not $NoTls) { $Tls = $true }
}

# Build exact python -m jeeves command line (FR #48)
$pyArgs = New-Object System.Collections.ArrayList
[void]$pyArgs.Add('-m')
[void]$pyArgs.Add('jeeves')
[void]$pyArgs.Add('all')
[void]$pyArgs.Add('--nick')
[void]$pyArgs.Add($Nick)
[void]$pyArgs.Add('--jeeves-home')
[void]$pyArgs.Add($JeevesHome)
[void]$pyArgs.Add('--digest-home')
[void]$pyArgs.Add($DigestHome)
[void]$pyArgs.Add('--receiver-bind')
[void]$pyArgs.Add($ReceiverBind)
[void]$pyArgs.Add('--receiver-port')
[void]$pyArgs.Add([string]$ReceiverPort)
[void]$pyArgs.Add('--host')
[void]$pyArgs.Add($IrcHost)
if ($IrcPort -gt 0) {
    [void]$pyArgs.Add('--port')
    [void]$pyArgs.Add([string]$IrcPort)
}
if ($Tls) {
    [void]$pyArgs.Add('--tls')
}
if ($SaslUser) {
    [void]$pyArgs.Add('--sasl-user')
    [void]$pyArgs.Add($SaslUser)
}
# password from file is loaded by start helper into env — never embed secret in sc/nssm args
$serviceCmdline = 'python ' + ($pyArgs -join ' ')

$plan = [ordered]@{
    ok                   = $true
    dry_run              = [bool]$DryRun
    apply                = [bool]$Apply
    topology             = 'combined'
    topology_note        = 'One Windows service BobJeeves runs python -m jeeves all (chair+receiver). Separate BobReport optional only if split is required later.'
    service_name         = $ServiceName
    display_name         = $DisplayName
    depends_on           = $null
    depends_on_action    = 'none'
    never_touch_ircd     = $true
    no_bobircd_dependency = $true
    config_path          = $ConfigPath
    repo_root            = $RepoRoot
    jeeves_home          = $JeevesHome
    digest_home          = $DigestHome
    nick                 = $Nick
    irc_host             = $IrcHost
    irc_port             = $IrcPort
    tls                  = [bool]$Tls
    receiver_bind        = $ReceiverBind
    receiver_port        = [int]$ReceiverPort
    sasl_user            = $SaslUser
    sasl_password_file   = $SaslPasswordFile
    password_file        = $PasswordFile
    homes_distinct       = ($JeevesHome.TrimEnd('\').ToLowerInvariant() -ne $DigestHome.TrimEnd('\').ToLowerInvariant())
    python               = $null
    nssm                 = $null
    existing_service     = $null
    existing_status      = $null
    task_BobJeeves_chair = $null
    service_cmdline      = $serviceCmdline
    python_args          = @($pyArgs)
    steps                = New-Object System.Collections.ArrayList
    forbidden            = @(
        'Install-BobIrcd.ps1',
        'edit ircd.yaml',
        'sc create/config/start/stop BobIrcd',
        'nssm install BobIrcd',
        'depend= BobIrcd'
    )
    errors               = New-Object System.Collections.ArrayList
    warnings             = New-Object System.Collections.ArrayList
}

if (-not $plan.homes_distinct) {
    $plan.ok = $false
    [void]$plan.errors.Add('JeevesHome must differ from DigestHome (BOB_DIGEST_HOME)')
}

if ($Tls -and $IrcPort -le 0) {
    $plan.ok = $false
    [void]$plan.errors.Add('TLS production requires irc_port (e.g. 6697)')
}
if ($Tls -and [string]::IsNullOrWhiteSpace($IrcHost)) {
    $plan.ok = $false
    [void]$plan.errors.Add('TLS production requires irc_host')
}

# Resolve python
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
} else {
    # refresh cmdline with absolute python for display
    $plan.service_cmdline = '"' + $plan.python + '" ' + ($pyArgs -join ' ')
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
    [void]$plan.steps.Add("sc.exe config $ServiceName repair (clear BobIrcd dependency if any)")
} else {
    $plan.existing_service = $false
    [void]$plan.steps.Add("sc.exe create $ServiceName start= auto without BobIrcd dependency")
}
[void]$plan.steps.Add("service cmdline: $($plan.service_cmdline)")
[void]$plan.steps.Add("BOB_DIGEST_HOME=$DigestHome JEEVES_HOME=$JeevesHome")
[void]$plan.steps.Add('IRC connect retries with backoff if Ergo down (no BobIrcd start)')
[void]$plan.steps.Add('Disable scheduled task BobJeeves-chair after service healthy (Apply only)')

$task = Get-ScheduledTask -TaskName 'BobJeeves-chair' -ErrorAction SilentlyContinue
if ($task) {
    $plan.task_BobJeeves_chair = [string]$task.State
    [void]$plan.warnings.Add('Scheduled task BobJeeves-chair still present')
} else {
    $plan.task_BobJeeves_chair = 'absent'
}

# Self-check: never invoke sc against BobIrcd; never set depend= BobIrcd on create/config
$self = $MyInvocation.MyCommand.Path
if ($self -and (Test-Path -LiteralPath $self)) {
    $raw = Get-Content -LiteralPath $self -Raw -Encoding UTF8
    if ($raw -match '(?i)&\s*sc\.exe\s+\w+\s+BobIrcd') {
        $plan.ok = $false
        [void]$plan.errors.Add('installer source must not mutate BobIrcd')
    }
    # Real sc create/config WITH depend= BobIrcd (exclude "no depend=" prose)
    if ($raw -match '(?i)sc\.exe\s+(create|config)[^\r\n]*\bdepend=\s*BobIrcd') {
        $plan.ok = $false
        [void]$plan.errors.Add('installer must not depend= BobIrcd')
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
        New-Item -ItemType Directory -Force -Path $JeevesHome, $DigestHome | Out-Null
        if (-not $plan.python) {
            $plan.ok = $false
            [void]$plan.errors.Add('python required for Apply')
        } elseif (-not $plan.nssm) {
            $plan.ok = $false
            [void]$plan.errors.Add('nssm required for Apply')
        } else {
            $binPath = '"' + $plan.nssm + '"'
            # NO depend= BobIrcd
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
                # clear any prior dependency on BobIrcd
                & sc.exe config $ServiceName depend= / | Out-Null
            }
            if ($plan.ok) {
                & sc.exe description $ServiceName 'gh-Jeeves chair+receiver (python -m jeeves all). No BobIrcd dependency. Never manages Ergo.' | Out-Null
                $appParams = ($pyArgs -join ' ')
                & $plan.nssm set $ServiceName Application $plan.python | Out-Null
                & $plan.nssm set $ServiceName AppDirectory $RepoRoot | Out-Null
                & $plan.nssm set $ServiceName AppParameters $appParams | Out-Null
                $envExtra = @(
                    "BOB_DIGEST_HOME=$DigestHome",
                    "JEEVES_HOME=$JeevesHome",
                    "PYTHONPATH=$RepoRoot\src",
                    "AGENTIC_IRC_HOST=$IrcHost",
                    "AGENTIC_IRC_PORT=$IrcPort"
                )
                if ($SaslUser) { $envExtra += "AGENTIC_IRC_SASL_USER=$SaslUser" }
                if ($SaslPasswordFile) { $envExtra += "AGENTIC_IRC_SASL_PASSWORD_FILE=$SaslPasswordFile" }
                if ($PasswordFile) { $envExtra += "AGENTIC_IRC_PASSWORD_FILE=$PasswordFile" }
                & $plan.nssm set $ServiceName AppEnvironmentExtra $envExtra | Out-Null
                [void]$plan.steps.Add('Apply completed (nssm -> full jeeves all cmdline; no BobIrcd depend)')
            }
        }
    }
}

$plan.steps = @($plan.steps)
$plan.errors = @($plan.errors)
$plan.warnings = @($plan.warnings)
$plan.python_args = @($pyArgs)

if ($Json -or $DryRun) {
    $plan | ConvertTo-Json -Depth 8
}
if (-not $plan.ok) { exit 2 }
exit 0
