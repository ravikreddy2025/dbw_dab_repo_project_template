"""Use case us3 - events.

Two zones, and the split is deliberate:

  src/ported/       Cloudera code, lifted near as-is, run as notebook tasks.
                    No package structure required, no tests required.
  src/us3_module/  Code refactored into the wheel: importable, unit-tested.

Stage 3 is the destination, not stage 1. Demanding a full refactor before
anything can run is how a migration stalls. See docs/14-porting-guide.md.

Everything here is a pure DataFrame-in / DataFrame-out function so it can be
tested without a cluster. Nothing here reads the environment for itself - the
RuntimeContext is always passed in.
"""

from us3_module.curated import conform_events
from us3_module.datamart import FACT_TABLE, MART_EXPECTATIONS, MART_TABLES

__all__ = ["conform_events", "FACT_TABLE", "MART_EXPECTATIONS", "MART_TABLES"]
