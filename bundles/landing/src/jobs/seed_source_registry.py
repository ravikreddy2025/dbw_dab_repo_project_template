# Databricks notebook source
# MAGIC %md
# MAGIC # Seed ops.config.landing_source from conf/<use_case>/sources.yml
# MAGIC
# MAGIC Promotes source metadata the same way code is promoted: the YAML is
# MAGIC reviewed in a PR by the owning use case, travels through
# MAGIC `main` -> `release/*`, and is applied to each environment by this job.
# MAGIC
# MAGIC Run with `dry_run=true` to print the plan without writing anything.

# COMMAND ----------

from pathlib import Path

import yaml
from dab_common.audit import audited_run
from dab_common.config import build_context
from landing_module.seed import load_seed_file, plan_seed_merge

ctx = build_context(dbutils.widgets.getAll())
dry_run = str(ctx.extra.get("dry_run", "false")).lower() == "true"
only_use_case = (ctx.extra.get("only_use_case") or "").strip()

notebook_dir = Path(
    dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
).parent
conf_dir = Path(f"/Workspace{notebook_dir.parent}/conf")

print(f"target  : {ctx.config_table('landing_source')}")
print(f"conf    : {conf_dir}")
print(f"scope   : {only_use_case or 'ALL use cases'}")
print(f"dry run : {dry_run}")

# COMMAND ----------

# MAGIC %md ## 1. Read and validate every use case seed file
# MAGIC
# MAGIC `load_seed_file` re-runs every framework validation and cross-checks the
# MAGIC declared `use_case` against the folder it was found in. A source cannot be
# MAGIC registered against the wrong use case, which would land its data in
# MAGIC another team schema.

# COMMAND ----------

desired: list[dict] = []
for uc_dir in sorted(p for p in conf_dir.iterdir() if p.is_dir()):
    if only_use_case and uc_dir.name != only_use_case:
        continue
    seed_file = uc_dir / "sources.yml"
    if not seed_file.exists():
        print(f"  {uc_dir.name}: no sources.yml - skipped")
        continue
    raw = yaml.safe_load(seed_file.read_text(encoding="utf-8"))
    rows = load_seed_file(raw, expected_use_case=uc_dir.name)
    print(f"  {uc_dir.name}: {len(rows)} source(s)")
    desired.extend(rows)

if not desired:
    raise SystemExit("No sources found. Check conf/ and the only_use_case parameter.")

# COMMAND ----------

# MAGIC %md ## 2. Plan the change

# COMMAND ----------

existing = [
    r.asDict(recursive=True)
    for r in spark.table(ctx.config_table("landing_source")).collect()
]
# When seeding one use case, only that use case is in scope - otherwise every
# other use case would look "missing" and be deactivated.
if only_use_case:
    existing = [r for r in existing if r.get("use_case") == only_use_case]

diff = plan_seed_merge(desired, existing)

print(diff.summary())
for row in diff.to_insert:
    print(f"  INSERT     {row['source_id']}")
for row in diff.to_update:
    print(f"  UPDATE     {row['source_id']}")
for sid in diff.to_deactivate:
    print(f"  DEACTIVATE {sid}   (removed from the seed file; the row is kept)")

if diff.is_empty:
    print("registry already matches the seed files - nothing to do")

# COMMAND ----------

# MAGIC %md ## 3. Apply
# MAGIC
# MAGIC MERGE, not INSERT OVERWRITE: a source that disappears from a seed file is
# MAGIC deactivated, never deleted, so its watermark and audit history survive.

# COMMAND ----------

if dry_run:
    print("dry_run=true - stopping before any write")
elif not diff.is_empty:
    with audited_run(spark, ctx, layer="landing"):
        target = ctx.config_table("landing_source")
        staged = f"{ctx.catalog('ops')}.{ctx.ops_schema('config')}._stg_landing_source"

        spark.createDataFrame(desired).write.mode("overwrite").option(
            "overwriteSchema", "true"
        ).saveAsTable(staged)

        spark.sql(
            f"""
            MERGE INTO {target} AS t
            USING {staged} AS s ON t.source_id = s.source_id
            WHEN MATCHED THEN UPDATE SET
              t.use_case         = s.use_case,
              t.source_system    = s.source_system,
              t.source_object    = s.source_object,
              t.target_table     = s.target_table,
              t.load_strategy    = s.load_strategy,
              t.watermark_column = s.watermark_column,
              t.primary_keys     = s.primary_keys,
              t.secret_scope     = s.secret_scope,
              t.options          = s.options,
              t.is_active        = s.is_active,
              t.owner_email      = s.owner_email,
              t.updated_at       = current_timestamp()
            WHEN NOT MATCHED THEN INSERT *
            """
        )

        if diff.to_deactivate:
            ids = ", ".join(f"'{s}'" for s in diff.to_deactivate)
            spark.sql(
                f"UPDATE {target} SET is_active = false, updated_at = current_timestamp() "
                f"WHERE source_id IN ({ids})"
            )

        spark.sql(f"DROP TABLE IF EXISTS {staged}")
        print(f"applied: {diff.summary()}")

# COMMAND ----------

display(
    spark.sql(
        f"SELECT use_case, source_id, source_system, load_strategy, is_active "
        f"FROM {ctx.config_table('landing_source')} ORDER BY use_case, source_id"
    )
)
