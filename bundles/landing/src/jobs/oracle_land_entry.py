# Databricks notebook source
# MAGIC %md
# MAGIC # Land one Oracle table
# MAGIC
# MAGIC One iteration of the `for_each` task. Deliberately thin: it resolves
# MAGIC context, looks up the source spec, and hands off to `edp_landing.oracle`.
# MAGIC All the logic worth testing lives in the wheel, where
# MAGIC `libs/edp_landing/tests/` covers it with no cluster.

# COMMAND ----------

from dab_common.audit import audited_run
from dab_common.config import build_context, ensure_schema
from edp_landing import oracle
from edp_landing.registry import select_sources

# `source_id` arrives from the for_each input substitution; the five base
# parameters are inherited from the job-level `parameters:` block.
ctx = build_context(dbutils.widgets.getAll())
source_id = ctx.extra["source_id"]

print(f"landing {source_id} into {ctx.fq_schema('landing')}")

# COMMAND ----------

rows = [r.asDict(recursive=True) for r in spark.table(ctx.config_table("landing_source")).collect()]
spec = select_sources(rows, source_ids=[source_id])[0]

# In a personal sandbox the target schema may not exist yet. No-ops in every
# shared environment - see dab_common.config.ensure_schema.
ensure_schema(spark, ctx, "landing")

# COMMAND ----------

# audited_run guarantees exactly one terminal row in ops.audit.job_run whether
# this succeeds or throws, and re-raises so the Databricks run still fails.
with audited_run(spark, ctx, layer="landing"):
    rows_written = oracle.ingest(spark, dbutils, ctx, spec)
    print(f"{source_id}: {rows_written:,} rows -> {ctx.table('landing', spec.target_table)}")
