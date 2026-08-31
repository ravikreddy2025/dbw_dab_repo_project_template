# 04 — Bundle authoring

[← Developer guide](03-developer-guide.md) · [Start here](00-START-HERE.md)

How to add things to a bundle without breaking the pattern.
[`bundles/landing/databricks.yml`](../bundles/landing/databricks.yml) is the
canonical example — it is the most heavily commented file in the repo. Read it once
before you read this.

---

## The one rule

> **If a value differs between dev and prod, it is a variable. If it is the same
> everywhere, hardcode it in the resource file.**

A resource file must never contain an environment name, a workspace host, a catalog
name, a cluster ID, a warehouse ID or a policy ID. If you find yourself typing
`edp_curated_prod` into `resources/`, stop.

The cross-reference audit enforces part of this — `test_publish.py` fails if a `.sql`
file hardcodes a catalog — but most of it is on review.

---

## 1. Bundle anatomy

```
bundles/<module>/
├─ databricks.yml        bundle name, artifacts, the four targets
├─ variables.yml         variable DECLARATIONS (values live in targets)
├─ pyproject.toml        packaging for the module wheel
├─ resources/            one file per logical group of resources
│  ├─ *.job.yml
│  └─ *.pipeline.yml
├─ src/
│  ├─ jobs/              thin notebook entry points
│  ├─ pipelines/         declarative pipeline sources
│  ├─ sql/               SQL task files
│  └─ <module>_module/   the wheel: testable logic
├─ conf/                 config-as-data (source registries, mappings)
├─ tests/                pytest, runs with no cluster
└─ dist/                 built wheels (gitignored)
```

`include:` in `databricks.yml` pulls in `variables.yml` and `resources/*.yml`. Adding
a new file under `resources/` needs no wiring — the glob picks it up.

---

### What lives in `src/`, and why it is all `.py`

```
src/
├─ jobs/           notebooks run by a JOB task        (notebook_task)
├─ pipelines/      source for a declarative PIPELINE  (pipeline resource)
├─ <uc>_module/    importable library code            (built into a wheel)
├─ ddl/            SQL — see §7
└─ ported/         lift-and-shift code, not yet refactored
```

**`jobs/` and `pipelines/` are notebooks.** Every file there starts with

```python
# Databricks notebook source
```

which is what makes a `.py` file a notebook to Databricks — cells are separated
by `# COMMAND ----------` and markdown by `# MAGIC %md`. It is the **source
format** of a notebook, not a different thing.

We use it instead of `.ipynb` because a `.py` notebook diffs, reviews, lints and
merges like code. An `.ipynb` is JSON with embedded outputs: every run changes
the file, review is unreadable, and merge conflicts are unresolvable.

**`jobs/` vs `pipelines/` is not stylistic — they are different resource types.**

| | `src/jobs/` | `src/pipelines/` |
|---|---|---|
| Referenced by | `notebook_task.notebook_path` | a `pipelines:` resource's `libraries` |
| Executes | top to bottom, once | continuously, as a dataflow graph |
| You write | imperative code | `@dlt.table` definitions |
| Owns its tables | no | **yes** — the pipeline creates and manages them |

The last row matters: a declarative pipeline owns the lifecycle of the tables it
declares, so those tables are **not** in `src/ddl/` and are not migrated by
`<uc>_migrate`. Changing them means changing the `@dlt.table` definition.
[`kafka_landing.py`](../bundles/landing/src/pipelines/kafka_landing.py) is the
example.

**`<uc>_module/` is not a notebook.** No magic header, plain importable Python,
unit tested without Spark. Logic goes here; `jobs/` just wires it up.

---

## 2. Adding a job

Create `resources/<name>.job.yml`:

```yaml
resources:
  jobs:
    curation_reconcile_daily:              # job KEY - what you type in bundle run
      name: curation_reconcile_daily        # display name in the workspace
      description: "Reconcile curated against the source system row counts."

      tags:
        module: curation
        managed_by: databricks_bundle
        environment: ${var.env}

      schedule:
        quartz_cron_expression: "0 30 5 * * ?"
        timezone_id: UTC
        pause_status: UNPAUSED              # forced to PAUSED in dev by mode:development

      max_concurrent_runs: 1

      email_notifications:
        on_failure:
          - ${var.alert_email}

      # The five base parameters. EVERY job passes these.
      parameters:
        - name: env
          default: ${var.env}
        - name: use_case
          default: us1
        - name: catalog_prefix
          default: ${var.catalog_prefix}
        - name: schema_prefix
          default: ${var.schema_prefix}
        - name: bundle_target
          default: ${bundle.target}

      tasks:
        - task_key: reconcile
          notebook_task:
            notebook_path: ../src/jobs/reconcile.py
          environment_key: default
          max_retries: ${var.max_retries}

      environments:
        - environment_key: default
          spec:
            environment_version: "2"
            dependencies:
              - ./dist/dab_common-*.whl
              - ./dist/edp_us1-*.whl
```

