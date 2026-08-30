"""The extract query decides how much data prod pulls from Oracle. Assert it."""

import pytest
from edp_landing.oracle import (
    JdbcConnection,
    OracleIngestionError,
    build_extract_query,
    build_read_options,
    validate_source_object,
)
from edp_landing.registry import SourceSpec

INCR = SourceSpec(
    source_id="ora_customers",
    source_system="oracle",
    source_object="SALES.CUSTOMERS",
    use_case="us1",
    target_table="ora_customers",
    load_strategy="incremental",
    watermark_column="LAST_UPDATE_DT",
    secret_scope="edp-oracle",
)
FULL = SourceSpec(
    source_id="ora_products",
    source_system="oracle",
    source_object="SALES.PRODUCTS",
    use_case="us1",
    target_table="ora_products",
    load_strategy="full",
    secret_scope="edp-oracle",
)
CONN = JdbcConnection(url="jdbc:oracle:thin:@//host:1521/svc", user="u", password="p")


def test_full_load_has_no_predicate():
    assert build_extract_query(FULL, None) == "SELECT * FROM SALES.PRODUCTS"


def test_first_incremental_run_reads_everything():
    """No stored watermark means the table has never loaded - take it all."""
    assert build_extract_query(INCR, None) == "SELECT * FROM SALES.CUSTOMERS"


def test_incremental_run_is_bounded_by_the_watermark():
    sql = build_extract_query(INCR, "2026-08-01T00:00:00.000")
    assert "WHERE LAST_UPDATE_DT > TO_TIMESTAMP('2026-08-01T00:00:00.000'" in sql


def test_watermark_is_strictly_greater_not_greater_or_equal():
    """`>=` would re-load the boundary row on every run and duplicate it."""
    assert ">=" not in build_extract_query(INCR, "2026-08-01T00:00:00.000")


def test_quotes_in_a_watermark_cannot_break_out_of_the_literal():
    sql = build_extract_query(INCR, "2026-08-01' OR 1=1--")
    assert "OR 1=1" in sql and "''" in sql  # escaped, still inside the literal


def test_cdc_stream_strategy_is_refused_over_jdbc():
    spec = SourceSpec(**{**FULL.__dict__, "load_strategy": "cdc_stream"})
    with pytest.raises(OracleIngestionError, match="not supported over JDBC"):
        build_extract_query(spec, "2026-01-01T00:00:00.000")


@pytest.mark.parametrize("bad", ["CUSTOMERS", "A.B.C", "SALES.CUST OMERS", "SALES.'x'"])
def test_malformed_source_objects_are_rejected(bad):
    with pytest.raises(OracleIngestionError):
        validate_source_object(bad)


def test_read_options_carry_credentials_and_query():
    opts = build_read_options(FULL, CONN, "SELECT * FROM SALES.PRODUCTS")
    assert opts["url"] == CONN.url
    assert opts["query"] == "SELECT * FROM SALES.PRODUCTS"
    assert opts["driver"] == "oracle.jdbc.OracleDriver"


def test_partitioned_read_is_opt_in():
    assert "numPartitions" not in build_read_options(FULL, CONN, "q")


def test_partitioned_read_emits_all_four_jdbc_options():
    spec = SourceSpec(
        **{
            **FULL.__dict__,
            "options": {
                "partition_column": "PRODUCT_ID",
                "lower_bound": 1,
                "upper_bound": 1_000_000,
                "num_partitions": 8,
            },
        }
    )
    opts = build_read_options(spec, CONN, "q")
    assert opts["partitionColumn"] == "PRODUCT_ID"
    assert opts["numPartitions"] == "8"


def test_partition_column_without_bounds_fails_at_config_time():
    spec = SourceSpec(**{**FULL.__dict__, "options": {"partition_column": "PRODUCT_ID"}})
    with pytest.raises(OracleIngestionError, match="missing"):
        build_read_options(spec, CONN, "q")
