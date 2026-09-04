"""Which environment am I in?

THE PROBLEM
-----------
A developer opening a notebook has no job parameters, so something has to tell
the framework which environment it is in. Asking them to type it means the
default is wrong the first time somebody opens a notebook in preprod, and a
wrong environment silently reads and writes the wrong catalog.

THE ANSWER
----------
The workspace already knows. One workspace per environment, so the host IS the
environment, and `environments.yml` is the one place that mapping lives.

    ctx = interactive_context("us1")     # no env argument, ever

WHAT DETECTION IS AND IS NOT AUTHORITATIVE FOR
----------------------------------------------
A DEPLOYED JOB still takes its environment from a job parameter, set by the
bundle target. That is deliberate:

  * unit tests have no workspace, and 260-odd of them depend on being able to
    build a context from a plain dict;
  * a deployment declaring its own target is information worth keeping.

Detection is then used as a GUARD: when a job declares one environment and is
running in another, that is a bundle deployed to the wrong workspace, and it
raises instead of quietly writing somewhere it should not.

FAILING CLOSED
--------------
An unrecognised host raises. It never falls back to nonprod - a default is how
a job ends up writing to the wrong catalog while every log line looks normal.

CACHING
-------
The environment is cached for the session, because a workspace cannot change
underneath you. The CONTEXT is deliberately not cached: it carries `use_case`
and `schema_prefix`, which a notebook may legitimately change halfway through,
and a stale one would only bite the person working on two use cases at once.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from dab_common.config import ConfigError

_CONFIG_FILE = Path(__file__).with_name("environments.yml")

# Spark sets this on every Databricks compute, driver-side. Reading it is a
# dictionary lookup, not an API call.
_WORKSPACE_URL_CONF = "spark.databricks.workspaceUrl"


@dataclass(frozen=True)
class Environment:
    """One physical environment: a workspace, and what it is called."""

    name: str
    workspace_host: str
    description: str = ""


def _normalise_host(host: str) -> str:
    """Compare hosts without tripping over https:// or a trailing slash."""
    host = (host or "").strip().lower()
    for prefix in ("https://", "http://"):
        if host.startswith(prefix):
            host = host[len(prefix):]
    return host.rstrip("/")


@lru_cache(maxsize=1)
def load_config(path: str | None = None) -> dict:
    """Parse environments.yml. Cached - the file cannot change mid-session."""
    source = Path(path) if path else _CONFIG_FILE
    if not source.exists():
        raise ConfigError(
            f"Environment config not found at {source}. It ships inside the "
            "dab_common wheel; if this is a source checkout, reinstall with "
            "`pip install -e libs/dab_common`."
        )
    doc = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not doc.get("environments"):
        raise ConfigError(f"{source} declares no environments.")
    return doc


def catalog_prefix() -> str:
    """First segment of every catalog name."""
    return load_config().get("catalog_prefix", "edp")


def environments() -> dict[str, Environment]:
    """Every declared environment, by name."""
    return {
        name: Environment(
            name=name,
            workspace_host=spec["workspace_host"],
            description=spec.get("description", ""),
        )
        for name, spec in load_config()["environments"].items()
    }


def target_environment(target: str) -> str:
    """Which environment a bundle target deploys into.

    `dev` is a target, not an environment: it deploys into nonprod with a
    per-developer schema prefix.
    """
    targets = load_config().get("targets") or {}
    if target not in targets:
        raise ConfigError(
            f"Unknown bundle target {target!r}. Declared targets: "
            f"{sorted(targets)}. Add it to environments.yml."
        )
    return targets[target]["environment"]


def environment_for_host(host: str) -> Environment:
    """Map a workspace host to its environment, or raise.

    Never guesses. An unknown host means a workspace nobody has declared, and
    continuing would mean reading and writing catalogs chosen by accident.
    """
    wanted = _normalise_host(host)
    for env in environments().values():
        if _normalise_host(env.workspace_host) == wanted:
            return env
    known = ", ".join(_normalise_host(e.workspace_host) for e in environments().values())
    raise ConfigError(
        f"Workspace {wanted!r} is not declared in environments.yml. "
        f"Known workspaces: {known}. Add it there rather than passing env by "
        "hand - a guess here writes to the wrong catalog."
    )


def current_host(spark=None) -> str | None:
    """The workspace host this session is running in, or None if unknowable.

    None is normal: a unit test has no workspace. Callers treat None as "cannot
    detect" and fall back to what was declared, never to a default environment.
    """
    if spark is None:
        try:
            from pyspark.sql import SparkSession

            spark = SparkSession.getActiveSession()
        except Exception:
            spark = None

    if spark is not None:
        try:
            host = spark.conf.get(_WORKSPACE_URL_CONF)
            if host:
                return host
        except Exception:
            pass

    # Set by the CLI and by `databricks auth`; present in a local session.
    return os.environ.get("DATABRICKS_HOST") or None


def detect_environment(spark=None, host: str | None = None) -> Environment | None:
    """The environment this session is in, or None if it cannot be determined.

    Returns None only when there is no workspace to ask. A workspace that IS
    reachable but unrecognised raises - that is a real misconfiguration.
    """
    resolved = host or current_host(spark)
    if not resolved:
        return None
    return environment_for_host(resolved)


def require_environment(spark=None, host: str | None = None) -> Environment:
    """Like detect_environment, but refuses to proceed without an answer."""
    env = detect_environment(spark, host)
    if env is None:
        raise ConfigError(
            "Could not determine the workspace. Pass env explicitly, or set "
            "DATABRICKS_HOST. Guessing an environment is not an option here."
        )
    return env


def clear_cache() -> None:
    """Forget the parsed config. For tests, and for editing the file live."""
    load_config.cache_clear()
