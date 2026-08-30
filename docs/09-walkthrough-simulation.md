# 09 — Walkthrough: two weeks in the life of the team

[← Troubleshooting](08-troubleshooting.md) · [Start here](00-START-HERE.md)

A full cycle, narrated. Two developers on different use cases, a review, a release,
a QA bug, a production hotfix — and a cutover. Every command is real and
corresponds to something that exists in this repository.

**Cast:** Jaya (us2, Oracle), Arun (us1, Kafka), Priya (platform lead),
Sam (QA), Client approver.

---

## Day 1 — Jaya onboards

Jaya joined this morning, on the us2 team. Priya sends her
[00 — Start here](00-START-HERE.md).

```bash
winget install Databricks.DatabricksCLI
databricks --version
# Databricks CLI v0.240.0
```

```bash
git clone https://dev.azure.com/contoso/EDP/_git/edp-databricks
cd edp-databricks
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements-dev.txt
pip install -e libs/dab_common -e libs/edp_landing
```

```bash
databricks auth login --host https://adb-0000000000000001.1.azuredatabricks.net
databricks current-user me
# { "userName": "jaya@example.com", ... }
```

Only nonprod. She has no access to preprod or prod, and will not need any.

Her first deploy:

```bash
pwsh ./scripts/dev/Deploy-Sandbox.ps1 -Bundle us2
```

```
Databricks CLI: Databricks CLI v0.240.0
Building dab_common -> bundles\us2\dist
    dab_common-0.4.0-py3-none-any.whl
Validating bundles\us2 against target 'dev'...
Deploying to your sandbox...

Name: edp_us2
Target: dev
Workspace:
  User: jaya@example.com
  Path: /Workspace/Users/jaya@example.com/.bundle/edp_us2/dev
Resources:
  Jobs:
    us2_curated:    Name: [dev jaya] us2_curated
    us2_datamart:   Name: [dev jaya] us2_datamart
```

Two jobs, not three. Reconciliation against Cloudera is **not** in her bundle - it
belongs to QA, in `bundles/recon`. She can still run it against her own sandbox
tables when she wants to check a port:

```bash
pwsh ./scripts/dev/Deploy-Sandbox.ps1 -Bundle recon
databricks bundle run recon_us2 --target dev
```

Twenty minutes in and she has her own isolated copy of us2. Her jobs are prefixed
`[dev jaya]`, her schedules are paused, and her tables will land in
`edp_curated_nonprod.jaya_us2`.

She checks where her data will go:

```python
from dab_common.config import build_context
ctx = build_context({"env": "nonprod", "use_case": "us2", "schema_prefix": "jaya_"})
ctx.table("landing",  "ora_customers")  # edp_landing_nonprod.jaya_us2.ora_customers
ctx.table("curated",  "customers")      # edp_curated_nonprod.jaya_us2.customers
ctx.audit_table("job_run")              # edp_ops_nonprod.jaya_audit.job_run
```

Four catalogs, one schema each, all hers.

---

## Day 2 — Two developers, two use cases, one workspace

### Jaya: DAB-123, onboard the invoices table (us2, Oracle)

```bash
git checkout main && git pull
git checkout -b feature/DAB-123-onboard-invoices
```

She does not write a notebook. She adds a row to the **shared landing bundle**, in
her use case's folder —
[`bundles/landing/conf/us2/sources.yml`](../bundles/landing/conf/us2/sources.yml):

```yaml
  - source_id: us2_ora_invoices
    source_system: oracle
    source_object: SALES.INVOICES
    target_table: ora_invoices
    load_strategy: incremental
    watermark_column: INVOICE_UPDATED_TS
    primary_keys: [INVOICE_ID]
    secret_scope: edp-oracle
    owner_email: edp-us2-team@example.com
    options:
      partition_column: INVOICE_ID
      lower_bound: 1
      upper_bound: 2000000
      num_partitions: 4
```

```bash
pytest bundles/landing/tests/test_source_files.py -q
# ...................                                    [100%]
```

Her row passed every validation: strategy consistent with watermark, `source_id`
prefixed with her use case, partition bounds complete, owner present, secret scope
one the platform bundle actually declares.

Note what she did **not** do: she did not touch `bundles/us2/`. Landing is a
separate bundle with a separate pipeline. And she did not need a landing-team
reviewer — `CODEOWNERS` routes `conf/us2/` to the us2 team.

