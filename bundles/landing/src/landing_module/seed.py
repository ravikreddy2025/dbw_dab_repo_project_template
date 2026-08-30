"""Turn conf/<use_case>/sources.yml files into landing-registry rows.

Source metadata is code: it lives in git, is reviewed in a PR by the owning use
case, and is promoted through nonprod -> preprod -> prod on the same branches as
the Python. This module is the translation layer, and it is pure so the
promotion logic is unit-tested without a cluster.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from edp_landing.registry import SourceSpec

# Columns of ops.config.landing_source a seed file may set. Anything else is a
# typo and is rejected rather than silently ignored - a silently dropped key is
# a config bug that surfaces as missing data weeks later.
SEEDABLE_COLUMNS = {
    "source_id",
    "use_case",
    "source_system",
    "source_object",
    "target_table",
    "load_strategy",
    "watermark_column",
    "primary_keys",
    "secret_scope",
    "options",
    "is_active",
    "owner_email",
}

# Partitioned JDBC reads need all four settings or none. A partition_column with
# no bounds is accepted by SourceSpec (it is a framework option, not a registry
# column) and then fails at RUNTIME - i.e. at 02:00, in whichever environment ran
# first. Validating here moves that failure to PR time.
PARTITION_REQUIRED = ("lower_bound", "upper_bound", "num_partitions")


class SeedError(ValueError):
    pass


def validate_options(source_id: str, options: dict[str, Any] | None) -> None:
    """Reject framework options that are internally inconsistent."""
    options = options or {}
    if "partition_column" not in options:
        return

    missing = [k for k in PARTITION_REQUIRED if options.get(k) is None]
    if missing:
        raise SeedError(
            f"{source_id}: options.partition_column is set but {missing} are missing. "
            "A partitioned JDBC read needs all four settings or none."
        )

    try:
        lower = int(options["lower_bound"])
        upper = int(options["upper_bound"])
        parts = int(options["num_partitions"])
    except (TypeError, ValueError) as exc:
        raise SeedError(f"{source_id}: partition bounds must be integers") from exc

    if upper <= lower:
        raise SeedError(
            f"{source_id}: options.upper_bound ({upper}) must be greater than lower_bound ({lower})"
        )
    if parts < 1:
        raise SeedError(f"{source_id}: options.num_partitions must be at least 1")


@dataclass
class SeedDiff:
    """What a seed run would change. Printed in dry-run mode before writing."""

    to_insert: list[dict[str, Any]] = field(default_factory=list)
    to_update: list[dict[str, Any]] = field(default_factory=list)
    to_deactivate: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.to_insert or self.to_update or self.to_deactivate)

    def summary(self) -> str:
        return (
            f"insert={len(self.to_insert)} update={len(self.to_update)} "
            f"deactivate={len(self.to_deactivate)} unchanged={len(self.unchanged)}"
        )


def load_seed_file(raw: dict[str, Any], expected_use_case: str | None = None) -> list[dict[str, Any]]:
    """Validate one parsed conf/<use_case>/sources.yml and return normalised rows.

    Takes the parsed dict rather than a path so it is testable with no filesystem
    and no workspace.

    `use_case` is declared once at the top of the file and stamped onto every
    row, so a source cannot accidentally be registered against the wrong use case
    - which would land its data in another team's schema.
    """
    use_case = raw.get("use_case")
    if not use_case:
        raise SeedError("Seed file must declare a top-level `use_case:`.")
    if expected_use_case and use_case != expected_use_case:
        raise SeedError(
            f"Seed file in conf/{expected_use_case}/ declares use_case: {use_case!r}. "
            "The folder and the declaration must agree, or sources land in the wrong schema."
        )

    sources = raw.get("sources")
    if not isinstance(sources, list) or not sources:
        raise SeedError(f"{use_case}: seed file must contain a non-empty `sources:` list.")

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for entry in sources:
        unknown = set(entry) - SEEDABLE_COLUMNS
        if unknown:
            raise SeedError(
                f"{entry.get('source_id', '<no source_id>')}: unknown key(s) {sorted(unknown)}. "
                f"Allowed: {sorted(SEEDABLE_COLUMNS)}"
            )

        sid = entry.get("source_id")
        if not sid:
            raise SeedError(f"{use_case}: every source needs a source_id.")
        if sid in seen:
            raise SeedError(f"{use_case}: duplicate source_id {sid!r} in the same seed file.")
        seen.add(sid)

        # Constructing the SourceSpec runs every framework validation (strategy /
        # watermark consistency, identifier safety) at PR time rather than at 2am.
        spec = SourceSpec.from_row({**entry, "use_case": use_case})
        validate_options(spec.source_id, entry.get("options"))

        row = {c: entry.get(c) for c in SEEDABLE_COLUMNS}
        row["source_id"] = spec.source_id
        row["use_case"] = use_case
        row["is_active"] = bool(entry.get("is_active", True))
        row["primary_keys"] = ",".join(spec.primary_keys) if spec.primary_keys else None
        row["options"] = {k: str(v) for k, v in (entry.get("options") or {}).items()} or None
        rows.append(row)

    return sorted(rows, key=lambda r: r["source_id"])


def plan_seed_merge(
    desired: list[dict[str, Any]],
    existing: list[dict[str, Any]],
    deactivate_missing: bool = True,
) -> SeedDiff:
    """Compare desired (git) against existing (registry) and plan the change.

    `deactivate_missing` never DELETES: a source dropped from a seed file is
    marked is_active=false so its watermark and audit history survive. Deleting
    the row would orphan years of table_load audit.
    """
    by_id_existing = {r["source_id"]: r for r in existing}
    by_id_desired = {r["source_id"]: r for r in desired}
    diff = SeedDiff()

    for sid, want in by_id_desired.items():
        have = by_id_existing.get(sid)
        if have is None:
            diff.to_insert.append(want)
            continue
        # Compare only the columns a seed file owns. created_at/updated_at and
        # anything an operator set directly are not the seed file's business.
        changed = any(
            _normalise(want.get(c)) != _normalise(have.get(c))
            for c in SEEDABLE_COLUMNS
            if c != "source_id"
        )
        (diff.to_update if changed else diff.unchanged).append(want if changed else sid)

    if deactivate_missing:
        diff.to_deactivate = sorted(
            sid
            for sid, have in by_id_existing.items()
            if sid not in by_id_desired and have.get("is_active")
        )

    return diff


def _normalise(value: Any) -> Any:
    """Make YAML and Delta representations comparable.

    Delta returns None for an absent MAP; YAML gives {}. Without this, every run
    would report a spurious UPDATE on every source.
    """
    if value in (None, "", {}, []):
        return None
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in sorted(value.items())}
    if isinstance(value, bool):
        return value
    return str(value)
