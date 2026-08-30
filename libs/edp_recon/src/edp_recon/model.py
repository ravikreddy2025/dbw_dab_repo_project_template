"""Reconciliation contracts: what parity means, and the SQL that measures it.

WHY THIS EXISTS
---------------
For a lift and shift, the question the client actually asks is not "did it
deploy?" but "does Databricks produce the same numbers Cloudera did?". Without a
structured answer, go-live is a judgement call and every discrepancy found later
is a crisis. With one, every use case carries a queryable, dated parity record.

WHAT THIS IS
------------
The *contract and the shape*, not a comparison engine. It defines what a
reconciliation is, validates the config, and builds the SQL that measures each
check. How you get the Cloudera side of the comparison into Databricks (an
exported table, a federated query, a landed extract) is a project decision -
`source_ref` is deliberately opaque so any of those work.

GENERIC BY DESIGN
-----------------
For a lift and shift, every use case is the same job: compare this target table
against that source table. Nothing here knows about us1 or us2. The only
per-use-case artefact is the CONFIG, in bundles/recon/conf/<use_case>.yml, so
onboarding a sixth use case adds a config file and a job entry - never code.

Results land in ops.recon (owned by the _platform bundle, written only by the
recon bundle run-as identity):

    ops.recon.parity_run            one row per reconciliation run
    ops.recon.parity_check_result   one row per check
    ops.recon.parity_exception      sample rows that did not match

CUTOVER
-------
A use case is cleared for cutover when its checks pass within tolerance across
an agreed number of consecutive runs. That is a fact in a table, not an opinion
in a meeting. See docs/13-migration-and-cutover.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from dab_common.config import RuntimeContext, validate_identifier

CheckType = Literal["row_count", "column_sum", "column_hash", "distinct_count", "min_max"]

CHECK_TYPES = ("row_count", "column_sum", "column_hash", "distinct_count", "min_max")

# A check that needs a column but is given none is a config error, not a
# runtime surprise. Declared here so validation stays data-driven.
CHECKS_REQUIRING_COLUMN = ("column_sum", "column_hash", "distinct_count", "min_max")


class ReconError(ValueError):
    """Raised when a reconciliation definition is missing or self-inconsistent."""


@dataclass(frozen=True)
class ReconCheck:
    """One comparison between the legacy platform and Databricks.

    `tolerance` is FRACTIONAL (0.0 = exact). Anything above zero must be
    justified in the config file - a tolerance nobody can explain is how a real
    defect gets signed off.
    """

    name: str
    check_type: CheckType
    column: str | None = None
    tolerance: float = 0.0
    filter_expr: str | None = None
    justification: str | None = None

    def __post_init__(self) -> None:
        if self.check_type not in CHECK_TYPES:
            raise ReconError(f"{self.name}: unknown check_type {self.check_type!r}")
        if self.check_type in CHECKS_REQUIRING_COLUMN and not self.column:
            raise ReconError(f"{self.name}: check_type {self.check_type!r} requires a column")
        if self.column:
            validate_identifier(self.column, "column")
        if not 0.0 <= self.tolerance < 1.0:
            raise ReconError(f"{self.name}: tolerance must be in [0.0, 1.0), got {self.tolerance}")
        if self.tolerance > 0 and not self.justification:
            raise ReconError(
                f"{self.name}: tolerance {self.tolerance} needs a justification. "
                "An unexplained tolerance is how a real defect gets signed off."
            )

    def measure_sql(self, table: str) -> str:
        """SQL returning a single `metric` column for one side of the comparison.

        Pure, so the measurement each check performs is asserted in unit tests
        rather than inferred from a run.
        """
        where = f" WHERE {self.filter_expr}" if self.filter_expr else ""
        col = self.column
        expr = {
            "row_count": "count(*)",
            "column_sum": f"coalesce(sum(try_cast({col} AS DECIMAL(38,6))), 0)",
            # Order-independent so the two platforms need not agree on sort.
            "column_hash": f"coalesce(sum(crc32(coalesce(cast({col} AS STRING), '<null>'))), 0)",
            "distinct_count": f"count(DISTINCT {col})",
            "min_max": f"coalesce(max({col}), 0) - coalesce(min({col}), 0)",
        }[self.check_type]
        return f"SELECT {expr} AS metric FROM {table}{where}"  # noqa: S608 - table validated by caller

    def passed(self, legacy: float, target: float) -> bool:
        """Compare two measurements within tolerance."""
        if legacy == target:
            return True
        if self.tolerance == 0.0:
            return False
        # Relative to the legacy value, which is the reference. A zero reference
        # with a non-zero target is always a failure - there is no meaningful
        # relative difference from zero.
        if legacy == 0:
            return False
        return abs(legacy - target) / abs(legacy) <= self.tolerance


@dataclass(frozen=True)
class ReconTarget:
    """One table pair to reconcile."""

    name: str
    layer: str
    target_table: str
    source_ref: str
    key_columns: tuple[str, ...] = ()
    checks: tuple[ReconCheck, ...] = ()
    owner_email: str = ""

    def __post_init__(self) -> None:
        validate_identifier(self.target_table, "target_table")
        if not self.checks:
            raise ReconError(f"{self.name}: at least one check is required")
        if not self.source_ref:
            raise ReconError(f"{self.name}: source_ref (the legacy side) is required")
        names = [c.name for c in self.checks]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ReconError(f"{self.name}: duplicate check name(s) {sorted(dupes)}")

    def resolve_target(self, ctx: RuntimeContext) -> str:
        """Fully-qualified Databricks table, sandbox-aware like everything else."""
        return ctx.table(self.layer, self.target_table)


@dataclass
class ReconPlan:
    """Everything a use case reconciles. Built from conf/reconciliation.yml."""

    use_case: str
    targets: list[ReconTarget] = field(default_factory=list)

    @property
    def check_count(self) -> int:
        return sum(len(t.checks) for t in self.targets)

    def summary(self) -> str:
        return f"{self.use_case}: {len(self.targets)} table(s), {self.check_count} check(s)"


def load_recon_config(raw: dict) -> ReconPlan:
    """Validate a parsed conf/reconciliation.yml and return a plan.

    Takes the parsed dict rather than a path so it is testable with no
    filesystem and no workspace - and so PR validation can check the real
    committed config files.
    """
    use_case = raw.get("use_case")
    if not use_case:
        raise ReconError("reconciliation.yml must declare a use_case")

    entries = raw.get("targets")
    if not isinstance(entries, list) or not entries:
        raise ReconError(f"{use_case}: reconciliation.yml must contain a non-empty `targets:` list")

    targets: list[ReconTarget] = []
    seen: set[str] = set()
    for entry in entries:
        name = entry.get("name")
        if not name:
            raise ReconError(f"{use_case}: every target needs a name")
        if name in seen:
            raise ReconError(f"{use_case}: duplicate target name {name!r}")
        seen.add(name)

        checks = tuple(
            ReconCheck(
                name=c["name"],
                check_type=c["check_type"],
                column=c.get("column"),
                tolerance=float(c.get("tolerance", 0.0)),
                filter_expr=c.get("filter_expr"),
                justification=c.get("justification"),
            )
            for c in entry.get("checks", [])
        )
        targets.append(
            ReconTarget(
                name=name,
                layer=entry.get("layer", "curated"),
                target_table=entry["target_table"],
                source_ref=entry.get("source_ref", ""),
                key_columns=tuple(entry.get("key_columns", ())),
                checks=checks,
                owner_email=entry.get("owner_email", ""),
            )
        )

    return ReconPlan(use_case=use_case, targets=targets)
