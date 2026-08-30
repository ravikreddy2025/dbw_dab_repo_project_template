# 06 — Environments and access

[← CI/CD pipelines](05-cicd-pipelines.md) · [Start here](00-START-HERE.md)

Who can do what, where. Set this up once; get it wrong and you will be unpicking it
for months.

---

## 1. Workspaces

| | nonprod (dev) | preprod | prod |
|---|---|---|---|
| Workspace | `adb-…0001` | `adb-…0002` | `adb-…0003` |
| Catalog | `edp_curated_nonprod` | `edp_curated_preprod` | `edp_curated_prod` |
| Key Vault | `kv-edp-nonprod` | `kv-edp-preprod` | `kv-edp-prod` |
| Developers | **Can use**, deploy sandboxes | No access | No access |
| QA | Read | **Can view + run** | No access |
| Leads | Admin | Admin | Admin |
| Support | No | No | Read + view runs |
| Deploy SP | `sp-edp-deploy-nonprod` | `sp-edp-deploy-preprod` | `sp-edp-deploy-prod` |
| Run-as SP | `sp-edp-run-nonprod` | `sp-edp-run-preprod` | `sp-edp-run-prod` |

Separate Key Vaults per environment, so a misconfigured nonprod job cannot read
production credentials even if it asks for the right scope name.

**Developers have no interactive access to preprod or prod.** Not a restriction to
work around — it is what makes the release process meaningful. If something needs
fixing there, it is fixed in code and deployed.

---

## 2. Groups

Define these in Azure AD (or as Databricks account groups) and sync them to all three
workspaces. Same names everywhere — that consistency is what lets the same
`permissions:` block work in every target.

| Group | Members | Purpose |
|---|---|---|
| `edp-platform-leads` | 2–3 leads | Admin everywhere; approve preprod |
| `edp-developers` | all 10 developers | Use nonprod, deploy sandboxes |
| `edp-landing-team` | ingestion module devs | `CAN_MANAGE_RUN` on ingestion jobs |
| `edp-us1-team` | curation module devs | `CAN_MANAGE_RUN` on curation jobs |
| `edp-us1-team` | datamart module devs | `CAN_MANAGE_RUN` on datamart jobs |
| `edp-qa` | QA testers | View + run in preprod, read preprod data |
| `edp-support` | production support | Read prod data, view prod run history |
| `edp-business-analysts` | consumers | `SELECT` on `edp_datamart_prod` only |
| `edp-client-approvers` | client representatives | Approve the prod gate |

A developer is in `edp-developers` **and** their use-case team. Module membership is
what gives them run rights on their own jobs in nonprod without giving them run
rights on everybody's.

---

## 3. Service principals

Two per environment, with different jobs. This separation is Databricks'
recommendation and it limits blast radius in both directions.

### Deploy SP — used by the pipeline

Authenticates via workload identity federation; has no stored credential.

| Needs | Value |
|---|---|
| Workspace entitlement | Workspace access |
| Folder | `CAN_MANAGE` on `/Workspace/Applications/` |
| Jobs / pipelines | Permission to create and update |
| Unity Catalog | `USE CATALOG`, `USE SCHEMA`, `CREATE SCHEMA`, `CREATE TABLE` on its catalog |
| **Must not have** | `SELECT` on production data |

It deploys definitions. It does not need to read a single row, and it should not be
able to.

### Run-as SP — the identity jobs execute under

Set by `run_as` in each production-mode target.

