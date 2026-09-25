#Requires -Version 5.1
# FR #48 / #39: start helper for BobJeeves. Loads config/bobjeeves.json.
# Topology: combined chair+receiver (python -m jeeves all). Receiver default 19781.
# Does not touch Ergo/BobIrcd. Default -DryRun.
[CmdletBinding()]
param(
    [string]$RepoRoot,
    [string]$ConfigPath,
    [string]$JeevesHome,
    [string]$DigestHome,
    [string]$Nick,
    [string]$IrcHost,
    [int]$IrcPort = 0,
    [string]$ReceiverPort,
    [string]$Python = '',
    [string]$Mode = 'all',
    [switch]$Tls,
    [switch]$NoTls,
    [switch]$Production,
    [switch]$DryRun,
    [switch]$Apply
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

if (-not $ConfigPath) {
    foreach ($c in @(
            (Join-Path $RepoRoot 'config\bobjeeves.json'),
            (Join-Path $env:USERPROFILE '.agentic-irc-jeeves\bobjeeves.json'),
            (Join-Path $RepoRoot 'config\bobjeeves.example.json')
        )) {
        if (Test-Path -LiteralPath $c) { $ConfigPath = $c; break }
    }
}
$cfg = $null
if ($ConfigPath -and (Test-Path -LiteralPath $ConfigPath)) {
    $cfg = (Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8) | ConvertFrom-Json
}

if ($Production -or ($cfg -and $cfg.tls -eq $true)) {
    if (-not $NoTls) { $Tls = $true }
}
if (-not $Nick) { $Nick = if ($cfg -and $cfg.nick) { [string]$cfg.nick } else { 'Jeeves' } }
if (-not $IrcHost) {
    if ($cfg -and $cfg.irc_host) { $IrcHost = [string]$cfg.irc_host }
    elseif ($Tls) { $IrcHost = 'irc.ntsa.uk' }
    else { $IrcHost = '127.0.0.1' }
}
if ($IrcPort -le 0) {
    if ($cfg -and $cfg.irc_port) { $IrcPort = [int]$cfg.irc_port }
    elseif ($Tls) { $IrcPort = 6697 }
}
if (-not $ReceiverPort) {
    $ReceiverPort = if ($cfg -and $cfg.receiver_port) { [string]$cfg.receiver_port } else { '19781' }
}
$ReceiverBind = if ($cfg -and $cfg.receiver_bind) { [string]$cfg.receiver_bind } else { '127.0.0.1' }

if (-not $JeevesHome) {
    $JeevesHome = if ($cfg -and $cfg.jeeves_home) { Expand-EnvPath ([string]$cfg.jeeves_home) }
    else { Join-Path $env:USERPROFILE '.agentic-irc-jeeves' }
}
if (-not $DigestHome) {
    $DigestHome = if ($cfg -and $cfg.digest_home) { Expand-EnvPath ([string]$cfg.digest_home) }
    else { Join-Path $env:USERPROFILE '.agentic-irc-bobiverse' }
}
$JeevesHome = [IO.Path]::GetFullPath((Expand-EnvPath $JeevesHome))
$DigestHome = [IO.Path]::GetFullPath((Expand-EnvPath $DigestHome))
if ($JeevesHome.TrimEnd('\').ToLowerInvariant() -eq $DigestHome.TrimEnd('\').ToLowerInvariant()) {
    throw 'JeevesHome must differ from DigestHome'
}

# Load SASL password from file into env (never print)
$saslUser = if ($cfg -and $cfg.sasl_user) { [string]$cfg.sasl_user } else { '' }
$saslFile = if ($cfg -and $cfg.sasl_password_file) { Expand-EnvPath ([string]$cfg.sasl_password_file) } else { '' }
if ($saslFile -and (Test-Path -LiteralPath $saslFile)) {
    $env:AGENTIC_IRC_SASL_PASSWORD = (Get-Content -LiteralPath $saslFile -TotalCount 1).Trim()
}
if ($saslUser) { $env:AGENTIC_IRC_SASL_USER = $saslUser }

if (-not $Python) {
    foreach ($c in @(
            (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
            (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'),
            'python'
        )) {
        if ($c -eq 'python') { $Python = $c; break }
        if (Test-Path -LiteralPath $c) { $Python = $c; break }
    }
}

$argList = New-Object System.Collections.ArrayList
[void]$argList.AddRange(@('-m', 'jeeves', $Mode, '--nick', $Nick,
        '--jeeves-home', $JeevesHome, '--digest-home', $DigestHome,
        '--receiver-bind', $ReceiverBind, '--receiver-port', $ReceiverPort,
        '--host', $IrcHost))
if ($IrcPort -gt 0) {
    [void]$argList.Add('--port')
    [void]$argList.Add([string]$IrcPort)
}
if ($Tls) { [void]$argList.Add('--tls') }
if ($saslUser) {
    [void]$argList.Add('--sasl-user')
    [void]$argList.Add($saslUser)
}

$plan = [ordered]@{
    dry_run        = [bool]$DryRun
    apply          = [bool]$Apply
    topology       = 'combined'
    config_path    = $ConfigPath
    nick           = $Nick
    mode           = $Mode
    irc_host       = $IrcHost
    irc_port       = $IrcPort
    tls            = [bool]$Tls
    receiver_bind  = $ReceiverBind
    receiver_port  = $ReceiverPort
    jeeves_home    = $JeevesHome
    digest_home    = $DigestHome
    queue_json     = (Join-Path $DigestHome 'queue.json')
    python         = $Python
    args           = @($argList)
    service_cmdline = ('{0} {1}' -f $Python, ($argList -join ' '))
    never_touch    = @('Ergo', 'BobIrcd', 'ircd.yaml')
    no_bobircd_dependency = $true
    entry          = 'python -m jeeves all'
}

if ($DryRun) {
    $plan | ConvertTo-Json -Depth 6
    exit 0
}

$env:BOB_DIGEST_HOME = $DigestHome
$env:JEEVES_HOME = $JeevesHome
$env:PYTHONPATH = (Join-Path $RepoRoot 'src')
$env:AGENTIC_IRC_HOST = $IrcHost
if ($IrcPort -gt 0) { $env:AGENTIC_IRC_PORT = [string]$IrcPort }
New-Item -ItemType Directory -Force -Path $JeevesHome, $DigestHome | Out-Null
Write-Output "Start-BobJeeves: $($plan.service_cmdline)"
& $Python @argList
exit $LASTEXITCODE
