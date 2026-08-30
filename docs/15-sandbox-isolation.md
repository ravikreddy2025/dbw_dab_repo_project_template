# 15 — Sandbox isolation: the prefix rule

[← Porting guide](14-porting-guide.md) · [Start here](00-START-HERE.md)

**This document is deliberately framework-neutral.** It states the rule and the
reasoning so they survive whatever you actually build. The accessors named at the
bottom are one implementation of it, not the point.

---

## The rule

> **Prefix what a job WRITES. Never prefix what it READS from upstream.**

A developer sandbox is not "a private copy of the platform". It is **private
outputs over shared inputs**. Get that backwards and sandboxes either become
useless or become expensive — usually both.

---

## Why prefix writes

Jobs collide on *names*. Tables collide on *state*, which is worse.

Without write isolation, ten developers all write `curated.us1.orders`:

- one broken transform corrupts what nine colleagues are reading
- nobody can say whose run produced the current contents
- two people running at once race, and the loser never finds out
- a failed run leaves partial state that looks like real data

None of that is fixed by being careful. It needs a boundary.

---

## Why NOT prefix upstream reads

Three reasons, and the first is the one that actually bites.

**A prefixed read hits an empty schema.** `landing.jsmith_us1` contains nothing
until jsmith personally runs the landing pipeline. A curated job reading it
processes zero rows and **reports success**. That is the worst failure shape
available: green, fast, and wrong. People trust it for weeks.

**Upstream data is large.** Landing is your biggest layer. A copy per developer is
N times the storage to produce N different stale snapshots for people to disagree
about.

**It breaks the thing sandboxes are for.** You want developers testing against
data that looks like production. Forcing them to synthesise their own inputs means
they test against data that does not.

---

## The distinction that trips people

"Upstream" means *another pipeline produced it*. It does not mean *earlier in the
DAG*.

| Reading… | Prefixed? |
|---|---|
| Landing, from a curated job (**another bundle** produced it) | **no** — shared |
| Curated, from a datamart job in the **same** bundle (you just wrote it) | **yes** — yours |
| Another use case's published output | **no** — shared |
| A table your own job wrote earlier in this run | **yes** — yours |

Rule of thumb: **if this deployment produced it, read your own. If something else
produced it, read shared.**

---

## Where a prefix applies at all

| Object | Prefixed | Why |
|---|---|---|
| Catalog | **no** | Catalogs are environment-scoped, not developer-scoped. N catalogs per developer is unmanageable. |
| Schema you write | **yes** | This is the boundary. |
| Schema you read from upstream | **no** | See above. |
| Table / view | **no**, inherits its schema | Prefixing table names too is redundant and makes names unreadable. |
| Operational schemas (audit, config, logs) **you write** | **yes** | A sandbox must not pollute shared audit, and must not overwrite shared config. |
| Operational schemas you **read** for upstream context | **no** | Must match the side you are reading. |
| Volume paths (checkpoints, quarantine) | **yes** | Two developers sharing a streaming checkpoint corrupt each other's stream state. |
| Job / pipeline **names** | **yes** | So a shared workspace is navigable. Handled by the deployment tool, not by you. |

> **Prefix the schema, not the table.** One boundary, applied once. Prefixing both
> gives you `jsmith_us1.jsmith_orders`, which is noise, and it makes every
> hardcoded table reference wrong instead of just misplaced.

---

## Make it a boundary, not a convention

A naming convention that only the framework honours is not isolation — one
hardcoded string gets past it. Back it with grants:

| Principal | Shared schemas | Catalog |
|---|---|---|
| Developers | `SELECT` only | `CREATE SCHEMA` |
| Pipeline service principal | full write | `CREATE SCHEMA` |

Developers **own** the sandbox schema they create, so they write it freely — and
they cannot write the shared one at all. A typo then produces a permission error
instead of a corrupted table four people are reading.

This is the half most teams skip, and it is the half that makes the other half
true.