| Needs | Value |
|---|---|
| Workspace entitlement | Workspace access, cluster creation via policy |
| Unity Catalog | Exactly the grants its workload needs — see [§4](#4-unity-catalog-grants) |
| Secrets | `READ` on the scopes its jobs use |
| **Must not have** | Any deployment rights |

If a job is compromised it cannot deploy code. If the pipeline is compromised it
cannot read data. Neither can do the other's job.

### In a sandbox there is no SP at all

The `dev` target sets no `run_as`, so a developer's jobs run as the developer, under
the developer's own grants. A developer cannot accidentally use data access they do
not personally have.

---

## 4. Unity Catalog grants

Schema-level grants are declared in
[`bundles/_platform/resources/schemas_curated.yml`](../bundles/_platform/resources/schemas_curated.yml)
and applied by the platform bundle. They are code, reviewed in a PR, identical in
shape across environments.

### The matrix

| Principal | `landing` | `curated` | `datamart` | `ops` |
|---|---|---|---|---|
| `edp-platform-leads` | ALL | ALL | ALL | ALL |
| Run-as SP (`writer_group`) | USE, CREATE TABLE, MODIFY, SELECT | same | same | same |
| **nonprod** `edp-developers` | USE, CREATE TABLE, MODIFY, SELECT | same | same | same |
| **preprod** `edp-qa` | USE, SELECT | USE, SELECT | USE, SELECT | USE, SELECT |
| **prod** `edp-support` | USE, SELECT | USE, SELECT | USE, SELECT | USE, SELECT |
| **prod** `edp-business-analysts` | — | — | SELECT (table-level) | — |

Two things to note.

**`ops` is writable by every module.** Every job appends audit rows, so `MODIFY` is
broader there than in the data layers. That is intentional; the tables are append-only
by construction.

**Business analysts get table-level grants, not schema-level.** Applied by
[`publish_marts.py`](../bundles/us1/src/jobs/publish_marts.py) after the tables
exist, via [`us1_module.publish`](../bundles/us1/src/us1_module/datamart.py).
A schema-level grant would expose intermediate tables the moment someone created one.

### Developers need `CREATE SCHEMA` in nonprod

For `ensure_schema()` to create sandbox schemas at runtime, `edp-developers` needs
`CREATE SCHEMA` on `edp_curated_nonprod`. That is granted at the catalog level in
[`catalogs.yml`](../bundles/_platform/resources/catalogs.yml) via `writer_group`.

Not granted in preprod or prod — where `writer_group` is `edp-platform-leads`.

---

## 5. Secrets

The bundle creates the **scope**; the values live in Key Vault and never touch git.

```yaml
# bundles/_platform/resources/secret_scopes.yml
resources:
  secret_scopes:
    edp_oracle:
      name: edp-oracle
      backend_type: AZURE_KEYVAULT
      keyvault_metadata:
        resource_id: /subscriptions/…/vaults/kv-edp-${var.env}
        dns_name: https://kv-edp-${var.env}.vault.azure.net/
```

Scope names are **identical in all three environments**; the Key Vault behind them
differs. So the same job config works everywhere, and a preprod job physically cannot
reach production credentials.

### Expected keys

| Scope | Keys | Read by |
|---|---|---|
| `edp-oracle` | `jdbc-url`, `username`, `password` | `edp_landing.oracle.resolve_connection` |
| `edp-kafka` | `bootstrap-servers`, `sasl-connection-string` | `edp_landing.kafka.resolve_bootstrap` |

### Adding a secret

```bash
az keyvault secret set --vault-name kv-edp-preprod \
  --name jdbc-url --value "jdbc:oracle:thin:@//host:1521/SVC"
```

Then grant the workspace's managed identity **Get** and **List** on the vault. The
Databricks scope reads through to Key Vault, so rotating a secret needs no
redeployment.

### Rules

- Never `dbutils.secrets.put`. Scopes are Key Vault-backed and read-only from
  Databricks.
- Never print a secret. `dbutils.secrets.get` redacts in notebook output; an f-string
  into a log does not.
- Never put a secret in a job parameter, a cluster env var or a bundle variable.
- If a secret is ever committed, rotate it. Removing the commit is not enough.

---

## 6. Cluster policies

**Not a bundle resource type.** Created by the platform team in the admin UI or with
Terraform, and referenced by name:

```yaml
  etl_policy_id:
    lookup:
      cluster_policy: edp-etl-standard
```

The convention that makes this work: **the policy carries the same name in all three
workspaces**. Only its ID differs, and that is what the lookup resolves.

`edp-etl-standard` should pin at minimum: allowed node types, max autoscale workers,
mandatory tags (`module`, `environment`), auto-termination, and Unity Catalog access
mode.

Policies belong outside bundles because they are a **guardrail**. If a use-case team
could change the policy in the same PR as the job that violates it, it would not be a
guardrail.

SQL warehouses are the same: created by the platform team, named
`edp-sql-warehouse` in every workspace, looked up by name.

---

## 7. Standing up a new environment

In order. Skipping step 4 is the usual cause of "the pipeline says permission
denied".

1. **Workspace + metastore** — cloud platform team, Terraform.
2. **Key Vault** `kv-edp-<env>`, granted to the workspace managed identity.
3. **Groups** synced into the workspace.
4. **Service principals** — two, registered in the workspace, with the grants above.
5. **Cluster policy** `edp-etl-standard` and warehouse `edp-sql-warehouse`, same
   names as elsewhere.
6. **Service connection** in Azure DevOps with federated credentials
   ([05 §3](05-cicd-pipelines.md#setting-up-a-service-connection)).
7. **Variable group** `edp-<env>` with `DATABRICKS_HOST`.
8. **Environment** `dbx-<env>` with its approval check.
9. **Run `cd-platform`** — creates the four catalogs, all schemas, volumes, scopes and `ops.*`.
10. **Run each `cd-<module>`.**
11. **Seed the control tables**: `databricks bundle run landing_seed_source_registry`.

Steps 9 and 10 are ordered. Module bundles assume the platform bundle's objects
exist.

---

## 8. Auditing access

Who can read the production marts:

```sql
SHOW GRANTS ON SCHEMA edp_datamart_prod;
SHOW GRANTS ON TABLE edp_datamart_prod.us1.fct_orders;
```

What a principal can reach:

```sql
SHOW GRANTS `sp-edp-run-prod` ON CATALOG edp_prod;
```

Who actually queried it — Unity Catalog system tables:

```sql
SELECT event_time, user_identity.email, request_params.full_name_arg AS object
FROM system.access.audit
WHERE service_name = 'unityCatalog'
  AND action_name  = 'getTable'
  AND event_date  >= current_date() - INTERVAL 7 DAYS
ORDER BY event_time DESC;
```

Review quarterly. Two things drift: people change teams and keep old group
membership, and a table-level grant gets added by hand during an incident and never
removed.

---

[← CI/CD pipelines](05-cicd-pipelines.md) · [Next: Release process →](07-release-process.md)
