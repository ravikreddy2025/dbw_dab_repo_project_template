# Databricks notebook source
# MAGIC %md
# MAGIC # Reconcile one use case against the legacy platform
# MAGIC
# MAGIC **ONE NOTEBOOK, EVERY USE CASE.** Which one it checks comes from the
# MAGIC `use_case` job parameter; what it checks comes from
# MAGIC `conf/<use_case>.yml`. Onboarding a sixth use case is a config file and a
# MAGIC job entry - never a change to this file.
# MAGIC
# MAGIC That is not an accident of design. For a lift and shift every use case is
# MAGIC the same job: compare this target table against that source table.
# MAGIC
# MAGIC **This is the migration gate.** Results land in `ops.recon`, where
# MAGIC `cutover_readiness` turns go-live from a judgement call into a query.
# MAGIC
# MAGIC See `docs/13-migration-and-cutover.md`.

# COMMAND ----------

from datetime import UTC, datetime
from pathlib import Path

import yaml
from dab_common.audit import audited_run
from dab_common.config import build_context
from edp_recon.gate import STATUS_FAILED, STATUS_PASSED, STATUS_SKIPPED, etl_completed_for
from edp_recon.model import load_recon_config

ctx = build_context(dbutils.widgets.getAll())

if str(ctx.extra.get("recon_enabled", "true")).lower() != "true":
    dbutils.notebook.exit(
        f"recon_enabled=false in {ctx.env} - reconciliation is retired for this environment."
    )

print(f"use case : {ctx.use_case}")
print(f"env      : {ctx.env}")
print(f"target   : {ctx.fq_schema('curated')} / {ctx.fq_schema('datamart')}")
print(f"results  : {ctx.recon_table('parity_run')}")

# COMMAND ----------

# MAGIC %md ## 1. Load this use case's parity definition

# COMMAND ----------

notebook_dir = Path(
    dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
).parent
conf_path = Path(f"/Workspace{notebook_dir.parent}/conf/{ctx.use_case}.yml")

plan = load_recon_config(yaml.safe_load(conf_path.read_text(encoding="utf-8")))
if plan.use_case != ctx.use_case:
    # The job says us1 and the config says us2: one of them is wrong, and either
    # way the comparison would be meaningless.
    raise ValueError(
        f"job parameter use_case={ctx.use_case!r} but {conf_path.name} declares "
        f"{plan.use_case!r}. Fix the job entry or the config."
    )

print(plan.summary())

# COMMAND ----------

# MAGIC %md ## 2. Gate: did the ETL actually run?
# MAGIC
# MAGIC Recon runs on its own schedule, decoupled from the ETL. The cost of that
# MAGIC is that it can fire when the ETL did not run - and comparing yesterday's
# MAGIC Databricks output against today's Cloudera extract produces a mismatch
# MAGIC that looks exactly like a migration defect.
# MAGIC
# MAGIC A run whose ETL did not complete is recorded **SKIPPED**, not FAILED.
# MAGIC Skipped is not clean either: `cutover_readiness` counts only clean runs,
# MAGIC so a use case whose ETL keeps failing never accumulates the consecutive
# MAGIC passes it needs. The gate cannot be used to dodge the check.

# COMMAND ----------

gate = etl_completed_for(
    spark, ctx, within_hours=int(ctx.extra.get("etl_window_hours", 24))
)
print(gate)

# COMMAND ----------

# MAGIC %md ## 3. Measure both sides
# MAGIC
# MAGIC `check.measure_sql(table)` builds the SAME measurement for both sides, so a
# MAGIC difference can only come from the data - never from two different queries.
# MAGIC That property is what makes the result trustworthy, and it is unit-tested
# MAGIC in `libs/edp_recon/tests/test_model.py`.

# COMMAND ----------

started = datetime.now(UTC)
run_rows: list[dict] = []
check_rows: list[dict] = []

