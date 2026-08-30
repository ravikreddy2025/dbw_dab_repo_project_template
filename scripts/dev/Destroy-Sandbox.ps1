<#
.SYNOPSIS
    Tear down your personal sandbox: deployed resources, and optionally the data.

.DESCRIPTION
    A sandbox has TWO halves, and they are removed by different mechanisms:

      1. DEPLOYED RESOURCES - jobs, pipelines, synced files.
         Removed by `databricks bundle destroy`. This script does that.

      2. SCHEMAS, TABLES AND VOLUMES - created at RUNTIME by ensure_schema(),
         so the bundle has no record of them and `bundle destroy` cannot touch
         them. Removed only by dropping the schemas. Use -IncludeData.

    Half two is the half people forget. One developer across five use cases
    accumulates 19 schemas; ten developers who never clean up leave ~190, and
    the metastore becomes unnavigable.

.PARAMETER Bundle
    Bundle folder name under bundles/. Use -All for every bundle you deploy.

.PARAMETER All
    Tear down every deployable bundle, not just one.

.PARAMETER IncludeData
    ALSO drop your sandbox schemas (with CASCADE) in all four catalogs.
    Prompts per schema unless -Force. This is destructive and irreversible.

.PARAMETER Force
    Skip the per-schema confirmation. Intended for offboarding, where a human has
    already decided. Think before using it on your own account.

.PARAMETER WhatIf
    Show what would be destroyed and print the DROP statements, changing nothing.
    Run this first.

.EXAMPLE
    .\scripts\dev\Destroy-Sandbox.ps1 -Bundle us1
    Removes the deployed jobs. Leaves your data alone.

.EXAMPLE
    .\scripts\dev\Destroy-Sandbox.ps1 -Bundle us1 -IncludeData -WhatIf
    Shows exactly what would go, including the DROP statements. Changes nothing.

.EXAMPLE
    .\scripts\dev\Destroy-Sandbox.ps1 -All -IncludeData
    Full cleanup across every bundle. What you run when a feature is merged.

.NOTES
    Streaming checkpoints live on a UC volume inside the landing schema, so
    dropping the schema removes them. STOP ANY RUNNING STREAM FIRST - dropping a
    checkpoint out from under a live query leaves it unable to restart cleanly.
