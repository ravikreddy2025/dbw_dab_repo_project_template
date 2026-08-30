# 14 — Porting guide

[← Migration and cutover](13-migration-and-cutover.md) · [Start here](00-START-HERE.md)

How Cloudera code gets into this repository without either (a) stalling the
migration or (b) permanently inheriting Cloudera's problems.

---

## The two zones

Every use case has both:

```
bundles/us1/src/
  ported/          Cloudera code, lifted near as-is. Runs as notebook tasks.
                   No package structure required. No unit tests required.
  us1_module/      Refactored: importable, unit-tested, shipped as a wheel.
  jobs/            Thin entry points that call one or the other.
```

### Why not just refactor everything on the way in

Because it does not work. A migration that requires a full rewrite before anything
can run has two failure modes, and projects hit both:

1. **It stalls.** Refactoring 200 Cloudera scripts before the first one runs in
   Databricks means months with nothing demonstrable.
2. **It gets routed around.** Teams under deadline pressure find a way to deploy
   that skips the structure, and you end up with unreviewed code in places nobody
   is looking.

Two zones make the compromise **explicit and visible** instead of implicit and
denied. `ported/` is in the tree, in the PR, in the file count. It cannot quietly
become permanent, because you can measure it:

```bash
find bundles/*/src/ported -name "*.py" ! -name "example_*" | wc -l
```

---

## The three stages

| Stage | Lives in | Runs as | Tested | Can deploy to prod |
|---|---|---|---|---|
| 1. Ported | `src/ported/` | notebook / script task | no | **yes** |
| 2. Wrapped | `src/ported/`, called from `src/jobs/` | notebook task | smoke | yes |
| 3. Refactored | `src/<uc>_module/` | wheel on the job | unit tests | yes |

Stage 1 can go to production. That is the point — a lift and shift means running
the same logic on new infrastructure. Refactoring is a **separate**, later
improvement, and pretending otherwise conflates two risks into one deployment.

---

## Stage 1: lifting a script in

### The one refactor that is not optional

**Environment values must come from job parameters.** Everything else can wait.

Cloudera code typically hardcodes paths, database names and cluster addresses.
Left as-is, the code cannot be promoted between environments at all — which
defeats the entire deployment model.

```python
# BEFORE - Cloudera
df = spark.table("prod_warehouse.orders")
df.write.saveAsTable("prod_marts.fct_orders")
```

```python
# AFTER - stage 1. Not refactored, just parameterised.
def run_legacy_orders_load(spark, ctx) -> int:
    source = ctx.table("landing", "kfk_orders")
    target = ctx.table("curated", "orders_legacy")
    df = spark.table(source)
    # ... original transformation, unchanged ...
    df.write.mode("overwrite").saveAsTable(target)
    return spark.table(target).count()
```

The transformation in the middle is untouched. Only the names changed, and they now
come from `RuntimeContext`, so the same function runs in a sandbox, nonprod,
preprod and prod.

### The header

Every ported file records its origin:

```python
"""Ported from : cloudera-us1-etl @ a1b2c3d
Ported by   : J Smith, 2026-08-30
Refactor    : DAB-412
"""
```

When someone questions behaviour in six months, the original must be findable.
`example_legacy_orders_load.py` in each use case shows the full shape.

### Credentials

Cloudera code frequently carries connection strings and passwords inline. Move
them to a secret scope **before the first commit**.

If a credential ever reached git, **rotate it**. Removing the commit is not enough.

### The entry point

Call ported code from `src/jobs/`, do not paste it there:

```python
# bundles/us1/src/jobs/curate.py
from ported.legacy_orders import run_legacy_orders_load

ctx = build_context(dbutils.widgets.getAll())
with audited_run(spark, ctx, layer="curated"):
    rows = run_legacy_orders_load(spark, ctx)
```

The entry point owns context, audit and schema creation. The ported module owns the
transformation. That separation is what lets you refactor underneath without
touching the job definition — and it means even stage-1 code writes proper audit
rows.

---

## Stage 2: wrapping

