"""Reconciliation decides whether a use case is fit to cut over. Assert it."""

import pytest
from dab_common.config import build_context
from edp_recon.model import ReconCheck, ReconError, ReconTarget, load_recon_config

CONFIG = {
    "use_case": "us1",
    "targets": [
        {
            "name": "customer_curated",
            "layer": "curated",
            "target_table": "customer",
            "source_ref": "edp_ops_nonprod.recon.legacy_us1_customer",
            "key_columns": ["customer_id"],
            "owner_email": "us1@example.com",
            "checks": [
                {"name": "row_count", "check_type": "row_count"},
                {"name": "balance_sum", "check_type": "column_sum", "column": "balance"},
            ],
        }
    ],
}


# -- config validation -------------------------------------------------------

def test_valid_config_loads():
    plan = load_recon_config(CONFIG)
    assert plan.use_case == "us1"
    assert plan.check_count == 2
    assert "1 table(s), 2 check(s)" in plan.summary()


def test_config_without_a_use_case_is_rejected():
    with pytest.raises(ReconError, match="must declare a use_case"):
        load_recon_config({"targets": []})


def test_config_with_no_targets_is_rejected():
    with pytest.raises(ReconError, match="non-empty"):
        load_recon_config({"use_case": "us1", "targets": []})


def test_target_with_no_checks_is_rejected():
    """A reconciliation that checks nothing would report success forever."""
    cfg = {"use_case": "us1", "targets": [{**CONFIG["targets"][0], "checks": []}]}
    with pytest.raises(ReconError, match="at least one check"):
        load_recon_config(cfg)


def test_target_without_a_legacy_source_is_rejected():
    cfg = {"use_case": "us1", "targets": [{**CONFIG["targets"][0], "source_ref": ""}]}
    with pytest.raises(ReconError, match="source_ref"):
        load_recon_config(cfg)


def test_duplicate_target_names_are_rejected():
    cfg = {"use_case": "us1", "targets": [CONFIG["targets"][0], CONFIG["targets"][0]]}
    with pytest.raises(ReconError, match="duplicate target"):
        load_recon_config(cfg)


def test_duplicate_check_names_within_a_target_are_rejected():
    """Duplicates would silently overwrite each other in the result table."""
    dup = {**CONFIG["targets"][0], "checks": [
        {"name": "row_count", "check_type": "row_count"},
        {"name": "row_count", "check_type": "distinct_count", "column": "customer_id"},
    ]}
    with pytest.raises(ReconError, match="duplicate check"):
        load_recon_config({"use_case": "us1", "targets": [dup]})


# -- check semantics ---------------------------------------------------------

def test_column_check_without_a_column_is_rejected():
    with pytest.raises(ReconError, match="requires a column"):
        ReconCheck(name="c", check_type="column_sum")


def test_unknown_check_type_is_rejected():
    with pytest.raises(ReconError, match="unknown check_type"):
        ReconCheck(name="c", check_type="vibes")


def test_tolerance_must_be_justified():
    """An unexplained tolerance is how a real defect gets signed off."""
    with pytest.raises(ReconError, match="needs a justification"):
        ReconCheck(name="c", check_type="row_count", tolerance=0.01)


def test_justified_tolerance_is_accepted():
    c = ReconCheck(name="c", check_type="row_count", tolerance=0.01,
                   justification="Cloudera run lands 30 min earlier; late arrivals differ.")
    assert c.tolerance == 0.01


def test_tolerance_of_one_or_more_is_rejected():
    with pytest.raises(ReconError, match="tolerance must be"):
        ReconCheck(name="c", check_type="row_count", tolerance=1.0, justification="x")


def test_injection_in_a_column_name_is_refused():
    with pytest.raises(Exception, match="Invalid column"):
        ReconCheck(name="c", check_type="column_sum", column="x; DROP TABLE y")


# -- comparison arithmetic ---------------------------------------------------

def test_exact_match_passes():
    assert ReconCheck(name="c", check_type="row_count").passed(1000, 1000)


def test_any_difference_fails_at_zero_tolerance():
    assert not ReconCheck(name="c", check_type="row_count").passed(1000, 999)