### Arun: DAB-124, currency conversion in us1 curated

At the same moment, in the same workspace:

```bash
git checkout -b feature/DAB-124-currency-conversion
```

He edits [`us1_module/curated.py`](../bundles/us1/src/us1_module/curated.py) and
writes the test first, then:

```bash
pytest bundles/us1/tests -q
pwsh ./scripts/dev/Deploy-Sandbox.ps1 -Bundle us1 -Run us1_curated
```

### They do not collide

Same workspace, same hour. Nothing overlaps:

| | Jaya | Arun |
|---|---|---|
| Bundle | `landing` | `us1` |
| Jobs | `[dev jaya] landing_us2` | `[dev arun] us1_curated` |
| Files | `/Workspace/Users/jaya@…/.bundle/…` | `/Workspace/Users/arun@…/.bundle/…` |
| Curated | `edp_curated_nonprod.jaya_us2` | `edp_curated_nonprod.arun_us1` |
| Registry | `edp_ops_nonprod.jaya_config` | `edp_ops_nonprod.arun_config` |
| Schedules | paused | paused |

That last row matters most. Jaya is seeding a source registry in her sandbox. If
`ops.config` were shared, her test rows would land in the registry that shared
nonprod jobs read — and Arun's next nonprod run would try to load her invoices
table. **The one rule** prevents it: every schema is prefixed in a sandbox, ops
included.

---

## Day 3 — Pull requests

Jaya runs the full local check first:

```bash
pwsh ./scripts/dev/Validate-All.ps1
```

```
=== Lint (ruff) ===
All checks passed!
=== Unit tests (excluding Spark) ===
................................................................ [100%]
=== Bundle structure ===
  OK  bundles/_platform ... bundles/us5
=== Cross-reference audit ===
Cross-reference audit: all references resolve.

All checks passed. Safe to push.
```

She pushes and opens a PR into `main`. `ci-pr-validation` diffs against `main`,
sees only `bundles/landing/`, and validates just that bundle — Arun's us1 change is
not rebuilt.

Priya reviews. The build already checked syntax and tests, so she looks at
judgement:

> *Partitioned read on `INVOICE_ID` — is that column indexed? If not, four
> partitions will be slower than one. Also: `upper_bound: 2000000` — what is the
> current max, and what is the growth rate?*

Jaya confirms the index with the DBA and notes the max is 1.4M. Priya and a
teammate approve. **Squash merge.**

`cd-landing` triggers, builds, deploys to shared nonprod, and runs
`landing_seed_source_registry` as the smoke job — so `us2_ora_invoices` reaches
`edp_ops_nonprod.config.landing_source` in the same deployment as the code that
reads it.

Arun's PR merges the same afternoon. `cd-us1` runs. `cd-landing` does not — path
filters.

---

## Day 8 — Priya cuts the release

```bash
git checkout main && git pull
git log --oneline $(git describe --tags --abbrev=0)..HEAD
```

```
a3f2e11 DAB-131: add settlement status to us5 curated
9c81d40 DAB-124: currency conversion in us1 curated
7b4a992 DAB-123: onboard SALES.INVOICES for us2
```

```bash
git checkout -b release/2026.09.1
git push -u origin release/2026.09.1
```

Three pipelines trigger — `cd-landing`, `cd-us1`, `cd-us5`. Not `cd-platform`
(nothing under `bundles/_platform/` changed), not `cd-us2`, `cd-us3`, `cd-us4`.

Each builds, then **stops at the preprod gate**. Nothing has deployed.

Priya writes release notes, confirms nonprod has been green for a week, and
approves the three `dbx-preprod` gates. She approves `cd-landing` first and lets it
finish — us1 and us5 read data it lands.

---

## Day 9 — Sam finds a bug

Sam has `edp-qa` rights in preprod: view jobs, trigger runs, read data. No deploy
rights.

```sql
SELECT use_case, task_key, status, duration_seconds, error_message
FROM edp_ops_preprod.audit.job_run
WHERE started_at >= current_date() ORDER BY started_at DESC;
```

All `SUCCESS`. Data landed. Then:

