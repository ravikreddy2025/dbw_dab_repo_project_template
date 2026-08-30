# Databricks notebook source
# MAGIC %md
# MAGIC # Summarise the landing run
# MAGIC
# MAGIC Runs with `run_if: ALL_DONE`, so it executes even when some tables failed
# MAGIC - a partial-failure night is exactly when someone needs the summary.

# COMMAND ----------

from dab_common.config import build_context

ctx = build_context(dbutils.widgets.getAll())

# COMMAND ----------

summary = spark.sql(
    f"""
    SELECT source_id, status, rows_written, watermark_from, watermark_to, loaded_at
    FROM {ctx.audit_table('table_load')}
    WHERE run_id = :run_id AND use_case = :use_case
    ORDER BY source_id
    """,
    args={"run_id": ctx.run_id, "use_case": ctx.use_case},
)
display(summary)

# COMMAND ----------

failed = spark.sql(
    f"""
    SELECT task_key, error_message
    FROM {ctx.audit_table('job_run')}
    WHERE run_id = :run_id AND status = 'FAILED'
    """,
    args={"run_id": ctx.run_id},
).collect()

if failed:
    for row in failed:
        print(f"FAILED  {row['task_key']}: {row['error_message']}")
    # Surface the partial failure on the summary task too, so the job is
    # unambiguously red rather than green with a red iteration inside it.
    raise RuntimeError(
        f"{len(failed)} table load(s) failed - see {ctx.audit_table('job_run')}"
    )

total = sum(r["rows_written"] or 0 for r in summary.collect())
print(f"all {ctx.use_case} Oracle sources landed successfully: {total:,} rows")
