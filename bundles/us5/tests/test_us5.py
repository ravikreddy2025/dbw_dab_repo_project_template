"""Unit tests for us5.

These run in PR validation with no Spark, no Java and no cluster. Anything
needing a real SparkSession goes in a file guarded by
`pytest.importorskip("pyspark")` and marked `@pytest.mark.integration`.
"""

from pathlib import Path

import pytest
from dab_common.config import build_context
from us5_module.curated import SETTLEMENT_PAYLOAD_SCHEMA, VALID_STATUSES, dedupe_by_key
from us5_module.datamart import MART_TABLES

BUNDLE = Path(__file__).resolve().parents[1]
SQL_DIR = BUNDLE / "src" / "sql"

# NOTE: reconciliation is NOT tested here. It lives in bundles/recon, owned by
# QA, with its own tests - see bundles/recon/tests/test_recon_configs.py.


# -- isolation ---------------------------------------------------------------

def test_this_use_case_cannot_collide_with_another():
    a = build_context({"env": "prod", "use_case": "us5"})
    b = build_context({"env": "prod", "use_case": "us9"})
    assert a.fq_schema("curated") != b.fq_schema("curated")


def test_layers_resolve_to_different_catalogs():
    ctx = build_context({"env": "prod", "use_case": "us5"})
    assert ctx.catalog("landing") != ctx.catalog("curated") != ctx.catalog("datamart")


# -- curated contract --------------------------------------------------------

def test_payload_schema_declares_every_field_conform_reads():
    """conform_settlement selects these by name; dropping one would fail at
    runtime with an unresolved-column error, in prod."""
    for field in ("settlement_id", "event_ts", "status", "amount", "currency"):
        assert f"{field} " in SETTLEMENT_PAYLOAD_SCHEMA


def test_amount_is_typed_not_inferred():
    """Inferring amount from JSON yields a string often enough to matter."""
    assert "amount DOUBLE" in SETTLEMENT_PAYLOAD_SCHEMA


def test_valid_statuses_are_upper_case():
    """conform_settlement upper-cases before comparing; a lower-case entry here
    would silently null every row with that status."""
    assert all(s == s.upper() for s in VALID_STATUSES)


def test_dedupe_rejects_an_empty_key_list():
    """Without keys the window would collapse the whole table to one row."""
    with pytest.raises(ValueError, match="at least one key"):
        dedupe_by_key(df=None, keys=[], order_by="event_ts")


# -- datamart grants ---------------------------------------------------------

@pytest.mark.parametrize("table", MART_TABLES)
def test_every_declared_mart_has_a_sql_file(table):
    assert (SQL_DIR / f"{table}.sql").exists(), f"{table} is in MART_TABLES but has no .sql"


def test_every_sql_file_is_a_declared_mart():
    """Catches the reverse mistake: a new mart whose audit and grants were
    forgotten."""
    on_disk = {p.stem for p in SQL_DIR.glob("*.sql")}
    assert on_disk == set(MART_TABLES), f"drift: {on_disk ^ set(MART_TABLES)}"


@pytest.mark.parametrize("path", sorted(SQL_DIR.glob("*.sql")), ids=lambda p: p.name)
def test_sql_uses_bound_parameters_not_hardcoded_catalogs(path):
    """A hardcoded catalog is the classic way a preprod job writes to prod."""
    body = "\n".join(
        ln for ln in path.read_text(encoding="utf-8").splitlines()
        if not ln.strip().startswith("--")
    )
    for forbidden in ("edp_curated_", "edp_datamart_", "edp_landing_", "edp_ops_"):
        assert forbidden not in body, f"{path.name} hardcodes a catalog ({forbidden}...)"
    assert ":catalog" in body and ":schema" in body

