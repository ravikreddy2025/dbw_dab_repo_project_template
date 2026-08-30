# Databricks notebook source
# MAGIC %md
# MAGIC # us5: landing -> curated
# MAGIC
# MAGIC Thin entry point. The transformation itself is
# MAGIC `us5_module.curated.conform_settlement`, unit-tested in `tests/`.
# MAGIC
# MAGIC **Porting note.** While us5 logic still lives in `src/ported/`, call it
# MAGIC from here rather than inlining it - that keeps this file the single place
# MAGIC that knows about context, audit and schema creation, so the ported code can
# MAGIC be refactored underneath it without touching the job definition.

# COMMAND ----------

from dab_common.audit import audited_run, record_table_load
from dab_common.config import build_context, ensure_schema
from dab_common.quality import evaluate, non_negative, not_null, unique
from us5_module.curated import conform_settlement, dedupe_by_key

ctx = build_context(dbutils.widgets.getAll())
fail_on_error = str(ctx.extra.get("dq_fail_on_error", "true")).lower() == "true"

# Landed by the SHARED landing bundle into this use case schema of the landing
# catalog. us5 reads it; it never writes there.
source = ctx.table("landing", "kfk_orders")
target = ctx.table("curated", "settlement")
print(f"{source} -> {target}")

# COMMAND ----------

# audited_run guarantees exactly one terminal row in ops.audit.job_run whether
# this succeeds or throws, and re-raises so the Databricks run still fails.
with audited_run(spark, ctx, layer="curated"):
    ensure_schema(spark, ctx, "curated")

    landed = spark.table(source)

    # >>> PLACEHOLDER: replace with the ported us5 transformation. <<<
    curated = conform_settlement(landed)
    # Landing is append-only, so an incremental reload or a stream replay can
    # carry the same record twice. Latest event_ts wins.
    curated = dedupe_by_key(curated, keys=["settlement_id"], order_by="event_ts")

    curated.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(target)
    rows = spark.table(target).count()

    record_table_load(
        spark, ctx, source_id="us5_settlement_curated", target_table=target,
        rows_written=rows, load_strategy="full",
    )
    print(f"{rows:,} settlement rows written")

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
        not_null("settlement_id"),
        unique("settlement_id"),
        # conform_settlement nulls unrecognised statuses rather than dropping the
        # row, so this is what surfaces a new upstream status value.
        not_null("status"),
        non_negative("amount"),
    ],
    raise_on_error=fail_on_error,
)
for r in results:
    flag = "PASS" if r["passed"] else f"FAIL ({r['rows_failed']:,})"
    print(f"  {r['expectation_name']:<28} {flag}")
