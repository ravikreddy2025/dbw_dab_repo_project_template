<#
.SYNOPSIS
    Builds the shared wheels into a bundle dist/ folder.

.DESCRIPTION
    Two shared wheels reach every job:
      libs/dab_common   config, audit, quality, recon  - needed by EVERY bundle
      libs/edp_landing  registry, kafka, oracle        - the landing framework

    DABs requires library paths to live inside the bundle root, so they are copied
    into each bundle rather than referenced across the repo. This is the local
    equivalent of the build-wheels.yml pipeline step - run it before
    `databricks bundle deploy`, or the deploy fails with a missing wheel.

    Deploy-Sandbox.ps1 calls this for you; you only need it directly if you are
    running `databricks bundle deploy` by hand.

.PARAMETER Bundle
    Bundle folder name under bundles/. Omit to build for every bundle.

.EXAMPLE
    .\scripts\dev\Build-Wheels.ps1 -Bundle us1

.EXAMPLE
    .\scripts\dev\Build-Wheels.ps1
#>
[CmdletBinding()]
param(
    [ValidateSet('_platform', 'landing', 'recon', 'us1', 'us2', 'us3', 'us4', 'us5')]
    [string] $Bundle
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

$allBundles = @('_platform', 'landing', 'recon', 'us1', 'us2', 'us3', 'us4', 'us5')
$targets = if ($Bundle) { @($Bundle) } else { $allBundles }

# Which shared wheels each bundle embeds. Not every bundle gets every wheel:
# installing edp_recon into a use-case bundle would be the coupling this repo
# exists to avoid.
$libsByBundle = @{
    '_platform' = @('dab_common')
    'landing'   = @('dab_common', 'edp_landing')
    'recon'     = @('dab_common', 'edp_recon')
    'us1'       = @('dab_common')
    'us2'       = @('dab_common')
    'us3'       = @('dab_common')
    'us4'       = @('dab_common')
    'us5'       = @('dab_common')
}

# `python -m build` needs the `build` package. Fail with a useful message rather
# than a ModuleNotFoundError traceback.
try {
    & python -c "import build" 2>$null
    if ($LASTEXITCODE -ne 0) { throw }
}
catch {
    Write-Host "The 'build' package is missing. Install the dev tooling first:" -ForegroundColor Yellow
    Write-Host "    pip install -r requirements-dev.txt" -ForegroundColor Yellow
    exit 1
}

foreach ($name in $targets) {
    $bundleDir = Join-Path $repoRoot "bundles\$name"
    $distDir = Join-Path $bundleDir 'dist'

    if (-not (Test-Path $bundleDir)) {
        Write-Warning "bundles\$name does not exist - skipping"
        continue
    }

    $libs = $libsByBundle[$name] -join ', '
    Write-Host "Building $libs -> bundles\$name\dist" -ForegroundColor Cyan

    # Wipe dist/ first. A stale wheel from a previous version still matches the
    # ./dist/<name>-*.whl glob and could be the one that gets installed.
    if (Test-Path $distDir) { Remove-Item -Recurse -Force $distDir }
    New-Item -ItemType Directory -Path $distDir | Out-Null

    foreach ($lib in $libsByBundle[$name]) {
        & python -m build --wheel --outdir $distDir (Join-Path $repoRoot "libs\$lib")
        if ($LASTEXITCODE -ne 0) { throw "wheel build failed for libs\$lib" }
    }

    Get-ChildItem $distDir -Filter *.whl | ForEach-Object {
        Write-Host "    $($_.Name)" -ForegroundColor Green
    }
}

Write-Host ''
Write-Host 'Done. The bundle own wheel is built by the bundle at deploy time' -ForegroundColor Gray
Write-Host '(the artifacts: block in databricks.yml) - you do not build it here.' -ForegroundColor Gray
