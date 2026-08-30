# 16 — Do's and don'ts

[← Sandbox isolation](15-sandbox-isolation.md) · [Start here](00-START-HERE.md)

Scannable rules distilled from the rest of these docs. Each one exists because
the opposite has cost somebody a day. Where a rule is enforced by a test or a
grant, that is noted — those are the ones you cannot quietly skip.

---

# Part 1 — Developers

## Sandboxes

| | |
|---|---|
| ✅ **Do** | Deploy to your sandbox as often as you like. It costs nothing and disturbs nobody. |
| ✅ **Do** | Read upstream with `ctx.upstream(...)` and write with `ctx.table(...)`. |
| ✅ **Do** | Wrap upstream reads in `ctx.sample(...)` while iterating. |
| ✅ **Do** | Run `Show-Sandbox.ps1` before you wonder what you are holding. |
| ✅ **Do** | Tear down with `Destroy-Sandbox.ps1 -All -IncludeData` when a feature merges. |
| ❌ **Don't** | Read an upstream layer with `ctx.table(...)`. Your sandbox copy is empty, the job processes zero rows and **reports success**. *(CI check)* |
| ❌ **Don't** | Copy upstream data into your sandbox to "have your own". Use `SHALLOW CLONE` if you need a writable copy. |
| ❌ **Don't** | Assume `bundle destroy` removed your data. It removes jobs and files; schemas are created at runtime and survive. |
| ❌ **Don't** | Leave a `dev_sample_rows` value on a shared target. It would silently truncate real output. |

## Code

| | |
|---|---|
| ✅ **Do** | Keep entry notebooks thin — context, audit, and a call into the wheel. |
| ✅ **Do** | Put logic in `src/<uc>_module/` as DataFrame-in / DataFrame-out functions. |
| ✅ **Do** | Declare schemas explicitly rather than inferring them. |
| ✅ **Do** | Validate arguments **before** importing Spark, so the check is testable. |
| ✅ **Do** | Pass all five base parameters on every job, at **job** level. |
| ❌ **Don't** | Write `if env == "prod"` anywhere. If you need one, the context abstraction is missing something — fix that instead. |
| ❌ **Don't** | Hardcode a catalog, schema, host, cluster ID or warehouse ID. *(test)* |
| ❌ **Don't** | Let a notebook grow past ~50 lines of real logic. A notebook cannot be unit tested. |
| ❌ **Don't** | Concatenate values into SQL. Bind them — control-table rows are data, and data is not trusted to be SQL. |

## Bundles

| | |
|---|---|
| ✅ **Do** | Put a value in `variables.yml` if it differs between dev and prod. |
| ✅ **Do** | Look workspace objects up by name (`lookup:`), never by ID. |
| ✅ **Do** | Keep job keys stable — a rename breaks a smoke test that only runs at release time. *(CI check)* |
| ✅ **Do** | Use `databricks bundle init ./templates/use-case-bundle` for a new use case. |
| ❌ **Don't** | Declare `resources.schemas` in a use-case bundle. Dev mode prefixes resource names and `[dev jsmith] us1` is not a legal schema name. *(structural check)* |
| ❌ **Don't** | Add `git.branch` to a target. Azure DevOps checks out detached-HEAD; it misfires and trains everyone to pass `--force`. |
| ❌ **Don't** | Pass `${var.photon}` where `runtime_engine` is expected. One is a boolean, the other an enum. |

## Working with the team

| | |
|---|---|
| ✅ **Do** | Run `Validate-All.ps1` before pushing. Ten seconds beats ten minutes of build-agent round trips. |
| ✅ **Do** | Onboard a source with a YAML row in `bundles/landing/conf/<uc>/sources.yml`. |
| ✅ **Do** | Fix preprod and prod bugs on the **release branch**, then back-merge. |
| ✅ **Do** | Say in the PR description that you checked all consumers of a `libs/` change. |
| ❌ **Don't** | Fix anything by editing in the Databricks UI outside your sandbox. It is erased by the next deploy and exists in no branch. |
| ❌ **Don't** | Branch a preprod fix from `main`. |
| ❌ **Don't** | Skip the back-merge because "it was only a small fix". That is exactly the fix that gets shipped again next month. |
| ❌ **Don't** | Edit `bundles/recon/conf/*.yml` to make a parity check pass. That is the migration gate. |

---

# Part 2 — DevOps and platform leads

## Setting the repo up

Do these **in order**. Steps 1–4 are prerequisites; the rest fails confusingly
without them.

| # | Do | Why |
|---|---|---|
| 1 | Provision workspaces, metastore, Key Vaults per environment | Nothing below works without them |
| 2 | Create the groups and sync them into all three workspaces | Grants reference them by name |
| 3 | Create **three identities per environment**: deploy SP, run-as SP, recon SP | Each needs a different, smaller set of rights |
| 4 | Create the cluster policy and SQL warehouse with the **same name** in all three workspaces | `lookup:` resolves by name; only the ID differs |
| 5 | Create ARM service connections with federated credentials, restricted to `cd-*` pipelines | A PR build must never reach a real workspace |
| 6 | Run `Az-DevOps-Bootstrap.ps1` | Environments, variable groups, pipelines, branch policies |
| 7 | Add approval checks to `dbx-preprod` and `dbx-prod` in the UI | The gate must not be editable in a PR |
| 8 | Run `cd-platform` **to completion** | Everything else assumes its catalogs and schemas exist |
| 9 | Run `cd-landing`, then the use-case pipelines, then `cd-recon` | Dependency order |
| 10 | Seed the source registry, then verify one end-to-end run | Proves the whole chain |

