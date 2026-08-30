# 03 — Developer guide

[← Branching strategy](02-branching-strategy.md) · [Start here](00-START-HERE.md)

Everything you need on day one. Windows-first, because the team is on Windows;
macOS and Linux notes are inline where they differ.

---

## 1. Install the tools

### Databricks CLI

```bash
winget install Databricks.DatabricksCLI
```

macOS: `brew tap databricks/tap && brew install databricks`.
Linux / WSL: `curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh`

Check it:

```bash
databricks --version
```

You need **v0.240.0 or later**. The version is pinned in
[`templates/vars/common.yml`](../.azure-pipelines/templates/vars/common.yml) and in
every bundle's `databricks_cli_version`, so your laptop and the build agent agree.

> ### The mistake everybody makes once
>
> **`pip install databricks-cli` installs the wrong thing.** That is the deprecated
> v0.17 Python CLI, which has no `bundle` command at all. If `databricks bundle
> validate` says `Error: unknown command "bundle"`, this is why. Uninstall it
> (`pip uninstall databricks-cli`) and install the real CLI as above.

### Python

Python 3.11, matching the pipeline and the Databricks runtime.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
pip install -e libs/dab_common
```

### VS Code (recommended)

Install the **Databricks** extension. It gives you bundle target switching, job runs
from the editor, and notebook sync. It reads the same `databricks.yml`, so it and the
CLI never disagree.

---

## 2. Authenticate

Once, per workspace:

```bash
databricks auth login --host https://adb-0000000000000001.1.azuredatabricks.net
```

A browser opens; sign in with your Azure AD account. The CLI stores an OAuth token
and refreshes it. There is nothing to paste and nothing to rotate.

Verify:

```bash
databricks current-user me
```

> You authenticate to **nonprod only**. Developers have no interactive access to
> preprod or prod — those are reached exclusively by the CD pipeline's service
> principal. That is the point. See [06](06-environments-and-access.md).

### Do not use personal access tokens

PATs are user-scoped, expire, and end up pasted into files. OAuth via `auth login`
is the supported path for humans; workload identity federation is the path for CI.
Neither needs a stored secret.

---

## 3. Deploy your sandbox

```bash
git clone <your Azure Repos URL>
cd repo_setup
pwsh ./scripts/dev/Deploy-Sandbox.ps1 -Bundle landing
```

That script checks your CLI and auth, builds the shared wheel into the bundle's
`dist/`, validates, deploys to the `dev` target, and prints what it created.

### What you just got

Suppose you are `jsmith@example.com`:

| | Value |
|---|---|
| Jobs | `[dev jsmith] landing_us2` |
| Files | `/Workspace/Users/jsmith@example.com/.bundle/edp_landing/dev/` |
| Tables | `edp_landing_nonprod.jsmith_us1.ora_customers` |
| Schedules | **paused** |
| Runs as | you |

None of it can touch a colleague's work or shared nonprod. Deploy as often as you
like.

### Run something

```bash
cd bundles/landing
databricks bundle run landing_us2 --target dev
```

Schedules are paused in a sandbox, so `bundle run` is how you trigger anything.

Narrow it to one table while you iterate:

```bash
databricks bundle run landing_us2 --target dev --params source_ids=ora_customers
```

### See what you have deployed

```bash
databricks bundle summary --target dev
```

---

## 4. The fast inner loop

A job cluster takes a few minutes to start. When you are iterating on logic, that is
the whole cost of the loop. Reuse a running all-purpose cluster instead:

```bash
pwsh ./scripts/dev/Deploy-Sandbox.ps1 -Bundle landing -ClusterId 0812-164512-abc123de
```

or directly:

```bash
databricks bundle deploy --target dev --cluster-id 0812-164512-abc123de
```

This overrides **every** cluster definition in the bundle. It only works in
development mode — production targets reject it, by design, so this can never
accidentally apply to a shared environment.

Get your cluster ID from the workspace UI, or:

```bash
databricks clusters list --output json
```

### Even faster: test without a cluster at all

Most of the logic in this repo is pure and runs locally:

```bash
pytest libs/dab_common/tests -q
pytest bundles/landing/tests -q
```

Two seconds, no cluster. If you are changing watermark predicates, control-table
filtering, seed-file parsing or grant logic, **this is the loop** — deploy only once
it passes.

Spark-backed tests are marked `integration` and skipped unless you have pyspark:

```bash
pip install pyspark
pytest bundles/us1/tests -q          # now includes the Spark tests
```

---

## 4a. Working with shared data — reads vs writes

<a name="reading-shared-data"></a>

The single most important thing to understand about sandboxes on a data project.

### Writes are isolated. Reads are shared.

| | Prefixed in a sandbox? | Why |
|---|---|---|
| What your job **writes** | **yes** — `jsmith_us1` | Ten people writing `us1.orders` overwrite each other |
| What your job **reads from upstream** | **no** — shared `us1` | Your sandbox copy is empty, and copying real volume per developer is wasteful |

Two accessors, and the call site chooses:

```python
source = ctx.upstream("landing", "kfk_orders")   # edp_landing_nonprod.us1.kfk_orders
target = ctx.table("curated", "orders")          # edp_curated_nonprod.jsmith_us1.orders
```

**In every shared environment the two return the same string**, because
`schema_prefix` is empty. The distinction exists only inside a sandbox — which is
exactly where it matters — so there is no environment branch to get wrong.

### Why not prefix reads too

Two reasons, and the first is fatal:

**Your sandbox upstream schema is empty.** `edp_landing_nonprod.jsmith_us1` does
not contain anything until *you* run the landing pipeline. A curated job reading it
processes zero rows and reports success — the worst kind of failure, because it
looks like a pass.

**Upstream data is large.** Landing is the biggest layer you have. Materialising a
copy per developer costs ten times the storage and gives ten people ten different
stale snapshots to disagree about.

### Why prefix writes, then

Because without it the sandbox is not a sandbox:

- jsmith deploys a broken transform, runs it, and shared `us1.orders` is now wrong
- apatel's datamart job reads it and either fails or produces bad marts
- nobody can say whose run produced the current state
- two developers running at once race each other

And it is not just a naming convention — **the grants enforce it**. Developers hold
`SELECT` on the shared schemas and `CREATE SCHEMA` on the catalog. They own the
sandbox schema they create and can write it freely; they physically cannot write to
`us1`. A typo produces a permission error, not a corrupted shared table.

### Chaining your own upstream

When you *are* the landing developer, or you want to test the whole chain against
your own landed data:

```bash
databricks bundle run us1_curated --target dev --params upstream_mode=sandbox
```

Per **run**, not per target — it is a temporary state while you test a chain, not
how your sandbox normally behaves.

### Keeping sandbox runs cheap

A full rebuild against real volume is fine in nonprod, where it happens once a
night. Ten developers doing it on every iteration is what makes sandboxes
expensive. Wrap upstream reads:

```python
landed = ctx.sample(spark.table(ctx.upstream("landing", "kfk_orders")))
```

`sample()` applies `dev_sample_rows` **in a sandbox only** and is a no-op
everywhere else, so the same line is correct in production. `dev_sample_rows` is
`0` in every shared target — a row cap that leaked into prod would silently
truncate real output, which is far worse than a slow sandbox.

### When you need a big, writable copy

Testing a `MERGE`, a backfill, or a schema migration needs real data you can
write to. Do not copy it — **shallow clone** it. Zero data movement, fully
writable, completely isolated:

```sql
CREATE TABLE edp_curated_nonprod.jsmith_us1.orders
SHALLOW CLONE edp_curated_nonprod.us1.orders;
```

The clone shares the underlying files until you write, then diverges. Drop it when
you are done; the shared table is untouched either way.

Use a **deep clone** only if you need the data to survive the source being
vacuumed — it does copy, so treat it as a real cost.

### The decision, in one table

| You want to… | Use |
|---|---|
| Read upstream data another pipeline produced | `ctx.upstream(layer, table)` |
| Read what *your* job wrote earlier in the chain | `ctx.table(layer, table)` |
| Write anything | `ctx.table(layer, table)` |
| Read another use case's shared output | `ctx.upstream(layer, table, use_case="us3")` |
| Test against your own upstream output | `--params upstream_mode=sandbox` |
| Keep a sandbox run cheap | `ctx.sample(...)` + `dev_sample_rows` |
| Get a big writable copy to test a MERGE | `SHALLOW CLONE` |
| Reconcile **your own** port against Cloudera | `databricks bundle run recon_us1 -t dev --params upstream_mode=sandbox` |
| Reconcile the **shared** output (QA authoring a check) | `databricks bundle run recon_us1 -t dev` |

A CI check fails the build if a use-case job reads the landing layer with
`ctx.table()` — the mistake is easy to make and silent when made.

---

## 5. Before you push

```bash
pwsh ./scripts/dev/Validate-All.ps1
```

Runs exactly what the PR build runs:

1. `ruff check .`
2. `pytest -m "not integration"`
3. Bundle structure check (targets, paths, variable declarations)
4. Cross-reference audit (pipeline templates, smoke job names, doc links)

Add `-Spark` to include the pyspark tests the build agent skips.

---

## 6. Clean up

When the feature is merged:

```bash
pwsh ./scripts/dev/Destroy-Sandbox.ps1 -Bundle landing
```

This removes your jobs, pipelines and synced files. It does **not** drop your
schemas or tables — losing a day of test data to a typo is worse than a stale schema.
Drop them yourself when you are sure:

```sql
DROP SCHEMA IF EXISTS edp_landing_nonprod.jsmith_us1 CASCADE;
```

Ten developers who never clean up leave a workspace nobody can navigate. Make it
part of closing the ticket.

---

## 7. Working with notebooks

Notebooks in this repo are **thin entry points**. Look at
[`oracle_ingest_entry.py`](../bundles/landing/src/jobs/oracle_land_entry.py) —
about twenty lines. It resolves context, looks up a source, and calls the wheel.

That is the rule: **logic goes in the wheel, orchestration goes in the notebook.**
A notebook cannot be unit tested; a wheel can. If a notebook grows past roughly fifty
lines of real logic, move that logic into `src/<module>_module/` and give it a test.

### Editing in the workspace

You can edit a notebook in the Databricks UI — it is your sandbox. But **the
workspace is not the source of truth**. Your next `bundle deploy` overwrites it.

The supported round trip:

```bash
# pull workspace edits back into the repo
databricks workspace export-dir \
  /Workspace/Users/$USER/.bundle/edp_landing/dev/files/src \
  ./bundles/landing/src --overwrite
