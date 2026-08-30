# 12 — Conventions

[← FAQ](11-faq.md) · [Start here](00-START-HERE.md)

Naming rules. Adjust the `edp` prefix and the `us1`–`us5` names to your standard
**before you start** — changing them later means touching every file.

---

## Unity Catalog

### Catalogs — layer per environment

| Pattern | Examples |
|---|---|
| `<prefix>_<layer>_<env>` | `edp_landing_nonprod`, `edp_curated_preprod`, `edp_datamart_prod`, `edp_ops_prod` |

Layers: `landing`, `curated`, `datamart`, `ops`.
Environments: `nonprod`, `preprod`, `prod`.

> The `<env>` suffix is **required, not cosmetic**. One Unity Catalog metastore
> serves all three workspaces, so catalog names must be unique metastore-wide.

Built by `dab_common.config.RuntimeContext.catalog()`. Never typed by hand, and
never hardcoded in a resource file or a `.sql` file — there is a test that fails
if a SQL file names a catalog.

### Schemas

| Catalog | Schema axis | Examples |
|---|---|---|
| `edp_landing_<env>` | use case | `us1`, `us2`, `us3`, `us4`, `us5` |
| `edp_curated_<env>` | use case | `us1` … `us5` |
| `edp_datamart_<env>` | use case | `us1` … `us5` |
| `edp_ops_<env>` | **function** | `audit`, `config`, `logs`, `recon` |

Ops is the deliberate exception: audit and config are cross-cutting, and no use
case owns them.

**Sandbox schemas** carry the developer prefix: `jsmith_us1`, `jsmith_audit`. The
prefix comes from `${workspace.current_user.short_name}` — never typed by hand.

> **THE ONE RULE.** Every schema this framework touches is prefixed in a sandbox,
> ops included. If `ops.config` were shared, a developer seeding test sources
> would overwrite the registry that shared nonprod jobs read.

### Use cases

`us1` … `us5`. Lower case, no separators, matching the bundle folder.

**Sub-use-cases are a code boundary, not a schema boundary.** They appear in
folders (`src/us1_module/billing/`) and in table prefixes
(`billing_invoice`), never as their own schema.

### Volumes

| Volume | Where | Purpose |
|---|---|---|
| `_checkpoints` | `edp_landing_<env>.<uc>` | Structured Streaming checkpoints |
| `inbound` | `edp_landing_<env>.<uc>` | Files that arrive as files |
| `_quarantine` | `edp_curated_<env>.<uc>` | Rows rejected by DQ checks |
| `legacy_extracts` | `edp_ops_<env>.recon` | Cloudera extracts for parity. Deleted at cutover. |

Leading underscore means "internal — not a data product".

### Tables

| Layer | Pattern | Examples |
|---|---|---|
| landing | `<system>_<object>` | `kfk_orders`, `ora_customers` |
| curated | plural business noun | `orders`, `customers` |
| datamart | `dim_*` / `fct_*` / `agg_*` | `dim_orders`, `fct_orders`, `agg_daily_revenue` |
| ops.audit | singular purpose | `job_run`, `table_load`, `data_quality_result` |
| ops.config | `<domain>_<thing>` | `landing_source`, `landing_watermark` |
| ops.recon | `parity_*` | `parity_run`, `parity_check_result`, `parity_exception` |

Landing keeps the source-system prefix because the same entity may arrive from two
places. Curated drops it — by then it is conformed, and nothing downstream should
care where it came from.

### Columns

- `snake_case` everywhere, including columns from a SHOUTING Oracle source —
  `conform_*` functions do the renaming.
- Framework-added columns are prefixed `_`: `_ingested_at`, `_source_id`, `_run_id`.
- Timestamps end `_at` (`ingested_at`) or `_ts` (`event_ts`). Dates end `_date`.
- Surrogate keys end `_key`; natural keys end `_id`.
- Booleans start `is_` or `has_`.

---

## Bundles and resources

| Thing | Pattern | Examples |
|---|---|---|
| Bundle folder | `<use_case>`, plus `_platform` and `landing` | `bundles/us1`, `bundles/_platform` |
| Bundle name | `edp_<folder>` | `edp_us1`, `edp_landing`, `edp_platform` |
| Resource file | `<subject>.<type>.yml` | `curated.job.yml`, `kafka_landing_us1.pipeline.yml` |
| Job key | `<use_case>_<layer>` | `us1_curated`, `us1_datamart`, `us1_reconcile`, `landing_us2` |
| Task key | verb or verb_noun | `curate`, `list_sources`, `land_one_table`, `build_dim_orders` |
| Job cluster key | `<purpose>_<role>` | `oracle_etl` |
| Environment key | `default` unless there is a reason | `default` |

