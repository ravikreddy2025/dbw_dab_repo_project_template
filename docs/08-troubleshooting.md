# 08 — Troubleshooting

[← Release process](07-release-process.md) · [Start here](00-START-HERE.md)

The errors you will actually hit, with the exact text. Use your browser's find.

---

## CLI and setup

### `Error: unknown command "bundle" for "databricks"`

You have the deprecated v0.17 Python CLI. `pip install databricks-cli` installs the
wrong thing — it has no `bundle` command.

```bash
pip uninstall databricks-cli
winget install Databricks.DatabricksCLI
databricks --version        # must be 0.240.0 or later
```

### `Error: cannot resolve bundle auth configuration`

Not authenticated, or authenticated to a different host than the target expects.

```bash
databricks auth login --host https://adb-0000000000000001.1.azuredatabricks.net
databricks current-user me
```

Check that the host matches `workspace.host` for the target you are deploying.

### `Error: requires databricks_cli_version >= 0.240.0`

Your CLI is older than the bundle's pin. Upgrade:

```bash
winget upgrade Databricks.DatabricksCLI
```

If you cannot upgrade, do not lower the pin — the agent uses the pinned version, and
lowering it means your laptop and CI behave differently.

---

## Deploy failures

### `Error: path ... is not contained in bundle root path`

<a name="bundle-root"></a>
A library, notebook or file path points outside the bundle folder. Usually a shared
wheel referenced as `../../libs/dab_common/dist/*.whl`.

**Fix:** build the shared wheel *into* the bundle's `dist/`:

```bash
pwsh ./scripts/dev/Build-Wheels.ps1 -Bundle landing
```