| | |
|---|---|
| ❌ **Don't** | Run a module pipeline before `cd-platform` has succeeded in that environment. |
| ❌ **Don't** | Give a service connection access to all pipelines. |
| ❌ **Don't** | Put the approval gate in pipeline YAML. On the Environment, it cannot be removed by a PR. |
| ❌ **Don't** | Reuse one service principal across environments. |

## Identities and grants

| | |
|---|---|
| ✅ **Do** | Keep deploy, run-as and recon identities separate. Deploy needs no data; run-as needs no deploy rights; recon reads everything and writes only `ops.recon`. |
| ✅ **Do** | Give developers `SELECT` on shared schemas and `CREATE SCHEMA` on the catalog. They own their sandbox and cannot touch shared. |
| ✅ **Do** | Grant business consumers at **table** level, after `publish_marts` creates the tables. |
| ✅ **Do** | Review grants quarterly. People change teams; incident-time grants are never removed. |
| ❌ **Don't** | Give `MODIFY` on a shared schema to a human group. That makes the sandbox prefix a convention rather than a boundary. |
| ❌ **Don't** | Give the recon SP write access to any data catalog. A use case must not be able to write its own exam results. |
| ❌ **Don't** | Grant business users at schema level. They would see every intermediate table someone creates later. |

## Secrets

| | |
|---|---|
| ✅ **Do** | Use workload identity federation. There is then no secret to store, rotate or leak. |
| ✅ **Do** | Keep secret scope **names** identical across environments, pointing at different Key Vaults. |
| ✅ **Do** | Rotate anything that has ever been in git. Removing the commit is not enough. |
| ❌ **Don't** | Put a secret in a job parameter, cluster env var, bundle variable or plain pipeline variable. |
| ❌ **Don't** | Use PATs for CI. They are user-scoped and expire at the worst time. |

## Pipelines

| | |
|---|---|
| ✅ **Do** | Pin the CLI version, and keep it in step with every `databricks_cli_version`. *(CI check)* |
| ✅ **Do** | Name the specific `libs/` a bundle embeds in its trigger paths. *(CI check)* |
| ✅ **Do** | Build wheels **once** and have every deploy stage download that artifact. |
| ✅ **Do** | Let every stage of a run check out the same commit. That is what makes promotion immutable. |
| ❌ **Don't** | Use a blanket `libs/*` trigger. A QA parity change would redeploy production ETL. *(CI check)* |
| ❌ **Don't** | Rebuild wheels in the prod stage. Prod must install the binary QA approved, not a rebuild of the same source. |
| ❌ **Don't** | Give `ci-pr-validation` a service connection. |

## Housekeeping

| | |
|---|---|
| ✅ **Do** | Review `ops.audit.stale_sandbox_schemas` weekly and chase the owners. |
| ✅ **Do** | Make sandbox teardown part of closing a ticket, not a quarterly cleanup. |
| ✅ **Do** | Run the full offboarding checklist when someone leaves. Orphaned schemas have no owner to chase. |
| ❌ **Don't** | Automate dropping developer schemas. The views generate the `DROP` statements; a human decides. Deleting someone's work-in-progress is the kind of helpful that costs a day. |
| ❌ **Don't** | Delete a streaming checkpoint volume without stopping the stream first. |

## Migration-specific

| | |
|---|---|
| ✅ **Do** | Keep `bundles/recon` and `libs/edp_recon` separate from the ETL. Different owner, different identity, different lifespan. |
| ✅ **Do** | Require a written justification for every non-zero tolerance. *(enforced in code)* |
| ✅ **Do** | Include a month-end close in the parallel-run window before cutover. |
| ✅ **Do** | Keep Cloudera **read-only** at cutover, not switched off. |
| ❌ **Don't** | Let a use case cut over on preprod evidence alone. Prod has the data preprod does not. |
| ❌ **Don't** | Treat a `SKIPPED` parity run as a pass. It means nothing was compared. |
| ❌ **Don't** | Force every ported script into a wheel before it can run. That is how migrations stall. |

---

## The five that matter most

If the rest is forgotten, keep these:

1. **Read shared, write your own.** The one mistake that fails silently.
   → [15](15-sandbox-isolation.md)
2. **The gate lives on the Environment, not in the YAML.** A PR cannot remove it.
   → [05](05-cicd-pipelines.md)
3. **Fix preprod bugs on the release branch, and back-merge.** Without it you ship
   the same bug twice. → [02](02-branching-strategy.md)
4. **Grants, not conventions.** A naming rule only the framework honours is not
   isolation. → [06](06-environments-and-access.md)
5. **Parity is evidence, not opinion.** Cutover is a query.
   → [13](13-migration-and-cutover.md)

---

[← Sandbox isolation](15-sandbox-isolation.md) · [Start here](00-START-HERE.md)
