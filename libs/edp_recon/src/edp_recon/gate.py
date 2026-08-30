"""Has the ETL this reconciliation checks actually run?

WHY THIS EXISTS
---------------
Recon lives in its own bundle on its own schedule, deliberately decoupled from the
ETL it checks. The cost of that decoupling is that recon can fire when the ETL did
not run - a failed load, a paused schedule, a slow night that overran.

Comparing YESTERDAY's Databricks output against TODAY's Cloudera extract produces
a mismatch that looks exactly like a migration defect. Chasing one of those costs a
day, and worse, it teaches people that recon failures are usually noise - which is
precisely the belief that makes the real failure get ignored.

So a run whose ETL did not complete is recorded as SKIPPED, not FAILED. A skipped
run is not clean either: `cutover_readiness` counts only clean runs, so a use case
whose ETL keeps failing never accumulates the consecutive passes it needs. The
gate cannot be used to quietly avoid the check.
"""

from __future__ import annotations

from dataclasses import dataclass

from dab_common.config import RuntimeContext

# Terminal states of a parity run. SKIPPED is distinct from FAILED on purpose:
# "we could not check" is different information from "we checked and it differs".
STATUS_PASSED = "PASSED"
STATUS_FAILED = "FAILED"
STATUS_SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class EtlGateResult:
    """Whether reconciliation should proceed, and why."""

    proceed: bool
    reason: str
    last_success_at: str | None = None

    def __str__(self) -> str:
        return f"{'PROCEED' if self.proceed else 'SKIP'}: {self.reason}"


def build_gate_query(ctx: RuntimeContext, job_names: list[str], within_hours: int) -> str:
    """SQL returning the most recent successful run of any named job.

    Pure, so the window logic is unit-tested rather than inferred from a run.

    `use_case` is filtered as well as the task name, because ops.audit.job_run is
    shared across every use case - without it, a successful us3 run would satisfy
    the gate for us1.

    Reads the audit log via `upstream_ops_table`, so it follows the SAME
    `upstream_mode` as the tables being compared. Checking the shared audit log
    while comparing sandbox tables (or the reverse) would answer about a
    different run entirely.
    """
    names = ", ".join("'" + n.replace("'", "''") + "'" for n in job_names)
    return f"""
        SELECT max(ended_at) AS last_success
        FROM {ctx.upstream_ops_table('audit', 'job_run')}
        WHERE use_case = '{ctx.use_case}'
          AND env      = '{ctx.env}'
          AND status   = 'SUCCESS'
          AND task_key IN ({names})
          AND ended_at >= current_timestamp() - INTERVAL {int(within_hours)} HOURS
    """  # noqa: S608 - table name and use_case are validated by RuntimeContext


def etl_completed_for(
    spark,
    ctx: RuntimeContext,
    job_names: list[str] | None = None,
    within_hours: int = 24,
) -> EtlGateResult:
    """Check that the ETL feeding this reconciliation completed recently.

    `job_names` are TASK keys from ops.audit.job_run, not job keys. Defaults to the
    tasks the standard use-case bundle runs last in each layer.

    `within_hours` should exceed the ETL interval with headroom: 24 for a daily
    load, so a run that starts a few hours late still satisfies the gate.
    """
    tasks = job_names or ["publish_marts", "curated_quality_gate"]

    rows = spark.sql(build_gate_query(ctx, tasks, within_hours)).collect()
    last = rows[0]["last_success"] if rows else None

    if last is None:
        return EtlGateResult(
            proceed=False,
            reason=(
                f"no successful {tasks} for {ctx.use_case} in {ctx.env} within "
                f"{within_hours}h - the ETL has not run, so any comparison would be "
                "against stale output. Recording SKIPPED."
            ),
        )

    return EtlGateResult(
        proceed=True,
        reason=f"ETL for {ctx.use_case} last succeeded at {last}",
        last_success_at=str(last),
    )
