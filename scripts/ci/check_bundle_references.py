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
# 5. The pinned CLI version agrees between the pipeline and every bundle,
#    AND the pipeline actually installs the version it claims to.
#
#    These are two different checks and only having the first is a trap. The
#    original install step piped setup-cli/main/install.sh into sh with a
#    version argument. That script accepts no arguments - VERSION is hardcoded
#    inside it - so the argument was discarded and the agent installed whatever
#    main pinned that day. Comparing numbers across two YAML files "passed"
#    while the number had no bearing on the binary. Check the mechanism, not
#    just the declaration.
# ---------------------------------------------------------------------------
def check_cli_install_honours_pin() -> None:
    step = PIPELINES / "templates" / "steps" / "setup-tooling.yml"
    if not step.exists():
        err("templates/steps/setup-tooling.yml is missing")
        return

    # Comments explain the trap; they must not satisfy the check.
    body = " ".join(
        line for line in step.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )

    if "setup-cli/main" in body:
        err(
            f"{rel(step)}: installs the CLI from setup-cli@main. install.sh takes no "
            "version argument, so the pin is ignored and the agent gets whatever "
            "main points at today. Pin by release tag or download the archive."
        )
    if "$(DATABRICKS_CLI_VERSION)" not in body:
        err(f"{rel(step)}: install step never references DATABRICKS_CLI_VERSION")
    if not ("-v" in body and "exit 1" in body):
        err(
            f"{rel(step)}: no post-install assertion that the installed CLI matches "
            "the pin - a silently wrong version would reach the prod deploy stage"
        )


def check_cli_version_pin() -> None:
    common = (PIPELINES / "templates" / "vars" / "common.yml").read_text(encoding="utf-8")
    m = re.search(r'DATABRICKS_CLI_VERSION:\s*"([^"]+)"', common)
    if not m:
        err("templates/vars/common.yml: DATABRICKS_CLI_VERSION not found")
        return
    pinned = tuple(int(x) for x in m.group(1).split("."))

    targets = sorted(BUNDLES.glob("*/databricks.yml"))
    targets += sorted(REPO.glob("templates/*/template/**/databricks.yml.tmpl"))
    for db in targets:
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
    targets = sorted(BUNDLES.glob("*/databricks.yml"))
    targets += sorted(REPO.glob("templates/*/template/**/databricks.yml.tmpl"))
    for db in targets:
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
        paths = (doc.get("trigger") or {}).get("paths") or {}
        includes = paths.get("include") or []

        # A single `*` does not cross a path separator. `bundles/us1/*` matches
        # bundles/us1/databricks.yml but NOT bundles/us1/src/jobs/curate.py, so
        # the pipeline quietly stops triggering on the code it deploys. The
        # documented form for "this folder and everything under it" is the bare
        # folder name.
        for pattern in includes + (paths.get("exclude") or []):
            if pattern.endswith("/*"):
                err(
                    f"{rel(pipeline)}: path filter '{pattern}' - a single * does not "
                    f"cross '/', so changes in subfolders never trigger it. "
                    f"Use '{pattern[:-2]}'."
                )

        triggered_libs = {
            p.split("/")[1]
            for p in includes
            if p.startswith("libs/") and len(p.split("/")) > 1
        }
        if "*" in triggered_libs or any(p in ("libs", "libs/*") for p in includes):
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
# 9e. The repository map in the front-door docs describes what actually exists.
#
#     README.md still showed reconciliation living inside the use-case bundles
#     months after it was extracted into its own QA-owned bundle, and listed a
#     `recon` module inside dab_common that had never been there. A structure
#     diagram nobody re-reads is the first thing a new joiner trusts.
# ---------------------------------------------------------------------------
MAP_DOCS = ("README.md", "docs/00-START-HERE.md")
# Matches a fenced code block. Built from chr() so no escape survives an edit.
MAP_BLOCK_RE = "```[a-z]*" + chr(92) + "n(.*?)```"


