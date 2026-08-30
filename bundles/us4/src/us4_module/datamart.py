"""us4 datamart-layer helpers.

Most of this layer is SQL in src/sql/. The Python here is limited to what a SQL
task cannot do: audit writes, grants, and assertions. Pure functions returning
SQL strings rather than executing them, so grant logic is unit-tested.
"""

from __future__ import annotations

from dab_common.config import RuntimeContext

# The marts this use case owns. Adding one means adding it here AND adding the
# .sql file - a test asserts the two stay in step, so neither can be forgotten.
MART_TABLES = ("dim_inventory", "fct_inventory")

# Groups granted SELECT on published marts, per environment. Schema-level grants
# live in the _platform bundle; these are the table-level grants that can only be
# applied once the table exists.
READER_GROUPS = {
    "nonprod": ("edp-developers",),
    "preprod": ("edp-qa",),
    "prod": ("edp-support", "edp-business-analysts"),
}


def reader_grant_statements(ctx: RuntimeContext) -> list[str]:
    """GRANT SELECT statements for every mart in this environment.

    Empty in a sandbox: a developer private mart should not be readable by the
    whole team, and granting on it would clutter the metastore.
    """
    if ctx.is_sandbox:
        return []

    return [
        f"GRANT SELECT ON TABLE {ctx.table('datamart', table)} TO `{group}`"
        for group in READER_GROUPS.get(ctx.env, ())
        for table in MART_TABLES
    ]
