"""Migration ordering rules.

Every test here is a situation that has shipped a broken preprod somewhere.
"""

import pytest
from dab_common.config import ConfigError
from dab_common.migrate import (
    Migration,
    checksum,
    parse_migration,
    plan,
    verify_unchanged,
)


# -- filenames --------------------------------------------------------------
def test_parses_a_well_formed_name():
    assert parse_migration("V007__add_settlement_currency.sql") == Migration(
        7, "add_settlement_currency", "V007__add_settlement_currency.sql"
    )


@pytest.mark.parametrize(
    "bad",
    [
        "007__add_currency.sql",          # no V
        "V007_add_currency.sql",          # one underscore
        "V007__AddCurrency.sql",          # not snake_case
        "V007__add currency.sql",         # space
        "V__add_currency.sql",            # no digits
        "add_currency.sql",               # no version at all
        "V007__add_currency.txt",         # not .sql
    ],
)
def test_rejects_malformed_names(bad):
    with pytest.raises(ConfigError, match="Bad migration filename"):
        parse_migration(bad)


def test_version_is_numeric_not_lexical():
    # V010 must sort AFTER V009, which string ordering gets right only by luck
    # of zero-padding. Parse to int so an unpadded V10 still works.
    got = plan(["V009__b.sql", "V10__c.sql", "V002__a.sql"], applied=[])
    assert [m.version for m in got] == [2, 9, 10]


# -- planning ---------------------------------------------------------------
def test_plan_returns_only_pending_in_order():
    got = plan(
        ["V001__a.sql", "V002__b.sql", "V003__c.sql"],
        applied=["V001__a.sql"],
    )
    assert [m.filename for m in got] == ["V002__b.sql", "V003__c.sql"]


def test_plan_is_empty_when_everything_is_applied():
    assert plan(["V001__a.sql"], applied=["V001__a.sql"]) == []


def test_plan_on_a_fresh_environment_returns_everything():
    got = plan(["V001__a.sql", "V002__b.sql"], applied=[])
    assert len(got) == 2


def test_duplicate_version_is_refused():
    # Two branches both wrote V007. The second to merge would otherwise apply
    # in an order nobody chose.
    with pytest.raises(ConfigError, match="Duplicate migration version"):
        plan(["V007__alice.sql", "V007__bob.sql"], applied=[])


def test_duplicate_version_names_both_files():
    with pytest.raises(ConfigError) as exc:
        plan(["V007__alice.sql", "V007__bob.sql"], applied=[])
    assert "V007__alice.sql" in str(exc.value)
    assert "V007__bob.sql" in str(exc.value)


def test_out_of_order_arrival_is_refused():
    # A long-lived branch merges V005 after V006 already ran in prod. V005 was
    # written against a schema that no longer exists.
    with pytest.raises(ConfigError, match="out of order"):
        plan(
            ["V005__late.sql", "V006__already.sql"],
            applied=["V006__already.sql"],
        )


def test_applied_but_deleted_from_repo_is_refused():
    with pytest.raises(ConfigError, match="missing from the repo"):
        plan(["V002__b.sql"], applied=["V001__deleted.sql", "V002__b.sql"])


def test_gaps_in_numbering_are_fine():
    # Two developers reserve V004 and V009; V005-V008 never exist. Harmless.
    got = plan(["V004__a.sql", "V009__b.sql"], applied=["V004__a.sql"])
    assert [m.filename for m in got] == ["V009__b.sql"]


# -- checksum drift ---------------------------------------------------------
def test_checksum_ignores_line_endings_and_trailing_space():
    assert checksum("ALTER TABLE x ADD COLUMN y INT;\n") == checksum(
        "ALTER TABLE x ADD COLUMN y INT;  \r\n"
    )


def test_checksum_detects_a_real_edit():
    assert checksum("ALTER TABLE x ADD COLUMN y INT;") != checksum(
        "ALTER TABLE x ADD COLUMN y STRING;"
    )


def test_verify_unchanged_flags_an_edited_migration():
    recorded = {"V001__a.sql": "aaaa", "V002__b.sql": "bbbb"}
    current = {"V001__a.sql": "aaaa", "V002__b.sql": "CHANGED"}
    assert verify_unchanged(recorded, current) == ["V002__b.sql"]


def test_verify_unchanged_ignores_files_never_applied():
    recorded = {"V001__a.sql": "aaaa"}
    current = {"V001__a.sql": "aaaa", "V002__new.sql": "whatever"}
    assert verify_unchanged(recorded, current) == []