def check_docs_describe_reality() -> None:
    on_disk_bundles = sorted(
        d.name for d in BUNDLES.iterdir()
        if d.is_dir() and (d / "databricks.yml").exists()
    )
    on_disk_libs = sorted(
        d.name for d in (REPO / "libs").iterdir()
        if d.is_dir() and (d / "pyproject.toml").exists()
    )
    doc_count = len(sorted((REPO / "docs").glob("*.md")))

    for name in MAP_DOCS:
        path = REPO / name
        if not path.exists():
            err(f"{name}: missing")
            continue
        # Only the fenced block that IS the map counts. A mention elsewhere in
        # the prose is not a structure diagram.
        blocks = [
            b for b in re.findall(MAP_BLOCK_RE, path.read_text(encoding="utf-8"), re.S)
            if "bundles/" in b and "libs/" in b
        ]
        if not blocks:
            err(f"{name}: no repository map code block found")
            continue
        text = "\n".join(blocks)

        # A map ENTRY, not a mention: `recon/` is a substring of `edp_recon/`,
        # so a plain `in text` silently passes when the entry is deleted.
        entries = {line.strip().split()[0] for line in text.splitlines() if line.strip()}

        for bundle in on_disk_bundles:
            # us2..us5 are covered by a "us1/ .. us5/" range in the map.
            if bundle.startswith("us") and bundle != "us1":
                continue
            if not any(e.startswith(f"{bundle}/") for e in entries):
                err(
                    f"{name}: repository map has no entry for bundles/{bundle}. "
                    "A structure diagram is the first thing a new joiner trusts."
                )
        for lib in on_disk_libs:
            if not any(e.startswith(f"{lib}/") for e in entries):
                err(f"{name}: repository map has no entry for libs/{lib}")

        stale = re.search(
            r"the team reference — (\d+) documents", path.read_text(encoding="utf-8")
        )
        if stale and int(stale.group(1)) != doc_count:
            err(
                f"{name}: claims {stale.group(1)} documents but docs/ holds "
                f"{doc_count}"
            )


# ---------------------------------------------------------------------------
# 9d. Define once. Where DABs makes sharing impossible, enforce sameness.
#
#     Two kinds of unavoidable duplication in a monorepo of bundles:
#
#     SHIMS   notebook_task.notebook_path must resolve inside the bundle root,
#             so a shared notebook cannot exist. The logic lives in dab_common
#             and each bundle keeps a shim - which must be BYTE-IDENTICAL, or
#             "shared" silently becomes five divergent copies again.
#
#     TARGETS databricks.yml has no cross-bundle include. Eight bundles each
#             declare the same four targets. A host or root_path that drifts in
#             one of them deploys that bundle to the wrong workspace, and the
#             only symptom is data appearing somewhere unexpected.
# ---------------------------------------------------------------------------
# The business noun each use case is about. Appears in table and file names, so
# it has to be normalised out before two resource files can be compared.
USE_CASE_DOMAINS = {
    "us1": "orders", "us2": "customers", "us3": "events",
    "us4": "inventory", "us5": "settlement",
}

SHARED_SHIMS = ("src/jobs/apply_migrations.py",)

# Families of per-use-case resource files that must stay identical apart from the
# use-case token. DABs has no cross-bundle or cross-file include for resources, so
# one job per use case means N near-identical files - and the runtime independence
# they buy (each use case schedules, runs and FAILS on its own) is worth keeping.
# What is not worth keeping is silent drift, so the sameness is checked instead.
#
# Deliberate exceptions, each for a stated reason:
RESOURCE_FAMILIES = (
    # (glob, members, reason a member is excluded)
    ("bundles/landing/resources/landing_{uc}.job.yml",
     ("us1", "us3", "us4", "us5"),
     "us2 lands from Oracle, not Kafka - a genuinely different job"),
    ("bundles/landing/resources/kafka_landing_{uc}.pipeline.yml",
     ("us1", "us3", "us4", "us5"),
     "us2 has no Kafka pipeline"),
    ("bundles/recon/resources/recon_{uc}.job.yml",
     ("us1", "us2", "us3", "us4", "us5"),
     ""),
    ("bundles/{uc}/resources/migrate.job.yml",
     ("us1", "us2", "us3", "us4", "us5"),
     ""),
    ("bundles/{uc}/resources/datamart.job.yml",
     ("us1", "us2", "us3", "us4", "us5"),
     ""),
)


