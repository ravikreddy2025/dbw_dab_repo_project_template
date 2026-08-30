"""Data quality expectations shared by curation and datamart.

Deliberately small. It builds SQL predicates and evaluates them against a table,
writing results to ops.audit.data_quality_result. The point is a uniform record of
"what did we check and what did it find" across every module, not a DQ engine.

For streaming landing tables, use Lakeflow Declarative Pipelines' own @dlt.expect_*
decorators instead - they run in-pipeline. This module covers batch tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from dab_common.config import RuntimeContext, validate_identifier

Severity = Literal["warn", "error"]


@dataclass(frozen=True)
class Expectation:
    """One named check: a name, a boolean SQL predicate, and what to do on failure.

    `predicate` is written as the condition rows MUST satisfy, so it reads the
    way you would say it out loud: "customer_id IS NOT NULL".
    """

    name: str
    predicate: str
    severity: Severity = "error"

    def failure_count_sql(self, table: str) -> str:
        """SQL returning the number of rows that VIOLATE this expectation."""
        # NOT (p) misses NULL results, which are violations too - hence the OR.
        return (
            f"SELECT count(*) AS failures FROM {table} "  # noqa: S608 - table validated by caller
            f"WHERE NOT ({self.predicate}) OR ({self.predicate}) IS NULL"
        )


def not_null(column: str) -> Expectation:
    validate_identifier(column, "column")
    return Expectation(name=f"{column}_not_null", predicate=f"{column} IS NOT NULL")


def unique(column: str) -> Expectation:
    """Uniqueness cannot be expressed as a row predicate; handled specially."""
    validate_identifier(column, "column")
    return Expectation(name=f"{column}_unique", predicate=f"__unique__({column})")


def in_set(column: str, allowed: list[str], severity: Severity = "error") -> Expectation:
    validate_identifier(column, "column")
    rendered = ", ".join("'" + a.replace("'", "''") + "'" for a in allowed)
    return Expectation(
        name=f"{column}_in_set", predicate=f"{column} IN ({rendered})", severity=severity
    )


def non_negative(column: str, severity: Severity = "error") -> Expectation:
    validate_identifier(column, "column")
    return Expectation(name=f"{column}_non_negative", predicate=f"{column} >= 0", severity=severity)


class DataQualityFailure(AssertionError):
    """Raised when an `error`-severity expectation is violated."""


def evaluate(
    spark,
    ctx: RuntimeContext,
    table: str,
    expectations: list[Expectation],
    raise_on_error: bool = True,
) -> list[dict]:
    """Run every expectation against `table`, persist results, optionally raise.

    Returns one result dict per expectation so callers can log or branch on them.
    """
    total = spark.table(table).count()
    results: list[dict] = []
    breached: list[str] = []

    for exp in expectations:
        if exp.predicate.startswith("__unique__("):
            column = exp.predicate[len("__unique__(") : -1]
            sql = (
                f"SELECT count(*) AS failures FROM ("  # noqa: S608 - identifiers validated
                f"SELECT {column} FROM {table} GROUP BY {column} HAVING count(*) > 1)"
            )
        else:
            sql = exp.failure_count_sql(table)

        failures = int(spark.sql(sql).collect()[0][0])
        passed = failures == 0
        results.append(
            {
                "run_id": ctx.run_id,
                "env": ctx.env,
                "use_case": ctx.use_case,
                "table_name": table,
                "expectation_name": exp.name,
                "expectation_predicate": exp.predicate,
                "severity": exp.severity,
                "rows_evaluated": total,
                "rows_failed": failures,
                "passed": passed,
                "evaluated_at": datetime.now(UTC),
            }
        )
        if not passed and exp.severity == "error":
            breached.append(f"{exp.name} ({failures}/{total} rows)")

    if results:
        spark.createDataFrame(results).write.mode("append").saveAsTable(
            ctx.audit_table("data_quality_result")
        )

    if breached and raise_on_error:
        raise DataQualityFailure(
            f"{table}: {len(breached)} error-severity expectation(s) failed: {'; '.join(breached)}"
        )
    return results