The **job key** is what you type in `bundle run`, and what a pipeline's
`runAfterDeploy` references. Keep it stable — renaming one breaks a smoke test in
a stage that only runs at release time. The cross-reference audit catches it.

Set `name:` equal to the job key. `mode: development` adds the `[dev <user>] `
prefix; you never write it.

### The five base parameters

Every job passes these, declared at **job** level so `for_each` sub-tasks inherit
them:

```yaml
      parameters:
        - name: env               # nonprod | preprod | prod
        - name: use_case          # us1 .. us5, or landing / platform
        - name: catalog_prefix    # edp
        - name: schema_prefix     # "jsmith_" in a sandbox, "" elsewhere
        - name: bundle_target     # dev | nonprod | preprod | prod
```

`dab_common.config.build_context()` reads them. A job that omits `use_case` raises
`ConfigError` on its first line — a much better failure than writing to the wrong
schema.

> `env` is the **physical** environment. `dev` is a bundle *target*, not an
> environment: a sandbox has `env: nonprod` plus a `schema_prefix`.

### Variables

`snake_case`, and the same name for the same concept in every bundle. The standard
set:

```
env  use_case  catalog_prefix  schema_prefix  max_retries  run_as_sp  alert_email
```

Plus where relevant: `min_workers`, `max_workers`, `runtime_engine`, `photon`,
`etl_policy_id`, `warehouse_id`, `dq_fail_on_error`, `recon_enabled`.

> `photon` is a **boolean** for declarative pipelines. `runtime_engine` is an
> **enum** (`STANDARD` / `PHOTON`) for job clusters. Two names for one idea, kept
> separate so neither is passed to the wrong place.

### Tags

Every job and pipeline:

```yaml
      tags:
        use_case: us1
        layer: curated            # landing | curated | datamart | recon | platform
        managed_by: databricks_bundle
        environment: ${var.env}
```

`managed_by: databricks_bundle` is how you find, in the workspace UI, what is
managed by code and what somebody created by hand.

---

## Git

| Thing | Pattern | Examples |
|---|---|---|
| Feature branch | `feature/<TICKET>-<slug>` | `feature/DAB-123-onboard-invoices` |
| Bug fix | `bugfix/<TICKET>-<slug>` | `bugfix/DAB-140-null-status` |
| Hotfix | `hotfix/<TICKET>-<slug>` | `hotfix/DAB-155-partition-bounds` |
| Release | `release/<yyyy>.<MM>.<n>` | `release/2026.09.1` |
| Back-merge | `backmerge/release-<version>` | `backmerge/release-2026.09.1` |
| Tag | `v<yyyy>.<MM>.<n>` | `v2026.09.1` |

Commit subject: `<TICKET>: <imperative summary>`, under 72 characters. PRs
squash-merge, so the PR title becomes the commit on `main` — make it good.

---

## Azure DevOps

| Thing | Pattern | Examples |
|---|---|---|
| Pipeline | `cd-<bundle>` / `ci-<purpose>` | `cd-us1`, `cd-landing`, `ci-pr-validation` |
| Environment | `dbx-<env>` | `dbx-preprod` |
| Variable group | `edp-<env>` | `edp-preprod` |
| Service connection | `dbx-<env>-svc-conn` | `dbx-preprod-svc-conn` |
| Pipeline stage | PascalCase | `Build`, `DeployPreProd` |
| Pipeline template | `<kind>/<verb>-<noun>.yml` | `steps/bundle-deploy.yml` |

---

## Identities

| Thing | Pattern | Examples |
|---|---|---|
| Platform group | `edp-<function>` | `edp-platform-leads`, `edp-developers`, `edp-qa` |
| Use-case team | `edp-<use_case>-team` | `edp-us1-team` … `edp-us5-team` |
| Landing team | `edp-landing-team` | — |
| Deploy SP | `sp-edp-deploy-<env>` | `sp-edp-deploy-prod` |
| Run-as SP | `sp-edp-run-<env>` | `sp-edp-run-prod` |
| Secret scope | `edp-<system>` | `edp-oracle`, `edp-kafka`, `edp-legacy` |
| Key Vault | `kv-edp-<env>` | `kv-edp-preprod` |
| Cluster policy | `edp-<purpose>` | `edp-etl-standard` |
| SQL warehouse | `edp-sql-warehouse` | — |

