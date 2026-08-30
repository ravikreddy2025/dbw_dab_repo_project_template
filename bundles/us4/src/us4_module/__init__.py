"""Use case us4 - inventory.

Two zones, and the split is deliberate:

  src/ported/       Cloudera code, lifted near as-is, run as notebook tasks.
                    No package structure required, no tests required.
  src/us4_module/  Code refactored into the wheel: importable, unit-tested.

Stage 3 is the destination, not stage 1. Demanding a full refactor before
anything can run is how a migration stalls. See docs/14-porting-guide.md.

Everything here is a pure DataFrame-in / DataFrame-out function so it can be
tested without a cluster. Nothing here reads the environment for itself - the
RuntimeContext is always passed in.
"""

from us4_module.curated import conform_inventory
from us4_module.datamart import MART_TABLES, reader_grant_statements

__all__ = ["conform_inventory", "MART_TABLES", "reader_grant_statements"]