def check_resource_families() -> None:
    """Per-use-case resource files must differ ONLY by the use-case token."""
    for pattern, members, _reason in RESOURCE_FAMILIES:
        shapes: dict[str, list[str]] = {}
        for uc in members:
            path = REPO / pattern.format(uc=uc)
            if not path.exists():
                err(f"{pattern.format(uc=uc)}: missing from the family")
                continue
            body = path.read_text(encoding="utf-8").replace(chr(13), "")
            # Normalise EVERY use-case token, not just this file's own: a comment
            # may name a sibling. No word boundaries - the token is embedded in
            # identifiers like recon_us1 and landing_us1.job.yml.
            body = re.sub(r"[uU][sS][1-5]", "UC", body)
            for noun in USE_CASE_DOMAINS.values():
                body = re.sub(rf"{noun}", "DOMAIN", body)
            shapes.setdefault(body, []).append(uc)
        if len(shapes) > 1:
            groups = " vs ".join("+".join(v) for v in shapes.values())
            err(
                f"{pattern.format(uc='<uc>')}: members have drifted ({groups}). "
                "These files must differ only by the use-case name - a change to "
                "one is a change to all. If the difference is deliberate, exclude "
                "that member in RESOURCE_FAMILIES with a reason."
            )


def check_shared_shims() -> None:
    for shim in SHARED_SHIMS:
        seen: dict[str, list[str]] = {}
        for bundle in sorted(BUNDLES.glob("us*/")):
            path = bundle / shim
            if not path.exists():
                continue
            body = path.read_text(encoding="utf-8")
            body = body.replace(chr(13), "")   # CRLF-insensitive
            seen.setdefault(body, []).append(bundle.name)
        if len(seen) > 1:
            groups = " vs ".join("+".join(v) for v in seen.values())
            err(
                f"{shim} has diverged across bundles ({groups}). It is a shim: "
                "the logic belongs in libs/dab_common. Make the copies identical "
                "and put the change in the library."
            )