def test_difference_within_tolerance_passes():
    c = ReconCheck(name="c", check_type="row_count", tolerance=0.01, justification="j")
    assert c.passed(1000, 995)


def test_difference_outside_tolerance_fails():
    c = ReconCheck(name="c", check_type="row_count", tolerance=0.01, justification="j")
    assert not c.passed(1000, 980)


def test_tolerance_is_symmetric():
    c = ReconCheck(name="c", check_type="row_count", tolerance=0.01, justification="j")
    assert c.passed(1000, 1005) and c.passed(1000, 995)


def test_zero_legacy_with_nonzero_target_fails():
    """There is no meaningful relative difference from zero - and 'legacy had
    nothing, we produced 40,000 rows' is exactly what must not pass silently."""
    c = ReconCheck(name="c", check_type="row_count", tolerance=0.5, justification="j")
    assert not c.passed(0, 40_000)


def test_both_zero_passes():
    assert ReconCheck(name="c", check_type="row_count").passed(0, 0)


# -- generated SQL -----------------------------------------------------------

def test_row_count_sql():
    sql = ReconCheck(name="c", check_type="row_count").measure_sql("cat.sch.tbl")
    assert sql == "SELECT count(*) AS metric FROM cat.sch.tbl"


def test_column_sum_casts_before_summing():
    """Decimal vs double drift between platforms is a classic false failure."""
    sql = ReconCheck(name="c", check_type="column_sum", column="amount").measure_sql("t")
    assert "try_cast(amount AS DECIMAL(38,6))" in sql


def test_column_hash_is_order_independent():
    """The two platforms need not agree on row order, so the hash must aggregate
    commutatively - sum of crc32, not a running digest."""
    sql = ReconCheck(name="c", check_type="column_hash", column="id").measure_sql("t")
    assert "sum(crc32(" in sql


def test_null_column_values_are_hashed_not_dropped():
    sql = ReconCheck(name="c", check_type="column_hash", column="id").measure_sql("t")
    assert "'<null>'" in sql


def test_filter_expression_is_applied():
    c = ReconCheck(name="c", check_type="row_count", filter_expr="load_date = '2026-08-01'")
    assert "WHERE load_date = '2026-08-01'" in c.measure_sql("t")


def test_sums_coalesce_so_an_empty_table_measures_zero_not_null():
    """NULL != NULL would make two empty tables compare as a failure."""
    assert "coalesce(" in ReconCheck(name="c", check_type="column_sum", column="a").measure_sql("t")


# -- target resolution -------------------------------------------------------

def test_target_resolves_through_the_normal_catalog_rules():
    target = load_recon_config(CONFIG).targets[0]
    ctx = build_context({"env": "preprod", "use_case": "us1"})
    assert target.resolve_target(ctx) == "edp_curated_preprod.us1.customer"


def test_target_resolution_is_sandbox_aware():
    target = load_recon_config(CONFIG).targets[0]
    ctx = build_context({"env": "nonprod", "use_case": "us1", "schema_prefix": "jsmith_"})
    assert target.resolve_target(ctx) == "edp_curated_nonprod.jsmith_us1.customer"


def test_layer_defaults_to_curated():
    cfg = {"use_case": "us1", "targets": [{k: v for k, v in CONFIG["targets"][0].items()
                                           if k != "layer"}]}
    assert load_recon_config(cfg).targets[0].layer == "curated"


def test_datamart_targets_resolve_to_the_datamart_catalog():
    cfg = {"use_case": "us1", "targets": [{**CONFIG["targets"][0], "layer": "datamart"}]}
    ctx = build_context({"env": "prod", "use_case": "us1"})
    assert load_recon_config(cfg).targets[0].resolve_target(ctx).startswith("edp_datamart_prod.")


def test_unknown_layer_fails_when_resolved():
    cfg = {"use_case": "us1", "targets": [{**CONFIG["targets"][0], "layer": "silver"}]}
    ctx = build_context({"env": "prod", "use_case": "us1"})
    with pytest.raises(Exception, match="Unknown layer"):
        load_recon_config(cfg).targets[0].resolve_target(ctx)


def test_recon_targets_can_reference_a_shared_target_type():
    assert isinstance(load_recon_config(CONFIG).targets[0], ReconTarget)
