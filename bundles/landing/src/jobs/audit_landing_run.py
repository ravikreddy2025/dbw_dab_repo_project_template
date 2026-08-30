# Databricks notebook source
# MAGIC %md
# MAGIC # Audit a Kafka landing pipeline run
# MAGIC
# MAGIC Lakeflow Declarative Pipelines keep their own event log, but the rest of
# MAGIC the platform reads `ops.audit.table_load`. This task copies the row counts
# MAGIC across, so one query answers "what landed last night" for Kafka and Oracle
# MAGIC alike.

# COMMAND ----------

from dab_common.audit import audited_run, record_table_load
from dab_common.config import build_context
from edp_landing.registry import read_active_sources

ctx = build_context(dbutils.widgets.getAll())

# COMMAND ----------

with audited_run(spark, ctx, layer="landing"):
    for spec in read_active_sources(spark, ctx, source_system="kafka", use_case=ctx.use_case):
        target = ctx.table("landing", spec.target_table)

        # Rows added by the most recent pipeline update, read from the Delta
        # history rather than a full count - landing tables get large.
        added = spark.sql(
            f"""
            SELECT coalesce(
                     max(try_cast(operationMetrics['numOutputRows'] AS BIGINT)), 0
                   ) AS rows_added
            FROM (DESCRIBE HISTORY {target})
            WHERE operation IN ('STREAMING UPDATE', 'WRITE', 'MERGE')
            """
        ).collect()[0]["rows_added"]

        record_table_load(
            spark,
            ctx,
            source_id=spec.source_id,
            target_table=target,
            rows_written=added,
            load_strategy=spec.load_strategy,
        )
        print(f"{spec.source_id}: {added:,} rows -> {target}")
