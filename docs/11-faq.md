# 11 — FAQ

[← Onboarding checklists](10-onboarding-checklist.md) · [Start here](00-START-HERE.md)

---

## Working day to day

### Can I still develop in the Databricks UI?

In your **sandbox**, yes — it is yours. But the workspace is not the source of truth.
Your next `bundle deploy` overwrites whatever you changed there.

The workflow that actually works: edit locally (VS Code with the Databricks extension
syncs continuously), deploy, run. `bundle deploy` on an unchanged bundle takes
seconds.

If you have already made an edit in the UI you want to keep:

```bash
databricks workspace export-dir \
  /Workspace/Users/$USER/.bundle/edp_landing/dev/files/src \
  ./bundles/landing/src --overwrite
```

In **nonprod, preprod or prod**, no. Those are deployed by a service principal and a
UI edit is drift that vanishes at the next deploy.

### What if two developers change the same job?

Same as any code conflict: git resolves it at PR time. Because job definitions are
YAML in the repo rather than state in a workspace, a conflicting change is a merge
conflict you see in the PR — not a silent last-writer-wins overwrite in the workspace.

Deployments do not conflict at all: each developer deploys to their own target with
their own file root.

### Do I need to coordinate with anyone before deploying?

Not for your sandbox. Ever. That is the whole point of `mode: development` plus the
schema prefix — deploy as often as you like.

### Why is my job called `[dev jaya] landing_us2`?

`mode: development` prefixes every resource name so ten developers can share one
workspace and still tell whose job is whose. You cannot turn it off in the `dev`
target, and you would not want to.

### Why did my schedule not fire?

`mode: development` pauses every schedule and trigger. A sandbox that fired on a timer
would run overnight, cost money and confuse everyone. Trigger it yourself:

```bash
databricks bundle run <job_key> --target dev
```

### Can I run a job against the shared nonprod data from my sandbox?

You can read it — `edp_landing_nonprod.us1` is readable by `edp-developers`. Just be aware
that `ctx.table("landing", "x")` in your sandbox resolves to `jaya_us1`, so reading
shared data means naming it explicitly. Do not write to shared schemas from a sandbox.

### How do I test against a realistic data volume?

Copy a slice into your sandbox:

```sql
CREATE TABLE edp_landing_nonprod.jaya_us2.ora_customers
AS SELECT * FROM edp_landing_nonprod.us1.ora_customers LIMIT 100000;
```

Preprod is the place where full-volume behaviour is verified, and it is sized like
prod for exactly that reason.

---

## Bundles

### Why one bundle per use case instead of one for everything?

Databricks recommends putting what a single team owns into one bundle, and preferring
small focused bundles over a monolith. Concretely: the us3 team's broken job does
not block the us1 team's release, each bundle has its own gate and its own
deploy history, and a change to one use case triggers one pipeline instead of four.

### Why not one repository per use case?

Databricks recommends a single repository even when you have several bundles with
separate deployment lifecycles. With a monorepo, a change to shared framework code and
the five use cases that consume it is **one PR, reviewed together**, and the wheel
version is always the commit.

Split into separate repositories when teams genuinely need independent release
cadences and separate access control — and when you do, publish `dab_common` to an
Azure Artifacts feed and pin versions. That is the trade you are making: independence
in exchange for versioned coordination.

### Why not one bundle per environment?

A bundle covers all its environments through `targets:`. One bundle per environment
guarantees drift — three files that are supposed to be identical, and are not.

### Can I add a `resources.schemas` block to my use-case bundle?