---

## Consuming jobs need an explicit mode

Anything that only *reads* — reconciliation, data quality auditing, profiling,
regression comparison — has no writes to anchor the decision, and its two sandbox
users want opposite things:

| Who | Wants | Because |
|---|---|---|
| A QA / platform person testing the **check itself** | shared tables | their own schema is empty; they run no ETL |
| A developer validating **their own change** | their own tables | shared would check code they did not write |

There is no safe default, so **make it an explicit parameter and print which side
was used.** Pick the default by which failure is worse — usually reading shared,
because "PASS on code you did not write" is a false pass, while "compared an empty
table" fails loudly and safely.

Whatever the read mode, **keep writes prefixed**: a sandbox validation run must
never land in the shared results table that decisions are made from.

---

## Escape hatches you will need

| Situation | Answer |
|---|---|
| Test the whole chain against **your own** upstream output | A per-run override flag. Per run, not per environment — it is a temporary state. |
| Sandbox runs too slow or too expensive | A row cap applied **only** in a sandbox. Never let it apply in a shared environment, or it silently truncates real output. |
| Need a large **writable** copy (testing a MERGE, a backfill, a schema migration) | `SHALLOW CLONE`. Zero data movement, fully isolated, drop it when done. Deep clone only if it must survive a `VACUUM` of the source. |

```sql
CREATE TABLE edp_curated_nonprod.jsmith_us1.orders
SHALLOW CLONE edp_curated_nonprod.us1.orders;
```

---

## Implementation checklist

Whatever you build, it needs:

- [ ] **Two accessors, not one.** One for what you write, one for upstream reads.
      A single "get table name" function cannot express the rule and will get it
      wrong half the time.
- [ ] **Identical behaviour in shared environments.** With an empty prefix both
      accessors return the same string, so there is no `if env == "prod"` anywhere
      and no branch that only executes in production.
- [ ] **Prefix supplied by the platform**, from the current user — never typed by a
      developer, never committed.
- [ ] **Sandbox schema created on demand** at runtime, by the framework, idempotently.
- [ ] **Grants that enforce it** (see above).
- [ ] **An explicit mode for read-only jobs**, with the choice printed at runtime.
- [ ] **A CI check** that fails when an upstream layer is read with the write-side
      accessor. This mistake is silent when made, so review will not catch it.

## Test for these failure modes

The ones that are silent, and therefore the ones worth tests:

- [ ] A sandbox read of an upstream layer resolves to the **shared** schema
- [ ] Two developers never resolve to the same write target
- [ ] Two use cases never resolve to the same write target
- [ ] Shared environments resolve both accessors **identically**
- [ ] A sampling cap is inert outside a sandbox
- [ ] A sandbox run of a validation job writes to the **sandbox** results schema
- [ ] An unknown read-mode value **raises** rather than falling back to a default

---

## How this repo implements it

One instance of the rule, for reference:

| Concern | Here |
|---|---|
| Write accessor | `ctx.table(layer, name)` — prefixed in a sandbox |
| Upstream read accessor | `ctx.upstream(layer, name)` — shared |
| Ops write / ops upstream read | `ctx.audit_table(...)` / `ctx.upstream_ops_table(...)` |
| Per-run override | `--params upstream_mode=sandbox` |
| Sampling | `ctx.sample(df)` + `dev_sample_rows`, non-zero on `dev` targets only |
| Prefix source | `${workspace.current_user.short_name}_` on the `dev` target |
| Grants | [`bundles/_platform/resources/schemas_*.yml`](../bundles/_platform/resources) |
| CI check | `check_upstream_reads` in [`check_bundle_references.py`](../scripts/ci/check_bundle_references.py) |

Day-to-day usage, with commands:
[03 — Developer guide §4a](03-developer-guide.md#reading-shared-data).

---

[← Porting guide](14-porting-guide.md) · [Start here](00-START-HERE.md)
