"""Kafka landing framework (Structured Streaming -> landing).

>>> REFERENCE SKELETON <<<
Illustrative of the shape, not a framework you must adopt. Your Cloudera code
lands in bundles/landing/src/ported/ and is refactored into here over time.
What is load-bearing is the contract: a SourceSpec in, a landed table out, an
audit row written. See edp_landing/__init__.py.


Used two ways in this repo, both driven by the same control rows:

  * inside a Lakeflow Declarative Pipeline (bundles/landing/src/pipelines/
    kafka_landing.py) - the pipeline owns checkpointing and restarts;
  * standalone from a job, using `stream_to_landing` below, when a topic needs to
    land outside a declarative pipeline.

Checkpoints live on a Unity Catalog volume under the *target* schema, so a
developer sandbox gets its own checkpoint directory automatically and can never
resume from - or corrupt - a shared environment's stream state.
"""

from __future__ import annotations

from dab_common.config import RuntimeContext

from edp_landing import registry

# Auth to Azure Event Hubs / Kafka via SASL. The JAAS config is assembled from a
# secret so the password never appears in a plan, a log, or the Spark UI.
_JAAS = (
    'kafkashaded.org.apache.kafka.common.security.plain.PlainLoginModule required '
    'username="$ConnectionString" password="{secret}";'
)


class KafkaIngestionError(RuntimeError):
    pass


def checkpoint_path(ctx: RuntimeContext, spec: registry.SourceSpec) -> str:
    """Per-source, per-environment checkpoint location on a UC volume.

    The `_checkpoints` volume is declared in the _platform bundle, one per
    use-case schema in the landing catalog. Including source_id in the path
    means adding a topic never disturbs an existing stream.
    """
    return ctx.volume_path("landing", "_checkpoints", "kafka", spec.source_id)


def build_stream_options(
    spec: registry.SourceSpec,
    bootstrap_servers: str,
    sasl_secret: str | None = None,
) -> dict[str, str]:
    """Build the readStream option map for one topic. Pure, so it is unit-tested.

    Defaults chosen deliberately:
      startingOffsets=earliest  - a brand new landing table should backfill the
                                  retention window rather than silently start empty;
                                  override per source via control-row options.
      failOnDataLoss=false      - a topic whose retention expired mid-outage should
                                  page a human via audit, not crash-loop the stream.
    """
    if not bootstrap_servers:
        raise KafkaIngestionError(f"{spec.source_id}: bootstrap_servers is empty")

    extra = spec.options or {}
    opts = {
        "kafka.bootstrap.servers": bootstrap_servers,
        "subscribe": spec.source_object,
        "startingOffsets": str(extra.get("starting_offsets", "earliest")),
        "failOnDataLoss": str(extra.get("fail_on_data_loss", False)).lower(),
        "maxOffsetsPerTrigger": str(extra.get("max_offsets_per_trigger", 500_000)),
    }
    if sasl_secret:
        opts.update(
            {
                "kafka.security.protocol": "SASL_SSL",
                "kafka.sasl.mechanism": "PLAIN",
                "kafka.sasl.jaas.config": _JAAS.format(secret=sasl_secret),
            }
        )
    return opts


def resolve_bootstrap(dbutils, spec: registry.SourceSpec) -> tuple[str, str | None]:
    """Read broker list and SASL secret from the source's secret scope."""
    scope = spec.secret_scope
    if not scope:
        raise KafkaIngestionError(f"{spec.source_id}: no secret_scope on the control row")
    servers = dbutils.secrets.get(scope, "bootstrap-servers")
    try:
        secret = dbutils.secrets.get(scope, "sasl-connection-string")
    except Exception:  # noqa: BLE001 - unauthenticated dev brokers have no SASL key
        secret = None
    return servers, secret


def decode_envelope(df):
    """Standard landing envelope: keep the raw payload, add Kafka metadata.

    Landing stays lossless - the value is retained as a string and parsed in
    curation. That way a schema change upstream is a curation fix, not a lost
    day of data.
    """
    from pyspark.sql import functions as F  # noqa: N812 - runtime-only import

    return df.select(
        F.col("key").cast("string").alias("kafka_key"),
        F.col("value").cast("string").alias("payload"),
        F.col("topic").alias("kafka_topic"),
        F.col("partition").alias("kafka_partition"),
        F.col("offset").alias("kafka_offset"),
        F.col("timestamp").alias("kafka_timestamp"),
        F.current_timestamp().alias("_ingested_at"),
    )


def stream_to_landing(spark, dbutils, ctx: RuntimeContext, spec: registry.SourceSpec):
    """Start (or resume) a streaming load for one topic. Returns the StreamingQuery."""
    servers, secret = resolve_bootstrap(dbutils, spec)
    target = ctx.table("landing", spec.target_table, use_case=spec.use_case)

    stream = (
        spark.readStream.format("kafka")
        .options(**build_stream_options(spec, servers, secret))
        .load()
    )
    return (
        decode_envelope(stream)
        .writeStream.format("delta")
        .outputMode("append")
        .option("checkpointLocation", checkpoint_path(ctx, spec))
        .queryName(f"kafka_landing_{spec.source_id}")
        # availableNow gives a job-triggered micro-batch drain: the job finishes
        # instead of running forever, which is what a scheduled bundle job wants.
        .trigger(availableNow=True)
        .toTable(target)
    )