#>
[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
param(
    [ValidateSet('landing', 'recon', 'us1', 'us2', 'us3', 'us4', 'us5')]
    [string] $Bundle,

    [switch] $All,
    [switch] $IncludeData,
    [switch] $Force
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

if (-not $Bundle -and -not $All) {
    Write-Host 'Specify -Bundle <name> or -All.' -ForegroundColor Yellow
    exit 1
}

$deployable = @('landing', 'recon', 'us1', 'us2', 'us3', 'us4', 'us5')
$targets = if ($All) { $deployable } else { @($Bundle) }

# -- who am I? The prefix comes from the platform, never from a parameter ------
$me = & databricks current-user me --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Not authenticated. Run: databricks auth login --host <nonprod>' }
$shortName = ($me.userName -split '@')[0] -replace '[^A-Za-z0-9_]', '_'
$prefix = "${shortName}_"

Write-Host "Sandbox owner : $($me.userName)" -ForegroundColor Cyan
Write-Host "Schema prefix : $prefix" -ForegroundColor Cyan
Write-Host ''

# =============================================================================
# 1. Deployed resources
# =============================================================================
foreach ($name in $targets) {
    $bundleDir = Join-Path $repoRoot "bundles\$name"
    if (-not (Test-Path (Join-Path $bundleDir 'databricks.yml'))) {
        Write-Warning "bundles\$name not found - skipping"
        continue
    }

    Push-Location $bundleDir
    try {
        Write-Host "=== bundles\$name ===" -ForegroundColor Cyan
        & databricks bundle summary --target dev 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Host '  nothing deployed' -ForegroundColor Gray
            continue
        }

        if ($PSCmdlet.ShouldProcess("bundles\$name (target: dev)", 'databricks bundle destroy')) {
            & databricks bundle destroy --target dev --auto-approve
            if ($LASTEXITCODE -ne 0) { throw "bundle destroy failed for $name" }
            Write-Host '  resources destroyed' -ForegroundColor Green
        }
    }
    finally { Pop-Location }
}

# =============================================================================
# 2. Data - schemas created at runtime, invisible to `bundle destroy`
# =============================================================================
if (-not $IncludeData) {
    Write-Host ''
    Write-Host 'Your sandbox SCHEMAS and TABLES were not touched.' -ForegroundColor Yellow
    Write-Host 'They were created at runtime, so the bundle has no record of them.' -ForegroundColor Gray
    Write-Host 'To see and remove them:' -ForegroundColor Gray
    Write-Host "    .\scripts\dev\Destroy-Sandbox.ps1 -All -IncludeData -WhatIf" -ForegroundColor Gray
    return
}

# Mirrors dab_common.config: three data catalogs keyed by use case, plus the
# four functional ops schemas. Kept in step by Validate-All.ps1.
$catalogPrefix = 'edp'
$env_ = 'nonprod'                        # sandboxes only ever live in nonprod
$useCases = @('us1', 'us2', 'us3', 'us4', 'us5')
$opsSchemas = @('audit', 'config', 'logs', 'recon')

$schemas = @()
foreach ($layer in @('landing', 'curated', 'datamart')) {
    foreach ($uc in $useCases) {
        $schemas += "${catalogPrefix}_${layer}_${env_}.${prefix}${uc}"
    }
}
foreach ($ops in $opsSchemas) {
    $schemas += "${catalogPrefix}_ops_${env_}.${prefix}${ops}"
}

Write-Host ''
Write-Host '=== sandbox schemas ===' -ForegroundColor Cyan
Write-Host 'Only schemas that actually exist are dropped; the rest are skipped.' -ForegroundColor Gray
Write-Host ''

foreach ($schema in $schemas) {
    $catalog, $schemaName = $schema -split '\.', 2

    # Does it exist? Never issue a DROP for something that was never created.
    $exists = & databricks api get "/api/2.1/unity-catalog/schemas/$schema" 2>$null
    if ($LASTEXITCODE -ne 0) { continue }

    $sql = "DROP SCHEMA IF EXISTS $schema CASCADE"

    if ($WhatIfPreference) {
        Write-Host "  WhatIf: $sql" -ForegroundColor Yellow
        continue
    }

    if (-not $Force) {
        $answer = Read-Host "  Drop $schema and everything in it? [y/N]"
        if ($answer -ne 'y') { Write-Host '    skipped' -ForegroundColor Gray; continue }
    }

    if ($PSCmdlet.ShouldProcess($schema, 'DROP SCHEMA ... CASCADE')) {
        Write-Host "  $sql" -ForegroundColor Yellow
        # No CLI verb drops a schema, so this goes through a SQL warehouse.
        # Requires SQL_WAREHOUSE_ID; see docs/03-developer-guide.md#tearing-down.
        if (-not $env:SQL_WAREHOUSE_ID) {
            Write-Host '    SQL_WAREHOUSE_ID not set - run the statement above yourself.' -ForegroundColor Yellow
            continue
        }
        & databricks api post /api/2.0/sql/statements `
            --json "{`"warehouse_id`":`"$env:SQL_WAREHOUSE_ID`",`"statement`":`"$sql`",`"wait_timeout`":`"30s`"}" | Out-Null
        if ($LASTEXITCODE -eq 0) { Write-Host '    dropped' -ForegroundColor Green }
        else { Write-Warning "    failed - run it by hand in a SQL editor" }
    }
}

Write-Host ''
Write-Host 'Done. Verify nothing of yours is left:' -ForegroundColor Gray
Write-Host "    SELECT * FROM system.information_schema.schemata" -ForegroundColor Gray
Write-Host "    WHERE schema_name LIKE '${prefix}%';" -ForegroundColor Gray
