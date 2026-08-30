<#
.SYNOPSIS
    Run everything the PR-validation pipeline runs, locally, before you push.

.DESCRIPTION
    Same checks as .azure-pipelines/ci-pr-validation.yml, in the same order:
    lint, unit tests, the structural bundle check and the cross-reference audit.
    Running this first turns a ten-minute round trip through the build agent into
    a ten-second one.

    Add -Spark to also run the pyspark-backed tests, which the build agent skips.

.EXAMPLE
    .\scripts\dev\Validate-All.ps1

.EXAMPLE
    .\scripts\dev\Validate-All.ps1 -Spark
#>
[CmdletBinding()]
param(
    [switch] $Spark
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Push-Location $repoRoot

$failed = @()

function Invoke-Check {
    param([string] $Name, [scriptblock] $Body)
    Write-Host "`n=== $Name ===" -ForegroundColor Cyan
    & $Body
    if ($LASTEXITCODE -ne 0) {
        $script:failed += $Name
        Write-Host "FAILED: $Name" -ForegroundColor Red
    }
}

try {
    Invoke-Check 'Lint (ruff)' { & python -m ruff check . }

    if ($Spark) {
        # No marker filter: run everything, including the pyspark-backed tests
        # that the build agent skips because it has no Spark and no Java.
        Invoke-Check 'Unit tests (including Spark)' { & python -m pytest -q }
    }
    else {
        Invoke-Check 'Unit tests (excluding Spark)' { & python -m pytest -q -m 'not integration' }
    }

    Invoke-Check 'Bundle structure' {
        & python scripts/ci/validate_bundle_yaml.py `
            bundles/_platform bundles/landing bundles/recon `
            bundles/us1 bundles/us2 bundles/us3 bundles/us4 bundles/us5
    }

    Invoke-Check 'Cross-reference audit' { & python scripts/ci/check_bundle_references.py }

    Write-Host ''
    if ($failed.Count -gt 0) {
        Write-Host "$($failed.Count) check(s) failed: $($failed -join ', ')" -ForegroundColor Red
        exit 1
    }
    Write-Host 'All checks passed. Safe to push.' -ForegroundColor Green
}
finally {
    Pop-Location
}
