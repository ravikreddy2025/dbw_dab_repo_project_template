<#
.SYNOPSIS
    Tear down your personal sandbox deployment of a bundle.

.DESCRIPTION
    Removes the jobs, pipelines and synced files that `bundle deploy --target dev`
    created under your user. It does NOT drop your sandbox schemas or tables -
    that is deliberate, because losing a day of test data to a typo is worse than
    a stale schema. Drop those yourself if you want them gone:

        DROP SCHEMA IF EXISTS edp_curated_nonprod.<you>_us1 CASCADE;
        DROP SCHEMA IF EXISTS edp_landing_nonprod.<you>_us1 CASCADE;
        DROP SCHEMA IF EXISTS edp_datamart_nonprod.<you>_us1 CASCADE;
        DROP SCHEMA IF EXISTS edp_ops_nonprod.<you>_audit CASCADE;

    Run this when you finish a feature. Ten developers who never clean up leave a
    workspace nobody can navigate.

.PARAMETER Bundle
    Bundle folder name under bundles/.

.EXAMPLE
    .\scripts\dev\Destroy-Sandbox.ps1 -Bundle us1
#>
[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
param(
    [Parameter(Mandatory)]
    [ValidateSet('landing', 'recon', 'us1', 'us2', 'us3', 'us4', 'us5')]
    [string] $Bundle
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

Push-Location (Join-Path $repoRoot "bundles\$Bundle")
try {
    Write-Host "These resources will be destroyed from your sandbox:" -ForegroundColor Yellow
    & databricks bundle summary --target dev

    if ($PSCmdlet.ShouldProcess("bundles\$Bundle (target: dev)", 'databricks bundle destroy')) {
        & databricks bundle destroy --target dev --auto-approve
        if ($LASTEXITCODE -ne 0) { throw 'bundle destroy failed' }
        Write-Host 'Sandbox torn down.' -ForegroundColor Green
        Write-Host 'Your sandbox schemas and tables were NOT dropped - see the help for this script.' -ForegroundColor Gray
    }
}
finally {
    Pop-Location
}
