# Databricks notebook source
# MAGIC %md
# MAGIC # us4: publish the marts
# MAGIC
# MAGIC Runs after the SQL tasks have built the tables. Does the three things a
# MAGIC SQL task cannot: writes audit rows through `dab_common`, applies
# MAGIC table-level grants, and asserts the marts are usable.

# COMMAND ----------

from dab_common.audit import audited_run, record_table_load
from dab_common.config import build_context
from dab_common.quality import evaluate, non_negative, not_null, unique
from us4_module.datamart import MART_TABLES, reader_grant_statements

ctx = build_context(dbutils.widgets.getAll())
print(f"publishing us4 marts in {ctx.fq_schema('datamart')}")

# COMMAND ----------

# MAGIC %md ## 1. Audit what was built

# COMMAND ----------

with audited_run(spark, ctx, layer="datamart"):
    for table in MART_TABLES:
        fq = ctx.table("datamart", table)
        rows = spark.table(fq).count()
        record_table_load(
            spark, ctx, source_id=f"us4.{table}", target_table=fq,
            rows_written=rows, load_strategy="full",
        )
        print(f"  {table:<20} {rows:,} rows")

# COMMAND ----------

# MAGIC %md ## 2. Quality gate on the fact table

# COMMAND ----------

fct = ctx.table("datamart", "fct_inventory")
results = evaluate(
    spark,
    ctx,
    fct,
    [
        not_null("inventory_id"),
        unique("inventory_id"),
        non_negative("amount"),
    ],
)
for r in results:
    print(f"  {r['expectation_name']:<28} {'PASS' if r['passed'] else 'FAIL'}")

# COMMAND ----------

# MAGIC %md ## 3. Table-level grants
# MAGIC
# MAGIC Schema-level grants belong to the `_platform` bundle. These are the
# MAGIC table-level grants that only make sense once the table exists.

# COMMAND ----------

for statement in reader_grant_statements(ctx):
    print(f"  {statement}")
    spark.sql(statement)

print("us4 marts published")