> **Secret scopes, cluster policies and warehouses carry the SAME NAME in all three
> workspaces.** Only the underlying resource differs. That is what makes
> `lookup: cluster_policy: edp-etl-standard` work identically in every target, and
> what lets one scope name resolve to three different Key Vaults.

---

## Registry and control values

| Field | Allowed values |
|---|---|
| `source_system` | `oracle`, `kafka` |
| `load_strategy` | `full`, `incremental`, `cdc_stream` |
| `status` (audit) | `RUNNING`, `SUCCESS`, `FAILED`, `SKIPPED` |
| `layer` (audit) | `landing`, `curated`, `datamart`, `recon`, `platform` |
| `severity` (DQ) | `warn`, `error` |
| `check_type` (recon) | `row_count`, `column_sum`, `column_hash`, `distinct_count`, `min_max` |
| `source_id` | `<use_case>_<system>_<object>` — `us1_kfk_orders`, `us2_ora_customers` |

`source_id` is the primary key of the **shared** `ops.config.landing_source`, so
the use-case prefix is not decoration — without it, two use cases onboarding a
table of the same name collide. A test enforces it.

**Never reuse a `source_id`** for a different object; the history would be
uninterpretable.

Enumerations are validated in code (`edp_landing.registry`, `dab_common.recon`), so
an invalid value fails at PR time rather than at 2am.

---

## Python

Standard `ruff` rules from [`pyproject.toml`](../pyproject.toml), line length 110.

| Thing | Pattern |
|---|---|
| Shared wheel | `libs/<name>/src/<name>/` — `dab_common`, `edp_landing` |
| Use-case wheel package | `<use_case>_module` — `us1_module` |
| Sub-use-case | `<use_case>_module/<sub>/` — `us1_module/billing/` |
| Ported code | `src/ported/<sub>/` — `src/ported/billing/load_invoices.py` |
| Entry notebook | `src/jobs/<verb>[_<noun>].py` — `curate.py`, `publish_marts.py` |
| Pipeline source | `src/pipelines/<subject>.py` — `kafka_landing.py` |
| SQL file | `src/sql/<table_name>.sql` — matches the table it builds |
| Layer contract DDL | `src/ddl/<layer>/<subject>.sql` |
| Test file | `tests/test_<subject>.py` |
| Test name | `test_<what>_<expected behaviour>` |

Test names describe behaviour, not implementation:
`test_removed_source_is_deactivated_never_deleted`, not `test_plan_seed_merge_3`.
When one fails at 2am, the name should tell you what broke.

### Three structural rules

1. **Nothing at import time touches Spark.** Every function needing a session takes
   it as its first argument. That is what lets the shared test suites run on an
   agent with no cluster, no Spark and no Java.
2. **Validate arguments before importing Spark**, so a bad argument fails
   identically whether or not a session exists — and so the check is testable.
3. **Logic in the wheel, orchestration in the notebook.** A notebook cannot be unit
   tested. Past ~50 lines of real logic, move it into `src/<uc>_module/`.

---

## Schedules

Staggered so each layer has its inputs. All UTC.

| Job | Cron | Time |
|---|---|---|
| `landing_us1` / `us3` / `us4` (Kafka) | `0 0/30 * * * ?` | every 30 min |
| `landing_us2` (Oracle) | `0 0 2 * * ?` | 02:00 |
| `<uc>_curated` | `0 0 4 * * ?` | 04:00 |
| `<uc>_datamart` | `0 0 6 * * ?` | 06:00 |
| `<uc>_reconcile` | `0 0 8 * * ?` | 08:00 |

Two-hour gaps are generous on purpose — a slow night should not cascade. If you add
a job, fit it into this order and say so in the PR.

Quartz cron has **six or seven** fields (seconds first), not five. `0 0 2 * * ?` is
02:00:00 daily; `?` in the day-of-week position means "no specific value".

---

[← FAQ](11-faq.md) · [Next: Migration and cutover →](13-migration-and-cutover.md)
