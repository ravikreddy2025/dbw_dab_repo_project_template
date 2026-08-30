"""Run and table level audit.

Two grains, deliberately:

  ops.audit.job_run    - one row per (run_id, task_key). "Did this task run, and
                         did it work?" Written by every job in every bundle.
  ops.audit.table_load - one row per table written. "How many rows landed, from
                         which watermark to which?" Written by landing + curated.

These live in the OPS catalog, not alongside the data, so a support team can be
granted operational visibility without any access to data.

Both are append-only. Nothing rewrites history; a retry appends a new row with a
new attempt. That makes the audit tables safe to read from a dashboard while a
load is in flight.
"""

from __future__ import annotations

import traceback
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from dab_common.config import RuntimeContext

STATUS_RUNNING = "RUNNING"
STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"
STATUS_SKIPPED = "SKIPPED"


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class RunAuditRecord:
    """The row shape of ctrl.job_run_audit. Column order matches the DDL."""

    run_id: str
    job_id: str
    task_key: str
    use_case: str
    layer: str
    env: str
    bundle_target: str
    status: str
    started_at: datetime
    ended_at: datetime | None = None
    duration_seconds: float | None = None
    error_message: str | None = None
    error_detail: str | None = None
    context_tags: dict[str, str] = field(default_factory=dict)


def _insert(spark, table: str, record: dict[str, Any]) -> None:
    """Append a single dict as one row.

    Uses createDataFrame + append rather than an INSERT string so values are
    bound, never interpolated - audit rows carry error text, and error text
    carries quotes.
    """
    df = spark.createDataFrame([record])
    df.write.mode("append").saveAsTable(table)


def record_run(
    spark,
    ctx: RuntimeContext,
    layer: str,
    status: str,
    started_at: datetime,
    ended_at: datetime | None = None,
    error: BaseException | None = None,
) -> None:
    """Write one ctrl.job_run_audit row."""
    ended = ended_at or _utcnow()
    rec = RunAuditRecord(
        run_id=ctx.run_id,
        job_id=ctx.job_id,
        task_key=ctx.task_key,
        use_case=ctx.use_case,
        layer=layer,
        env=ctx.env,
        bundle_target=ctx.bundle_target,
        status=status,
        started_at=started_at,
        ended_at=ended,
        duration_seconds=round((ended - started_at).total_seconds(), 3),
        error_message=(f"{type(error).__name__}: {error}"[:1000] if error else None),
        error_detail=("".join(traceback.format_exception(error))[:8000] if error else None),
        context_tags=ctx.tags(),
    )
    _insert(spark, ctx.audit_table("job_run"), asdict(rec))


@contextmanager
def audited_run(spark, ctx: RuntimeContext, layer: str):
    """Wrap a task so it always lands exactly one terminal audit row.

    Usage in a notebook entry point:

        with audited_run(spark, ctx, layer="curated"):
            build_customer_dimension(spark, ctx)

    On success -> one SUCCESS row. On exception -> one FAILED row carrying the
    traceback, and the exception is re-raised so the Databricks run is still
    marked failed. Never swallows.
    """
    started = _utcnow()
    try:
        yield
    except BaseException as exc:  # noqa: BLE001 - deliberate: audit then re-raise
        try:
            record_run(spark, ctx, layer, STATUS_FAILED, started, error=exc)
        except Exception:  # noqa: BLE001
            # An audit-write failure must not mask the real error the user needs.
            print("WARNING: failed to write FAILED audit row; original error follows.")
        raise
    else:
        record_run(spark, ctx, layer, STATUS_SUCCESS, started)


def record_table_load(
    spark,
    ctx: RuntimeContext,
    source_id: str,
    target_table: str,
    rows_written: int,
    load_strategy: str,
    watermark_from: str | None = None,
    watermark_to: str | None = None,
    status: str = STATUS_SUCCESS,
) -> None:
    """Write one ctrl.table_load_audit row."""
    _insert(
        spark,
        ctx.audit_table("table_load"),
        {
            "run_id": ctx.run_id,
            "use_case": ctx.use_case,
            "source_id": source_id,
            "target_table": target_table,
            "load_strategy": load_strategy,
            "rows_written": int(rows_written),
            "watermark_from": watermark_from,
            "watermark_to": watermark_to,
            "status": status,
            "env": ctx.env,
            "loaded_at": _utcnow(),
        },
    )
