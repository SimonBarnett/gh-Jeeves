#Requires -Version 5.1
# Start helper for BobJeeves. Does not touch Ergo/BobIrcd. Prefer -DryRun.
[CmdletBinding()]
param(
    [string]$JeevesHome = $(Join-Path $env:USERPROFILE '.agentic-irc-jeeves'),
    [string]$DigestHome = $(Join-Path $env:USERPROFILE '.agentic-irc-bobiverse'),
    [string]$Nick = 'Jeeves',
    [string]$Python = 'python',
    [string]$RepoRoot,
    [switch]$DryRun
)

if (-not $RepoRoot) { $RepoRoot = Split-Path $PSScriptRoot -Parent }
$env:BOB_DIGEST_HOME = $DigestHome
$env:JEEVES_HOME = $JeevesHome

if ($DryRun) {
    [ordered]@{
        dry_run     = $true
        nick        = $Nick
        jeeves_home = $JeevesHome
        digest_home = $DigestHome
        command     = "$Python -c print-jeeves-start-dry"
        never_touch = @('Ergo', 'BobIrcd', 'ircd.yaml')
    } | ConvertTo-Json
    exit 0
}

# Live start is operator-only; FR workers must use -DryRun.
Write-Output 'Start-BobJeeves: refuse live start without operator -Confirm path; use -DryRun'
exit 3
