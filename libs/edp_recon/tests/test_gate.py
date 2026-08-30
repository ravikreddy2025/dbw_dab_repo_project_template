"""The ETL gate decides whether a comparison is meaningful at all."""

import pytest
from dab_common.config import build_context
from edp_recon.gate import (
    STATUS_FAILED,
    STATUS_PASSED,
    STATUS_SKIPPED,
    EtlGateResult,
    build_gate_query,
    etl_completed_for,
)

CTX = build_context({"env": "prod", "use_case": "us1"})


class FakeSpark:
    """Minimal stand-in: records the SQL and returns one canned row."""

    def __init__(self, last_success=None):
        self._last = last_success
        self.sql_seen = None

    def sql(self, statement, **_):
        self.sql_seen = statement
        return self

    def collect(self):
        return [{"last_success": self._last}]


# -- query construction ------------------------------------------------------

def test_query_reads_the_shared_audit_table():
    sql = build_gate_query(CTX, ["publish_marts"], 24)
    assert "edp_ops_prod.audit.job_run" in sql


def test_query_is_scoped_to_this_use_case():
    """ops.audit.job_run is shared across all five use cases. Without this filter
    a successful us3 run would satisfy the gate for us1."""
    assert "use_case = 'us1'" in build_gate_query(CTX, ["publish_marts"], 24)


def test_query_is_scoped_to_this_environment():
    """A successful nonprod run must not satisfy a prod gate."""
    assert "env      = 'prod'" in build_gate_query(CTX, ["publish_marts"], 24)


def test_query_only_counts_successful_runs():
    assert "status   = 'SUCCESS'" in build_gate_query(CTX, ["publish_marts"], 24)


def test_query_applies_the_time_window():
    assert "INTERVAL 24 HOURS" in build_gate_query(CTX, ["publish_marts"], 24)


def test_window_is_coerced_to_an_integer():
    """The window arrives as a job parameter, i.e. as a string."""
    assert "INTERVAL 48 HOURS" in build_gate_query(CTX, ["publish_marts"], "48")


def test_multiple_task_names_are_all_accepted():
    sql = build_gate_query(CTX, ["publish_marts", "curated_quality_gate"], 24)
    assert "'publish_marts', 'curated_quality_gate'" in sql


def test_quotes_in_a_task_name_cannot_break_the_literal():
    sql = build_gate_query(CTX, ["odd'name"], 24)
    assert "'odd''name'" in sql


# -- gate decision -----------------------------------------------------------

def test_recent_success_proceeds():
    result = etl_completed_for(FakeSpark(last_success="2026-08-30 06:12:00"), CTX)
    assert result.proceed
    assert result.last_success_at == "2026-08-30 06:12:00"


def test_no_recent_success_skips():
    """Comparing yesterday's Databricks output against today's Cloudera extract
    produces a mismatch that looks exactly like a migration defect."""
    result = etl_completed_for(FakeSpark(last_success=None), CTX)
    assert not result.proceed
    assert "has not run" in result.reason


def test_skip_reason_names_the_use_case_and_environment():
    """The reason lands in parity_run.notes, where someone reads it days later."""
    reason = etl_completed_for(FakeSpark(None), CTX).reason
    assert "us1" in reason and "prod" in reason


def test_default_tasks_cover_both_layers():
    """Either the mart build or the curated gate succeeding is enough evidence
    that the ETL ran; requiring both would skip whenever a use case has no mart."""
    spark = FakeSpark(last_success="2026-08-30 06:00:00")
    etl_completed_for(spark, CTX)
    assert "publish_marts" in spark.sql_seen
    assert "curated_quality_gate" in spark.sql_seen


def test_explicit_task_names_override_the_default():
    spark = FakeSpark(last_success="2026-08-30 06:00:00")
    etl_completed_for(spark, CTX, job_names=["my_custom_task"])
    assert "my_custom_task" in spark.sql_seen
    assert "publish_marts" not in spark.sql_seen


def test_window_is_configurable_per_environment():
    spark = FakeSpark(last_success="2026-08-30 06:00:00")
    etl_completed_for(spark, CTX, within_hours=720)
    assert "INTERVAL 720 HOURS" in spark.sql_seen


# -- statuses ----------------------------------------------------------------

def test_skipped_is_distinct_from_failed():
    """'We could not check' is different information from 'we checked and it
    differs'. Collapsing them would let a broken ETL look like a broken migration."""
    assert STATUS_SKIPPED != STATUS_FAILED != STATUS_PASSED


@pytest.mark.parametrize("status", [STATUS_PASSED, STATUS_FAILED, STATUS_SKIPPED])
def test_statuses_match_the_ddl_comment(status):
    assert status in ("PASSED", "FAILED", "SKIPPED")


def test_gate_result_is_printable_for_the_run_log():
    assert str(EtlGateResult(proceed=False, reason="nothing ran")).startswith("SKIP: ")
    assert str(EtlGateResult(proceed=True, reason="ok")).startswith("PROCEED: ")
