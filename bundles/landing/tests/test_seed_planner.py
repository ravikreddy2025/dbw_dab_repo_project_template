"""The seed planner decides what changes in the shared source registry."""

import pytest
from landing_module.seed import SeedError, load_seed_file, plan_seed_merge

DOC = {
    "use_case": "us2",
    "sources": [
        {
            "source_id": "us2_ora_customers",
            "source_system": "oracle",
            "source_object": "SALES.CUSTOMERS",
            "target_table": "ora_customers",
            "load_strategy": "incremental",
            "watermark_column": "LAST_UPDATE_DT",
            "primary_keys": ["CUSTOMER_ID"],
            "secret_scope": "edp-oracle",
            "owner_email": "team@example.com",
        }
    ],
}


# -- file-level validation ---------------------------------------------------

def test_use_case_is_stamped_onto_every_row():
    assert load_seed_file(DOC)[0]["use_case"] == "us2"


def test_file_without_a_use_case_is_rejected():
    doc = {k: v for k, v in DOC.items() if k != "use_case"}
    with pytest.raises(SeedError, match="must declare a top-level"):
        load_seed_file(doc)


def test_declared_use_case_must_match_the_folder():
    """Otherwise a source registers against the wrong use case and its data
    lands in another team's schema."""
    with pytest.raises(SeedError, match="must agree"):
        load_seed_file(DOC, expected_use_case="us4")


def test_matching_folder_is_accepted():
    assert load_seed_file(DOC, expected_use_case="us2")


def test_primary_keys_list_becomes_a_csv_string():
    assert load_seed_file(DOC)[0]["primary_keys"] == "CUSTOMER_ID"


def test_unknown_key_is_rejected_rather_than_ignored():
    """A typo'd column silently dropped is a config bug that surfaces as missing
    data weeks later."""
    doc = {**DOC, "sources": [{**DOC["sources"][0], "watermarkColumn": "X"}]}
    with pytest.raises(SeedError, match="unknown key"):
        load_seed_file(doc)


def test_duplicate_source_id_in_one_file_is_rejected():
    doc = {**DOC, "sources": [DOC["sources"][0], DOC["sources"][0]]}
    with pytest.raises(SeedError, match="duplicate source_id"):
        load_seed_file(doc)


def test_empty_sources_list_is_rejected():
    with pytest.raises(SeedError, match="non-empty"):
        load_seed_file({**DOC, "sources": []})


# -- merge planning ----------------------------------------------------------

def test_new_source_is_planned_as_an_insert():
    diff = plan_seed_merge(load_seed_file(DOC), existing=[])
    assert [r["source_id"] for r in diff.to_insert] == ["us2_ora_customers"]


def test_identical_state_plans_no_change():
    """Re-running the seed job must be a no-op, or every deploy looks like a
    change and nobody reads the plan any more."""
    desired = load_seed_file(DOC)
    diff = plan_seed_merge(desired, existing=list(desired))
    assert diff.is_empty
    assert diff.unchanged == ["us2_ora_customers"]


def test_absent_map_and_empty_map_compare_equal():
    """Delta returns None for an unset MAP, YAML gives {} - without
    normalisation every run reports a spurious UPDATE."""
    desired = load_seed_file(DOC)
    assert plan_seed_merge(desired, [{**desired[0], "options": None}]).is_empty


def test_changed_watermark_is_planned_as_an_update():
    desired = load_seed_file(DOC)
    diff = plan_seed_merge(desired, [{**desired[0], "watermark_column": "OLD_COL"}])
    assert [r["source_id"] for r in diff.to_update] == ["us2_ora_customers"]


def test_removed_source_is_deactivated_never_deleted():
    """Deleting the row would orphan its watermark and audit history."""
    existing = [*load_seed_file(DOC), {"source_id": "us2_ora_gone", "is_active": True}]
    assert plan_seed_merge(load_seed_file(DOC), existing).to_deactivate == ["us2_ora_gone"]


def test_already_inactive_source_is_not_deactivated_again():
    existing = [*load_seed_file(DOC), {"source_id": "us2_ora_gone", "is_active": False}]
    assert plan_seed_merge(load_seed_file(DOC), existing).to_deactivate == []


# -- partition options -------------------------------------------------------
# Regression tests: a partition_column with no bounds passed PR validation and
# failed only at runtime, in whichever environment ran first.

PARTITIONED = {
    **DOC["sources"][0],
    "load_strategy": "full",
    "watermark_column": None,
    "options": {
        "partition_column": "CUSTOMER_ID",
        "lower_bound": 1,
        "upper_bound": 5_000_000,
        "num_partitions": 8,
    },
}


def test_complete_partition_options_are_accepted():
    doc = {**DOC, "sources": [PARTITIONED]}
    assert load_seed_file(doc)[0]["options"]["num_partitions"] == "8"


def test_partition_column_without_bounds_is_rejected_at_pr_time():
    """The one that would otherwise fail at 02:00 instead of in CI."""
    doc = {**DOC, "sources": [{**PARTITIONED, "options": {"partition_column": "CUSTOMER_ID"}}]}
    with pytest.raises(SeedError, match="all four settings or none"):
        load_seed_file(doc)


def test_partial_partition_options_are_rejected():
    doc = {**DOC, "sources": [
        {**PARTITIONED, "options": {"partition_column": "CUSTOMER_ID", "lower_bound": 1}}
    ]}
    with pytest.raises(SeedError, match="upper_bound"):
        load_seed_file(doc)


def test_inverted_bounds_are_rejected():
    """upper <= lower yields a zero or negative stride, which Spark reports
    obscurely at runtime."""
    doc = {**DOC, "sources": [
        {**PARTITIONED, "options": {**PARTITIONED["options"], "lower_bound": 100, "upper_bound": 10}}
    ]}
    with pytest.raises(SeedError, match="greater than"):
        load_seed_file(doc)


def test_non_integer_bounds_are_rejected():
    doc = {**DOC, "sources": [
        {**PARTITIONED, "options": {**PARTITIONED["options"], "upper_bound": "five million"}}
    ]}
    with pytest.raises(SeedError, match="must be integers"):
        load_seed_file(doc)


def test_zero_partitions_is_rejected():
    doc = {**DOC, "sources": [
        {**PARTITIONED, "options": {**PARTITIONED["options"], "num_partitions": 0}}
    ]}
    with pytest.raises(SeedError, match="at least 1"):
        load_seed_file(doc)


def test_unpartitioned_sources_are_unaffected():
    assert load_seed_file(DOC)[0]["options"] is None
