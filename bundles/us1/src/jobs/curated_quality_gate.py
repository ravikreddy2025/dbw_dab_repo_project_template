# Databricks notebook source
# MAGIC %md
# MAGIC # us1 curated quality gate
# MAGIC
# MAGIC Cross-table checks no single curation task can make on its own, plus a
# MAGIC summary of everything this run recorded. The datamart job sits behind
# MAGIC this: if curated is wrong, marts should not be built on top of it.

# COMMAND ----------

from dab_common.audit import audited_run
from dab_common.config import build_context

ctx = build_context(dbutils.widgets.getAll())
fail_on_error = str(ctx.extra.get("dq_fail_on_error", "true")).lower() == "true"

curated = ctx.table("curated", "orders")

# COMMAND ----------

with audited_run(spark, ctx, layer="curated"):
    # >>> PLACEHOLDER: add the us1 cross-table integrity checks here. <<<
    # The shape to follow: measure something, print it, and raise only when
    # fail_on_error is set - so a lead can downgrade the gate during an incident
    # without a code change, and the measurement is still recorded either way.
    total = spark.table(curated).count()
    undated = spark.sql(
        f"SELECT count(*) AS n FROM {curated} WHERE event_ts IS NULL"
    ).collect()[0]["n"]

    print(f"orders: {total:,} rows, {undated:,} with no event timestamp")

    if undated and fail_on_error:
        raise AssertionError(
            f"{undated:,} of {total:,} rows in {curated} have a null event_ts. "
            "Datamart build is blocked - check the landing payload schema."
        )

# COMMAND ----------

# MAGIC %md ## What this run recorded

# COMMAND ----------

display(
    spark.sql(
        f"""
        SELECT table_name, expectation_name, severity, rows_evaluated, rows_failed, passed
        FROM {ctx.audit_table('data_quality_result')}
        WHERE run_id = :run_id
        ORDER BY passed, table_name, expectation_name
        """,
        args={"run_id": ctx.run_id},
    )
)
