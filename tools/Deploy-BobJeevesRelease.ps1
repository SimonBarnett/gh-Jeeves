#Requires -Version 5.1
# K5 / FR #6: deploy BobJeeves from a tagged gh-Jeeves release (not a dirty worktree).
# Default -DryRun. Never touches Ergo/BobIrcd. Never live-installs from FR workers.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$Tag = '',
    [string]$RepoRoot,
    [string]$DeployRoot,
    [string]$RemoteUrl = 'https://github.com/SimonBarnett/gh-Jeeves.git',
    [switch]$DryRun,
    [switch]$Apply,
    [switch]$Json
)

$ErrorActionPreference = 'Stop'
if (-not $Apply) { $DryRun = $true }
if ($Apply -and $DryRun) { throw 'Use either -Apply or -DryRun, not both.' }

if (-not $RepoRoot) { $RepoRoot = Split-Path $PSScriptRoot -Parent }
$RepoRoot = [IO.Path]::GetFullPath($RepoRoot)
if (-not $DeployRoot) {
    $DeployRoot = Join-Path $env:USERPROFILE 'jeeves-releases'
}

function Normalize-Tag([string]$t) {
    if ([string]::IsNullOrWhiteSpace($t)) { return '' }
    $s = $t.Trim()
    if ($s -match '^v') { return $s }
    if ($s -match '^\d+\.\d+') { return "v$s" }
    return $s
}

# Prefer explicit Tag, else VERSION file, else refuse
if (-not $Tag) {
    $verFile = Join-Path $RepoRoot 'VERSION'
    if (Test-Path -LiteralPath $verFile) {
        $Tag = (Get-Content -LiteralPath $verFile -TotalCount 1)
    }
}
$Tag = Normalize-Tag $Tag

$plan = [ordered]@{
    ok              = $true
    dry_run         = [bool]$DryRun
    apply           = [bool]$Apply
    tag             = $Tag
    remote_url      = $RemoteUrl
    repo_root       = $RepoRoot
    deploy_root     = $DeployRoot
    deploy_path     = ''
    steps           = New-Object System.Collections.ArrayList
    forbidden       = @('edit ircd.yaml', 'Install-BobIrcd', 'hotpatch live worktree without tag')
    never_touch_ircd = $true
    errors          = New-Object System.Collections.ArrayList
    warnings        = New-Object System.Collections.ArrayList
    version_env     = 'JEEVES_RELEASE_TAG'
}

if (-not $Tag) {
    $plan.ok = $false
    [void]$plan.errors.Add('Tag required (pass -Tag vX.Y.Z or ship VERSION file)')
}

$dest = Join-Path $DeployRoot $Tag
$plan.deploy_path = $dest

[void]$plan.steps.Add("mkdir $DeployRoot")
[void]$plan.steps.Add("git clone --depth 1 --branch $Tag $RemoteUrl $dest")
[void]$plan.steps.Add("write VERSION + EXPECTED_RELEASE = $Tag")
[void]$plan.steps.Add("set JEEVES_RELEASE_TAG=$Tag for BobJeeves service")
[void]$plan.steps.Add('Install-BobJeeves.ps1 -DryRun then operator -Apply')
[void]$plan.steps.Add('POST version to digest webhook (jeeves_version)')
[void]$plan.steps.Add('drift check: running tag == expected tag')

if ($Apply -and $plan.ok) {
    New-Item -ItemType Directory -Force -Path $DeployRoot | Out-Null
    if (-not (Test-Path -LiteralPath $dest)) {
        & git clone --depth 1 --branch $Tag $RemoteUrl $dest
        if ($LASTEXITCODE -ne 0) {
            $plan.ok = $false
            [void]$plan.errors.Add("git clone failed $LASTEXITCODE")
        }
    }
    if ($plan.ok) {
        $v = $Tag.TrimStart('v')
        Set-Content -LiteralPath (Join-Path $dest 'VERSION') -Value $v -Encoding ascii
        Set-Content -LiteralPath (Join-Path $dest 'EXPECTED_RELEASE') -Value $Tag -Encoding ascii
        [void]$plan.steps.Add('Apply clone complete')
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