```sql
SELECT use_case, table_name, expectation_name, rows_failed, rows_evaluated
FROM edp_ops_preprod.audit.data_quality_result
WHERE evaluated_at >= current_date() AND NOT passed;
```

```
us1   edp_curated_preprod.us1.orders   status_not_null   1,204   84,551
```

1,204 orders with a null status. Sam checks landing and finds a status the code
does not recognise: `RETURNED`. `conform_orders` nulls unrecognised statuses rather
than dropping the row — the value is intact — but the gate is correctly flagging
it.

He raises **DAB-140** with the run ID, the table, the count and the offending
value.

### The fix goes on the release branch

Arun branches from `release/2026.09.1`, **not** from `main`:

```bash
git checkout release/2026.09.1 && git pull
git checkout -b bugfix/DAB-140-returned-status
```

Test first, in [`test_us1.py`](../bundles/us1/tests/test_us1.py):

```python
def test_returned_is_a_valid_status():
    assert "RETURNED" in VALID_STATUSES
```

Red. Then the one-word fix in
[`us1_module/curated.py`](../bundles/us1/src/us1_module/curated.py):

```python
VALID_STATUSES = ("NEW", "ACTIVE", "SETTLED", "CANCELLED", "RETURNED")
```

Green.

```bash
pwsh ./scripts/dev/Deploy-Sandbox.ps1 -Bundle us1 -Run us1_curated
pwsh ./scripts/dev/Validate-All.ps1
git push -u origin bugfix/DAB-140-returned-status
```

PR **into `release/2026.09.1`**. Priya approves, merge, `cd-us1` runs again from the
release branch, preprod redeploys. Sam retests — zero failures — and records
**sign-off**.

> Note what did not happen. Nobody edited a notebook in the preprod workspace. The
> fix is a commit, tested and reviewed like any other change, and it is permanently
> in the release branch rather than living in a workspace until the next deploy
> erases it.

---

## Day 10 — Production, and the back-merge

The client approver reads the release notes and Sam's sign-off, and approves
`dbx-prod`. The prod stage deploys **the same commit and the same wheels** preprod
ran — the artifact from that run's Build stage, not a rebuild.

```bash
git tag -a v2026.09.1 -m "Release 2026.09.1" && git push origin v2026.09.1
```

The release is **not finished**. Arun's `RETURNED` fix exists only on the release
branch:

```bash
git checkout main && git pull
git checkout -b backmerge/release-2026.09.1
git merge origin/release/2026.09.1
```

A conflict in `curated.py` — `main` has moved on. Arun resolves it, keeping both
changes, validates, and opens a PR into `main` titled
`Back-merge release/2026.09.1`. Merged. **Now** the release is closed.

---

## Day 12 — A production incident, and a gap in the guardrails

02:00. `landing_us2` fails in prod.

```sql
SELECT use_case, task_key, status, error_message
FROM edp_ops_prod.audit.job_run
WHERE status = 'FAILED' AND started_at >= current_date() - INTERVAL 1 DAY;
```

```
us2  land_one_table  FAILED  OracleIngestionError: us2_ora_invoices:
     options.partition_column is set but ['lower_bound', 'upper_bound',
     'num_partitions'] are missing. A partitioned JDBC read needs all four
     settings or none.
```

Someone tuned the invoices extract during the release, adding a `partition_column`
and dropping the three bound settings that must accompany it.

### Why did this reach production?

Worth sitting with rather than skipping.

The seed tests validated a great deal — strategy consistent with watermark,
`source_id` prefixed, owner present. But `options` is a free-form map of *framework*
settings, not registry columns, so **nothing checked it**. The inconsistency was
caught only by `build_read_options`, at runtime, in whichever environment ran first.

Preprod did not catch it either: the tuning went in after QA signed off, as a
"config-only" change that felt too small to retest.

Not a process failure so much as a **missing guardrail**. The repo made the mistake
possible.

So the hotfix is two things, and the second matters more.

```bash
git checkout -b hotfix/DAB-155-partition-bounds v2026.09.1
```

**1. Restore the bounds** in `conf/us2/sources.yml`.

**2. Make it impossible to repeat.** `validate_options()` in
[`seed.py`](../bundles/landing/src/landing_module/seed.py), wired into
`load_seed_file`, so an inconsistent partition config is rejected at PR time — plus
seven regression tests covering the neighbouring mistakes nobody had made *yet*:
inverted bounds, non-integer bounds, zero partitions.

