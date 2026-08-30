"""Cross-cutting framework shared by EVERY bundle.

Deliberately small and layer-agnostic: runtime configuration, audit and data
quality. Anything specific to a layer lives
elsewhere: the landing framework is `libs/edp_landing`, the parity framework is
`libs/edp_recon`, and use-case logic lives in that use case's own bundle.

Owned by the platform team (see CODEOWNERS). Changing this changes every use
case, so it carries the strictest review and the fullest test coverage.

Design rule that makes it testable: *nothing at import time touches Spark*.
Every function needing a SparkSession takes it as its first argument, and all
SQL construction is pure so it can be asserted in plain pytest.
"""

from dab_common.config import RuntimeContext, build_context, ensure_ops_schema, ensure_schema

__all__ = ["RuntimeContext", "build_context", "ensure_schema", "ensure_ops_schema"]
__version__ = "0.4.0"
