"""Runtime configuration: which catalog, which schema, which use case.

THE CATALOG MODEL
-----------------
Four catalogs per environment, one per layer, shared across all use cases:

    edp_landing_<env>     raw landed data          (Kafka, Oracle, ...)
    edp_curated_<env>     cleansed / conformed     (the middle layer)
    edp_datamart_<env>    business-facing marts    (gold / semantic)
    edp_ops_<env>         audit, config, logs, recon

...where <env> is nonprod | preprod | prod. The environment suffix is not
cosmetic: one Unity Catalog metastore serves all three workspaces, so catalog
names must be unique metastore-wide.

Inside the three DATA catalogs, schemas are per use case:

    edp_curated_nonprod.us1
    edp_curated_nonprod.us2   ...

Inside the OPS catalog, schemas are functional and cross-cutting, because audit
and config are not owned by any single use case:

    edp_ops_nonprod.audit / .config / .logs / .recon

THE ONE RULE
------------
Every schema this framework touches is prefixed with `schema_prefix` in a
developer sandbox - data catalogs AND ops. One rule, no exceptions. A developer
seeding config into `jsmith_config` can never overwrite the shared registry that
shared nonprod jobs read from. An inconsistent rule here would be a footgun.

JOB PARAMETERS
--------------
Every job in every bundle passes the same five base parameters, set from bundle
variables in the resource YAML:

    parameters:
      - name: env
        default: ${var.env}                 # nonprod | preprod | prod
      - name: use_case
        default: ${var.use_case}            # us1 .. us5, or landing / platform
      - name: catalog_prefix
        default: ${var.catalog_prefix}      # edp
      - name: schema_prefix
        default: ${var.schema_prefix}       # "jsmith_" in a sandbox, "" elsewhere
      - name: bundle_target
        default: ${bundle.target}           # dev | nonprod | preprod | prod

That convention is what lets identical code run in a developer sandbox and in
production with no branching on environment anywhere in the business logic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Unity Catalog identifiers: letters, digits, underscore. Anything else is
# refused rather than quoted - a name needing quotes is almost always a
# parameter-injection mistake rather than a real object name.
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Physical environments. A developer sandbox is NOT an environment - it lives
# inside nonprod and is distinguished by a non-empty schema_prefix.
VALID_ENVS = ("nonprod", "preprod", "prod")

# The four layers, each backed by its own catalog.
LAYERS = ("landing", "curated", "datamart", "ops")

# Functional schemas inside the ops catalog. Not per use case.
OPS_SCHEMAS = ("audit", "config", "logs", "recon")

# How a catalog name is built. Overridable per layer via catalog_overrides for
# the case where an existing catalog does not follow the convention.
CATALOG_PATTERN = "{prefix}_{layer}_{env}"


class ConfigError(ValueError):
    """Raised when job parameters are missing or unusable."""


def validate_identifier(value: str, what: str = "identifier") -> str:
    """Return `value` if it is a safe unquoted SQL identifier, else raise.

    Applied to every catalog/schema/table fragment before it is interpolated
    into SQL. Config rows are data, and data is not trusted to be SQL.
    """
    if not isinstance(value, str) or not _IDENT.match(value):
        raise ConfigError(f"Invalid {what}: {value!r}. Expected ^[A-Za-z_][A-Za-z0-9_]*$")
    return value


@dataclass(frozen=True)
class RuntimeContext:
    """Everything a task needs to know about *where* it is running.

    Immutable on purpose: tasks derive names from it, they never mutate it.
    """

    env: str
    use_case: str
    catalog_prefix: str = "edp"
    schema_prefix: str = ""
    bundle_target: str = ""
    job_id: str = ""
    run_id: str = ""
    task_key: str = ""
    # layer -> explicit catalog name, for catalogs that predate the convention.
    catalog_overrides: dict[str, str] = field(default_factory=dict)
    extra: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.env not in VALID_ENVS:
            raise ConfigError(
                f"env must be one of {VALID_ENVS}, got {self.env!r}. "
                "A developer sandbox uses env=nonprod with a schema_prefix - "
                "'dev' is a bundle target, not an environment."
            )
        validate_identifier(self.use_case, "use_case")
        validate_identifier(self.catalog_prefix, "catalog_prefix")
        if self.schema_prefix and not _IDENT.match(self.schema_prefix.rstrip("_") or "_"):
            raise ConfigError(f"Invalid schema_prefix: {self.schema_prefix!r}")
        for layer, name in self.catalog_overrides.items():
            if layer not in LAYERS:
                raise ConfigError(f"Unknown layer in catalog_overrides: {layer!r}. Expected {LAYERS}")
            validate_identifier(name, f"catalog override for {layer}")

    # -- identity ------------------------------------------------------------

    @property
    def is_sandbox(self) -> bool:
        """True when this is an individual developer's isolated deployment.

        Sandboxes are the only place where the framework creates schemas on the
        fly, and the only place where destructive helpers are permitted.
        """
        return bool(self.schema_prefix)

    # -- catalogs ------------------------------------------------------------

    def catalog(self, layer: str) -> str:
        """Catalog name for a layer, e.g. catalog("curated") -> edp_curated_prod."""
        if layer not in LAYERS:
            raise ConfigError(f"Unknown layer: {layer!r}. Expected one of {LAYERS}")
        if layer in self.catalog_overrides:
            return self.catalog_overrides[layer]
        return CATALOG_PATTERN.format(prefix=self.catalog_prefix, layer=layer, env=self.env)

    # -- schemas -------------------------------------------------------------

    def schema(self, use_case: str | None = None) -> str:
        """Use-case schema name, sandbox-prefixed.

        `ctx.schema()` -> "us1" in shared environments, "jsmith_us1" in a
        sandbox. This is the whole per-developer isolation mechanism.

        Pass `use_case` explicitly only to read ANOTHER use case's data - which
        is legitimate (a shared dimension) but should be deliberate and visible
        at the call site.
        """
        target = use_case or self.use_case
        validate_identifier(target, "use_case")
        return f"{self.schema_prefix}{target}"

    def fq_schema(self, layer: str, use_case: str | None = None) -> str:
        """Fully-qualified `catalog.schema` for a layer."""
        return f"{self.catalog(layer)}.{self.schema(use_case)}"

    def table(self, layer: str, table: str, use_case: str | None = None) -> str:
        """Fully-qualified `catalog.schema.table`.

        ctx.table("curated", "customer") in us1, in prod
            -> edp_curated_prod.us1.customer
        the same call in jsmith's sandbox
            -> edp_curated_nonprod.jsmith_us1.customer
        """
        validate_identifier(table, "table")
        return f"{self.fq_schema(layer, use_case)}.{table}"

    # -- ops -----------------------------------------------------------------

    def ops_schema(self, name: str) -> str:
        """Functional ops schema, sandbox-prefixed like every other schema."""
        if name not in OPS_SCHEMAS:
            raise ConfigError(f"Unknown ops schema: {name!r}. Expected one of {OPS_SCHEMAS}")
        return f"{self.schema_prefix}{name}"

    def ops_table(self, ops_schema: str, table: str) -> str:
        """Fully-qualified ops table.

        ctx.ops_table("audit", "job_run") -> edp_ops_prod.audit.job_run

        Prefixed in a sandbox exactly like data schemas, so a developer seeding
        ops.config cannot overwrite the registry shared nonprod jobs read.
        """
        validate_identifier(table, "table")
        return f"{self.catalog('ops')}.{self.ops_schema(ops_schema)}.{table}"

    # Shorthands for the tables every module touches, so callers never repeat
    # the schema name and a rename happens in one place.
    def audit_table(self, table: str) -> str:
        return self.ops_table("audit", table)

    def config_table(self, table: str) -> str:
        return self.ops_table("config", table)

    def recon_table(self, table: str) -> str:
        return self.ops_table("recon", table)

    # -- volumes -------------------------------------------------------------

    def volume_path(self, layer: str, volume: str, *parts: str) -> str:
        """`/Volumes/...` path, for streaming checkpoints and landed files."""
        validate_identifier(volume, "volume")
        tail = "/".join(p.strip("/") for p in parts if p)
        base = f"/Volumes/{self.catalog(layer)}/{self.schema()}/{volume}"
        return f"{base}/{tail}" if tail else base

    # -- observability -------------------------------------------------------

    def tags(self) -> dict[str, str]:
        """Standard tag set stamped onto audit rows and streaming queries."""
        return {
            "env": self.env,
            "use_case": self.use_case,
            "bundle_target": self.bundle_target,
            "sandbox": str(self.is_sandbox).lower(),
        }


def build_context(params: dict[str, str] | None = None, **overrides: str) -> RuntimeContext:
    """Build a RuntimeContext from a plain dict of job parameters.

    Kept dict-in / object-out so unit tests never need dbutils. Notebook entry
    points call `dbutils.widgets.getAll()` and hand the result straight in.

    Any parameter named `catalog_<layer>` becomes an explicit catalog override,
    for the case where an existing catalog does not follow the naming convention.
    """
    merged: dict[str, str] = dict(params or {})
    merged.update({k: v for k, v in overrides.items() if v is not None})

    missing = [k for k in ("env", "use_case") if not merged.get(k)]
    if missing:
        raise ConfigError(
            f"Missing required job parameter(s): {', '.join(missing)}. "
            "Every job must pass env and use_case from bundle variables - "
            "see docs/04-bundle-authoring.md."
        )

    catalog_overrides = {
        layer: merged[f"catalog_{layer}"].strip()
        for layer in LAYERS
        if merged.get(f"catalog_{layer}")
    }

    known = {
        "env", "use_case", "catalog_prefix", "schema_prefix", "bundle_target",
        "job_id", "run_id", "task_key",
        *(f"catalog_{layer}" for layer in LAYERS),
    }
    return RuntimeContext(
        env=merged["env"].strip(),
        use_case=merged["use_case"].strip(),
        catalog_prefix=(merged.get("catalog_prefix") or "edp").strip(),
        schema_prefix=merged.get("schema_prefix", "").strip(),
        bundle_target=merged.get("bundle_target", "").strip(),
        job_id=merged.get("job_id", ""),
        run_id=merged.get("run_id", ""),
        task_key=merged.get("task_key", ""),
        catalog_overrides=catalog_overrides,
        extra={k: v for k, v in merged.items() if k not in known},
    )


def ensure_schema(spark, ctx: RuntimeContext, layer: str) -> str:
    """Create the sandbox schema for a developer if it does not exist yet.

    Why this exists instead of a `resources.schemas` block in a use-case bundle:
    in `mode: development` DABs prefixes resource names with `[dev jsmith] `,
    which is not a legal Unity Catalog schema name. Shared schemas are therefore
    declared once in the `_platform` bundle, and per-developer sandbox schemas
    are created here at runtime. See docs/08-troubleshooting.md.

    No-ops outside a sandbox: shared environments must never gain a schema as a
    side effect of a job run.
    """
    fq = ctx.fq_schema(layer)
    if not ctx.is_sandbox:
        return fq
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {fq}")
    return fq


def ensure_ops_schema(spark, ctx: RuntimeContext, name: str) -> str:
    """Sandbox equivalent of ensure_schema for the ops catalog."""
    fq = f"{ctx.catalog('ops')}.{ctx.ops_schema(name)}"
    if not ctx.is_sandbox:
        return fq
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {fq}")
    return fq
