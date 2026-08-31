# Databricks notebook source
# MAGIC %md
# MAGIC # Apply schema changes
# MAGIC
# MAGIC Runs after `bundle deploy`, before the smoke run, in every environment.
# MAGIC
# MAGIC **This file is a shim and is IDENTICAL in every use-case bundle.** The
# MAGIC logic lives once, in `dab_common.migrate.run_migrations`. It exists only
# MAGIC because `notebook_task.notebook_path` must resolve to a file inside the
# MAGIC bundle root — not because anything here differs per use case.
# MAGIC
# MAGIC `check_shared_shims` fails the build if the copies drift apart. If you
# MAGIC need to change what the migrate job does, change `dab_common`, not this.
# MAGIC
# MAGIC Two phases, both driven by `src/ddl/`:
# MAGIC
# MAGIC 1. **Current shape** — `ddl/curated/*.sql`, `ddl/datamart/*.sql`.
# MAGIC    `CREATE TABLE IF NOT EXISTS`, so it builds a new environment and is a
# MAGIC    no-op on an existing one.
# MAGIC 2. **Migrations** — `ddl/migrations/V*.sql`, in order, once each,
# MAGIC    recorded in `ops.config.schema_migration`.

# COMMAND ----------

from pathlib import Path

from dab_common import build_context
from dab_common.migrate import run_migrations

ctx = build_context(dbutils.widgets.getAll())

notebook_dir = Path(
    dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
).parent

run_migrations(spark, ctx, ddl_root=Path(f"/Workspace{notebook_dir}/../ddl"))

# COMMAND ----------

# MAGIC %md ## What this environment has applied

# COMMAND ----------

display(
    spark.sql(
        f"SELECT * FROM {ctx.config_table('schema_migration')} "
        "WHERE use_case = :uc ORDER BY version",
        args={"uc": ctx.use_case},
    )
)
