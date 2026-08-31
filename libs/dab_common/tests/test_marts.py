"""Mart publishing policy - reader groups and grants.

These assertions used to live five times, once per use-case bundle, and were
identical every time. Grant policy is platform policy: it belongs in one place,
and so does its test.
"""

from dab_common.config import build_context
from dab_common.marts import READER_GROUPS, publish, reader_grant_statements

TABLES = ("dim_orders", "fct_orders")


def ctx_for(env="prod", prefix=""):
    return build_context({"env": env, "use_case": "us1", "schema_prefix": prefix})


# -- grants ------------------------------------------------------------------
def test_a_sandbox_grants_nothing():
    # A developer's private mart must not be readable by the whole team, and the
    # grant would outlive the schema it was made on.
    assert reader_grant_statements(ctx_for("nonprod", "jsmith_"), TABLES) == []


def test_prod_grants_business_readers_on_every_table():
    stmts = reader_grant_statements(ctx_for("prod"), TABLES)
    assert len(stmts) == len(READER_GROUPS["prod"]) * len(TABLES)
    joined = " ".join(stmts)
    assert "edp-business-analysts" in joined
    assert "edp-support" in joined


def test_grants_are_table_level_not_schema_level():
    # A schema-level grant would also expose every intermediate table someone
    # creates in that schema later.
    for stmt in reader_grant_statements(ctx_for("prod"), TABLES):
        assert "ON TABLE " in stmt
        assert "ON SCHEMA" not in stmt


def test_grants_name_the_fully_qualified_table():
    joined = " ".join(reader_grant_statements(ctx_for("prod"), TABLES))
    assert "edp_datamart_prod.us1.dim_orders" in joined
    assert "edp_datamart_prod.us1.fct_orders" in joined


def test_nonprod_does_not_grant_business_users():
    joined = " ".join(reader_grant_statements(ctx_for("nonprod"), TABLES))
    assert "edp-business-analysts" not in joined


def test_an_unknown_environment_grants_nothing_rather_than_everything():
    ctx = build_context({"env": "preprod", "use_case": "us1"})
    stmts = reader_grant_statements(ctx, TABLES)
    assert all("edp-qa" in s for s in stmts)


def test_group_names_are_backtick_quoted():
    # Group names contain hyphens, which are not legal unquoted identifiers.
    for stmt in reader_grant_statements(ctx_for("prod"), TABLES):
        assert stmt.rstrip().endswith("`")


# -- publish orchestration ---------------------------------------------------
class FakeDF:
    def __init__(self, n):
        self._n = n

    def count(self):
        return self._n


class FakeSpark:
    def __init__(self):
        self.granted = []

    def table(self, _fq):
        return FakeDF(42)

    def sql(self, statement, args=None):
        if statement.startswith("GRANT"):
            self.granted.append(statement)

        class _R:
            def collect(self_inner):
                return []

        return _R()


def test_publish_grants_after_auditing(monkeypatch):
    calls = []
    monkeypatch.setattr("dab_common.audit.record_table_load",
                        lambda *a, **k: calls.append("audit"))

    class _NullCtxMgr:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("dab_common.audit.audited_run", lambda *a, **k: _NullCtxMgr())

    spark = FakeSpark()
    result = publish(spark, ctx_for("prod"), TABLES, log=lambda *_: None)

    assert calls == ["audit", "audit"]
    assert result["rows"] == {"dim_orders": 42, "fct_orders": 42}
    assert result["grants"] == len(spark.granted) == 4
