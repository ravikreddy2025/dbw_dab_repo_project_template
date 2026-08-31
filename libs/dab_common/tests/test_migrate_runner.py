"""The migrate runner, exercised without Spark.

This is the payoff for moving the runner out of five notebooks: notebook code
cannot be unit tested at all, so the ordering and recording logic - the part
that can silently corrupt a history table - had no coverage.
"""

import pytest
from dab_common.config import ConfigError, build_context
from dab_common.migrate import (
    apply_migrations,
    apply_shape,
    run_migrations,
    split_statements,
)


# -- fakes -------------------------------------------------------------------
class FakeRow(dict):
    def __getitem__(self, key):
        return dict.__getitem__(self, key)


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def collect(self):
        return self._rows


class FakeSpark:
    """Records every statement, and answers the history query from `applied`."""

    def __init__(self, applied=None):
        self.applied = dict(applied or {})
        self.statements = []

    def sql(self, statement, args=None):
        self.statements.append((" ".join(statement.split()), args or {}))
        if "FROM" in statement and "schema_migration" in statement and "SELECT" in statement:
            return FakeResult(
                [FakeRow(filename=f, checksum=c) for f, c in self.applied.items()]
            )
        return FakeResult([])


def ctx_for(prefix=""):
    return build_context(
        {"env": "nonprod", "use_case": "us1", "schema_prefix": prefix,
         "bundle_target": "nonprod"}
    )


def write(root, rel, body):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


# -- statement splitting -----------------------------------------------------
def test_split_drops_comments_and_blanks():
    sql = """
    -- a leading comment
    CREATE TABLE a (x INT);

    -- another comment
    ALTER TABLE a ADD COLUMN y INT;
    """
    assert split_statements(sql) == [
        "CREATE TABLE a (x INT)",
        "ALTER TABLE a ADD COLUMN y INT",
    ]


def test_split_on_a_comment_only_file_returns_nothing():
    assert split_statements("-- nothing here\n-- at all\n") == []


# -- phase 1 -----------------------------------------------------------------
def test_apply_shape_runs_curated_then_datamart(tmp_path):
    write(tmp_path, "curated/orders.sql", "CREATE TABLE IF NOT EXISTS o (x INT);")
    write(tmp_path, "datamart/marts.sql", "CREATE TABLE IF NOT EXISTS m (x INT);")
    spark = FakeSpark()

    count = apply_shape(spark, ctx_for(), tmp_path, log=lambda *_: None)

    assert count == 2
    executed = [s for s, _ in spark.statements]
    assert executed[0].startswith("CREATE TABLE IF NOT EXISTS o")
    assert executed[1].startswith("CREATE TABLE IF NOT EXISTS m")


def test_apply_shape_binds_the_right_catalog_per_layer(tmp_path):
    write(tmp_path, "curated/orders.sql", "SELECT 1;")
    write(tmp_path, "datamart/marts.sql", "SELECT 1;")
    spark = FakeSpark()

    apply_shape(spark, ctx_for(), tmp_path, log=lambda *_: None)

    args = [a for _, a in spark.statements]
    assert args[0]["catalog"] == "edp_curated_nonprod"
    assert args[1]["catalog"] == "edp_datamart_nonprod"


def test_apply_shape_uses_the_sandbox_schema(tmp_path):
    write(tmp_path, "curated/orders.sql", "SELECT 1;")
    spark = FakeSpark()

    apply_shape(spark, ctx_for("jsmith_"), tmp_path, log=lambda *_: None)

    # statements[0] is the CREATE SCHEMA that ensure_schema issues in a sandbox;
    # the DDL from the file is the one carrying bound arguments.
    bound = [a for _, a in spark.statements if "schema" in a]
    assert bound[0]["schema"] == "jsmith_us1"
    assert bound[0]["catalog"] == "edp_curated_nonprod"


def test_apply_shape_creates_a_schema_only_in_a_sandbox(tmp_path):
    write(tmp_path, "curated/orders.sql", "SELECT 1;")

    sandbox = FakeSpark()
    apply_shape(sandbox, ctx_for("jsmith_"), tmp_path, log=lambda *_: None)
    assert any(s.startswith("CREATE SCHEMA") for s, _ in sandbox.statements)

    # In a shared environment the schema is owned by the _platform bundle, with
    # its grants. A job must never create it.
    shared = FakeSpark()
    apply_shape(shared, ctx_for(""), tmp_path, log=lambda *_: None)
    assert not any(s.startswith("CREATE SCHEMA") for s, _ in shared.statements)


def test_apply_shape_tolerates_a_missing_layer(tmp_path):
    write(tmp_path, "curated/orders.sql", "SELECT 1;")
    spark = FakeSpark()
    assert apply_shape(spark, ctx_for(), tmp_path, log=lambda *_: None) == 1


