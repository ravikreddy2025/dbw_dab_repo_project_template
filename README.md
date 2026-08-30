# EDP — Databricks Asset Bundles + Azure DevOps reference

A **working reference implementation** for a 10-person team lifting and shifting from
Cloudera onto Azure Databricks: Databricks Asset Bundles for deployment, Azure DevOps
for source control and release gating, three environments, four catalogs per
environment, five use cases, eight bundles.

Everything here runs. Tests pass, pipelines are complete, the audit scripts are real.
The only placeholders are workspace URLs, service principal GUIDs and subscription
IDs — every one marked `# PLACEHOLDER`.

> **📖 [Start with `docs/00-START-HERE.md`](docs/00-START-HERE.md)** — it routes you by
> role: developer, lead, QA, or "I am porting Cloudera code".

---

## What this demonstrates

**Deployment**
- Ten developers, one dev workspace, **zero collisions** — `mode: development` plus a
  per-user schema prefix
- One bundle per use case, each with its own pipeline, gate and release cadence
- One bundle covers all four targets — dev sandbox, nonprod, preprod, prod
- Release-branch promotion with Azure DevOps approval gates instead of environment
  branches
- The same commit and the same wheels reach production that QA approved in preprod
- No secrets anywhere — workload identity federation for CI, OAuth for humans

**Structure**
- Four catalogs per environment (`landing` / `curated` / `datamart` / `ops`), schemas
  per use case
- A **shared** landing bundle serving all five use cases, with per-use-case config
  folders and their own reviewers
- Metadata-driven landing — onboarding a Kafka topic or Oracle table is a reviewed
  YAML row, never a new notebook
- Four compute styles side by side: serverless, job cluster + policy, declarative
  pipeline, DBSQL warehouse

**Migration**
- A `ported/` zone per use case where Cloudera code runs before it is refactored,
  with a documented, measurable path out
- A reconciliation harness that proves Databricks produces the same numbers Cloudera
  did, and a `cutover_readiness` view that turns go-live into a query

---

## Layout

```
bundles/
  _platform/    4 catalogs x 3 envs, all schemas, volumes, scopes, ops DDL  [FIRST]
  landing/      SHARED - Kafka (us1/us3/us4) + Oracle (us2) -> edp_landing_<env>.<uc>
  us1/ .. us5/  curated + datamart + reconciliation, one bundle per use case
libs/
  dab_common/   config, audit, quality, recon    <- every bundle depends on this
  edp_landing/  registry, kafka, oracle          <- the landing framework
.azure-pipelines/  ci-pr-validation + one cd-* per bundle
scripts/           dev commands, one-time ADO bootstrap, CI audits
templates/         `bundle init` scaffold for a new use case
docs/              the team reference — 15 documents
```

### The catalog model

| Catalog (per env) | Schemas |
|---|---|
| `edp_landing_<env>` | `us1` … `us5` |
| `edp_curated_<env>` | `us1` … `us5` |
| `edp_datamart_<env>` | `us1` … `us5` |
| `edp_ops_<env>` | `audit`, `config`, `logs`, `recon` |

`<env>` is `nonprod` / `preprod` / `prod`. The suffix is **required**: one Unity
Catalog metastore serves all three workspaces, so catalog names must be unique
metastore-wide.

---

## Try it now

No Databricks workspace needed:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
pip install -e libs/dab_common -e libs/edp_landing
pip install -e bundles/landing -e bundles/us1 -e bundles/us2 -e bundles/us3 -e bundles/us4 -e bundles/us5