Then reference it as `./dist/dab_common-*.whl`. `Deploy-Sandbox.ps1` does this for
you. Background: [04 §6](04-bundle-authoring.md#6-wheels).

### `Error: no such file or directory: dist/dab_common-*.whl`

Same cause. Run `Build-Wheels.ps1` before `databricks bundle deploy`.

### `Error: Invalid schema name: [dev jsmith] us1`

<a name="schema-prefix"></a>
You declared a `resources.schemas` entry in a use-case bundle. In `mode: development`
DABs prefixes resource names with `[dev <you>] `, which is not a legal Unity Catalog
schema name.

**Fix:** do not declare schemas in use-case bundles.

- Shared schemas belong in
  [`bundles/_platform/resources/schemas_curated.yml`](../bundles/_platform/resources/schemas_curated.yml)
  — that bundle has no development-mode target.
- Sandbox schemas are created at runtime:

  ```python
  from dab_common.config import ensure_schema
  ensure_schema(spark, ctx, "curated")     # no-ops outside a sandbox
  ```

There is an experimental preset (`experimental.skip_name_prefix_for_schema: true`)
that also works. This repo does not use it: the runtime approach works on every CLI
version and needs no experimental flag.

### `Error: deployment lock acquired by another user`

Someone else is deploying the same bundle to the same target, or a previous deploy
died holding the lock.

Wait. If it is stale:

```bash
databricks bundle deploy --target nonprod --force-lock
```

Only use `--force-lock` when you are certain nothing else is running. In `dev` targets
the lock is disabled by `mode: development`, so this only occurs on shared targets.

### `Error: cannot find cluster policy "edp-etl-standard"`

The lookup could not resolve. Either the policy does not exist in **this** workspace,
or it is named differently there.

The convention this repo depends on: **the same name in all three workspaces**.

```bash
databricks cluster-policies list --output json | grep -i etl
```

Fix the policy name in the workspace, not the lookup in the bundle. Policies are not
a bundle resource type — see [06 §6](06-environments-and-access.md#6-cluster-policies).

### `Error: cannot find warehouse "edp-sql-warehouse"`

Same cause, for the datamart's `lookup: warehouse:`. Create the warehouse with that
exact name in the target workspace.

### `Error: PERMISSION_DENIED: User does not have CREATE SCHEMA on catalog`

The deploy SP (or you, in a sandbox) lacks the grant.

- In a sandbox: `edp-developers` needs `CREATE SCHEMA` on `edp_curated_nonprod`. Granted by
  the platform bundle via `writer_group`.
- In a shared environment: the deploy SP needs it. See
  [06 §3](06-environments-and-access.md#3-service-principals).

### `Error: target "preprod" is in production mode but run_as is not set`

A production-mode target must declare `run_as`. The structural check catches this
before deploy:

```bash
python scripts/ci/validate_bundle_yaml.py bundles/landing
```

### `Error: cannot use --cluster-id with a target in production mode`

Working as designed. Cluster override is a development-mode convenience only, so it
can never accidentally apply to a shared environment. Use `--target dev`.

---

## Git and CI

### `Error: not on the right Git branch` on deploy

<a name="detached-head"></a>
A target has `git.branch` set, and the CLI cannot match it. In Azure DevOps this
happens because `checkout` leaves you in **detached HEAD**, so there is no branch name
to compare.

**This repo does not use `git.branch` for exactly this reason.** If you have added it,
remove it. Branch control belongs in the pipeline `trigger` blocks and Azure Repos
branch policies, where it cannot be bypassed with `--force`.

If you must keep it, `--force` overrides the check — but that trains everyone to pass
`--force`, which defeats the purpose.

### PR validation cannot diff against the target branch

`fatal: ambiguous argument 'origin/main...HEAD'`

The checkout was shallow. `ci-pr-validation.yml` sets `fetchDepth: 0` for this reason.
If you copied a step into a new pipeline, carry that setting across.

### The pipeline did not trigger

Check the `trigger.paths.include` block. A change to `docs/` or any `*.md` is excluded
on purpose.

A change under `libs/` triggers **all four** CD pipelines, because all four embed that
wheel.

### The `Build` stage passes but `DeployProd` never runs

By design. Prod only runs from a `release/*` branch:

```yaml
condition: and(succeeded(), startsWith(variables['Build.SourceBranch'], 'refs/heads/release/'))
```

From `main`, only `DeployNonProd` runs.

### `runAfterDeploy 'xyz' is not a job key`

The cross-reference audit caught a smoke job name that does not match any job in the
bundle. Usually a job was renamed and the pipeline was not updated. Compare the
pipeline's `runAfterDeploy` against the job keys in `resources/*.job.yml`.

---

## Runtime failures

### `ConfigError: Missing required job parameter(s): use_case`

The job does not pass the five base parameters. Every job needs:

```yaml
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
```

Declare them at **job** level, not per task — `for_each` sub-tasks inherit job-level
parameters.

### `ModuleNotFoundError: No module named 'dab_common'`

The wheel is not in the job's libraries.

- Serverless: check `environments[].spec.dependencies` includes
  `./dist/dab_common-*.whl`.
- Job cluster: check the task's `libraries:` block.
- Then confirm the wheel is actually in `dist/` — run `Build-Wheels.ps1`.

### The job ran the old code

Wheel caching. `dynamic_version: true` on the `artifacts:` entry exists to prevent
this by appending a content-based suffix. If it still happens:

```bash
rm -rf bundles/landing/dist
pwsh ./scripts/dev/Build-Wheels.ps1 -Bundle landing
databricks bundle deploy --target dev
```

For a job cluster, a running cluster may hold the old wheel — restart it.

### `ControlError: ... ingestion_source is empty`

The control tables have not been seeded in this environment.

```bash
databricks bundle run landing_seed_source_registry --target dev
```

### `ControlError: Requested source_id(s) not active/known: ['ora_foo']`

You asked for a source that is missing or `is_active: false`. Deliberately loud — an
empty result would look identical to a successful no-op load.

```sql
SELECT source_id, is_active FROM edp_ops_nonprod.config.landing_source ORDER BY source_id;
```

### `SeedError: ... unknown key(s) ['watermarkColumn']`

A typo in `conf/<use_case>/sources.yml`. Keys are snake_case and are validated against a fixed list, so
a misspelled key is rejected rather than silently ignored — a silently dropped key is
a config bug that shows up as missing data weeks later.

Catch it before you push:

```bash
pytest bundles/landing/tests/test_seed_files.py -q
```

### `DataQualityFailure: ... expectation(s) failed`

The data is bad, and the gate did its job. Look at what failed:

```sql
SELECT * FROM edp_ops_nonprod.audit.data_quality_result
WHERE run_id = '<the run id>' AND NOT passed;
```

Then decide: is the data wrong, or is the expectation wrong? Both happen. If the
expectation is wrong, change it in code — do not disable the gate.

`${var.dq_fail_on_error}` exists so a lead can turn the gate off for one environment
during an incident. It is `"true"` everywhere by default, including sandboxes, so
nobody is surprised in preprod by a check that never ran in dev.

### The Oracle load duplicated rows

Check the load strategy. `incremental` appends, and landing is append-only by design —
duplicates are removed in curation with `dedupe_by_key`.

If duplicates reached **curated**, the dedup key is wrong. Check `primary_keys` in the
control row and the `keys=` argument in the curation task.

### The watermark went backwards / rows were skipped

`advance_watermark` refuses to move backwards — `WHEN MATCHED AND s.watermark_value >
t.watermark_value`. If rows were skipped, the more likely cause is that the source
system back-dated a row so it fell below the current watermark.

```sql
SELECT * FROM edp_ops_prod.config.landing_watermark WHERE source_id = 'ora_customers';
```

To reload from a point:

```sql
UPDATE edp_ops_prod.config.landing_watermark
SET watermark_value = '2026-08-01T00:00:00.000', updated_at = current_timestamp()
WHERE source_id = 'ora_customers';
```

Then rerun. This is a production data change — do it with a lead, and record it.

### The Kafka stream will not resume

Checkpoint state. Checkpoints live at
`/Volumes/<catalog>/<schema>/_checkpoints/kafka/<source_id>`, so they are isolated per
environment and per developer.

Never delete a checkpoint in a shared environment without understanding the
consequence: the stream restarts from `startingOffsets`, which for most sources here
is `earliest` — a full replay.

In a sandbox, deleting it is a legitimate way to start clean.

---

## "It works in my sandbox but not in nonprod"

Nearly always one of four things.

| Symptom | Cause |
|---|---|
| Writes to the wrong schema | You tested with `schema_prefix` set; shared has `""`. Check `ctx.table(...)` output. |
| Permission denied | Your grants ≠ the run-as SP's grants. Compare against [06 §4](06-environments-and-access.md#4-unity-catalog-grants). |
| Schedule fires unexpectedly | Sandboxes pause schedules; shared targets do not. |
| Cluster behaves differently | You used `--cluster-id`. Shared targets always create job clusters from the policy. |

Reproduce a shared deploy locally against nonprod — you have access:

```bash
cd bundles/landing
databricks bundle validate --target nonprod
```

---

## Diagnostic commands

```bash
# What does the resolved config actually say?
databricks bundle validate --target dev --output json

# What is deployed right now, with links?
databricks bundle summary --target dev

# Open a resource in the browser
databricks bundle open landing_us2 --target dev

# Run with a variable overridden
databricks bundle deploy --target dev --var="max_workers=4"

# Everything the PR build runs
pwsh ./scripts/dev/Validate-All.ps1
```

Useful audit queries:

```sql
-- Recent failures across every module
SELECT started_at, module, task_key, status, error_message
FROM edp_ops_nonprod.audit.job_run
WHERE status = 'FAILED' AND started_at >= current_date() - INTERVAL 7 DAYS
ORDER BY started_at DESC;

-- Did last night land?
SELECT source_id, target_table, rows_written, status, loaded_at
FROM edp_ops_nonprod.audit.table_load
WHERE loaded_at >= current_date() - INTERVAL 1 DAY
ORDER BY loaded_at DESC;

-- What is failing quality checks?
SELECT table_name, expectation_name, rows_failed, rows_evaluated, evaluated_at
FROM edp_ops_nonprod.audit.data_quality_result
WHERE NOT passed AND evaluated_at >= current_date() - INTERVAL 7 DAYS;
```

---

## Still stuck

1. `pwsh ./scripts/dev/Validate-All.ps1` — it catches most structural problems.
2. `databricks bundle validate --target <t> --output json` — see the fully resolved
   config, with every variable and lookup substituted.
3. Search `ops.audit.job_run` for the run ID; `error_detail` holds the full traceback.
4. Ask in the team channel with: the exact error, the bundle, the target, and your
   `databricks --version`.

---

[← Release process](07-release-process.md) · [Next: Walkthrough →](09-walkthrough-simulation.md)
