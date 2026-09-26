#Requires -Version 5.1
# FR #92: post mrb/verdict check run (reviewing seat). Never self-MRB.
# Example:
#   .\tools\Post-MrbVerdict.ps1 -Repo SimonBarnett/gh-Jeeves -PR 90 `
#     -ReviewerSeat flamingo-43052 -Verdict PASS `
#     -PytestCmd 'python -m pytest tests/ -q' -PytestExit 0 -DurationS 720
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Repo,
    [Parameter(Mandatory = $true)][int]$PR,
    [Parameter(Mandatory = $true)][string]$ReviewerSeat,
    [Parameter(Mandatory = $true)][ValidateSet('PASS', 'FAIL')][string]$Verdict,
    [Parameter(Mandatory = $true)][string]$PytestCmd,
    [Parameter(Mandatory = $true)][int]$PytestExit,
    [Parameter(Mandatory = $true)][double]$DurationS,
    [int]$MinDurationS = 600,
    [string]$AuthorSeat = '',
    [string]$Notes = '',
    [string]$RepoRoot,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
if (-not $RepoRoot) { $RepoRoot = Split-Path $PSScriptRoot -Parent }
$py = Join-Path $RepoRoot 'tools\post_mrb_verdict.py'
$args = @(
    $py,
    '--repo', $Repo,
    '--pr', "$PR",
    '--reviewer-seat', $ReviewerSeat,
    '--verdict', $Verdict,
    '--pytest-cmd', $PytestCmd,
    '--pytest-exit', "$PytestExit",
    '--duration-s', "$DurationS",
    '--min-duration-s', "$MinDurationS"
)
if ($AuthorSeat) { $args += @('--author-seat', $AuthorSeat) }
if ($Notes) { $args += @('--notes', $Notes) }
if ($DryRun) { $args += '--dry-run' }

$python = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $python) { throw 'python.exe not found' }
& $python.Source @args
exit $LASTEXITCODE