```bash
pytest bundles/landing/tests -q
# ........................................              [100%]
```

New release branch from the tag, both gates, `v2026.09.2`, back-merge.

**The gates stayed.** An incident is a bad reason to skip them, and this path is
under two hours.

---

## Week 6 — us2 cuts over

us2 has been running in parallel with Cloudera for a month. **Sam** checks the
evidence - reconciliation is QA's, and he has `CAN_MANAGE_RUN` on the recon jobs in
prod, so he can re-check on demand without any deploy rights:

```sql
SELECT * FROM edp_ops_prod.recon.cutover_readiness WHERE use_case = 'us2';
```

```
use_case  env   total_runs  clean_runs  last_run_at          last_run_clean
us2       prod  28          28          2026-10-04 08:03:52  true
```

28 consecutive clean runs in prod, on top of 14 in preprod, and zero skipped. One
tolerance is open — the `amount_sum` float-vs-decimal difference — justified in
[`bundles/recon/conf/us2.yml`](../bundles/recon/conf/us2.yml) and signed off by Sam
and the client.

When Sam widened that tolerance three weeks earlier, the PR triggered `cd-recon`
and **nothing else**. The us2 curated and datamart jobs were not rebuilt, not
redeployed and not restarted. A validation change should never be able to touch
production ETL, and here it structurally cannot.

The window included a month-end close. Consumers are identified and repointed.
Rollback has been rehearsed, not just documented.

us2 cuts over. Cloudera goes **read-only, not off** — and `recon_enabled` stays
`"true"`, because the daily comparison is still worth having.

Six weeks later, after a full business cycle, a final PR deletes
`bundles/recon/conf/us2.yml` and `bundles/recon/resources/recon_us2.job.yml`. The
config test fails if you remove one and not the other. That PR runs `cd-recon`
alone - the last commit of the us2 migration, and it touches no ETL at all.

Full gate: [13 — Migration and cutover](13-migration-and-cutover.md).

---

## What made this work

| Moment | What made it possible |
|---|---|
| Jaya productive in 20 minutes | One script, and a sandbox that cannot break anything |
| Two developers, one workspace, zero conflicts | `mode: development` + `schema_prefix` |
| Jaya's test registry not poisoning nonprod | **The one rule** — ops schemas prefixed too |
| Onboarding a table with no code | Metadata-driven landing, validated at PR time |
| Onboarding needing no landing-team reviewer | Per-use-case `conf/` folders in `CODEOWNERS` |
| Only the changed bundles rebuilt | One pipeline per bundle, path filters |
| Prod running exactly what QA approved | Same commit, same wheels, across all stages |
| The preprod bug fixed permanently | The fix was a commit on the release branch |
| The fix not lost next month | The mandatory back-merge |
| The 02:00 failure diagnosed in one query | Every job writes to `ops.audit.job_run` |
| A QA tolerance change not redeploying prod ETL | Recon is its own bundle with its own pipeline |
| A use case unable to write its own exam results | Only the recon SP has MODIFY on `ops.recon` |
| It never recurring | The hotfix closed the guardrail, not just the symptom |
| Cutover being a decision, not a leap | 28 clean parity runs in a table anyone can query |

One honest note: the day-12 incident happened because a validation gap existed.
Every guardrail in this repo exists because something like that was possible. When
you find the next gap, close it the way Jaya did — fix the symptom, then add the
check that makes the class of mistake impossible.

---

## Try it yourself

You cannot deploy without a workspace, but everything else runs now:

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements-dev.txt
pip install -e libs/dab_common -e libs/edp_landing -e libs/edp_recon
pip install -e bundles/landing -e bundles/recon -e bundles/us1 -e bundles/us2

pytest -q
python scripts/ci/validate_bundle_yaml.py bundles/_platform bundles/landing bundles/recon bundles/us1
python scripts/ci/check_bundle_references.py
```

Then break something on purpose — rename `us1_curated` in
`bundles/us1/resources/curated.job.yml` without updating
`.azure-pipelines/cd-us1.yml` — and watch the cross-reference audit catch it.

---

[← Troubleshooting](08-troubleshooting.md) · [Next: Onboarding checklists →](10-onboarding-checklist.md)
