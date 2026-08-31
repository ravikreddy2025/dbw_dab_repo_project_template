# Databricks notebook source
# MAGIC %md
# MAGIC # us1: publish the marts
# MAGIC
# MAGIC Runs after the SQL tasks have built the tables. Does the three things a
# MAGIC SQL task cannot: audit rows through `dab_common`, a quality gate on the
# MAGIC fact table, and table-level grants.
# MAGIC
# MAGIC The logic lives once in `dab_common.marts.publish`. This notebook exists
# MAGIC because `notebook_task` needs a file in the bundle root, and it names the
# MAGIC three things that are actually us1's: which tables, which fact table,
# MAGIC which expectations.

# COMMAND ----------

from dab_common import build_context
from dab_common.marts import publish
from us1_module.datamart import FACT_TABLE, MART_EXPECTATIONS, MART_TABLES

ctx = build_context(dbutils.widgets.getAll())
print(f"publishing us1 marts in {ctx.fq_schema('datamart')}")

# COMMAND ----------

publish(
    spark,
    ctx,
    tables=MART_TABLES,
    expectations=MART_EXPECTATIONS,
    fact_table=FACT_TABLE,
)
