#Requires -Version 5.1
# Start helper for BobReport receiver service (FR #9 / K8). Repo-owned; not profile script.
# Does not touch Ergo/BobIrcd. Prefer -DryRun from CI / FR workers.
[CmdletBinding()]
param(
    [string]$DigestHome = $(Join-Path $env:USERPROFILE '.agentic-irc-bobiverse'),
    [string]$Bind = '127.0.0.1',
    [int]$Port = 19781,
    [string]$Python = 'python',
    [string]$RepoRoot,
    [switch]$DryRun
)

if (-not $RepoRoot) { $RepoRoot = Split-Path $PSScriptRoot -Parent }
$env:BOB_DIGEST_HOME = $DigestHome
$env:BOB_REPORT_BIND = $Bind
$env:BOB_REPORT_PORT = "$Port"
$env:PYTHONPATH = (Join-Path $RepoRoot 'src')

if ($DryRun) {
    [ordered]@{
        dry_run     = $true
        digest_home = $DigestHome
        bind        = $Bind
        port        = $Port
        command     = "$Python -m jeeves receiver --digest-home $DigestHome --receiver-bind $Bind --receiver-port $Port"
        never_touch = @('Ergo', 'BobIrcd', 'ircd.yaml')
        replaces    = 'BobReport-ionos ad-hoc launcher'
    } | ConvertTo-Json
    exit 0
}

# Live start is operator-only via the Windows service AppParameters.
Write-Output 'Start-BobReport: use Windows service BobReport or: python -m jeeves receiver'
& $Python -m jeeves receiver --digest-home $DigestHome --receiver-bind $Bind --receiver-port $Port
exit $LASTEXITCODE