Then in the notebook:

```python
from dab_common.config import build_context
ctx = build_context(dbutils.widgets.getAll())
```

### Every task states a retry policy

An unset `max_retries` is a decision nobody made. This repo had ten `sql_task`s
with no retry policy sitting beside notebook tasks in the *same job* that had
one — so a transient warehouse blip failed the whole mart build while the task
after it would have retried.

| Task | Policy | Why |
|---|---|---|
| Produces data | `max_retries: ${var.max_retries}` | Cluster and warehouse blips are transient and real |
| Quality gate | `max_retries: 0` | Checking bad data twice returns the same bad data |
| Migration | `max_retries: 0` | A failed migration needs a human to read the error |
| `pipeline_task` | **omit** | The declarative pipeline owns its own retry policy |
| `for_each_task` | **omit** — set it on the **nested** task | The fan-out itself cannot fail transiently |

`check_retry_policy_is_explicit` fails the build on any task that fits none of
these. It is not asking you to retry — it is asking you to decide.

### The five base parameters are not optional

`dab_common.config.build_context()` reads them, and everything downstream — table
names, sandbox isolation, audit rows — depends on them. A job that omits `catalog`
raises `ConfigError` on its first line, which is a much better failure than writing
to the wrong catalog.

`parameters:` at **job level** is inherited by every task, including tasks generated
inside a `for_each_task`. Declare them once there, not per task.

---

## 3. Compute

Three styles are in this repo. Pick by workload shape.

### Serverless — the default for new work

```yaml
      tasks:
        - task_key: main
          notebook_task:
            notebook_path: ../src/jobs/main.py
          environment_key: default

      environments:
        - environment_key: default
          spec:
            environment_version: "2"
            dependencies:
              - ./dist/dab_common-*.whl
              - ./dist/edp_us1-*.whl
```

Use when the job is short or bursty. No cluster to size, no startup to wait for, no
policy to comply with. [`us1 curated.job.yml`](../bundles/us1/resources/curated.job.yml)
is the example — and us3, us4, us5 are the same.

### The same job, both ways

[`us2 curated.job.yml`](../bundles/us2/resources/curated.job.yml) is deliberately
the **classic-compute twin** of the us1 file. Same tasks, same parameters, same
notebooks — so the diff between them is exactly the difference between the two
styles and nothing else:

| | serverless (us1) | classic (us2) |
|---|---|---|
| Task binding | `environment_key: default` | `job_cluster_key: curated_etl` |
| Wheels | `environments[].spec.dependencies` | `libraries[]` on **each** task |
| Cluster | none declared | `job_clusters[].new_cluster` |
| Policy | n/a | `policy_id` from a `lookup` |
| Runtime version | managed for you | `spark_version` pinned by you |
| Sizing per env | n/a | `min_workers` / `max_workers` / `runtime_engine` |
| Cost tags | job tags | plus `custom_tags` on the VMs |

**Reach for classic when you need something serverless cannot give you**: a JDBC
driver JAR, a fixed egress identity a DBA can allow-list, an init script, GPUs,
a specific Spark version, or a long run where startup is noise. us2 is classic
because its source is Oracle. That is the whole reason — not preference.

Two things people get wrong on classic:

- **Pin `spark_version`.** "Latest LTS" is not a version. The same commit would
  deploy a different Spark to preprod next month than the one QA signed off, and
  a runtime upgrade arrives unannounced.
- **Share one job cluster across tasks** (`job_cluster_key` on both). A cluster
  per task means paying startup twice for a job whose second task takes seconds.

### Job cluster + policy

```yaml
      job_clusters:
        - job_cluster_key: oracle_etl
          new_cluster:
            policy_id: ${var.etl_policy_id}      # looked up by NAME, see below
            spark_version: "15.4.x-scala2.12"
            node_type_id: Standard_D4ds_v5
            data_security_mode: SINGLE_USER
            runtime_engine: ${var.runtime_engine}
            autoscale:
              min_workers: ${var.min_workers}
              max_workers: ${var.max_workers}
            custom_tags:
              module: ingestion
              environment: ${var.env}
```