No. In `mode: development` DABs prefixes resource names with `[dev jaya] `, which is
not a legal Unity Catalog schema name, so it breaks the moment anyone deploys a
sandbox. Shared schemas belong to the `_platform` bundle; sandbox schemas are created
at runtime by `ensure_schema()`. See
[08](08-troubleshooting.md#schema-prefix).

### Who owns the shared schemas?

Platform leads, via [`bundles/_platform/`](../bundles/_platform). To add one, PR the
change to `resources/schemas_curated.yml` with its grants and deploy the platform bundle
before the use case that needs it.

### Why is the shared wheel copied into every bundle instead of referenced?

DABs requires library paths to be inside the bundle root — `../../libs/...` produces
`path is not contained in bundle root path`. Building into `dist/` works on every CLI
version with no `sync.paths` and no experimental flags.
[04 §6](04-bundle-authoring.md#6-wheels).

### Why are cluster policies not in a bundle?

They are not a bundle resource type, and they should not be. A policy is a
**guardrail**. If a use-case team could change the policy in the same PR as the job that
violates it, it would not be one. Platform creates them; bundles look them up by name.

---

## Branching and releases

### Why release branches instead of `dev` / `preprod` / `prod` branches?

Because with environment branches, "what is in preprod" is whatever happens to be on
the `preprod` branch, and a fix applied there can silently never reach `dev`. Six
months of that and nobody can say what production is running without diffing three
branches.

With a release branch there is one artifact: the commit QA tested is the commit
production gets. Your approval gates are unchanged — they moved from "a PR into a
branch" to "an Azure DevOps Environment check", which is stronger, because a gate on
an Environment cannot be removed by editing a file in a PR.

### Do I have to back-merge? It is always a no-op.

If the release branch got no fixes, it is a one-minute no-op. The moment it does get a
fix, the back-merge is what stops you shipping the same bug next month. Making it
unconditional is what makes it reliable — a step people only do "when needed" is a
step people forget exactly when it was needed.

### Can we skip preprod for an urgent fix?

No, and you should not want to. The hotfix path — branch from the tag, new release
branch, both gates — takes under two hours in the
[walkthrough](09-walkthrough-simulation.md#day-12--a-production-incident). Deploying
untested code to production during an incident is how a bad night becomes a bad week.

### What if a release sits in preprod for weeks?

That is fine. `main` keeps moving; the release branch is frozen at what QA is testing.
When it finally ships, the back-merge reconciles the two.

If it happens routinely, the release scope is too big. Cut smaller ones.

### Can two releases be in flight at once?

Technically yes — two release branches, two sets of pipeline runs. Avoid it: the
back-merge ordering gets confusing fast. Add an **exclusive lock** check on the
`dbx-prod` environment so two runs cannot interleave in production.

### Who decides what goes in a release?

The release manager cuts from `main` at a point in time. Everything merged before that
point is in. There is no cherry-picking — if a feature is not ready, it should not be
on `main`.

---

## CI/CD

### Why does a change under `libs/` redeploy every bundle?

Because all four embed that wheel. A shared-code change that only redeployed one
module would leave the other three running the old version, which is exactly the drift
this repo is built to avoid.

### Why does PR validation not run `databricks bundle validate` against a workspace?

A PR branch is untrusted code. Giving it a service connection would let a PR reach a
real workspace. PR validation checks structure offline; the full workspace validation
runs in the `Build` stage after merge, where a connection is available.

### Why is the approval gate not in the pipeline YAML?

So a developer cannot remove it in a PR. The YAML says "deploy to `dbx-prod`"; Azure
DevOps decides what `dbx-prod` requires. That separation is deliberate.

### Do we need workload identity federation, or can we use a secret?

A secret works — the fallback is documented in
[`bundle-deploy.yml`](../.azure-pipelines/templates/steps/bundle-deploy.yml) and
[05 §3](05-cicd-pipelines.md#fallback-client-id--secret). Federation is better because
there is nothing to store, rotate or leak. Treat a secret as a temporary state.

### Why pin the CLI version?

So your laptop and the build agent behave identically. An unpinned CLI means a
pipeline that starts failing one morning because the agent picked up a new release.
The cross-reference audit checks the pin in `common.yml` satisfies every bundle's
`databricks_cli_version`.

---

## Data and frameworks

### How do I onboard a new Oracle table or Kafka topic?

A row in [`conf/us2/sources.yml`](../bundles/landing/conf/us2/sources.yml) or
`conf/us1/sources.yml`. No new notebook, no new job.
[03 §8](03-developer-guide.md#8-adding-a-new-oracle-table-or-kafka-topic).

### What happens if I remove a source from the seed file?

It is marked `is_active: false`, never deleted. Deleting the row would orphan its
watermark and years of `table_load_audit` history.

### Why does landing keep the Kafka payload as a string?

So landing is lossless. If the upstream schema changes, that is a curated-layer fix, not a
lost day of data. Parsing happens in `parse_kafka_payload` against a **declared**
schema, so an upstream change fails loudly with a column mismatch rather than silently
producing nulls.

### Why is `fct_orders` a LEFT JOIN to `dim_customer`?

So an order whose customer has not landed yet still appears, with a null
`customer_key`. The revenue is not silently lost. Curation's
`curated_quality_gate` alerts on the orphan, and `publish_marts` records it as a
**warning**, not an error.

### Can I turn off a data quality check?

Change it in code, in a PR. `${var.dq_fail_on_error}` exists so a lead can stop a
breach from *failing the job* in one environment during an incident — the results are
still recorded either way. It is `"true"` everywhere by default, including sandboxes,
so nobody meets a check for the first time in preprod.

### Where do I look when a job failed last night?

```sql
SELECT started_at, module, task_key, status, error_message, error_detail
FROM edp_ops_prod.audit.job_run
WHERE status = 'FAILED' AND started_at >= current_date() - INTERVAL 1 DAY
ORDER BY started_at DESC;
```

`error_detail` holds the traceback. More queries in
[08](08-troubleshooting.md#diagnostic-commands).

---

## Naming and history

### Is it "Databricks Asset Bundles" or "Declarative Automation Bundles"?

Both. Databricks renamed them in the 2026 documentation. Same product, same
`databricks bundle` CLI. Older articles and this repo's older references say "Asset
Bundles"; current Databricks docs say "Declarative Automation Bundles".

### We used to deploy with dbx / the Repos API / notebooks in `/Shared`. What changes?

| Before | Now |
|---|---|
| Jobs created in the UI | Jobs defined in `resources/*.job.yml`, in git |
| Notebooks in `/Shared`, everyone editing | Per-developer sandboxes, code in the repo |
| "Promote" by copying notebooks between workspaces | One bundle, four targets, one deploy command |
| Environment differences hand-managed | `variables:` per target |
| Manual approval by "ask before you deploy" | Azure DevOps Environment gates |
| No record of what ran | `ops.audit.job_run` |

The single biggest shift: **the repository is the source of truth, not the workspace.**
Anything you change in a workspace is gone at the next deploy. That feels restrictive
for a week and then feels like the only sane way to work.

---

[← Onboarding checklists](10-onboarding-checklist.md) · [Next: Conventions →](12-conventions.md)
