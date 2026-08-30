# Databricks notebook source
# MAGIC %md
# MAGIC # List Oracle sources to land
# MAGIC
# MAGIC Reads `ops.config.landing_source`, decides what this run should load, and
# MAGIC emits the list as a task value. The downstream `for_each` task fans out
# MAGIC over it.
# MAGIC
# MAGIC Splitting "decide what to load" from "load it" means a failed table shows
# MAGIC up as one failed iteration rather than a failed job, and re-running a
# MAGIC single table is a `--params source_ids=...` away.

# COMMAND ----------

import json

from dab_common.config import build_context
from edp_landing.registry import select_sources

ctx = build_context(dbutils.widgets.getAll())

# Optional narrowing, passed at run time:
#   databricks bundle run landing_us2 -t dev --params source_ids=us2_ora_customers
requested = [s.strip() for s in (ctx.extra.get("source_ids") or "").split(",") if s.strip()]

# COMMAND ----------

rows = [r.asDict(recursive=True) for r in spark.table(ctx.config_table("landing_source")).collect()]

# Scoped to THIS use case. The registry is shared across all five, so without
# this filter us2 would try to land us1 topics.
specs = select_sources(
    rows,
    source_system="oracle",
    use_case=ctx.use_case,
    source_ids=requested or None,
)

print(f"environment      : {ctx.env}")
print(f"use case         : {ctx.use_case}")
print(f"landing catalog  : {ctx.catalog('landing')}")
print(f"requested subset : {requested or 'ALL ACTIVE'}")
print(f"sources to land  : {len(specs)}")
for s in specs:
    print(f"  {s.source_id:<28} {s.source_object:<24} {s.load_strategy}")

# COMMAND ----------

source_ids = [s.source_id for s in specs]

if not source_ids:
    # An empty array would make for_each spawn zero iterations and report
    # success, which looks identical to a fully successful run. Fail instead.
    raise SystemExit(
        f"No active Oracle sources for {ctx.use_case} in {ctx.config_table('landing_source')}. "
        "Run landing_seed_source_registry for this environment first."
    )

dbutils.jobs.taskValues.set(key="source_ids", value=json.dumps(source_ids))
print(f"emitted source_ids = {json.dumps(source_ids)}")
