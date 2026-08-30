import pytest
from dab_common.config import build_context
from edp_landing.kafka import (
    KafkaIngestionError,
    build_stream_options,
    checkpoint_path,
)
from edp_landing.registry import SourceSpec

SPEC = SourceSpec(
    source_id="kfk_orders",
    source_system="kafka",
    source_object="orders.v1",
    use_case="us1",
    target_table="kfk_orders",
    load_strategy="cdc_stream",
    secret_scope="edp-kafka",
)


def test_topic_becomes_the_subscribe_option():
    assert build_stream_options(SPEC, "broker:9093")["subscribe"] == "orders.v1"


def test_defaults_backfill_and_survive_retention_loss():
    opts = build_stream_options(SPEC, "broker:9093")
    assert opts["startingOffsets"] == "earliest"
    assert opts["failOnDataLoss"] == "false"


def test_control_row_can_override_defaults():
    spec = SourceSpec(**{**SPEC.__dict__, "options": {"starting_offsets": "latest"}})
    assert build_stream_options(spec, "broker:9093")["startingOffsets"] == "latest"


def test_sasl_is_configured_only_when_a_secret_is_supplied():
    assert "kafka.sasl.jaas.config" not in build_stream_options(SPEC, "broker:9093")
    with_auth = build_stream_options(SPEC, "broker:9093", sasl_secret="Endpoint=sb://...")
    assert with_auth["kafka.security.protocol"] == "SASL_SSL"
    assert "Endpoint=sb://..." in with_auth["kafka.sasl.jaas.config"]


def test_empty_bootstrap_fails_fast():
    with pytest.raises(KafkaIngestionError):
        build_stream_options(SPEC, "")


def test_checkpoints_are_isolated_per_developer():
    """Sharing a checkpoint across sandboxes would corrupt stream state."""
    a = checkpoint_path(build_context({"env": "nonprod",
                                       "schema_prefix": "jsmith_", "use_case": "us1"}), SPEC)
    b = checkpoint_path(build_context({"env": "nonprod",
                                       "schema_prefix": "apatel_", "use_case": "us1"}), SPEC)
    assert a != b
    assert a.endswith("/_checkpoints/kafka/kfk_orders")


def test_checkpoints_are_isolated_per_source():
    ctx = build_context({"env": "prod", "use_case": "us1"})
    other = SourceSpec(**{**SPEC.__dict__, "source_id": "kfk_payments"})
    assert checkpoint_path(ctx, SPEC) != checkpoint_path(ctx, other)
