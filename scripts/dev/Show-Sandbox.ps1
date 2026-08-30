<#
.SYNOPSIS
    Show everything your sandbox currently occupies, across all bundles.

.DESCRIPTION
    Answers "what have I actually got deployed, and where is my data?" before you
    tear anything down - and before you wonder why nonprod is cluttered.

    Prints the deployed resources per bundle, plus the SQL to find every schema
    carrying your prefix. Read-only; changes nothing.

.EXAMPLE
    .\scripts\dev\Show-Sandbox.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

$me = & databricks current-user me --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Not authenticated. Run: databricks auth login --host <nonprod>' }
$shortName = ($me.userName -split '@')[0] -replace '[^A-Za-z0-9_]', '_'
$prefix = "${shortName}_"

Write-Host "Sandbox owner : $($me.userName)"   -ForegroundColor Cyan
Write-Host "Schema prefix : $prefix"           -ForegroundColor Cyan
Write-Host ''

Write-Host '=== deployed resources ===' -ForegroundColor Cyan
$any = $false
foreach ($name in @('landing', 'recon', 'us1', 'us2', 'us3', 'us4', 'us5')) {
    $dir = Join-Path $repoRoot "bundles\$name"
    if (-not (Test-Path (Join-Path $dir 'databricks.yml'))) { continue }

    Push-Location $dir
    try {
        $summary = & databricks bundle summary --target dev 2>$null
        if ($LASTEXITCODE -eq 0 -and $summary) {
            $any = $true
            Write-Host ""
            Write-Host "  bundles\$name" -ForegroundColor Green
            $summary | Select-String -Pattern 'Name:|Path:' | ForEach-Object {
                Write-Host "    $($_.Line.Trim())" -ForegroundColor Gray
            }
        }
    }
    finally { Pop-Location }
}
if (-not $any) { Write-Host '  nothing deployed' -ForegroundColor Gray }

Write-Host ''
Write-Host '=== your data ===' -ForegroundColor Cyan
Write-Host 'Schemas are created at RUNTIME, so the bundle does not know about them.'  -ForegroundColor Gray
Write-Host 'Run this in a SQL editor to see what you are holding:'                    -ForegroundColor Gray
Write-Host ''
Write-Host @"
    SELECT table_catalog, table_schema,
           count(*)                       AS tables,
           max(last_altered)              AS last_touched
    FROM system.information_schema.tables
    WHERE table_schema LIKE '$prefix%'
    GROUP BY table_catalog, table_schema
    ORDER BY last_touched;
"@ -ForegroundColor Yellow
Write-Host ''
Write-Host 'To clean up:' -ForegroundColor Gray
Write-Host '    .\scripts\dev\Destroy-Sandbox.ps1 -All -IncludeData -WhatIf' -ForegroundColor Gray
Write-Host '    .\scripts\dev\Destroy-Sandbox.ps1 -All -IncludeData'         -ForegroundColor Gray
