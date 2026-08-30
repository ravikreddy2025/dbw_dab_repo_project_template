"""Landing framework - shared by every use case.

>>> REFERENCE SKELETON <<<
This is deliberately illustrative, not a framework you are expected to adopt.
Your Cloudera ingestion code will be ported into `bundles/landing/src/ported/`
and refactored into this package over time. What matters here is the SHAPE:

  registry  - the source registry contract. Onboarding a Kafka topic or an
              Oracle table is a reviewed YAML row, never a new notebook. This
              part IS structurally load-bearing and worth keeping.
  kafka     - Kafka -> landing. Used by us1, us3, us4.
  oracle    - Oracle -> landing. Used by us2.

WHY LANDING IS SHARED AND USE CASES ARE NOT
-------------------------------------------
Landing is a horizontal capability: the same Kafka reader serves any use case
that arrives on Kafka, including ones that do not exist yet. Curation and
datamart logic is specific to a business domain and belongs to that use case's
own bundle.

The trade-off, stated plainly: one landing bundle means a change for us1
redeploys landing for us2-us5 as well. That is mitigated by per-use-case config
folders (with their own CODEOWNERS) and one job per use case, so runtime stays
independent even though deployment is atomic. If landing ever becomes a
bottleneck, splitting it is cheap precisely because the framework lives here in
`libs/` rather than inside the bundle.
"""

from edp_landing.registry import SourceSpec, read_active_sources, select_sources

__all__ = ["SourceSpec", "read_active_sources", "select_sources"]
__version__ = "0.4.0"
