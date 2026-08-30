# Databricks notebook source
# MAGIC %md
# MAGIC # Kafka -> landing (Lakeflow Declarative Pipeline)
# MAGIC
# MAGIC ONE FILE, FIVE PIPELINES. Each use case gets its own pipeline resource
# MAGIC (`kafka_landing_us1`, `kafka_landing_us3`, ...) pointing at this same
# MAGIC notebook. Which topics it declares is decided by the `edp.use_case`
# MAGIC configuration value, so adding a use case needs no new code here.
# MAGIC
# MAGIC Pipeline code cannot read job parameters, so context arrives as pipeline
# MAGIC `configuration` (set in `resources/kafka_landing_<uc>.pipeline.yml`) and is
# MAGIC read with `spark.conf.get`.

# COMMAND ----------

import dlt
from dab_common.config import build_context
from edp_landing.kafka import build_stream_options, decode_envelope
from edp_landing.registry import select_sources

ctx = build_context(
    {
        "env": spark.conf.get("edp.env"),
        "use_case": spark.conf.get("edp.use_case"),
        "catalog_prefix": spark.conf.get("edp.catalog_prefix", "edp"),
        "schema_prefix": spark.conf.get("edp.schema_prefix", ""),
        "bundle_target": spark.conf.get("edp.bundle_target", ""),
    }
)
secret_scope = spark.conf.get("edp.kafka_secret_scope")

print(f"declaring landing tables for {ctx.use_case} in {ctx.fq_schema('landing')}")

# COMMAND ----------

# The registry is read at pipeline GRAPH-BUILD time - this loop decides which
# streaming tables exist. A newly seeded topic therefore appears after the next
# pipeline update, not mid-run.
rows = [
    r.asDict(recursive=True)
    for r in spark.read.table(ctx.config_table("landing_source")).collect()
]
specs = select_sources(rows, source_system="kafka", use_case=ctx.use_case)

bootstrap = dbutils.secrets.get(secret_scope, "bootstrap-servers")
try:
    sasl = dbutils.secrets.get(secret_scope, "sasl-connection-string")
except Exception:  # noqa: BLE001 - an unauthenticated dev broker has no SASL key
    sasl = None

# COMMAND ----------


def make_landing_table(spec):
    """Declare one streaming table for one topic.

    Wrapped in a factory so each closure captures its own `spec` - declaring the
    tables directly in the loop would bind every table to the last one.
    """

    @dlt.table(
        name=spec.target_table,
        comment=f"Raw {spec.source_object} events for {spec.use_case}. Parsed in the curated layer.",
        table_properties={
            "quality": "landing",
            "edp.source_id": spec.source_id,
            "edp.use_case": spec.use_case,
            "delta.enableChangeDataFeed": "true",
        },
    )
    # Landing keeps everything. The only hard rule is that a row must be
    # traceable back to its offset, so a row without one is dropped and counted
    # rather than silently kept.
    @dlt.expect_or_drop("has_offset", "kafka_offset IS NOT NULL")
    @dlt.expect("payload_not_empty", "payload IS NOT NULL AND length(payload) > 0")
    def _table():
        stream = (
            spark.readStream.format("kafka")
            .options(**build_stream_options(spec, bootstrap, sasl))
            .load()
        )
        return decode_envelope(stream)

    return _table


for spec in specs:
    print(f"  {spec.source_id} <- {spec.source_object}")
    make_landing_table(spec)
