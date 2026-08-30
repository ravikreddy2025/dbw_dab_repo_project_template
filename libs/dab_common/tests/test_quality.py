from dab_common.quality import Expectation, in_set, non_negative, not_null, unique


def test_not_null_counts_violations_not_conformers():
    sql = not_null("customer_id").failure_count_sql("edp_curated_nonprod.us1.customers")
    assert "NOT (customer_id IS NOT NULL)" in sql


def test_null_predicate_results_count_as_failures():
    """`NOT (x = 1)` is NULL when x is NULL - without the OR those rows vanish."""
    assert "IS NULL" in Expectation("e", "amount = 1").failure_count_sql("t")


def test_in_set_escapes_embedded_quotes():
    assert "O''Brien" in in_set("name", ["O'Brien"]).predicate


def test_unique_is_flagged_for_special_handling():
    assert unique("customer_id").predicate == "__unique__(customer_id)"


def test_severity_defaults_to_error_and_can_be_relaxed():
    assert not_null("a").severity == "error"
    assert non_negative("amount", severity="warn").severity == "warn"