pytest -q
python scripts/ci/validate_bundle_yaml.py bundles/_platform bundles/landing bundles/us1 bundles/us2 bundles/us3 bundles/us4 bundles/us5
python scripts/ci/check_bundle_references.py
```

Or run all four checks the PR build runs:

```bash
pwsh ./scripts/dev/Validate-All.ps1
```

---

## With a workspace

Replace the placeholders (see
[docs/06](docs/06-environments-and-access.md#7-standing-up-a-new-environment)), then:

```bash
databricks auth login --host https://<your-nonprod-workspace>
pwsh ./scripts/dev/Deploy-Sandbox.ps1 -Bundle us1
```

---

## Adapting this to your project

Search and replace, in this order:

1. `edp` → your platform prefix (catalogs, bundles, groups, scopes)
2. `us1` … `us5` → your real use-case names, in folders, configs and pipelines
3. `adb-000000000000000X.X.azuredatabricks.net` → your three workspace URLs
4. The SP GUIDs `11111111-…`, `22222222-…`, `33333333-…` → your run-as SPs
5. `example.com` → your mail domain
6. `/subscriptions/00000000-…` → your subscription and Key Vault resource IDs
7. `DAB-` → your work-item prefix

Then re-run `python scripts/ci/check_bundle_references.py` — it fails if a real
workspace URL or a PAT was committed by accident.

**Two things to confirm before you start:**
- `bundles/landing/conf/us5/sources.yml` assumes **Kafka** for us5 and says so in a
  TODO banner. Change it if that is wrong.
- The `source_ref` values in each `bundles/recon/conf/<use_case>.yml` are placeholders — they
  need to point at however you land the Cloudera side.

---

## The six decisions everything follows from

1. **Four catalogs per environment, schemas per use case.** A grant becomes a
   layer-wide decision; business analysts get the datamart catalog and cannot see
   curated intermediates at all.
2. **Landing is shared; curated and datamart are per use case.** Landing is a
   horizontal capability that will serve use cases that do not exist yet.
3. **One bundle covers all its environments.** Never one bundle per environment.
4. **The dev workspace holds both sandboxes and shared nonprod**, kept apart by
   deployment mode and a schema prefix — ops schemas included.
5. **The gate lives on the Azure DevOps Environment**, not in the YAML, so a PR
   cannot remove it.
6. **Parity is evidence, not opinion.** Every use case reconciles against Cloudera
   into `edp_ops_<env>.recon`.

Rationale for each: [docs/01 — Architecture](docs/01-architecture.md).

---

> **Naming note.** Databricks renamed *Databricks Asset Bundles* to *Declarative
> Automation Bundles* in the 2026 documentation. Same product, same `databricks
> bundle` CLI. You will see both names in search results.

## Documents

| # | Document | Read it when |
|---|---|---|
| 00 | [Start here](docs/00-START-HERE.md) | First |
| 01 | [Architecture](docs/01-architecture.md) | You want the shape of the whole thing |
| 02 | [Branching strategy](docs/02-branching-strategy.md) | Before your first PR |
| 03 | [Developer guide](docs/03-developer-guide.md) | Day one, laptop setup |
| 04 | [Bundle authoring](docs/04-bundle-authoring.md) | Adding a job, pipeline or table |
| 05 | [CI/CD pipelines](docs/05-cicd-pipelines.md) | Setting up or debugging a pipeline |
| 06 | [Environments and access](docs/06-environments-and-access.md) | Catalogs, SPs, grants, secrets |
| 07 | [Release process](docs/07-release-process.md) | Cutting or approving a release |
| 08 | [Troubleshooting](docs/08-troubleshooting.md) | Something just failed |
| 09 | [Walkthrough](docs/09-walkthrough-simulation.md) | You learn best by watching |
| 10 | [Onboarding checklists](docs/10-onboarding-checklist.md) | Someone new is joining |
| 11 | [FAQ](docs/11-faq.md) | "But can I just…?" |
| 12 | [Conventions](docs/12-conventions.md) | Naming anything |
| 13 | [Migration and cutover](docs/13-migration-and-cutover.md) | Proving parity, going live |
| 14 | [Porting guide](docs/14-porting-guide.md) | Moving Cloudera code in |

Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)