Use when the job is long-running or needs specific tuning — JDBC extracts, big
shuffles. [`oracle_ingest.job.yml`](../bundles/landing/resources/landing_us2.job.yml)
is the example.

> **`runtime_engine` takes `STANDARD` or `PHOTON`, not a boolean.** Pipelines take
> `photon: true`. Two different types for the same idea; this repo keeps them as two
> separate variables (`runtime_engine` and `photon`) so neither is ever passed to
> the wrong one.

### SQL warehouse

```yaml
        - task_key: build_dim_customer
          sql_task:
            warehouse_id: ${var.warehouse_id}
            file:
              path: ../src/sql/dim_customer.sql
            parameters:
              catalog: ${var.catalog}
              schema_prefix: ${var.schema_prefix}
```

Use for set-based SQL that produces tables.
[`datamart.job.yml`](../bundles/us1/resources/datamart.job.yml) is the example.

In the `.sql` file, bind parameters — never concatenate:

```sql
INSERT OVERWRITE IDENTIFIER(:catalog || '.' || :schema || '.fct_orders')
BY NAME
SELECT ...
```

**Not `CREATE OR REPLACE TABLE`.** The table is created and evolved by
`<uc>_migrate` from `src/ddl/`; a SQL task writes DATA into it. `CREATE OR
REPLACE` would rebuild the table nightly from this SELECT and discard the
declared shape — along with any applied migration, while the history table went
on claiming it had been applied. `check_no_schema_clobber` rejects it.

`IDENTIFIER()` is what lets a bound parameter form part of an object name. A plain
`:param` cannot appear where SQL expects an identifier.

---

## 4. Variables

Declare in `variables.yml`, set per target in `databricks.yml`:

```yaml
# variables.yml
variables:
  max_workers:
    description: Autoscale ceiling for job clusters.
    default: 2
```

```yaml
# databricks.yml
targets:
  dev:
    variables:
      max_workers: 2
  prod:
    variables:
      max_workers: 12
```

Resolution order, highest wins:

1. `--var="name=value"` on the command line
2. `BUNDLE_VAR_<name>` environment variable
3. `.databricks/bundle/<target>/variable-overrides.json` (gitignored, local only)
4. Target-level `variables:`
5. The declared `default:`

### Lookup variables

Resolve a workspace object's ID from its name, at deploy time, per target:

```yaml
  etl_policy_id:
    lookup:
      cluster_policy: edp-etl-standard
  warehouse_id:
    lookup:
      warehouse: edp-sql-warehouse
```

Available for `cluster_policy`, `warehouse`, `cluster`, `instance_pool`, `job`,
`pipeline`, `metastore`, `service_principal`, `notification_destination`, `alert`,
`dashboard`, `query`.

This is how no workspace ID ever enters git. It works because of a convention you
must keep: **the object carries the same name in all three workspaces**, and only its
ID differs.

> **A `lookup:` takes a literal, not `${var.x}`.** Lookups resolve before variable
> interpolation. If you need the name to vary by environment you cannot use a
> lookup — pass the ID as a variable instead, and accept that it is then in git.

### Complex variables

For a whole block:

```yaml
  small_cluster:
    type: complex
    default:
      spark_version: "15.4.x-scala2.12"
      node_type_id: Standard_D4ds_v5
      num_workers: 2
```

Used as `new_cluster: ${var.small_cluster}`. Worth it when three or more jobs share a
cluster shape; overkill for one.

---

## 5. Targets

Every use-case bundle has exactly four, and
[`validate_bundle_yaml.py`](../scripts/ci/validate_bundle_yaml.py) fails the build if
one is missing or has the wrong mode.

| Target | Mode | `default` | run_as | Deployed by |
|---|---|---|---|---|
| `dev` | `development` | **yes** | none (you) | developer |
| `nonprod` | `production` | no | deploy SP | CI on `main` |
| `preprod` | `production` | no | deploy SP | CD, gate: leads |
| `prod` | `production` | no | deploy SP | CD, gate: client |

### What each mode gives you

