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

**"I deployed but my table is not in `landing`."** It is in `jsmith_us1`. Your
sandbox writes to your own schemas.

**"`bundle deploy` says a wheel is missing."** Run `Build-Wheels.ps1` first, or use
`Deploy-Sandbox.ps1`, which does it for you.

**"Another developer's job appeared in my summary."** It did not — `bundle summary`
only shows your target. You are probably looking at the workspace Jobs list, which
shows everyone's.

More, with the exact error text: [08 — Troubleshooting](08-troubleshooting.md).

---

[← Branching strategy](02-branching-strategy.md) · [Next: Bundle authoring →](04-bundle-authoring.md)
