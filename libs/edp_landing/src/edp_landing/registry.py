"""Metadata-driven landing: the source registry.

The landing framework is config-driven, not code-driven. Onboarding a new Oracle
table or Kafka topic is a row in `bundles/landing/conf/<use_case>/sources.yml`,
reviewed as a PR by that use case's owners, not a new notebook.

Registry tables live in the OPS catalog, because they are operational metadata
shared across use cases rather than anyone's data:

  ops.config.landing_source     - one row per source object; what to load and how
  ops.config.landing_watermark  - last successfully-loaded high-water mark

DDL: bundles/_platform/src/ddl/ops_config.sql
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dab_common.config import RuntimeContext, validate_identifier

# Load strategies understood by the frameworks. Anything else is a config error
# caught at seed time, not at 3am in a prod run.
LOAD_STRATEGIES = ("full", "incremental", "cdc_stream")
SOURCE_SYSTEMS = ("oracle", "kafka")


class RegistryError(RuntimeError):
    """Raised when registry content is missing or self-inconsistent."""


@dataclass(frozen=True)
class SourceSpec:
    """One row of ctrl.ingestion_source, validated."""

    source_id: str
    source_system: str          # oracle | kafka
    source_object: str          # ORACLE: SCHEMA.TABLE   KAFKA: topic name
    use_case: str               # which schema in the landing catalog: us1 .. us5
    target_table: str
    load_strategy: str          # full | incremental | cdc_stream
    watermark_column: str | None = None
    primary_keys: tuple[str, ...] = ()
    secret_scope: str = ""
    options: dict[str, Any] | None = None
    is_active: bool = True

    def __post_init__(self) -> None:
        if self.source_system not in SOURCE_SYSTEMS:
            raise RegistryError(f"{self.source_id}: unknown source_system {self.source_system!r}")
        if self.load_strategy not in LOAD_STRATEGIES:
            raise RegistryError(f"{self.source_id}: unknown load_strategy {self.load_strategy!r}")
        if self.load_strategy == "incremental" and not self.watermark_column:
            raise RegistryError(
                f"{self.source_id}: load_strategy='incremental' requires a watermark_column"
            )
        validate_identifier(self.target_table, "target_table")
        validate_identifier(self.use_case, "use_case")

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> SourceSpec:
        """Build from a Spark Row converted with `.asDict()` or from seed YAML."""
        pks = row.get("primary_keys") or ()
        if isinstance(pks, str):
            pks = tuple(p.strip() for p in pks.split(",") if p.strip())
        return cls(
            source_id=row["source_id"],
            source_system=row["source_system"],
            source_object=row["source_object"],
            use_case=row["use_case"],
            target_table=row["target_table"],
            load_strategy=row["load_strategy"],
            watermark_column=row.get("watermark_column") or None,
            primary_keys=tuple(pks),
            secret_scope=row.get("secret_scope", "") or "",
            options=row.get("options") or {},
            is_active=bool(row.get("is_active", True)),
        )


def select_sources(
    rows: list[dict[str, Any]],
    source_system: str | None = None,
    source_ids: list[str] | None = None,
    use_case: str | None = None,
) -> list[SourceSpec]:
    """Filter raw control rows down to the active sources this run should process.

    Pure function so the filtering rules are unit-tested without a cluster - this
    is the logic that decides what prod actually loads, so it earns real tests.
    """
    specs = [SourceSpec.from_row(r) for r in rows]
    specs = [s for s in specs if s.is_active]
    if use_case:
        # Landing runs one job per use case, so a run only ever processes its
        # own sources. Filtering here rather than in the job keeps that rule in
        # one testable place.
        specs = [s for s in specs if s.use_case == use_case]
    if source_system:
        specs = [s for s in specs if s.source_system == source_system]
    if source_ids:
        wanted = set(source_ids)
        specs = [s for s in specs if s.source_id in wanted]
        unknown = wanted - {s.source_id for s in specs}
        if unknown:
            raise RegistryError(f"Requested source_id(s) not active/known: {sorted(unknown)}")
    return sorted(specs, key=lambda s: s.source_id)


def read_active_sources(
    spark, ctx: RuntimeContext, source_system: str | None = None, use_case: str | None = None
) -> list[SourceSpec]:
    """Read ops.config.landing_source and return validated specs."""
    tbl = ctx.config_table("landing_source")
    df = spark.table(tbl)
    rows = [r.asDict(recursive=True) for r in df.collect()]
    if not rows:
        raise RegistryError(
            f"{tbl} is empty. Run the landing_seed_source_registry job for this "
            "environment first (see docs/07-release-process.md)."
        )
    return select_sources(rows, source_system=source_system, use_case=use_case)


# -- watermarks --------------------------------------------------------------


def read_watermark(spark, ctx: RuntimeContext, source_id: str) -> str | None:
    """Last successfully-loaded watermark value, or None on first ever load."""
    tbl = ctx.config_table("landing_watermark")
    df = spark.sql(
        f"SELECT watermark_value FROM {tbl} WHERE source_id = :sid",  # noqa: S608 - tbl is validated
        args={"sid": source_id},
    )
    rows = df.collect()
    return rows[0][0] if rows else None


def advance_watermark(spark, ctx: RuntimeContext, source_id: str, new_value: str) -> None:
    """Move the watermark forward. Only ever called after a successful commit.

    MERGE rather than INSERT so a rerun of a failed task is idempotent, and
    `WHEN MATCHED AND new > old` so an out-of-order retry can never move the
    watermark backwards and silently skip data.
    """
    tbl = ctx.config_table("landing_watermark")
    spark.sql(
        f"""
        MERGE INTO {tbl} AS t
        USING (SELECT :sid AS source_id, :val AS watermark_value) AS s
          ON t.source_id = s.source_id
        WHEN MATCHED AND s.watermark_value > t.watermark_value
          THEN UPDATE SET watermark_value = s.watermark_value, updated_at = current_timestamp()
        WHEN NOT MATCHED
          THEN INSERT (source_id, watermark_value, updated_at)
               VALUES (s.source_id, s.watermark_value, current_timestamp())
        """,  # noqa: S608 - tbl is built from validated identifiers
        args={"sid": source_id, "val": new_value},
    )