`mode: development`:
- prefixes resource names with `[dev <you>] `
- pauses all schedules and triggers
- roots files under `/Workspace/Users/<you>/.bundle/…`
- allows `--cluster-id` override
- disables the deployment lock and allows concurrent runs

`mode: production`:
- refuses `--cluster-id`
- validates pipelines are not marked `development: true`
- requires `run_as` and `permissions` to be explicit unless deploying as an SP
- validates the git branch **if** `git.branch` is set on the target

### Why `git.branch` is not used here

Azure DevOps checks out in detached-HEAD state, so branch validation misfires and
every deploy needs `--force` — which trains everyone to pass `--force`, which defeats
the check. Branch control lives in the pipeline `trigger` blocks and Azure Repos
branch policies instead. See [08](08-troubleshooting.md#detached-head).

---

## 6. Wheels

Two wheels reach every job:

| Wheel | Built by | When |
|---|---|---|
| `dab_common` | `Build-Wheels.ps1` / `build-wheels.yml` | before `bundle deploy` |
| `edp_<module>` | the bundle's own `artifacts:` block | during `bundle deploy` |

Both land in `bundles/<module>/dist/` and are referenced with a glob:

```yaml
            dependencies:
              - ./dist/dab_common-*.whl
              - ./dist/edp_us1-*.whl
```

Explicit globs rather than `./dist/*.whl` so a stray file in `dist/` cannot silently
become a job dependency.

`dynamic_version: true` on the module's `artifacts:` entry appends a content-based
suffix, so redeploying during iteration is not served a cached wheel with the same
version number.

### Why the shared wheel is copied rather than referenced

DABs requires library paths to be inside the bundle root. `../../libs/dab_common`
produces `path is not contained in bundle root path`. `sync.paths` can extend the
root, but it needs a recent CLI and complicates the mental model. Building into
`dist/` works everywhere.

When modules eventually move to separate repositories, switch to publishing
`dab_common` to an Azure Artifacts feed and pinning a version.

---

## 7. Unity Catalog objects

**Shared schemas belong to `_platform`.** Do not declare `resources.schemas` in a
use-case bundle.

The reason is concrete: in `mode: development`, DABs prefixes resource names with
`[dev jsmith] `, which is not a legal schema name, so a use-case bundle that declares a
schema fails the moment a developer deploys their sandbox.

To add a shared schema, add it to
[`bundles/_platform/resources/schemas_curated.yml`](../bundles/_platform/resources/schemas_curated.yml)
with its grants, and deploy the platform bundle before the use case that needs it.

For a sandbox schema, call the helper — it no-ops outside a sandbox:

```python
from dab_common.config import ensure_schema
ensure_schema(spark, ctx, "curated")
```

**Tables are never bundle resources.** They are created by SQL that a job runs.
There are exactly three places that SQL lives, and they run at different times:

| SQL | Where | Run by | When |
|---|---|---|---|
| `ops.*` tables | `bundles/_platform/src/ddl/` | `platform_bootstrap_ops` job | once per environment, and on any platform deploy |
| Current shape of a use case's tables | `bundles/<uc>/src/ddl/curated/`, `.../datamart/` | `<uc>_migrate` job | **every deploy**, all environments |
| Ordered changes to existing tables | `bundles/<uc>/src/ddl/migrations/` | `<uc>_migrate` job | **every deploy**, once each, recorded |

`<uc>_migrate` runs from the CD pipeline between `bundle deploy` and the smoke
run — see
[`bundle-deploy.yml`](../.azure-pipelines/templates/steps/bundle-deploy.yml).
That ordering is deliberate: a deploy publishes job *definitions* and touches no
table, so without this step a release that adds a column would deploy green and
then fail at 04:00 in an environment nobody is watching.

---

<a name="changing-an-existing-table"></a>

## 7a. Changing an existing table

`CREATE TABLE IF NOT EXISTS` builds a **new** environment. It does nothing to one
that already has the table. So an added column behaves like this:

| | |
|---|---|
| your fresh sandbox | table created **with** the new column → works |
| preprod | table already exists, unchanged → **fails** |
| prod | table already exists, unchanged → **fails** |

...and it fails after the approval gate, on the environment you cannot iterate on.

### While you are iterating — do nothing

A sandbox schema is yours and disposable:

```sql
DROP TABLE IF EXISTS edp_curated_nonprod.jsmith_us1.orders;
```

Rerun the job. No migration, no ceremony. Write the migration when the change is
going somewhere that already has the table — which means **when you open the PR**.

### Adding a column

Two files, one PR:

```sql
-- src/ddl/migrations/V007__add_orders_currency.sql   (the change)
ALTER TABLE IDENTIFIER(:catalog || '.' || :schema || '.orders')
  ADD COLUMN IF NOT EXISTS currency STRING COMMENT 'ISO-4217.';
```

```sql
-- src/ddl/curated/orders.sql                          (the shape, so a NEW
--                                                      environment gets it too)
    currency STRING COMMENT 'ISO-4217.',
```

Both, or the two paths diverge: fresh environments get one schema and existing
ones get another, and nothing reports it.

### Changing a column type

Delta will not rewrite a type in place. `ALTER COLUMN ... TYPE` only **widens**
(`INT`→`BIGINT`, `FLOAT`→`DOUBLE`). Anything else is four steps across two
releases:

```sql
-- V008__add_orders_amount_decimal.sql        release N
ALTER TABLE ... ADD COLUMN IF NOT EXISTS amount_v2 DECIMAL(18,2);
UPDATE ... SET amount_v2 = CAST(amount AS DECIMAL(18,2)) WHERE amount_v2 IS NULL;

-- ... readers move to amount_v2, ship it, confirm nothing reads `amount` ...

-- V011__drop_orders_amount_double.sql        release N+1
ALTER TABLE ... DROP COLUMN amount;
ALTER TABLE ... RENAME COLUMN amount_v2 TO amount;
```

`DROP` and `RENAME COLUMN` need column mapping enabled on the table:

```sql
ALTER TABLE ... SET TBLPROPERTIES ('delta.columnMapping.mode' = 'name');
```

The one-step alternative — rewriting the table with `overwriteSchema` — is
**rejected by `check_no_schema_clobber`** in this repo, because it discards the
declared shape and silently undoes applied migrations. If you genuinely want it
for a table nothing depends on yet, drop the table and let the shape DDL rebuild
it; do not reach for `overwriteSchema` in a job.

### The rules the tooling enforces

`V<digits>__<lower_snake_case>.sql`, and the PR audit plus the job both refuse:

| Refused | Because |
|---|---|
| Bad filename | Ordering has to be unambiguous |
| Duplicate version | Two branches used V007; the second to merge would apply in an order nobody chose |
| Out-of-order arrival | V005 pending while V006 is applied — a stale branch merged late, and V005 was written against a schema that no longer exists |
| Applied file edited afterwards | Two environments ran different text. Checksum drift. Fix forward |
| Applied file deleted | A migration that has run is a historical record |

Gaps in numbering are fine. Reserve a number when you start.

### There is no rollback

Delta has no transactional DDL, so "down" migrations are a comfortable fiction.
The recovery path is a **new forward migration**, or `RESTORE TABLE ... TO
VERSION AS OF`. Write migrations you would be willing to run twice.

---

## 8. Adding a whole new module

```bash
databricks bundle init ./templates/use-case-bundle --output-dir bundles
```

Answer the prompts (module name, owning team, compute style). Then, in order:

1. Add the module to [`CODEOWNERS`](../CODEOWNERS).
2. Copy `.azure-pipelines/cd-landing.yml` to `cd-<module>.yml` and change the three
   module-specific lines: pipeline `name`, `bundlePath`, `runAfterDeploy`.
3. Register the pipeline (`az pipelines create …`, or re-run the bootstrap script).
4. Deploy your sandbox and confirm it works.

The cross-reference audit **fails until step 2 is done**. That is deliberate: a
bundle with no pipeline can never be deployed, and a silent gap there is worse than a
red build.

---

## 9. Review checklist

Before you request review:

- [ ] `pwsh ./scripts/dev/Validate-All.ps1` passes
- [ ] No environment name, host, catalog or workspace ID in `resources/` or `src/`
- [ ] New job passes the five base parameters
- [ ] New job has `email_notifications.on_failure` and `tags`
- [ ] New logic lives in the wheel and has a test, not in the notebook
- [ ] A change under `libs/` was considered against **all three** modules
- [ ] A new source in `conf/<use_case>/sources.yml` has an owner, a secret scope and the right strategy
- [ ] Schedule fits the ingestion (02:00) → curation (04:00) → datamart (06:00) order
- [ ] Names follow [12 — Conventions](12-conventions.md)

---

[← Developer guide](03-developer-guide.md) · [Next: CI/CD pipelines →](05-cicd-pipelines.md)
