# Watch-JeevesHandoff.ps1 — thin wrapper around tools/watch_jeeves_handoff.py
# Local alerts only; never speaks on IRC. Does not touch Ergo/BobIrcd.
[CmdletBinding()]
param(
    [switch]$Once,
    [double]$LoopSeconds = 0,
    [switch]$Json,
    [switch]$QuietOk,
    [string]$DigestHome = '',
    [string]$JeevesHome = ''
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path -LiteralPath (Join-Path $repo 'src\jeeves'))) {
    $repo = Split-Path -Parent $MyInvocation.MyCommand.Path | Split-Path -Parent
}
$py = Join-Path $repo 'tools\watch_jeeves_handoff.py'
if (-not (Test-Path -LiteralPath $py)) { throw "missing $py" }
$argv = @($py)
if ($Once -or $LoopSeconds -le 0) { $argv += '--once' }
elseif ($LoopSeconds -gt 0) { $argv += @('--loop', "$LoopSeconds") }
if ($Json) { $argv += '--json' }
if ($QuietOk) { $argv += '--quiet-ok' }
if ($DigestHome) { $argv += @('--digest-home', $DigestHome) }
if ($JeevesHome) { $argv += @('--jeeves-home', $JeevesHome) }
$env:PYTHONPATH = (Join-Path $repo 'src')
& python @argv
exit $LASTEXITCODE
