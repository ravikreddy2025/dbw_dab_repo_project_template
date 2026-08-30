"""Cloudera-to-Databricks parity framework.

OWNED BY QA (@edp-qa). Depended on by `bundles/recon` and by nothing else.

WHY THIS IS ITS OWN LIBRARY
---------------------------
Reconciliation has a different owner, a different permission profile and a
different lifespan from the ETL it checks:

  owner      QA writes and signs off the checks; the use-case teams write the ETL.
  identity   The recon job READS every data catalog and WRITES only ops.recon.
             The ETL run-as identity writes data. Neither should have the other.
  lifespan   Recon exists for the migration. At decommission the whole thing is
             deleted, and that deletion must not be a change to production ETL.

Keeping it inside a use-case bundle broke all three: a QA tolerance change would
trigger that use case's CD pipeline and redeploy production ETL jobs.

WHAT IS HERE
------------
  model     ReconCheck / ReconTarget / ReconPlan - the contract, and the SQL each
            check measures. Pure and unit-tested.
  gate      "Did the ETL this run is checking actually complete?" Comparing stale
            Databricks output against fresh Cloudera output produces a false
            failure, and a false failure is worse than no check at all.

The framework is deliberately generic: for a lift and shift, every use case is
"compare this target table against that source table". Only the CONFIG differs,
and config lives in bundles/recon/conf/<use_case>.yml.
"""

from edp_recon.gate import EtlGateResult, etl_completed_for
from edp_recon.model import (
    CHECK_TYPES,
    ReconCheck,
    ReconError,
    ReconPlan,
    ReconTarget,
    load_recon_config,
)

__all__ = [
    "CHECK_TYPES",
    "EtlGateResult",
    "ReconCheck",
    "ReconError",
    "ReconPlan",
    "ReconTarget",
    "etl_completed_for",
    "load_recon_config",
]
__version__ = "0.4.0"
