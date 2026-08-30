<#
.SYNOPSIS
    Deploy a bundle to your personal sandbox in the nonprod workspace.

.DESCRIPTION
    The one command a developer runs all day. It builds the shared wheel, then
    deploys the bundle to the `dev` target, which:

      * names every job "[dev <you>] ..." so ten people share one workspace
      * writes to edp_<layer>_nonprod.<you>_<use_case> - your own data, in all
        four catalogs including ops
      * leaves every schedule PAUSED, so nothing of yours fires on a timer
      * roots files under /Workspace/Users/<you>/.bundle/...

    Nothing you deploy here can affect a colleague or a shared environment.

.PARAMETER Bundle
    Bundle folder name under bundles/.

.PARAMETER Run
    Job key to run immediately after deploying.

.PARAMETER ClusterId
    Reuse an existing all-purpose cluster instead of creating job clusters.
    Cuts the deploy-test loop from minutes to seconds. Only works in development
    mode - production targets reject it by design.

.PARAMETER WhatIf
    Validate only; deploy nothing.

.EXAMPLE
    .\scripts\dev\Deploy-Sandbox.ps1 -Bundle us1

.EXAMPLE
    .\scripts\dev\Deploy-Sandbox.ps1 -Bundle us2 -Run us2_curated

.EXAMPLE
    .\scripts\dev\Deploy-Sandbox.ps1 -Bundle us1 -ClusterId 0812-164512-abc123de
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)]
    [ValidateSet('_platform', 'landing', 'us1', 'us2', 'us3', 'us4', 'us5')]
    [string] $Bundle,

    [string] $Run,
    [string] $ClusterId
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$bundleDir = Join-Path $repoRoot "bundles\$Bundle"

if ($Bundle -eq '_platform') {
    Write-Host 'The _platform bundle has no personal dev target.' -ForegroundColor Yellow
    Write-Host 'Shared catalogs and schemas are deployed by the cd-platform pipeline only.' -ForegroundColor Yellow
    Write-Host 'See docs/06-environments-and-access.md.' -ForegroundColor Yellow
    exit 1
}

# -- 1. Is the CLI there, and is it the right one? ----------------------------
$cli = Get-Command databricks -ErrorAction SilentlyContinue
if (-not $cli) {
    Write-Host 'The Databricks CLI is not on PATH.' -ForegroundColor Red
    Write-Host 'Install it with:  winget install Databricks.DatabricksCLI' -ForegroundColor Yellow
    Write-Host 'NOTE: `pip install databricks-cli` installs the OLD v0.17 CLI,' -ForegroundColor Yellow
    Write-Host 'which has no `bundle` command. See docs/03-developer-guide.md.' -ForegroundColor Yellow
    exit 1
}
$version = (& databricks --version) -join ''
Write-Host "Databricks CLI: $version" -ForegroundColor Gray

# -- 2. Are we authenticated? --------------------------------------------------
& databricks current-user me --output json 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Not authenticated to the nonprod workspace.' -ForegroundColor Red
    Write-Host 'Run:  databricks auth login --host https://adb-0000000000000001.1.azuredatabricks.net' -ForegroundColor Yellow
    exit 1
}

# -- 3. Build the shared wheel -------------------------------------------------
& (Join-Path $PSScriptRoot 'Build-Wheels.ps1') -Bundle $Bundle
if ($LASTEXITCODE -ne 0) { exit 1 }

Push-Location $bundleDir
try {
    # -- 4. Validate -----------------------------------------------------------
    Write-Host "`nValidating bundles\$Bundle against target 'dev'..." -ForegroundColor Cyan
    & databricks bundle validate --target dev
    if ($LASTEXITCODE -ne 0) { throw 'bundle validate failed' }

    if ($WhatIfPreference) {
        Write-Host 'WhatIf: validation passed; stopping before deploy.' -ForegroundColor Yellow
        return
    }

    # -- 5. Deploy -------------------------------------------------------------
    $deployArgs = @('bundle', 'deploy', '--target', 'dev')
    if ($ClusterId) {
        $deployArgs += @('--cluster-id', $ClusterId)
        Write-Host "Overriding job clusters with all-purpose cluster $ClusterId" -ForegroundColor Gray
    }

    Write-Host "Deploying to your sandbox..." -ForegroundColor Cyan
    & databricks @deployArgs
    if ($LASTEXITCODE -ne 0) { throw 'bundle deploy failed' }

    Write-Host "`nDeployed. Your resources:" -ForegroundColor Green
    & databricks bundle summary --target dev

    # -- 6. Optionally run -----------------------------------------------------
    if ($Run) {
        Write-Host "`nRunning $Run..." -ForegroundColor Cyan
        & databricks bundle run $Run --target dev
        if ($LASTEXITCODE -ne 0) { throw "job run '$Run' failed" }
    }

    Write-Host ''
    Write-Host 'Reminder: schedules are PAUSED in a sandbox. Trigger jobs with:' -ForegroundColor Gray
    Write-Host "    databricks bundle run <job_key> --target dev" -ForegroundColor Gray
    Write-Host 'Tear your sandbox down when you are finished with the feature:' -ForegroundColor Gray
    Write-Host "    .\scripts\dev\Destroy-Sandbox.ps1 -Bundle $Bundle" -ForegroundColor Gray
}
finally {
    Pop-Location
}
