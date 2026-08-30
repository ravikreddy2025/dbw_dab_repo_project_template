# Databricks notebook source
# MAGIC %md
# MAGIC # us4: landing -> curated
# MAGIC
# MAGIC Thin entry point. The transformation itself is
# MAGIC `us4_module.curated.conform_inventory`, unit-tested in `tests/`.
# MAGIC
# MAGIC **Porting note.** While us4 logic still lives in `src/ported/`, call it
# MAGIC from here rather than inlining it - that keeps this file the single place
# MAGIC that knows about context, audit and schema creation, so the ported code can
# MAGIC be refactored underneath it without touching the job definition.

# COMMAND ----------

from dab_common.audit import audited_run, record_table_load
from dab_common.config import build_context, ensure_schema
from dab_common.quality import evaluate, non_negative, not_null, unique
from us4_module.curated import conform_inventory, dedupe_by_key

ctx = build_context(dbutils.widgets.getAll())
fail_on_error = str(ctx.extra.get("dq_fail_on_error", "true")).lower() == "true"

# Landed by the SHARED landing bundle into this use case schema of the landing
# catalog. us4 reads it; it never writes there.
source = ctx.table("landing", "kfk_orders")
target = ctx.table("curated", "inventory")
print(f"{source} -> {target}")

# COMMAND ----------

# audited_run guarantees exactly one terminal row in ops.audit.job_run whether
# this succeeds or throws, and re-raises so the Databricks run still fails.
with audited_run(spark, ctx, layer="curated"):
    ensure_schema(spark, ctx, "curated")

    landed = spark.table(source)

    # >>> PLACEHOLDER: replace with the ported us4 transformation. <<<
    curated = conform_inventory(landed)
    # Landing is append-only, so an incremental reload or a stream replay can
    # carry the same record twice. Latest event_ts wins.
    curated = dedupe_by_key(curated, keys=["inventory_id"], order_by="event_ts")

    curated.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(target)
    rows = spark.table(target).count()

    record_table_load(
        spark, ctx, source_id="us4_inventory_curated", target_table=target,
        rows_written=rows, load_strategy="full",
    )
    print(f"{rows:,} inventory rows written")

# COMMAND ----------

# MAGIC %md ## Data quality
# MAGIC
# MAGIC Results always land in `ops.audit.data_quality_result`; whether a breach
# MAGIC fails the job is controlled by `${var.dq_fail_on_error}`.

# COMMAND ----------

results = evaluate(
    spark,
    ctx,
    target,
    [
        not_null("inventory_id"),
        unique("inventory_id"),
        # conform_inventory nulls unrecognised statuses rather than dropping the
        # row, so this is what surfaces a new upstream status value.
        not_null("status"),
        non_negative("amount"),
    ],
    raise_on_error=fail_on_error,
)
for r in results:
    flag = "PASS" if r["passed"] else f"FAIL ({r['rows_failed']:,})"
    print(f"  {r['expectation_name']:<28} {flag}")
