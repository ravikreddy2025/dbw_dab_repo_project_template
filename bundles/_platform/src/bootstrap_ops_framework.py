# Databricks notebook source
# MAGIC %md
# MAGIC # Platform bootstrap - the ops catalog
# MAGIC
# MAGIC Creates (or upgrades) every table in `<catalog_prefix>_ops_<env>`:
# MAGIC `audit`, `config`, `logs` and `recon`.
# MAGIC
# MAGIC Idempotent - every statement is `CREATE TABLE IF NOT EXISTS` or
# MAGIC `CREATE OR REPLACE VIEW` - so the CD pipeline runs this on every platform
# MAGIC deploy, and a new column reaches all three environments the same way code
# MAGIC does.
# MAGIC
# MAGIC **Owned by the platform team.** Use-case teams read these tables; they do
# MAGIC not change this notebook.

# COMMAND ----------

from pathlib import Path

from dab_common.config import OPS_SCHEMAS, build_context

# The platform bundle is not scoped to a use case; `use_case=platform` is what
# its audit rows are stamped with.
ctx = build_context(dbutils.widgets.getAll())

print(f"env            : {ctx.env}")
print(f"ops catalog    : {ctx.catalog('ops')}")
print(f"data catalogs  : {', '.join(ctx.catalog(name) for name in ('landing', 'curated', 'datamart'))}")
assert not ctx.is_sandbox, "The platform bundle must never run against a sandbox."

# COMMAND ----------

# MAGIC %md ## 1. Apply the DDL
# MAGIC
# MAGIC One `.sql` file per ops schema, synced into the workspace beside this
# MAGIC notebook so the SQL is reviewable as SQL in a PR rather than buried in
# MAGIC Python strings.

# COMMAND ----------

# One or more DDL files per ops schema. Kept in step with
# dab_common.config.OPS_SCHEMAS by the assertion below, so adding a schema
# without its DDL fails loudly rather than producing a half-built ops catalog.
DDL_FILES = {
    "audit":  ["ddl/ops_audit.sql", "ddl/ops_housekeeping.sql"],
    "config": ["ddl/ops_config.sql"],
    "logs":   ["ddl/ops_logs.sql"],
    "recon":  ["ddl/ops_recon.sql"],
}
assert set(DDL_FILES) == set(OPS_SCHEMAS), (
    f"DDL files {sorted(DDL_FILES)} do not match OPS_SCHEMAS {sorted(OPS_SCHEMAS)}"
)

notebook_dir = Path(
    dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
).parent


def split_statements(script: str) -> list[str]:
    """Split on `;`, dropping comment-only and empty fragments."""
    out = []
    for raw in script.split(";"):
        stmt = "\n".join(
            line for line in raw.splitlines() if not line.strip().startswith("--")
        ).strip()
        if stmt:
            out.append(stmt)
    return out


for schema_name, ddl_files in DDL_FILES.items():
    # DDL_FILES values are LISTS - one ops schema can need several files.
    for ddl_file in ddl_files:
        script = Path(f"/Workspace{notebook_dir}/{ddl_file}").read_text(encoding="utf-8")

        # Both values were validated by build_context(), so this substitution cannot
        # inject anything a bundle variable did not already contain.
        script = (
            script.replace("{{catalog}}", ctx.catalog("ops"))
                  .replace("{{schema}}", ctx.ops_schema(schema_name))
        )

        print(f"\n--- {ddl_file} -> {ctx.catalog('ops')}.{ctx.ops_schema(schema_name)} ---")
        for statement in split_statements(script):
            print(f"  {statement.splitlines()[0][:100]}")
            spark.sql(statement)

# COMMAND ----------

# MAGIC %md ## 2. Report what exists now

# COMMAND ----------

for schema_name in OPS_SCHEMAS:
    print(f"\n=== {ctx.catalog('ops')}.{ctx.ops_schema(schema_name)} ===")
    display(spark.sql(f"SHOW TABLES IN {ctx.catalog('ops')}.{ctx.ops_schema(schema_name)}"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next steps for a brand-new environment
# MAGIC
# MAGIC 1. Deploy the **landing** bundle, then run `landing_seed_source_registry`
# MAGIC    to populate `ops.config.landing_source`.
# MAGIC 2. Deploy each **use-case** bundle (`cd-us1` … `cd-us5`).
# MAGIC 3. See `docs/07-release-process.md#new-environment`.