Add a smoke test — not a unit test of the logic, just proof it runs and produces
plausible output. Usually the `<uc>_curated` job succeeding in nonprod plus the
data quality gate passing.

At this stage the code has an audit trail and a reconciliation record even though
its internals are untested.

---

## Stage 3: refactoring out

### When

When one of these is true:

- The code is being **changed** anyway. Never refactor and change behaviour in the
  same PR — you lose the ability to attribute a parity break to one of them.
- It has **broken twice**. Two incidents is enough evidence that it needs tests.
- It is **shared** by more than one use case. Shared untested code is the worst
  category in the repo.
- Its use case has **cut over** and parity is stable. The safest moment: you have a
  reference to prove the refactor changed nothing.

### How

Split I/O from transformation. That single change is what makes it testable:

```python
# src/us1_module/curated.py - pure, testable
def conform_orders(df):
    """DataFrame in, DataFrame out. No I/O, no environment knowledge."""
    from pyspark.sql import functions as F
    return df.select(...)
```

```python
# src/jobs/curate.py - I/O only
curated = conform_orders(spark.table(source))
curated.write.mode("overwrite").saveAsTable(target)
```

Then add tests. Contract tests need no Spark at all — see
[`test_us1.py`](../bundles/us1/tests/test_us1.py) for the pattern, and
`test_transforms_spark.py`-style files with `pytest.importorskip("pyspark")` for
the ones that do.

### Proving the refactor changed nothing

**Run the reconciliation before and after.** If parity held before and holds after,
the refactor is safe. That is what the recon harness is for beyond cutover, and it
is why it stays on through phase 3.

---

## Rules while code is in `ported/`

- **It still gets reviewed.** Ported does not mean unreviewed.
- **No hardcoded catalogs, schemas or hosts.** Non-negotiable, at every stage.
- **No credentials.** Secret scope, always.
- **Origin and refactor ticket recorded** in the file header.
- **It still writes audit rows**, because the entry point does that.
- **It is still reconciled**, because reconciliation compares tables, not code.

---

## Sub-use-cases

Cloudera repos are split by use case *and* sub-use-case. Mirror it in the folders,
both zones:

```
bundles/us1/src/
  ported/
    billing/load_invoices.py
    claims/settle_claims.py
  us1_module/
    billing/
    claims/
```

Sub-use-cases do **not** get their own schema. Everything for us1 lands in
`edp_curated_<env>.us1`; separate tables are a name prefix (`billing_invoice`,
`claims_settlement`). Five use cases already give 15 data schemas; multiplying by
sub-use-case makes the grant matrix unmanageable for no isolation benefit.

---

## Consolidating multiple Cloudera repos

Your code is currently spread across repos by use case and sub-use-case. Bringing
one in:

1. **Map it to a use case first.** If a Cloudera repo spans two of the five, it
   becomes two folders, not one shared one. Resolve the ambiguity on the way in —
   it does not get easier later.
2. **Bring landing code to `bundles/landing/src/ported/`**, not to a use case. If it
   reads Kafka or Oracle, it is landing, even if only one use case uses it today.
3. **Bring its config with it**, into `conf/`. Cloudera config files usually become
   either a `sources.yml` row or a `reconciliation.yml` target.
4. **One PR per sub-use-case**, not one per repo. A 200-file PR gets rubber-stamped.
5. **Write the reconciliation config in the same PR.** Code arriving without a
   parity definition is code nobody can prove works.

---

## Tracking

**How much is left?**

```bash
find bundles/*/src/ported -name "*.py" ! -name "example_*" | wc -l
find bundles/*/src/ported -name "*.py" ! -name "example_*" | cut -d/ -f2 | sort | uniq -c
```

**Is it going down?** Track that count per sprint. A flat line means stage 3 is not
happening, and the honest response is to schedule it — not to redefine stage 1 as
acceptable.

If you want the exit enforced rather than tracked, add a CI check that fails when a
file in `ported/` has no refactor ticket in its header, or when it has sat there
past an agreed date. That is a policy decision, so it is not switched on by
default.

---

[← Migration and cutover](13-migration-and-cutover.md) · [Start here](00-START-HERE.md)
