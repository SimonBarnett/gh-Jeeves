#Requires -Version 5.1
# Apply the MRB #145 brace fix into .github/workflows/mrb-seat-trailer.yml.
# Needs a token with the `workflow` scope to push the result.
[CmdletBinding()]
param(
    [string]$RepoRoot = (Split-Path $PSScriptRoot -Parent),
    [switch]$Write
)
$ErrorActionPreference = 'Stop'
$src = Join-Path $RepoRoot 'tools\mrb-seat-trailer.yml.desired'
$dst = Join-Path $RepoRoot '.github\workflows\mrb-seat-trailer.yml'
if (-not (Test-Path -LiteralPath $src)) { throw "missing $src" }
$desired = Get-Content -LiteralPath $src -Raw
if ($desired -notmatch "pull_request\.body \|\| '' \}\}") {
    throw 'desired workflow missing balanced ${{ ... || '''' }} expression'
}
if (-not $Write) {
    Write-Host "Dry-run OK. Re-run with -Write to overwrite $dst"
    Write-Host "Then commit+push with a token that has the workflow scope."
    exit 0
}
New-Item -ItemType Directory -Force -Path (Split-Path $dst) | Out-Null
Set-Content -LiteralPath $dst -Value $desired -Encoding utf8 -NoNewline
Write-Host "Wrote $dst"