# -- phase 2 -----------------------------------------------------------------
def test_pending_migrations_run_in_version_order(tmp_path):
    write(tmp_path, "migrations/V002__b.sql", "ALTER TABLE t ADD COLUMN b INT;")
    write(tmp_path, "migrations/V001__a.sql", "ALTER TABLE t ADD COLUMN a INT;")
    spark = FakeSpark()

    applied = apply_migrations(spark, ctx_for(), tmp_path, log=lambda *_: None)

    assert [m.filename for m in applied] == ["V001__a.sql", "V002__b.sql"]
    ddl = [s for s, _ in spark.statements if s.startswith("ALTER")]
    assert ddl == ["ALTER TABLE t ADD COLUMN a INT", "ALTER TABLE t ADD COLUMN b INT"]


def test_each_applied_migration_is_recorded(tmp_path):
    write(tmp_path, "migrations/V001__a.sql", "ALTER TABLE t ADD COLUMN a INT;")
    spark = FakeSpark()

    apply_migrations(spark, ctx_for(), tmp_path, log=lambda *_: None)

    inserts = [(s, a) for s, a in spark.statements if s.startswith("INSERT INTO")]
    assert len(inserts) == 1
    args = inserts[0][1]
    assert args["filename"] == "V001__a.sql"
    assert args["version"] == 1
    assert args["statements"] == 1
    assert args["target"] == "nonprod"


def test_the_record_is_written_after_the_ddl_not_before(tmp_path):
    # If the INSERT went first, a statement that then failed would leave the
    # migration marked applied and the schema unchanged - the worst outcome.
    write(tmp_path, "migrations/V001__a.sql", "ALTER TABLE t ADD COLUMN a INT;")
    spark = FakeSpark()

    apply_migrations(spark, ctx_for(), tmp_path, log=lambda *_: None)

    kinds = [s.split()[0] for s, _ in spark.statements if not s.startswith("SELECT")]
    assert kinds == ["ALTER", "INSERT"]


def test_an_already_applied_migration_does_not_run_again(tmp_path):
    body = "ALTER TABLE t ADD COLUMN a INT;"
    path = write(tmp_path, "migrations/V001__a.sql", body)
    from dab_common.migrate import checksum

    spark = FakeSpark(applied={"V001__a.sql": checksum(path.read_text(encoding="utf-8"))})

    applied = apply_migrations(spark, ctx_for(), tmp_path, log=lambda *_: None)

    assert applied == []
    assert not [s for s, _ in spark.statements if s.startswith(("ALTER", "INSERT"))]


def test_an_edited_applied_migration_raises_before_running_anything(tmp_path):
    write(tmp_path, "migrations/V001__a.sql", "ALTER TABLE t ADD COLUMN a INT;")
    write(tmp_path, "migrations/V002__b.sql", "ALTER TABLE t ADD COLUMN b INT;")
    spark = FakeSpark(applied={"V001__a.sql": "STALE_CHECKSUM"})

    with pytest.raises(ConfigError, match="edited after the fact"):
        apply_migrations(spark, ctx_for(), tmp_path, log=lambda *_: None)

    # V002 was pending and legitimate, but nothing ran - drift stops everything.
    assert not [s for s, _ in spark.statements if s.startswith(("ALTER", "INSERT"))]


def test_a_multi_statement_migration_records_the_count(tmp_path):
    write(
        tmp_path,
        "migrations/V001__a.sql",
        "ALTER TABLE t ADD COLUMN a INT;\nUPDATE t SET a = 0 WHERE a IS NULL;",
    )
    spark = FakeSpark()

    apply_migrations(spark, ctx_for(), tmp_path, log=lambda *_: None)

    args = [a for s, a in spark.statements if s.startswith("INSERT INTO")][0]
    assert args["statements"] == 2


def test_no_migrations_folder_is_fine(tmp_path):
    spark = FakeSpark()
    assert apply_migrations(spark, ctx_for(), tmp_path, log=lambda *_: None) == []


# -- both phases -------------------------------------------------------------
def test_run_migrations_does_shape_before_migrations(tmp_path):
    write(tmp_path, "curated/orders.sql", "CREATE TABLE IF NOT EXISTS o (x INT);")
    write(tmp_path, "migrations/V001__a.sql", "ALTER TABLE o ADD COLUMN a INT;")
    spark = FakeSpark()

    result = run_migrations(spark, ctx_for(), tmp_path, log=lambda *_: None)

    kinds = [s.split()[0] for s, _ in spark.statements if not s.startswith("SELECT")]
    assert kinds == ["CREATE", "ALTER", "INSERT"]
    assert result == {"shape_statements": 1, "applied": ["V001__a.sql"]}


def test_run_migrations_is_idempotent_on_a_second_deploy(tmp_path):
    write(tmp_path, "curated/orders.sql", "CREATE TABLE IF NOT EXISTS o (x INT);")
    write(tmp_path, "migrations/V001__a.sql", "ALTER TABLE o ADD COLUMN a INT;")
    from dab_common.migrate import checksum

    body = (tmp_path / "migrations/V001__a.sql").read_text(encoding="utf-8")
    spark = FakeSpark(applied={"V001__a.sql": checksum(body)})

    result = run_migrations(spark, ctx_for(), tmp_path, log=lambda *_: None)

    # The shape DDL re-runs (IF NOT EXISTS, harmless); the migration does not.
    assert result["applied"] == []
    assert not [s for s, _ in spark.statements if s.startswith("ALTER")]
