#Requires -Version 5.1
# Start helper for BobJeeves (FR #39). Does not touch Ergo/BobIrcd.
# Default -DryRun prints the plan. -Apply runs python -m jeeves (operator only).
[CmdletBinding()]
param(
    [string]$JeevesHome = $(Join-Path $env:USERPROFILE '.agentic-irc-jeeves'),
    [string]$DigestHome = $(Join-Path $env:USERPROFILE '.agentic-irc-bobiverse'),
    [string]$Nick = 'Jeeves',
    [string]$Python = '',
    [string]$RepoRoot,
    [string]$Mode = 'all',
    [string]$ReceiverPort = '8765',
    [switch]$DryRun,
    [switch]$Apply,
    [switch]$Tls
)

$ErrorActionPreference = 'Stop'
if (-not $Apply) { $DryRun = $true }
if ($Apply -and $DryRun) { throw 'Use either -Apply or -DryRun, not both.' }

if (-not $RepoRoot) { $RepoRoot = Split-Path $PSScriptRoot -Parent }
$RepoRoot = [IO.Path]::GetFullPath($RepoRoot)
$JeevesHome = [IO.Path]::GetFullPath($JeevesHome)
$DigestHome = [IO.Path]::GetFullPath($DigestHome)

if ($JeevesHome.TrimEnd('\').ToLowerInvariant() -eq $DigestHome.TrimEnd('\').ToLowerInvariant()) {
    throw "JeevesHome must differ from DigestHome"
}

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

$argList = @(
    '-m', 'jeeves', $Mode,
    '--nick', $Nick,
    '--jeeves-home', $JeevesHome,
    '--digest-home', $DigestHome,
    '--receiver-port', $ReceiverPort
)
if ($Tls) { $argList += '--tls' }

$plan = [ordered]@{
    dry_run       = [bool]$DryRun
    apply         = [bool]$Apply
    nick          = $Nick
    mode          = $Mode
    jeeves_home   = $JeevesHome
    digest_home   = $DigestHome
    queue_json    = (Join-Path $DigestHome 'queue.json')
    python        = $Python
    args          = $argList
    never_touch   = @('Ergo', 'BobIrcd', 'ircd.yaml')
    entry         = 'python -m jeeves'
}

if ($DryRun) {
    # Also exercise module dry-run
    $env:PYTHONPATH = (Join-Path $RepoRoot 'src')
    $mod = & $Python -m jeeves dry-run --jeeves-home $JeevesHome --digest-home $DigestHome 2>&1
    $plan.module_dry_run = [string]$mod
    $plan | ConvertTo-Json -Depth 6
    exit 0
}

# Live Apply — operator only (FR workers must use -DryRun)
$env:BOB_DIGEST_HOME = $DigestHome
$env:JEEVES_HOME = $JeevesHome
$env:PYTHONPATH = (Join-Path $RepoRoot 'src')
New-Item -ItemType Directory -Force -Path $JeevesHome, $DigestHome | Out-Null
Write-Output "Start-BobJeeves: launching $Python $($argList -join ' ')"
& $Python @argList
exit $LASTEXITCODE