with audited_run(spark, ctx, layer="recon"):
    for target in plan.targets:
        databricks_table = target.resolve_target(ctx)
        legacy_table = target.source_ref

        print(f"\n=== {target.name} ===")
        print(f"  legacy     : {legacy_table}")
        print(f"  databricks : {databricks_table}")

        if not gate.proceed:
            print("  SKIPPED - ETL gate not satisfied")
            run_rows.append(
                {
                    "recon_run_id": ctx.run_id,
                    "use_case": ctx.use_case,
                    "target_name": target.name,
                    "layer": target.layer,
                    "target_table": databricks_table,
                    "source_ref": legacy_table,
                    "env": ctx.env,
                    "status": STATUS_SKIPPED,
                    "checks_total": len(target.checks),
                    "checks_passed": 0,
                    "checks_failed": 0,
                    "overall_passed": False,
                    "started_at": started,
                    "ended_at": datetime.now(UTC),
                    "notes": gate.reason,
                }
            )
            continue

        passed_count = 0
        for check in target.checks:
            legacy = float(spark.sql(check.measure_sql(legacy_table)).collect()[0]["metric"])
            actual = float(spark.sql(check.measure_sql(databricks_table)).collect()[0]["metric"])
            ok = check.passed(legacy, actual)
            passed_count += int(ok)

            check_rows.append(
                {
                    "recon_run_id": ctx.run_id,
                    "use_case": ctx.use_case,
                    "target_name": target.name,
                    "check_name": check.name,
                    "check_type": check.check_type,
                    "column_name": check.column,
                    "legacy_metric": legacy,
                    "target_metric": actual,
                    "difference": legacy - actual,
                    "relative_diff": (abs(legacy - actual) / abs(legacy)) if legacy else None,
                    "tolerance": check.tolerance,
                    "justification": check.justification,
                    "passed": ok,
                    "env": ctx.env,
                    "evaluated_at": datetime.now(UTC),
                }
            )
            flag = "PASS" if ok else "FAIL"
            print(f"  {flag}  {check.name:<26} legacy={legacy}  databricks={actual}")

        all_passed = passed_count == len(target.checks)
        run_rows.append(
            {
                "recon_run_id": ctx.run_id,
                "use_case": ctx.use_case,
                "target_name": target.name,
                "layer": target.layer,
                "target_table": databricks_table,
                "source_ref": legacy_table,
                "env": ctx.env,
                "status": STATUS_PASSED if all_passed else STATUS_FAILED,
                "checks_total": len(target.checks),
                "checks_passed": passed_count,
                "checks_failed": len(target.checks) - passed_count,
                "overall_passed": all_passed,
                "started_at": started,
                "ended_at": datetime.now(UTC),
                "notes": gate.reason,
            }
        )

    if check_rows:
        spark.createDataFrame(check_rows).write.mode("append").saveAsTable(
            ctx.recon_table("parity_check_result")
        )
    spark.createDataFrame(run_rows).write.mode("append").saveAsTable(
        ctx.recon_table("parity_run")
    )

# COMMAND ----------

# MAGIC %md ## 4. Cutover readiness
# MAGIC
# MAGIC A parity MISMATCH is a finding, not a job failure: the job completes and
# MAGIC records it. A red job nobody can query tells you less than a green job
# MAGIC with a row in `parity_check_result`.

# COMMAND ----------

display(
    spark.sql(
        f"SELECT * FROM {ctx.catalog('ops')}.{ctx.ops_schema('recon')}.cutover_readiness "
        f"WHERE use_case = :uc AND env = :env",
        args={"uc": ctx.use_case, "env": ctx.env},
    )
)

failed = [r for r in run_rows if r["status"] == STATUS_FAILED]
skipped = [r for r in run_rows if r["status"] == STATUS_SKIPPED]

if skipped:
    print(f"\n{len(skipped)} target(s) SKIPPED: {gate.reason}")
elif failed:
    names = [r["target_name"] for r in failed]
    print(f"\n{len(failed)} target(s) did NOT reach parity: {names}")
    print(
        f"Investigate: SELECT * FROM {ctx.recon_table('parity_check_result')} "
        f"WHERE recon_run_id = '{ctx.run_id}' AND NOT passed"
    )
else:
    print(f"\nall {ctx.use_case} targets reached parity")
