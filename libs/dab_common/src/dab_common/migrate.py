"""Ordered, once-only schema migrations.

WHY THIS EXISTS
---------------
`CREATE TABLE IF NOT EXISTS` builds a NEW environment. It does nothing at all to
an environment that already has the table. So the day you add a column:

    nonprod   fresh sandbox, table created with the new column   -> works
    preprod   table already exists, unchanged                    -> fails
    prod      table already exists, unchanged                    -> fails

...and it fails only after the approval gate, on the environment you cannot
iterate on. That is the failure this module exists to prevent.

THE TWO KINDS OF SQL, AND WHY THEY ARE SEPARATE
-----------------------------------------------
    src/ddl/curated/*.sql     the CURRENT SHAPE. CREATE TABLE IF NOT EXISTS.
    src/ddl/datamart/*.sql    Re-runnable. Builds an empty environment from
                              nothing. Edit freely - it is a description.

    src/ddl/migrations/*.sql  ORDERED CHANGES to tables that already exist.
                              Applied once per environment and recorded.
                              NEVER edited after merge - see checksum drift.

Both run in the same job, shape first. On a brand-new environment the shape DDL
creates the current table and the migrations then no-op; on an existing one the
shape DDL no-ops and the migrations do the work. Either way you converge.

NAMING
------
    V007__add_settlement_currency.sql

    V<digits>   ordering. Zero-padded so they sort the same in the file browser
                as they do here. Gaps are fine; duplicates are not.
    __          two underscores, separating version from description.
    name        lower_snake_case. It ends up in the history table and in the
                error message someone reads at 2am.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not generate migrations, diff schemas, or roll back. Delta has no
transactional DDL rollback, so "down" migrations are a comfortable fiction -
the recovery path is a new forward migration, or RESTORE to a version.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from dab_common.config import ConfigError

# V<digits>__<lower_snake>.sql
_MIGRATION_RE = re.compile(r"^V(\d+)__([a-z0-9_]+)\.sql$")


@dataclass(frozen=True)
class Migration:
    """One migration file, parsed. Ordering is by `version` only."""

    version: int
    name: str
    filename: str


def parse_migration(filename: str) -> Migration:
    """Parse `V007__add_currency.sql`, or raise with the rule that was broken."""
    match = _MIGRATION_RE.match(filename)
    if not match:
        raise ConfigError(
            f"Bad migration filename: {filename!r}. Expected "
            "V<digits>__<lower_snake_case>.sql, e.g. V007__add_settlement_currency.sql"
        )
    return Migration(int(match.group(1)), match.group(2), filename)


def checksum(sql: str) -> str:
    """Short content hash. Newlines normalised so CRLF is not a change."""
    normalised = "\n".join(line.rstrip() for line in sql.replace("\r\n", "\n").splitlines())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:16]


def plan(available: Sequence[str], applied: Iterable[str]) -> list[Migration]:
    """Return the migrations to run, in order.

    `available` is every filename in the migrations folder; `applied` is every
    filename this environment has already recorded.

    Raises rather than guessing on the three situations that mean two people
    made incompatible assumptions:

    Duplicate version
        Two branches both wrote V007. Whichever merged second is about to be
        applied in an order nobody chose. Renumber it.

    Out-of-order arrival
        V005 is pending but V006 is already applied - a long-lived branch just
        merged. V005 was written against a schema that no longer exists, so
        applying it now is not what its author tested. Renumber it above the
        high-water mark and re-check that it still makes sense.

    Applied but missing
        History records a migration that is not in the repo. Someone deleted a
        file that had already run somewhere. Restore it; a migration is a
        historical record, not current code.
    """
    parsed = [parse_migration(f) for f in available]

    by_version: dict[int, list[str]] = {}
    for migration in parsed:
        by_version.setdefault(migration.version, []).append(migration.filename)
    duplicates = {v: sorted(f) for v, f in by_version.items() if len(f) > 1}
    if duplicates:
        detail = "; ".join(f"V{v:03d}: {', '.join(f)}" for v, f in sorted(duplicates.items()))
        raise ConfigError(
            f"Duplicate migration version(s) - {detail}. Two branches used the "
            "same number. Renumber the one that merged second."
        )

    applied_names = set(applied)
    known = {m.filename for m in parsed}
    orphaned = sorted(applied_names - known)
    if orphaned:
        raise ConfigError(
            f"Migration(s) recorded as applied but missing from the repo: "
            f"{', '.join(orphaned)}. A migration that has run somewhere is a "
            "historical record - restore the file rather than deleting history."
        )

    pending = sorted((m for m in parsed if m.filename not in applied_names), key=lambda m: m.version)
    if not pending:
        return []

    applied_versions = [m.version for m in parsed if m.filename in applied_names]
    high_water = max(applied_versions, default=-1)
    late = [m for m in pending if m.version < high_water]
    if late:
        detail = ", ".join(m.filename for m in late)
        raise ConfigError(
            f"Migration(s) arriving out of order: {detail} (highest already "
            f"applied is V{high_water:03d}). A long-lived branch merged late. "
            "Renumber above the high-water mark and confirm the change still applies."
        )

    return pending


def verify_unchanged(
    recorded: Mapping[str, str], current: Mapping[str, str]
) -> list[str]:
    """Return filenames whose content changed after they were applied.

    An applied migration is history. Editing one means environments that ran the
    old text and environments that will run the new text disagree about what the
    schema is, and nothing reports it. Fix forward with a new migration instead.
    """
    return sorted(
        name
        for name, digest in recorded.items()
        if name in current and current[name] != digest
    )


# ---------------------------------------------------------------------------
# The runner. This lives here, not in five near-identical notebooks.
#
# Everything below is use-case agnostic: it is driven entirely by RuntimeContext
# and the contents of a ddl_root folder. The per-bundle notebook is a shim that
# builds a context and calls run_migrations(), because notebook_task requires a
# file inside the bundle root - not because anything here differs per use case.
# ---------------------------------------------------------------------------
def split_statements(sql: str) -> list[str]:
    """Split a script on `;`, dropping comment-only and empty fragments."""
    out = []
    for raw in sql.split(";"):
        stmt = "\n".join(
            line for line in raw.splitlines() if not line.strip().startswith("--")
        ).strip()
        if stmt:
            out.append(stmt)
    return out


def apply_shape(spark, ctx, ddl_root, log=print) -> int:
    """Phase 1 - CREATE TABLE IF NOT EXISTS for every table this use case owns.

    Builds an environment that does not have the tables yet: a fresh workspace,
    a new sandbox, a new use case. A no-op everywhere else, which is why it is
    safe on every deploy.

    This owns the SHAPE. The jobs write DATA and must not redefine it - see
    check_no_schema_clobber in scripts/ci/check_bundle_references.py.

    Returns the number of statements executed.
    """
    from dab_common.config import ensure_schema

    count = 0
    for layer in ("curated", "datamart"):
        folder = ddl_root / layer
        if not folder.is_dir():
            continue
        ensure_schema(spark, ctx, layer)
        args = {"catalog": ctx.catalog(layer), "schema": ctx.schema()}
        for sql_file in sorted(folder.glob("*.sql")):
            log(f"\n--- {layer}/{sql_file.name} ---")
            for stmt in split_statements(sql_file.read_text(encoding="utf-8")):
                log(f"  {stmt.splitlines()[0][:100]}")
                spark.sql(stmt, args=args)
                count += 1
    return count


def apply_migrations(spark, ctx, ddl_root, log=print) -> list[Migration]:
    """Phase 2 - ordered migrations, once each, recorded in ops.config.

    Raises before executing anything if an applied migration was edited, if two
    share a version, if one arrives out of order, or if history references a file
    that no longer exists.

    Returns the migrations that were applied by this call.
    """
    history = ctx.config_table("schema_migration")
    folder = ddl_root / "migrations"
    files = sorted(folder.glob("V*.sql")) if folder.is_dir() else []
    current = {f.name: checksum(f.read_text(encoding="utf-8")) for f in files}

    recorded = {
        row["filename"]: row["checksum"]
        for row in spark.sql(
            f"SELECT filename, checksum FROM {history} WHERE use_case = :uc",
            args={"uc": ctx.use_case},
        ).collect()
    }

    # An applied migration that was edited afterwards means two environments ran
    # different text and nothing reports it. Refuse rather than paper over it.
    drifted = verify_unchanged(recorded, current)
    if drifted:
        raise ConfigError(
            f"Applied migration(s) edited after the fact: {', '.join(drifted)}. "
            "A migration is history. Revert the edit and fix forward with a new file."
        )

    pending = plan(list(current), recorded.keys())
    log(
        f"{len(current)} migration(s) on disk, {len(recorded)} applied, "
        f"{len(pending)} pending"
    )

    for migration in pending:
        body = (folder / migration.filename).read_text(encoding="utf-8")
        stmts = split_statements(body)
        log(f"\n--- applying {migration.filename} ({len(stmts)} statement(s)) ---")

        args = {"catalog": ctx.catalog("curated"), "schema": ctx.schema()}
        for stmt in stmts:
            log(f"  {stmt.splitlines()[0][:100]}")
            spark.sql(stmt, args=args)

        # Recorded only after every statement succeeded. Delta has no
        # transactional DDL, so a half-applied file stays PENDING and is retried
        # on the next deploy - which is why migrations must be idempotent.
        spark.sql(
            f"""INSERT INTO {history}
                (use_case, version, name, filename, checksum, statements,
                 applied_by, bundle_target)
                VALUES (:uc, :version, :name, :filename, :checksum, :statements,
                        current_user(), :target)""",
            args={
                "uc": ctx.use_case,
                "version": migration.version,
                "name": migration.name,
                "filename": migration.filename,
                "checksum": current[migration.filename],
                "statements": len(stmts),
                "target": ctx.bundle_target,
            },
        )

    return pending


def run_migrations(spark, ctx, ddl_root, log=print) -> dict:
    """Both phases, in order. This is the whole body of the migrate job."""
    log(f"target : {ctx.bundle_target} ({ctx.env})")
    log(f"schema : {ctx.fq_schema('curated')}")
    log(f"history: {ctx.config_table('schema_migration')}")

    shape = apply_shape(spark, ctx, ddl_root, log=log)
    applied = apply_migrations(spark, ctx, ddl_root, log=log)

    log("\nschema is up to date")
    return {"shape_statements": shape, "applied": [m.filename for m in applied]}
