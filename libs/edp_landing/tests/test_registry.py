"""Registry filtering decides what prod lands. Tested as pure logic."""

import pytest
from edp_landing.registry import RegistryError, SourceSpec, select_sources

ROWS = [
    {
        "source_id": "ora_customers",
        "source_system": "oracle",
        "source_object": "SALES.CUSTOMERS",
        "use_case": "us1",
        "target_table": "ora_customers",
        "load_strategy": "incremental",
        "watermark_column": "LAST_UPDATE_DT",
        "primary_keys": "CUSTOMER_ID",
        "secret_scope": "edp-oracle",
        "is_active": True,
    },
    {
        "source_id": "ora_products",
        "source_system": "oracle",
        "source_object": "SALES.PRODUCTS",
        "use_case": "us1",
        "target_table": "ora_products",
        "load_strategy": "full",
        "secret_scope": "edp-oracle",
        "is_active": True,
    },
    {
        "source_id": "kfk_orders",
        "source_system": "kafka",
        "source_object": "orders.v1",
        "use_case": "us1",
        "target_table": "kfk_orders",
        "load_strategy": "cdc_stream",
        "secret_scope": "edp-kafka",
        "is_active": True,
    },
    {
        "source_id": "ora_retired",
        "source_system": "oracle",
        "source_object": "SALES.OLD",
        "use_case": "us1",
        "target_table": "ora_retired",
        "load_strategy": "full",
        "is_active": False,
    },
]


def test_inactive_sources_are_never_returned():
    ids = [s.source_id for s in select_sources(ROWS)]
    assert "ora_retired" not in ids


def test_filter_by_source_system():
    assert [s.source_id for s in select_sources(ROWS, source_system="kafka")] == ["kfk_orders"]


def test_filter_by_explicit_ids():
    got = select_sources(ROWS, source_ids=["ora_products"])
    assert [s.source_id for s in got] == ["ora_products"]


def test_requesting_an_inactive_source_fails_loudly():
    """A silent empty result here would look like a successful no-op load."""
    with pytest.raises(RegistryError, match="ora_retired"):
        select_sources(ROWS, source_ids=["ora_retired"])


def test_result_is_deterministically_ordered():
    assert [s.source_id for s in select_sources(ROWS)] == sorted(
        s.source_id for s in select_sources(ROWS)
    )


def test_primary_keys_are_split_from_csv():
    row = {**ROWS[0], "primary_keys": "A, B ,C"}
    assert SourceSpec.from_row(row).primary_keys == ("A", "B", "C")


def test_incremental_without_watermark_column_is_a_config_error():
    row = {**ROWS[0], "watermark_column": None}
    with pytest.raises(RegistryError, match="requires a watermark_column"):
        SourceSpec.from_row(row)


def test_unknown_load_strategy_is_rejected():
    with pytest.raises(RegistryError, match="unknown load_strategy"):
        SourceSpec.from_row({**ROWS[1], "load_strategy": "magic"})


def test_unknown_source_system_is_rejected():
    with pytest.raises(RegistryError, match="unknown source_system"):
        SourceSpec.from_row({**ROWS[1], "source_system": "db2"})
