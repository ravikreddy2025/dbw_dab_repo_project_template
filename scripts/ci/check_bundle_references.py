#!/usr/bin/env python3
"""Repo-wide cross-reference audit.

Catches the class of mistake that no single-file check can see: a pipeline
pointing at a template that was renamed, a doc linking to a file that moved, a
CD pipeline whose bundle folder does not exist, a smoke job that is not defined
in the bundle it is supposed to run.

Every one of these passes YAML parsing and lint, and every one of them fails at
deploy time - which is the worst place to find out.

Usage:  python scripts/ci/check_bundle_references.py
Exit:   0 clean, 1 problems found.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
PIPELINES = REPO / ".azure-pipelines"
BUNDLES = REPO / "bundles"
DOCS = REPO / "docs"

problems: list[str] = []


def err(msg: str) -> None:
    problems.append(msg)


def rel(p: Path) -> str:
    try:
        return str(p.relative_to(REPO))
    except ValueError:
        return str(p)


# ---------------------------------------------------------------------------
# 1. Pipeline `template:` references resolve to real files.
# ---------------------------------------------------------------------------
def check_pipeline_templates() -> None:
    for pipeline in sorted(PIPELINES.rglob("*.yml")):
        text = pipeline.read_text(encoding="utf-8")
        for ref in re.findall(r"^\s*-?\s*template:\s*(\S+)", text, flags=re.M):
            ref = ref.strip().strip("'\"")
            if "@" in ref:      # a template from another repo resource
                continue
            resolved = (pipeline.parent / ref).resolve()
            if not resolved.exists():
                err(f"{rel(pipeline)}: template '{ref}' does not exist")


# ---------------------------------------------------------------------------
# 2. Every `bundlePath:` in a pipeline points at a bundle that exists.
# ---------------------------------------------------------------------------
def check_bundle_paths() -> None:
    for pipeline in sorted(PIPELINES.glob("cd-*.yml")):
        text = pipeline.read_text(encoding="utf-8")
        for ref in set(re.findall(r"bundlePath:\s*(\S+)", text)):
            if "$" in ref or "{{" in ref:
                continue
            if not (REPO / ref / "databricks.yml").exists():
                err(f"{rel(pipeline)}: bundlePath '{ref}' has no databricks.yml")


# ---------------------------------------------------------------------------
# 3. Every `runAfterDeploy:` names a job the bundle actually defines.
#    This is the check that catches a renamed job breaking the smoke run.
# ---------------------------------------------------------------------------
def jobs_defined_in(bundle_dir: Path) -> set[str]:
    keys: set[str] = set()
    for res in (bundle_dir / "resources").glob("*.yml"):
        try:
            doc = yaml.safe_load(res.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue    # reported by validate_bundle_yaml.py
        keys |= set((doc.get("resources") or {}).get("jobs") or {})
    return keys


def check_smoke_jobs() -> None:
    for pipeline in sorted(PIPELINES.glob("cd-*.yml")):
        text = pipeline.read_text(encoding="utf-8")
        paths = set(re.findall(r"bundlePath:\s*(\S+)", text))
        jobs = set(re.findall(r"runAfterDeploy:\s*(\S+)", text))
        if not jobs:
            continue
        defined: set[str] = set()
        for p in paths:
            if "$" not in p and (REPO / p).is_dir():
                defined |= jobs_defined_in(REPO / p)
        for job in jobs:
            if job and job not in defined:
                err(
                    f"{rel(pipeline)}: runAfterDeploy '{job}' is not a job key in "
                    f"{sorted(paths)} (defined: {sorted(defined)})"
                )


# ---------------------------------------------------------------------------
# 4. Every bundle has a CD pipeline, and every CD pipeline has a bundle.
# ---------------------------------------------------------------------------
def check_pipeline_coverage() -> None:
    bundle_dirs = {p.name for p in BUNDLES.iterdir() if (p / "databricks.yml").exists()}
    covered = set()
    for pipeline in PIPELINES.glob("cd-*.yml"):
        for ref in re.findall(r"bundlePath:\s*bundles/(\S+)", pipeline.read_text(encoding="utf-8")):
            covered.add(ref)
    for missing in sorted(bundle_dirs - covered):
        err(f"bundles/{missing} has no cd-*.yml pipeline - it can never be deployed")


# ---------------------------------------------------------------------------
# 5. The pinned CLI version agrees between the pipeline and every bundle.
# ---------------------------------------------------------------------------
def check_cli_version_pin() -> None:
    common = (PIPELINES / "templates" / "vars" / "common.yml").read_text(encoding="utf-8")
    m = re.search(r'DATABRICKS_CLI_VERSION:\s*"([^"]+)"', common)
    if not m:
        err("templates/vars/common.yml: DATABRICKS_CLI_VERSION not found")
        return
    pinned = tuple(int(x) for x in m.group(1).split("."))

    for db in sorted(BUNDLES.glob("*/databricks.yml")):
        text = db.read_text(encoding="utf-8")
        cm = re.search(r'databricks_cli_version:\s*"?>=([0-9.]+)"?', text)
        if not cm:
            err(f"{rel(db)}: no databricks_cli_version pin")
            continue
        required = tuple(int(x) for x in cm.group(1).split("."))
        if pinned < required:
            err(
                f"{rel(db)} requires CLI >={cm.group(1)} but the pipeline installs "
                f"{m.group(1)} - the agent would fail on a feature the bundle needs"
            )


# ---------------------------------------------------------------------------
# 6. Relative links in docs resolve.
# ---------------------------------------------------------------------------
def check_doc_links() -> None:
    if not DOCS.exists():
        return
    for md in sorted(REPO.rglob("*.md")):
        if ".venv" in md.parts or "node_modules" in md.parts:
            continue
        for label, link in re.findall(r"\[([^\]]*)\]\(([^)]+)\)", md.read_text(encoding="utf-8")):
            if link.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target = (md.parent / link.split("#")[0]).resolve()
            if not target.exists():
                err(f"{rel(md)}: broken link [{label}]({link})")


# ---------------------------------------------------------------------------
# 7. No real workspace URL, SP GUID or secret has been committed by accident.
#    Placeholders in this repo follow a fixed shape; anything else is suspicious.
# ---------------------------------------------------------------------------
PLACEHOLDER_HOST = re.compile(r"adb-0+[0-9]\.\d\.azuredatabricks\.net")
ANY_HOST = re.compile(r"adb-\d+\.\d+\.azuredatabricks\.net")


def check_no_real_credentials() -> None:
    for db in sorted(BUNDLES.glob("*/databricks.yml")):
        text = db.read_text(encoding="utf-8")
        for host in set(ANY_HOST.findall(text)):
            if not PLACEHOLDER_HOST.match(host):
                err(f"{rel(db)}: '{host}' looks like a real workspace URL, not a placeholder")
        # A Databricks PAT always starts dapi; one should never be in git.
        if re.search(r"\bdapi[0-9a-f]{32}\b", text):
            err(f"{rel(db)}: contains what looks like a Databricks personal access token")


# ---------------------------------------------------------------------------
# 8. A pipeline must not rebuild on a shared library its bundle does not embed.
#    This is the check that keeps the recon/ETL separation real: without it,
#    someone widening a path filter back to `libs/*` would silently make a QA
#    parity change redeploy production ETL again, and nothing would complain.
# ---------------------------------------------------------------------------
def check_trigger_scope() -> None:
    for pipeline in sorted(PIPELINES.glob("cd-*.yml")):
        text = pipeline.read_text(encoding="utf-8")
        try:
            doc = yaml.safe_load(text) or {}
        except yaml.YAMLError:
            continue
        includes = ((doc.get("trigger") or {}).get("paths") or {}).get("include") or []
        triggered_libs = {
            p.split("/")[1] for p in includes if p.startswith("libs/") and "/" in p[5:]
        }
        if "*" in triggered_libs or any(p == "libs/*" for p in includes):
            err(
                f"{rel(pipeline)}: blanket `libs/*` trigger - name the specific "
                "libraries this bundle embeds, or a change to any framework "
                "redeploys this one"
            )
            continue

        # sharedLibs in the build stage is the declaration of what it embeds.
        m = re.search(r'sharedLibs:\s*"([^"]+)"', text)
        embedded = set(m.group(1).split()) if m else {"dab_common"}

        for extra in sorted(triggered_libs - embedded):
            err(
                f"{rel(pipeline)}: triggers on libs/{extra} but does not embed it "
                f"(embeds: {sorted(embedded)}). A change there would rebuild and "
                "redeploy this bundle for no reason."
            )


# ---------------------------------------------------------------------------
# 9. A use-case job must not read the LANDING layer with ctx.table().
#    table() is sandbox-prefixed, and a developer sandbox landing schema is empty
#    until they personally run the landing pipeline - so the job reads nothing and
#    reports success. Cross-bundle reads use ctx.upstream(), which resolves to the
#    shared schema. See docs/03-developer-guide.md#reading-shared-data.
# ---------------------------------------------------------------------------
def check_upstream_reads() -> None:
    for job in sorted(BUNDLES.glob("us*/src/jobs/*.py")):
        for n, line in enumerate(job.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if 'ctx.table("landing"' in stripped:
                err(
                    f"{rel(job)}:{n}: reads the landing layer with ctx.table(), which is "
                    "sandbox-prefixed and empty in a developer sandbox. Use "
                    "ctx.upstream(\"landing\", ...) - it resolves to the shared schema."
                )


def main() -> int:
    for check in (
        check_pipeline_templates,
        check_bundle_paths,
        check_smoke_jobs,
        check_pipeline_coverage,
        check_cli_version_pin,
        check_doc_links,
        check_no_real_credentials,
        check_trigger_scope,
        check_upstream_reads,
    ):
        check()

    if problems:
        print(f"Cross-reference audit found {len(problems)} problem(s):\n")
        for p in problems:
            print(f"  ERROR  {p}")
        return 1

    print("Cross-reference audit: all references resolve.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