def check_target_consistency() -> None:
    """Every bundle must agree on what each target IS."""
    reference: dict[str, dict] = {}
    source: dict[str, str] = {}

    for db in sorted(BUNDLES.glob("*/databricks.yml")):
        try:
            doc = yaml.safe_load(db.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        for name, target in (doc.get("targets") or {}).items():
            workspace = target.get("workspace") or {}
            shape = {
                "mode": target.get("mode"),
                "host": workspace.get("host"),
                "root_path": workspace.get("root_path"),
            }
            if name not in reference:
                reference[name], source[name] = shape, rel(db)
                continue
            for key, value in shape.items():
                if reference[name][key] != value:
                    err(
                        f"{rel(db)}: target '{name}' has {key}={value!r} but "
                        f"{source[name]} has {reference[name][key]!r}. Every bundle "
                        "must agree on what a target is - DABs cannot share this, "
                        "so it is checked here instead."
                    )


# ---------------------------------------------------------------------------
# 9f. Every task states a retry policy, or is a kind that owns its own.
#
#     Found by review, not by anything failing: ten sql_tasks had no retry policy
#     while the notebook task in the SAME job did. One transient warehouse blip
#     failed a whole mart build, and the task after it would have retried. Nobody
#     decided that - the field was just never filled in.
#
#     The rule:
#       data-producing task   max_retries: ${var.max_retries}
#       gate / migration      max_retries: 0   (a rerun gives the same answer)
#       pipeline_task         omit - the declarative pipeline owns its retries
#       for_each_task         omit - it goes on the NESTED task
# ---------------------------------------------------------------------------
OWNS_ITS_RETRIES = ("pipeline_task", "for_each_task", "condition_task", "run_job_task")


def check_retry_policy_is_explicit() -> None:
    for path in sorted(BUNDLES.glob("*/resources/*.yml")):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        for job_key, job in ((doc.get("resources") or {}).get("jobs") or {}).items():
            for task in job.get("tasks") or []:
                key = task.get("task_key")
                if any(kind in task for kind in OWNS_ITS_RETRIES):
                    # for_each_task delegates: the nested task must be explicit.
                    nested = (task.get("for_each_task") or {}).get("task")
                    if nested is not None and "max_retries" not in nested:
                        err(
                            f"{rel(path)}: '{key}' fans out to a task with no "
                            "max_retries - the retry policy belongs on the nested task"
                        )
                    continue
                if "max_retries" not in task:
                    err(
                        f"{rel(path)}: job '{job_key}' task '{key}' has no "
                        "max_retries. State it - 0 for a gate, ${var.max_retries} "
                        "for anything that produces data. An unset field is a "
                        "decision nobody made."
                    )


# ---------------------------------------------------------------------------
# 9c. Nothing in a use-case bundle may redefine a table it does not own.
#
#     The curated and datamart tables are created and evolved by <uc>_migrate
#     from src/ddl/. Two idioms silently take that ownership back:
#
#       overwriteSchema=true      replaces the table schema with whatever shape
#                                 the DataFrame has
#       CREATE OR REPLACE TABLE   rebuilds the table from a SELECT
#
#     Either one discards the declared contract AND any applied migration, while
#     ops.config.schema_migration still records the migration as applied. The
#     history table then lies, which is worse than having no history at all.
#
#     Landing is exempt: raw source schemas drift, and mergeSchema there is
#     deliberate. So is a genuine _stg_ staging table.
# ---------------------------------------------------------------------------
def check_no_schema_clobber() -> None:
    for bundle in sorted(BUNDLES.glob("us*/")):
        for path in sorted(bundle.rglob("*.py")) + sorted(bundle.rglob("*.sql")):
            if "ported" in path.parts or path.suffix == ".example":
                continue  # ported/ is lift-and-shift code, quarantined by design
            for num, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if line.lstrip().startswith(("#", "--")):
                    continue
                if "overwriteSchema" in line and "_stg_" not in line:
                    err(
                        f"{rel(path)}:{num}: overwriteSchema replaces the declared "
                        "table shape and undoes applied migrations. The table is "
                        "owned by src/ddl/ - write DATA here."
                    )
                if "CREATE OR REPLACE TABLE" in line.upper():
                    err(
                        f"{rel(path)}:{num}: CREATE OR REPLACE TABLE rebuilds the "
                        "table and discards its declared shape. Use INSERT OVERWRITE "
                        "into the table src/ddl/ created."
                    )


# ---------------------------------------------------------------------------
# 9a. Migrations are well-formed, and every bundle that has them runs them.
#     A bad migration name or a duplicate version is only discovered at deploy
#     time otherwise - which on a release branch means it is discovered in
#     preprod, after the gate, by whoever is on call.
# ---------------------------------------------------------------------------
def check_migrations() -> None:
    import sys

    sys.path.insert(0, str(REPO / "libs" / "dab_common" / "src"))
    try:
        from dab_common.config import ConfigError
        from dab_common.migrate import plan
    except ImportError:  # dab_common not importable; other checks will say so
        return

    for bundle in sorted(BUNDLES.glob("*/")):
        folder = bundle / "src" / "ddl" / "migrations"
        if not folder.is_dir():
            continue

        names = sorted(p.name for p in folder.glob("*.sql"))
        try:
            # applied=[] asks the same question CI can answer offline: are these
            # files internally consistent? Ordering against a real environment
            # is checked by the job itself at deploy time.
            plan(names, applied=[])
        except ConfigError as exc:
            err(f"{rel(folder)}: {exc}")

        if not (folder / "README.md").exists():
            err(f"{rel(folder)}: no README.md explaining the rules for this folder")

        runner = bundle / "src" / "jobs" / "apply_migrations.py"
        if not runner.exists():
            err(f"{rel(bundle)}: has migrations/ but no src/jobs/apply_migrations.py")

        job = bundle / "resources" / "migrate.job.yml"
        if not job.exists():
            err(f"{rel(bundle)}: has migrations/ but no resources/migrate.job.yml")
            continue

        # A migrate job nothing runs is worse than no migrate job: the schema
        # silently never changes and the failure surfaces as a data bug.
        pipeline = PIPELINES / f"cd-{bundle.name}.yml"
        if pipeline.exists():
            text = pipeline.read_text(encoding="utf-8")
            if f"migrateJob: {bundle.name}_migrate" not in text:
                err(
                    f"{rel(pipeline)}: does not pass migrateJob: {bundle.name}_migrate. "
                    "The schema would never be updated in a deployed environment."
                )


# ---------------------------------------------------------------------------
# 9b. No pipeline may carry a YAML `pr:` trigger block.
#     Azure Repos Git ignores YAML PR triggers outright: "For an Azure Repos Git
#     repo, you cannot configure a PR trigger in the YAML file. You need to use
#     branch policies." A `pr:` block therefore LOOKS like it filters branches
#     and paths and does nothing at all - a docs-only PR still runs the whole
#     build, and nothing in the log says why. Filtering belongs on the Build
#     Validation policy (see --path-filter in Az-DevOps-Bootstrap.ps1).
# ---------------------------------------------------------------------------
def check_pr_trigger_not_in_yaml() -> None:
    for pipeline in sorted(PIPELINES.glob("*.yml")):
        try:
            doc = yaml.safe_load(pipeline.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        if isinstance(doc.get("pr"), (dict, list)):
            err(
                f"{rel(pipeline)}: has a YAML `pr:` trigger block. Azure Repos "
                "ignores it, so its branch and path filters silently do nothing. "
                "Use `pr: none` and put the filters on the branch policy."
            )


# ---------------------------------------------------------------------------
# 9. A use-case job must not read the LANDING layer with ctx.table().
#    table() is sandbox-prefixed, and a developer sandbox landing schema is empty
#    until they personally run the landing pipeline - so the job reads nothing and
#    reports success. Cross-bundle reads use ctx.upstream(), which resolves to the
#    shared schema. See docs/03-developer-guide.md#reading-shared-data.
# ---------------------------------------------------------------------------
def check_upstream_reads() -> None:
    # Templates are scanned too. The scaffold shipped `ctx.table("landing", ...)`
    # once - generated code that fails the repo's own CI check is worse than no
    # scaffold, because it teaches the wrong pattern to every new use case.
    targets = sorted(BUNDLES.glob("us*/src/**/*.py"))
    targets += sorted((REPO / "templates").rglob("*.tmpl"))
    for job in targets:
        for n, line in enumerate(job.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if 'ctx.table("landing"' in stripped:
                err(
                    f"{rel(job)}:{n}: reads the landing layer with ctx.table(), which is "
                    "sandbox-prefixed and empty in a developer sandbox. Use "
                    "ctx.upstream(\"landing\", ...) - it resolves to the shared schema. "
                    "See docs/15-sandbox-isolation.md."
                )


def main() -> int:
    for check in (
        check_pipeline_templates,
        check_bundle_paths,
        check_smoke_jobs,
        check_pipeline_coverage,
        check_cli_install_honours_pin,
        check_cli_version_pin,
        check_doc_links,
        check_docs_describe_reality,
        check_no_real_credentials,
        check_migrations,
        check_no_schema_clobber,
        check_retry_policy_is_explicit,
        check_resource_families,
        check_shared_shims,
        check_target_consistency,
        check_trigger_scope,
        check_pr_trigger_not_in_yaml,
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
