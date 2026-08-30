"""EXAMPLE of stage-1 ported code.

Ported from : cloudera-us2-etl @ a1b2c3d  (replace with the real origin)
Ported by   : <name>, 2026-08-30
Refactor    : DAB-000  (replace with the real ticket)

>>> This file exists to show the SHAPE of acceptable stage-1 code. Delete it
>>> when real us2 code arrives.

What makes this acceptable at stage 1:
  * it does not know which environment it is in - context is passed in;
  * it has no hardcoded catalog, schema, host or credential;
  * its origin and its refactor ticket are recorded above.

What still makes it stage 1:
  * it is a script, not a tested package;
  * it mixes I/O with transformation, so it cannot be unit-tested.

Fixing that second point is the refactor. See docs/14-porting-guide.md.
"""

from __future__ import annotations


def run_legacy_customers_load(spark, ctx) -> int:
    """Lifted from the Cloudera job of the same name.

    `ctx` is a dab_common.config.RuntimeContext, passed in by the notebook entry
    point. Every table name comes from it - that is the one change made during
    the lift, and it is what makes the code promotable across environments.
    """
    source = ctx.table("landing", "ora_customers")
    target = ctx.table("curated", "customers_legacy")

    # ... original Cloudera transformation, unchanged ...
    df = spark.table(source)

    df.write.mode("overwrite").saveAsTable(target)
    return spark.table(target).count()
