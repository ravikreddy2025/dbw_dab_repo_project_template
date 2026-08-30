# 10 — Onboarding checklists

[← Walkthrough](09-walkthrough-simulation.md) · [Start here](00-START-HERE.md)

Copy the relevant block into the onboarding work item.

---

## New developer

### Before their first day (buddy or lead)

- [ ] Azure AD account added to `edp-developers`
- [ ] Added to their module group (`edp-landing-team` / `edp-us1-team` / `edp-us1-team`)
- [ ] Azure DevOps: contributor on the `EDP` project, `edp-databricks` repo
- [ ] Databricks nonprod workspace access confirmed (via group sync)
- [ ] Buddy assigned

### Day one (the developer)

- [ ] `winget install Databricks.DatabricksCLI`, confirm `databricks --version` ≥ 0.240.0
- [ ] Python 3.11 installed
- [ ] Repo cloned
- [ ] `python -m venv .venv` and `pip install -r requirements-dev.txt`
- [ ] `pip install -e libs/dab_common`
- [ ] `databricks auth login --host <nonprod>` succeeds
- [ ] `databricks current-user me` returns their identity
- [ ] `pytest -q` passes locally
- [ ] `pwsh ./scripts/dev/Validate-All.ps1` passes
- [ ] `pwsh ./scripts/dev/Deploy-Sandbox.ps1 -Bundle <their module>` succeeds
- [ ] They can see their `[dev <name>]` jobs in the workspace
- [ ] `databricks bundle run <a job> --target dev` succeeds
- [ ] They queried their own audit table:
      `SELECT * FROM edp_ops_nonprod.<name>_audit.job_run_audit`
- [ ] VS Code + Databricks extension installed (optional but recommended)

### Week one (reading)

- [ ] [00 — Start here](00-START-HERE.md)
- [ ] [01 — Architecture](01-architecture.md)
- [ ] [03 — Developer guide](03-developer-guide.md)
- [ ] [02 — Branching strategy](02-branching-strategy.md)
- [ ] [09 — Walkthrough](09-walkthrough-simulation.md)
- [ ] [12 — Conventions](12-conventions.md)
- [ ] Skimmed [08 — Troubleshooting](08-troubleshooting.md) so they know it exists

### Week one (doing)

- [ ] Read their module's `databricks.yml` end to end and can explain each target
- [ ] Read `libs/dab_common/src/dab_common/config.py` — the isolation mechanism
- [ ] Shipped one small PR through the full loop: branch → sandbox → validate → PR → merge
- [ ] Watched a `cd-*` pipeline run to completion
- [ ] Tore down their sandbox with `Destroy-Sandbox.ps1`

### They are up to speed when they can answer

- Why is their job called `[dev <name>] …`?
- Which schema do their tables land in, and why is that different from a colleague's?
- Why did their schedule not fire?
- What happens when they merge to `main`?
- Where does a preprod bug get fixed, and why not in `main`?
- What is the back-merge for?

---

## New lead

Everything in the developer list, plus:

- [ ] Member of `edp-platform-leads`
- [ ] Project Administrator in Azure DevOps
- [ ] Approver on the `dbx-preprod` environment
- [ ] Admin on all three Databricks workspaces
- [ ] Read [05 — CI/CD pipelines](05-cicd-pipelines.md) and
      [06 — Environments and access](06-environments-and-access.md) fully
- [ ] Read [07 — Release process](07-release-process.md) and understands what they
      are attesting to when approving preprod
- [ ] Can locate: service connections, variable groups, environments and their
      approval checks, branch policies
- [ ] Has shadowed one full release before approving one

---

## New QA tester

- [ ] Azure AD account in `edp-qa`
- [ ] Preprod workspace access confirmed
- [ ] Can view jobs and run history in preprod
- [ ] Can `SELECT` from `edp_landing_preprod`, `edp_curated_preprod`,
      `edp_datamart_preprod` and `edp_ops_preprod`
- [ ] Confirmed they **cannot** deploy or edit notebooks — that is correct
- [ ] Azure DevOps: can read work items and raise bugs
- [ ] Read [07 — Release process §3](07-release-process.md#3-qa-tests-preprod)
- [ ] Has the four standard verification queries saved
- [ ] Understands that fixes are never applied in the preprod workspace

---

## New environment

See [06 §7](06-environments-and-access.md#7-standing-up-a-new-environment) for detail.

**Infrastructure**
- [ ] Workspace provisioned and attached to the metastore
- [ ] Key Vault `kv-edp-<env>` created, workspace managed identity granted Get/List
- [ ] Groups synced into the workspace
- [ ] Cluster policy `edp-etl-standard` created — **same name as elsewhere**
- [ ] SQL warehouse `edp-sql-warehouse` created — **same name as elsewhere**

**Identities**
- [ ] Deploy SP registered, granted deploy rights, no data `SELECT`
- [ ] Run-as SP registered, granted only the data its workloads need
- [ ] Federated credential configured for the deploy SP

**Azure DevOps**
- [ ] Service connection `dbx-<env>-svc-conn`, restricted to `cd-*` pipelines
- [ ] Variable group `edp-<env>` with `DATABRICKS_HOST`
- [ ] Environment `dbx-<env>` created, with its approval check if gated

**Bundles, in this order**
- [ ] `cd-platform` deployed successfully
- [ ] `platform_bootstrap_ops` job run — `ops.audit/config/logs/recon` tables exist
- [ ] `cd-landing`, `cd-us1`, `cd-us1` deployed
- [ ] `landing_seed_source_registry` run — sources registered
- [ ] Secrets written to Key Vault
- [ ] One end-to-end run completed and verified in `ops.audit.table_load`

---

## New module

See [04 §8](04-bundle-authoring.md#8-adding-a-whole-new-module).

- [ ] `databricks bundle init ./templates/use-case-bundle --output-dir bundles`
- [ ] Owning team group created and added to [`CODEOWNERS`](../CODEOWNERS)
- [ ] `databricks.yml` reviewed — hosts, SPs and catalogs match the other bundles
- [ ] Any new shared schema added to the `_platform` bundle
- [ ] `cd-<module>.yml` created from `cd-landing.yml` (three lines changed)
- [ ] Pipeline registered in Azure DevOps
- [ ] Build validation policy covers the new path
- [ ] `python scripts/ci/check_bundle_references.py` passes
      *(it fails until the pipeline exists — that is the point)*
- [ ] Sandbox deploy succeeds
- [ ] First PR merged and deployed to nonprod

---

## Offboarding

- [ ] Removed from all `edp-*` groups
- [ ] Sandbox deployments destroyed:
      `databricks bundle destroy --target dev` per bundle
- [ ] Sandbox schemas dropped: `DROP SCHEMA edp_nonprod.<name>_* CASCADE`
- [ ] Removed from `CODEOWNERS` and any automatically-included-reviewer policy
- [ ] Removed as an approver on any environment
- [ ] Open PRs reassigned
- [ ] Any personal access token they created revoked
      *(there should be none — see [03 §2](03-developer-guide.md#2-authenticate))*

---

[← Walkthrough](09-walkthrough-simulation.md) · [Next: FAQ →](11-faq.md)
