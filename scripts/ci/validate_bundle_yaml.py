#!/usr/bin/env python3
"""Offline structural validation of one bundle.

`databricks bundle validate` is the real check, but it needs a live workspace
connection to resolve lookups - which a PR from an untrusted branch must not
have. This catches the mistakes that do not need a workspace: malformed YAML, a
missing target, a resource pointing at a file that is not in the repo.

Usage:  python scripts/ci/validate_bundle_yaml.py bundles/ingestion
Exit:   0 clean, 1 problems found (each printed with its file).
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

# Every deployable bundle (landing + the five use cases) must offer these. A
# missing `dev` target means developers cannot get a sandbox; a missing shared
# target means the CD pipeline breaks.
REQUIRED_MODULE_TARGETS = {"dev", "nonprod", "preprod", "prod"}
# The platform bundle deliberately has no personal dev target: individual
# developers never deploy shared catalogs.
REQUIRED_PLATFORM_TARGETS = {"nonprod", "preprod", "prod"}

# Resource keys whose value is a path relative to the file that declares it.
PATH_KEYS = ("notebook_path", "path", "file")


def load(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def walk(node, fn, trail=()):
    """Depth-first walk over nested dict/list, calling fn(key, value, trail)."""
    if isinstance(node, dict):
        for k, v in node.items():
            fn(k, v, trail)
            walk(v, fn, (*trail, str(k)))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk(v, fn, (*trail, f"[{i}]"))


def check_bundle(bundle_dir: Path) -> list[str]:
    problems: list[str] = []
    root = bundle_dir / "databricks.yml"

    if not root.exists():
        return [f"{bundle_dir}: no databricks.yml"]

    try:
        cfg = load(root)
    except yaml.YAMLError as exc:
        return [f"{root}: YAML parse error: {exc}"]

    # -- bundle block ---------------------------------------------------------
    if not cfg.get("bundle", {}).get("name"):
        problems.append(f"{root}: bundle.name is required")

    # -- targets --------------------------------------------------------------
    targets = cfg.get("targets") or {}
    is_platform = bundle_dir.name == "_platform"
    required = REQUIRED_PLATFORM_TARGETS if is_platform else REQUIRED_MODULE_TARGETS
    missing = required - set(targets)
    if missing:
        problems.append(f"{root}: missing target(s) {sorted(missing)}")

    defaults = [t for t, v in targets.items() if (v or {}).get("default")]
    if len(defaults) != 1:
        problems.append(f"{root}: exactly one target must be default:true, found {defaults}")

    for name, target in targets.items():
        target = target or {}
        if not (target.get("workspace") or {}).get("host"):
            problems.append(f"{root}: target '{name}' has no workspace.host")
        mode = target.get("mode")
        if name == "dev" and mode != "development":
            problems.append(f"{root}: target 'dev' must be mode:development, got {mode!r}")
        if name in {"nonprod", "preprod", "prod"} and mode != "production":
            problems.append(f"{root}: target '{name}' must be mode:production, got {mode!r}")
        # A production-mode target without run_as would deploy as whoever ran it.
        if mode == "production" and not target.get("run_as"):
            problems.append(f"{root}: target '{name}' is production mode but sets no run_as")

    # -- declared variables ---------------------------------------------------
    declared = set(cfg.get("variables") or {})
    for inc in cfg.get("include", []):
        for path in sorted(bundle_dir.glob(inc)):
            try:
                declared |= set(load(path).get("variables") or {})
            except yaml.YAMLError as exc:
                problems.append(f"{path}: YAML parse error: {exc}")

    # -- resource files -------------------------------------------------------
    resource_files = sorted((bundle_dir / "resources").glob("*.yml"))
    if not resource_files:
        problems.append(f"{bundle_dir}: no resource files under resources/")

    for res_path in resource_files:
        try:
            res = load(res_path)
        except yaml.YAMLError as exc:
            problems.append(f"{res_path}: YAML parse error: {exc}")
            continue

        if "resources" not in res:
            problems.append(f"{res_path}: no top-level `resources:` key")

        def check_node(key, value, trail, _p=res_path):
            if key not in PATH_KEYS or not isinstance(value, str):
                return
            # Only repo-relative paths; workspace and volume paths are absolute.
            if value.startswith(("/", "$", "dbfs:")) or "*" in value:
                return
            resolved = (_p.parent / value).resolve()
            if not resolved.exists():
                problems.append(f"{_p}: {key}: '{value}' does not exist ({resolved})")

        walk(res, check_node)

    # -- every ${var.x} used must be declared ---------------------------------
    import re

    used: set[str] = set()
    for path in [root, *resource_files, *bundle_dir.glob("variables.yml")]:
        used |= set(re.findall(r"\$\{var\.([A-Za-z_][A-Za-z0-9_]*)\}", path.read_text(encoding="utf-8")))
    undeclared = used - declared
    if undeclared:
        problems.append(f"{bundle_dir}: undeclared variable(s) referenced: {sorted(undeclared)}")

    unused = declared - used
    if unused:
        # Not fatal, but it is almost always a leftover from a refactor.
        print(f"  note: {bundle_dir.name} declares unused variable(s): {sorted(unused)}")

    return problems


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2

    all_problems: list[str] = []
    for arg in argv[1:]:
        bundle_dir = Path(arg)
        problems = check_bundle(bundle_dir)
        if problems:
            all_problems.extend(problems)
        else:
            print(f"  OK  {bundle_dir}")

    for p in all_problems:
        print(f"  ERROR  {p}")
    if all_problems:
        print(f"\n{len(all_problems)} problem(s) found.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