```

Better: use the VS Code extension, which syncs continuously, or just edit locally
and redeploy. `bundle deploy` on an unchanged bundle takes seconds.

---

## 8. Adding a new Oracle table or Kafka topic

You do not write code. You add a row:

```yaml
# bundles/landing/conf/us2/sources.yml
  - source_id: us2_ora_invoices
    source_system: oracle
    source_object: SALES.INVOICES
    target_table: ora_invoices
    load_strategy: incremental
    watermark_column: INVOICE_UPDATED_TS
    primary_keys: [INVOICE_ID]
    secret_scope: edp-oracle
    owner_email: edp-landing-team@example.com
```

Then:

```bash
pytest bundles/landing/tests/test_seed_files.py -q     # validates your row
pwsh ./scripts/dev/Deploy-Sandbox.ps1 -Bundle landing
databricks bundle run landing_seed_source_registry --target dev
databricks bundle run landing_us2 --target dev --params source_ids=ora_invoices
```

The seed job prints a plan before it writes. Run it with `dry_run=true` first if you
want to see the diff without applying it.

---

## 9. Commands you will use

| Command | What it does |
|---|---|
| `databricks bundle validate --target dev` | Check config; resolves lookups against the workspace |
| `databricks bundle deploy --target dev` | Deploy your sandbox |
| `databricks bundle run <job> --target dev` | Run a job and stream its output |
| `databricks bundle summary --target dev` | List what you have deployed, with links |
| `databricks bundle destroy --target dev` | Remove your sandbox deployment |
| `databricks bundle open <job> --target dev` | Open the resource in your browser |
| `databricks bundle deploy --target dev --var="max_workers=4"` | Override a variable for one deploy |

Always pass `--target`. The default is `dev`, but being explicit is a habit worth
having before you ever type a command with `preprod` in it.

---

## 10. Things that will trip you up

**"My schedule did not fire."** Correct. `mode: development` pauses every schedule.
Use `bundle run`.

**"My job is called `[dev jsmith] …` and I did not do that."** Also correct — that is
`mode: development` keeping your jobs distinguishable from the other nine developers'.

**"I deployed but my table is not in the shared schema."** It is in `jsmith_us1`.
Your sandbox WRITES to your own schemas and READS upstream from the shared ones —
see [§4a](#reading-shared-data).

**"`bundle deploy` says a wheel is missing."** Run `Build-Wheels.ps1` first, or use
`Deploy-Sandbox.ps1`, which does it for you.

**"Another developer's job appeared in my summary."** It did not — `bundle summary`
only shows your target. You are probably looking at the workspace Jobs list, which
shows everyone's.

More, with the exact error text: [08 — Troubleshooting](08-troubleshooting.md).

---

[← Branching strategy](02-branching-strategy.md) · [Next: Bundle authoring →](04-bundle-authoring.md)
