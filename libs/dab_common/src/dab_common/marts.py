"""Publishing a datamart: audit, quality gate, grants.

WHY THIS IS HERE AND NOT IN FIVE MODULES
----------------------------------------
Every use case published its marts with the same 60 lines and the same
READER_GROUPS table, differing only in which tables it owned and which columns
it checked. Those two things are the use case; everything else is platform
policy, and platform policy that exists in five places is policy that will
diverge - usually the day someone adds an environment or a reader group.

So the split is:

    dab_common.marts        HOW marts are published, and WHO may read them
    <uc>_module/datamart.py WHICH tables this use case owns, and what
                            "correct" means for them

Grant statements are RETURNED, not executed, so the policy stays unit-testable
without a workspace.
"""

from __future__ import annotations

from collections.abc import Sequence

from dab_common.config import RuntimeContext

# Groups granted SELECT on published marts, per environment.
#
# Schema-level grants belong to the _platform bundle. These are the TABLE-level
# grants that can only be applied once the table exists - which is why they run
# from a job rather than from a bundle resource.
#
# Business users are granted at table level deliberately: a schema-level grant
# would also expose every intermediate table anyone creates there later.
READER_GROUPS: dict[str, tuple[str, ...]] = {
    "nonprod": ("edp-developers",),
    "preprod": ("edp-qa",),
    "prod": ("edp-support", "edp-business-analysts"),
}


def reader_grant_statements(
    ctx: RuntimeContext, tables: Sequence[str]
) -> list[str]:
    """GRANT SELECT statements for every mart in this environment.

    Empty in a sandbox: a developer's private mart should not be readable by the
    whole team, and granting on it would clutter the metastore with permissions
    that outlive the schema.
    """
    if ctx.is_sandbox:
        return []

    return [
        f"GRANT SELECT ON TABLE {ctx.table('datamart', table)} TO `{group}`"
        for group in READER_GROUPS.get(ctx.env, ())
        for table in tables
    ]


def publish(
    spark,
    ctx: RuntimeContext,
    tables: Sequence[str],
    expectations: Sequence = (),
    fact_table: str | None = None,
    log=print,
) -> dict:
    """Audit every mart, run the quality gate, then grant readers.

    In that order, deliberately: the audit rows exist even if the gate fails, so
    a failed run is still visible in ops.audit rather than leaving a silent gap.
    Grants come last - nothing is published to readers before it has been checked.
    """
    from dab_common.audit import audited_run, record_table_load
    from dab_common.quality import evaluate

    counts: dict[str, int] = {}
    with audited_run(spark, ctx, layer="datamart"):
        for table in tables:
            fq = ctx.table("datamart", table)
            rows = spark.table(fq).count()
            record_table_load(
                spark, ctx,
                source_id=f"{ctx.use_case}.{table}",
                target_table=fq,
                rows_written=rows,
                load_strategy="full",
            )
            counts[table] = rows
            log(f"  {table:<20} {rows:,} rows")

    results = []
    if expectations and fact_table:
        results = evaluate(spark, ctx, ctx.table("datamart", fact_table), list(expectations))
        for result in results:
            log(f"  {result['expectation_name']:<28} "
                f"{'PASS' if result['passed'] else 'FAIL'}")

    grants = reader_grant_statements(ctx, tables)
    for statement in grants:
        log(f"  {statement}")
        spark.sql(statement)
    if not grants:
        log("  no grants (sandbox)")

    log(f"{ctx.use_case} marts published")
    return {"rows": counts, "expectations": results, "grants": len(grants)}
