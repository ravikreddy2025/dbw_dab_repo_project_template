"""us1 datamart-layer content.

WHAT THIS FILE IS FOR: the two things that are genuinely us1's.

Everything else about publishing a mart - the reader groups per environment, the
grant statements, the audit writes, the order they happen in - is platform policy
and lives once in `dab_common.marts`. Policy in five files is policy that
diverges the day someone adds an environment.
"""

from __future__ import annotations

from dab_common.quality import non_negative, not_null, unique

# The marts this use case owns. Adding one means adding it here AND adding the
# .sql file - a test asserts the two stay in step, so neither can be forgotten.
MART_TABLES = ("dim_orders", "fct_orders")

# The fact table the quality gate runs against.
FACT_TABLE = "fct_orders"

# >>> PLACEHOLDER: what "correct" means for us1. <<<
# These are use-case knowledge, not framework - which is exactly why they stay
# here while the machinery that runs them does not.
MART_EXPECTATIONS = (
    not_null("orders_id"),
    unique("orders_id"),
    non_negative("amount"),
)
