"""us4 curated-layer transformations: landing -> curated.

>>> PLACEHOLDER <<<
The real logic is being ported from Cloudera. What matters here is the SHAPE:

  * every function takes a DataFrame and returns a DataFrame
  * no function reads a table, writes a table, or knows its environment
  * schemas are DECLARED, not inferred, so an upstream change fails loudly with
    a column mismatch instead of silently producing nulls

That is what makes these functions unit-testable, and it is the standard a
ported script must reach before it leaves src/ported/.
"""

from __future__ import annotations

# Expected shape of the inventory payload landed by the shared landing bundle.
# Bumping this is a reviewable, testable change rather than a schema-inference
# surprise at 2am.
INVENTORY_PAYLOAD_SCHEMA = (
    "inventory_id STRING, event_ts STRING, status STRING, amount DOUBLE, currency STRING"
)

# Statuses the downstream marts understand. Anything else is nulled and flagged
# by the quality gate, never dropped - the row still carries value.
VALID_STATUSES = ("NEW", "ACTIVE", "SETTLED", "CANCELLED")


def dedupe_by_key(df, keys: list[str], order_by: str, ascending: bool = False):
    """Keep one row per key - the latest by `order_by`.

    Landing is append-only, so an incremental reload that re-reads a boundary
    row, or a Kafka replay, legitimately produces duplicates. Curated is where
    they go away. row_number rather than dropDuplicates because WHICH duplicate
    wins must be deterministic, not arbitrary.
    """
    # Validate before importing Spark, so a bad argument fails identically
    # whether or not a session exists - and so the check is testable.
    if not keys:
        raise ValueError("dedupe_by_key requires at least one key column")

    from pyspark.sql import Window
    from pyspark.sql import functions as F  # noqa: N812

    ordering = F.col(order_by).asc() if ascending else F.col(order_by).desc()
    window = Window.partitionBy(*keys).orderBy(ordering)
    return df.withColumn("_rn", F.row_number().over(window)).filter(F.col("_rn") == 1).drop("_rn")


def conform_inventory(df):
    """Standardise landed inventory records into the curated contract.

    >>> PLACEHOLDER - replace with the ported Cloudera transformation. <<<

    An unrecognised status becomes NULL rather than causing the row to be
    dropped: the row still carries value, and the quality gate flags the null so
    somebody investigates the new status instead of the data quietly vanishing.
    """
    from pyspark.sql import functions as F  # noqa: N812

    return df.select(
        F.col("inventory_id").cast("string").alias("inventory_id"),
        F.to_timestamp(F.col("event_ts")).alias("event_ts"),
        F.when(
            F.upper(F.trim(F.col("status"))).isin(list(VALID_STATUSES)),
            F.upper(F.trim(F.col("status"))),
        ).alias("status"),
        F.col("amount").cast("decimal(18,2)").alias("amount"),
        F.coalesce(F.upper(F.trim(F.col("currency"))), F.lit("USD")).alias("currency"),
        F.col("_ingested_at").alias("ingested_at"),
    )
