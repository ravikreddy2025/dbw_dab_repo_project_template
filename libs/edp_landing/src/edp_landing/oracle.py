"""Oracle landing framework (JDBC, batch, watermark-incremental).

>>> REFERENCE SKELETON <<<
Illustrative of the shape, not a framework you must adopt. Your Cloudera code
lands in bundles/landing/src/ported/ and is refactored into here over time.
What is load-bearing is the contract: a SourceSpec in, a landed table out, an
audit row written. See edp_landing/__init__.py.


Shape of a load:
  1. read the source spec + last watermark from the control tables
  2. build a bounded SELECT (full or incremental)
  3. read over JDBC, optionally partitioned for parallelism
  4. write to landing, append or overwrite per strategy
  5. advance the watermark and write the audit row - in that order, so a crash
     between them re-loads rather than skips

Credentials come from a secret scope named in the control row; nothing about a
specific database is hardcoded here.
"""

from __future__ import annotations

from dataclasses import dataclass

from dab_common import audit
from dab_common.config import RuntimeContext

from edp_landing import registry

# Oracle object names may be schema-qualified and are case-sensitive when quoted.
# We only allow the plain SCHEMA.TABLE form, validated before it reaches SQL.
_ORACLE_OBJECT_PARTS = 2


class OracleIngestionError(RuntimeError):
    pass


@dataclass(frozen=True)
class JdbcConnection:
    """Connection details resolved from a secret scope."""

    url: str
    user: str
    password: str
    driver: str = "oracle.jdbc.OracleDriver"

    def options(self) -> dict[str, str]:
        return {
            "url": self.url,
            "user": self.user,
            "password": self.password,
            "driver": self.driver,
            # Oracle defaults to 10 rows/round-trip; this is the single biggest
            # throughput lever for wide tables.
            "fetchsize": "10000",
            "oracle.jdbc.timezoneAsRegion": "false",
        }


def validate_source_object(source_object: str) -> tuple[str, str]:
    """Split and validate an Oracle `SCHEMA.TABLE` reference."""
    parts = source_object.split(".")
    if len(parts) != _ORACLE_OBJECT_PARTS:
        raise OracleIngestionError(
            f"source_object must be SCHEMA.TABLE, got {source_object!r}"
        )
    for p in parts:
        if not p or not p.replace("_", "").replace("$", "").isalnum():
            raise OracleIngestionError(f"Unsafe Oracle identifier in {source_object!r}")
    return parts[0], parts[1]


def build_extract_query(spec: registry.SourceSpec, watermark: str | None) -> str:
    """Build the bounded SELECT pushed down to Oracle.

    Pure: this is the function that decides how much data prod pulls, so it is
    unit-tested directly rather than inferred from a run.

    Incremental loads use `>` (strictly greater) against the stored watermark.
    That relies on the watermark column being monotonically non-decreasing and
    is why `advance_watermark` refuses to move backwards.
    """
    schema, table = validate_source_object(spec.source_object)
    base = f"SELECT * FROM {schema}.{table}"

    if spec.load_strategy == "full" or watermark is None:
        return base

    if spec.load_strategy != "incremental":
        raise OracleIngestionError(
            f"{spec.source_id}: strategy {spec.load_strategy!r} is not supported over JDBC"
        )

    col = spec.watermark_column
    # Watermarks are stored as ISO strings; Oracle needs them cast explicitly
    # rather than relying on NLS session settings, which differ per driver.
    safe = watermark.replace("'", "''")
    return f"{base} WHERE {col} > TO_TIMESTAMP('{safe}', 'YYYY-MM-DD\"T\"HH24:MI:SS.FF')"


def build_read_options(
    spec: registry.SourceSpec, conn: JdbcConnection, query: str
) -> dict[str, str]:
    """Assemble the full spark.read.format('jdbc') option map.

    Partitioned reads are opt-in per source via control-row `options`, because a
    partitioned read on an unindexed column is slower than a single stream.
    """
    opts = conn.options()
    opts["query"] = query

    extra = spec.options or {}
    part_col = extra.get("partition_column")
    if part_col:
        required = ("lower_bound", "upper_bound", "num_partitions")
        if not all(extra.get(k) is not None for k in required):
            raise OracleIngestionError(
                f"{spec.source_id}: partition_column set but missing {required}"
            )
        opts.update(
            {
                "partitionColumn": str(part_col),
                "lowerBound": str(extra["lower_bound"]),
                "upperBound": str(extra["upper_bound"]),
                "numPartitions": str(extra["num_partitions"]),
            }
        )
    return opts


def resolve_connection(dbutils, spec: registry.SourceSpec) -> JdbcConnection:
    """Pull JDBC credentials out of the secret scope named on the control row."""
    scope = spec.secret_scope
    if not scope:
        raise OracleIngestionError(f"{spec.source_id}: no secret_scope on the control row")
    return JdbcConnection(
        url=dbutils.secrets.get(scope, "jdbc-url"),
        user=dbutils.secrets.get(scope, "username"),
        password=dbutils.secrets.get(scope, "password"),
    )


def ingest(spark, dbutils, ctx: RuntimeContext, spec: registry.SourceSpec) -> int:
    """Run one Oracle source end to end. Returns rows written."""
    from pyspark.sql import functions as F  # noqa: N812 - runtime-only import

    target = ctx.table("landing", spec.target_table, use_case=spec.use_case)
    watermark_from = registry.read_watermark(spark, ctx, spec.source_id)

    conn = resolve_connection(dbutils, spec)
    query = build_extract_query(spec, watermark_from)
    options = build_read_options(spec, conn, query)

    df = spark.read.format("jdbc").options(**options).load()
    df = (
        df.withColumn("_ingested_at", F.current_timestamp())
        .withColumn("_source_id", F.lit(spec.source_id))
        .withColumn("_run_id", F.lit(ctx.run_id))
    )

    mode = "overwrite" if spec.load_strategy == "full" else "append"
    df.write.mode(mode).option("mergeSchema", "true").saveAsTable(target)
    rows = df.count()

    watermark_to = watermark_from
    if spec.load_strategy == "incremental" and rows:
        max_wm = spark.table(target).selectExpr(f"max({spec.watermark_column})").collect()[0][0]
        if max_wm is not None:
            watermark_to = max_wm.isoformat() if hasattr(max_wm, "isoformat") else str(max_wm)
            registry.advance_watermark(spark, ctx, spec.source_id, watermark_to)

    audit.record_table_load(
        spark,
        ctx,
        source_id=spec.source_id,
        target_table=target,
        rows_written=rows,
        load_strategy=spec.load_strategy,
        watermark_from=watermark_from,
        watermark_to=watermark_to,
    )
    return rows
