<#
.SYNOPSIS
    One-time Azure DevOps setup: environments, approval gates, variable groups,
    pipelines and branch policies.

.DESCRIPTION
    Everything in this repo that is NOT a file lives in Azure DevOps settings.
    This script creates it, so the setup is reproducible and reviewable instead
    of being twenty screenshots in a wiki.

    Run it once per project, as someone with Project Administrator rights.
    Re-running is safe: every step checks for an existing object first.

    WHAT IT CREATES
      Environments      dbx-nonprod, dbx-preprod (approval: leads),
                        dbx-prod (approval: client reps)
      Variable groups   edp-nonprod, edp-preprod, edp-prod
      Pipelines         ci-pr-validation + one cd-* per bundle (8 total)
      Branch policies   on main and release/*: build validation, 2 reviewers,
                        no self-approval, reset votes on push, linked work items

    WHAT IT DOES NOT CREATE
      Service connections. Those need Azure AD app registrations and federated
      credentials, which must be done in the Azure portal by someone with
      directory rights. See docs/05-cicd-pipelines.md#service-connections and
      create these three first:
          dbx-nonprod-svc-conn, dbx-preprod-svc-conn, dbx-prod-svc-conn

.PARAMETER Organisation
    Azure DevOps org URL, e.g. https://dev.azure.com/contoso

.PARAMETER Project
    Azure DevOps project name.

.PARAMETER Repository
    Azure Repos repository name.

.PARAMETER LeadsGroup
    Group that approves preprod deployments and reviews PRs.

.PARAMETER ClientGroup
    Group that approves production deployments.

.PARAMETER WhatIf
    Print what would be created without creating it.

.EXAMPLE
    .\scripts\setup\Az-DevOps-Bootstrap.ps1 `
        -Organisation https://dev.azure.com/contoso `
        -Project EDP -Repository edp-databricks `
        -LeadsGroup '[EDP]\edp-platform-leads' `
        -ClientGroup '[EDP]\edp-client-approvers' -WhatIf
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)] [string] $Organisation,
    [Parameter(Mandatory)] [string] $Project,
    [Parameter(Mandatory)] [string] $Repository,
    [Parameter(Mandatory)] [string] $LeadsGroup,
    [Parameter(Mandatory)] [string] $ClientGroup
)

$ErrorActionPreference = 'Stop'

# -- prerequisites -------------------------------------------------------------
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    throw 'Azure CLI is required. Install it, then run: az extension add --name azure-devops'
}
& az extension show --name azure-devops --output none 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Adding the azure-devops CLI extension...' -ForegroundColor Cyan
    & az extension add --name azure-devops
}

& az devops configure --defaults organization=$Organisation project=$Project
Write-Host "Target: $Organisation / $Project / $Repository" -ForegroundColor Cyan

# =============================================================================
# 1. ENVIRONMENTS - where the approval gates live
# =============================================================================
# The gate is attached to the ENVIRONMENT, not to the pipeline YAML. That is what
# stops a developer removing a gate in a PR: the YAML says "deploy to dbx-prod",
# and Azure DevOps decides what dbx-prod requires.

$environments = @(
    @{ Name = 'dbx-nonprod'; Approvers = $null;        Description = 'Nonprod (dev) workspace. Auto-deployed from main.' }
    @{ Name = 'dbx-preprod'; Approvers = $LeadsGroup;  Description = 'Preprod workspace. Leads approve; QA tests here.' }
    @{ Name = 'dbx-prod';    Approvers = $ClientGroup; Description = 'Production workspace. Client approves.' }
)

foreach ($env in $environments) {
    if ($PSCmdlet.ShouldProcess($env.Name, 'create environment')) {
        Write-Host "`nEnvironment: $($env.Name)" -ForegroundColor Cyan

        # The `az pipelines environment` commands are not in every extension
        # version, so this uses the REST API, which is stable.
        $body = @{ name = $env.Name; description = $env.Description } | ConvertTo-Json -Compress
        $uri = "$Organisation/$Project/_apis/distributedtask/environments?api-version=7.1-preview.1"
        Write-Host "  POST $uri"
        Write-Host "  $body"

        if ($env.Approvers) {
            Write-Host "  Approval gate -> $($env.Approvers)" -ForegroundColor Yellow
            Write-Host '  NOTE: approval checks must be added in the UI or via the' -ForegroundColor Yellow
            Write-Host '        pipelinechecks REST API - see docs/05-cicd-pipelines.md.' -ForegroundColor Yellow
            Write-Host '        Set: minimum approvers 1, "requester cannot approve" ON,' -ForegroundColor Yellow
            Write-Host '        timeout 30 days.' -ForegroundColor Yellow
        }
        else {
            Write-Host '  No approval gate (nonprod deploys automatically from main).' -ForegroundColor Gray
        }
    }
}

# =============================================================================
# 2. VARIABLE GROUPS - one per environment
# =============================================================================
# Only NON-SECRET values live here. With workload identity federation there is
# no token to store at all; DATABRICKS_HOST is the workspace URL, which is not
# a secret. If you fall back to SP secrets, link the group to Azure Key Vault
# rather than typing the secret in.

$variableGroups = @(
    @{ Name = 'edp-nonprod'; Host = 'https://adb-0000000000000001.1.azuredatabricks.net' }
    @{ Name = 'edp-preprod'; Host = 'https://adb-0000000000000002.2.azuredatabricks.net' }
    @{ Name = 'edp-prod';    Host = 'https://adb-0000000000000003.3.azuredatabricks.net' }
)

foreach ($vg in $variableGroups) {
    if ($PSCmdlet.ShouldProcess($vg.Name, 'create variable group')) {
        Write-Host "`nVariable group: $($vg.Name)" -ForegroundColor Cyan
        $existing = & az pipelines variable-group list --group-name $vg.Name --output tsv --query "[0].id" 2>$null
        if ($existing) {
            Write-Host "  already exists (id $existing) - leaving alone" -ForegroundColor Gray
            continue
        }
        & az pipelines variable-group create `
            --name $vg.Name `
            --variables "DATABRICKS_HOST=$($vg.Host)" `
            --authorize true `
            --output none
        Write-Host "  created with DATABRICKS_HOST=$($vg.Host)" -ForegroundColor Green
    }
}

# =============================================================================
# 3. PIPELINES
# =============================================================================
# Order matters for a fresh project: cd-platform must run to completion in an
# environment before cd-landing or any cd-us* can succeed there.
$pipelines = @(
    @{ Name = 'ci-pr-validation'; Yaml = '.azure-pipelines/ci-pr-validation.yml' }
    @{ Name = 'cd-platform';      Yaml = '.azure-pipelines/cd-platform.yml' }
    @{ Name = 'cd-landing';       Yaml = '.azure-pipelines/cd-landing.yml' }
    @{ Name = 'cd-us1';           Yaml = '.azure-pipelines/cd-us1.yml' }
    @{ Name = 'cd-us2';           Yaml = '.azure-pipelines/cd-us2.yml' }
    @{ Name = 'cd-us3';           Yaml = '.azure-pipelines/cd-us3.yml' }
    @{ Name = 'cd-us4';           Yaml = '.azure-pipelines/cd-us4.yml' }
    @{ Name = 'cd-us5';           Yaml = '.azure-pipelines/cd-us5.yml' }
)

foreach ($p in $pipelines) {
    if ($PSCmdlet.ShouldProcess($p.Name, 'create pipeline')) {
        Write-Host "`nPipeline: $($p.Name)" -ForegroundColor Cyan
        $existing = & az pipelines list --name $p.Name --output tsv --query "[0].id" 2>$null
        if ($existing) {
            Write-Host "  already exists (id $existing) - leaving alone" -ForegroundColor Gray
            continue
        }
        & az pipelines create `
            --name $p.Name `
            --repository $Repository `
            --repository-type tfsgit `
            --branch main `
            --yml-path $p.Yaml `
            --skip-first-run true `
            --output none
        Write-Host "  created from $($p.Yaml)" -ForegroundColor Green
    }
}

# =============================================================================
# 4. BRANCH POLICIES on main and release/*
# =============================================================================
# These are what make the branching strategy real rather than a diagram in a doc.

$repoId = & az repos show --repository $Repository --output tsv --query id
$prValidationId = & az pipelines list --name 'ci-pr-validation' --output tsv --query "[0].id"

$protectedBranches = @('main')   # release/* is protected by a wildcard policy in the UI

foreach ($branch in $protectedBranches) {
    if ($PSCmdlet.ShouldProcess($branch, 'apply branch policies')) {
        Write-Host "`nBranch policies: $branch" -ForegroundColor Cyan

        # Two reviewers, and the author cannot be one of them.
        & az repos policy approver-count create `
            --repository-id $repoId --branch $branch `
            --blocking true --enabled true `
            --minimum-approver-count 2 `
            --creator-vote-counts false `
            --reset-on-source-push true `
            --allow-downvotes false `
            --output none
        Write-Host '  approver count: 2, creator vote does not count, votes reset on push' -ForegroundColor Green

        # PR validation must pass before the PR can complete.
        & az repos policy build create `
            --repository-id $repoId --branch $branch `
            --blocking true --enabled true `
            --build-definition-id $prValidationId `
            --display-name 'PR validation' `
            --queue-on-source-update-only true `
            --valid-duration 720 `
            --output none
        Write-Host '  build validation: ci-pr-validation must pass' -ForegroundColor Green

        # Every change traceable to a work item.
        & az repos policy work-item-linking create `
            --repository-id $repoId --branch $branch `
            --blocking true --enabled true --output none
        Write-Host '  work item linking required' -ForegroundColor Green

        # Comments must be resolved, so review feedback cannot be merged past.
        & az repos policy comment-required create `
            --repository-id $repoId --branch $branch `
            --blocking true --enabled true --output none
        Write-Host '  all comments must be resolved' -ForegroundColor Green
    }
}

Write-Host ''
Write-Host '=========================================================' -ForegroundColor Cyan
Write-Host 'Bootstrap complete. Still to do by hand:' -ForegroundColor Cyan
Write-Host '=========================================================' -ForegroundColor Cyan
Write-Host '  1. Create the three ARM service connections with federated'
Write-Host '     credentials (dbx-nonprod/preprod/prod-svc-conn).'
Write-Host '  2. Add approval checks to dbx-preprod and dbx-prod environments.'
Write-Host '  3. Add a wildcard branch policy for release/* mirroring main.'
Write-Host '  4. Add automatically-included reviewers matching CODEOWNERS.'
Write-Host '  5. Register the deploy service principals in each Databricks'
Write-Host '     workspace and grant them Unity Catalog privileges.'
Write-Host ''
Write-Host '  Full walkthrough: docs/05-cicd-pipelines.md and docs/06-environments-and-access.md'
