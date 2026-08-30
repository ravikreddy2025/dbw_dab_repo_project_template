"""Validate the real conf/<use_case>.yml parity definitions.

This is what makes reconciliation trustworthy as a cutover gate: a config that
silently checks nothing would look identical to a passing migration. It reads the
actual files that ship, not fixtures.
"""

from pathlib import Path

import pytest
import yaml
from dab_common.config import build_context
from edp_recon.model import load_recon_config

CONF = Path(__file__).resolve().parents[1] / "conf"
RESOURCES = Path(__file__).resolve().parents[1] / "resources"
CONFIGS = sorted(CONF.glob("*.yml"))
EXPECTED_USE_CASES = {"us1", "us2", "us3", "us4", "us5"}


def _load(path: Path):
    return load_recon_config(yaml.safe_load(path.read_text(encoding="utf-8")))


# -- coverage ----------------------------------------------------------------

def test_every_use_case_has_a_parity_definition():
    """A use case with no config cannot be signed off for cutover - and would
    show up as 'no rows in parity_run', which is easy to mistake for 'fine'."""
    assert {p.stem for p in CONFIGS} == EXPECTED_USE_CASES


def test_every_config_has_a_job():
    """A config with no job never runs. Silently."""
    for path in CONFIGS:
        assert (RESOURCES / f"recon_{path.stem}.job.yml").exists(), (
            f"conf/{path.name} has no resources/recon_{path.stem}.job.yml"
        )


def test_every_job_has_a_config():
    """The reverse: a job whose config is missing fails at runtime, in whichever
    environment runs first."""
    for job in RESOURCES.glob("recon_*.job.yml"):
        use_case = job.name[len("recon_"):-len(".job.yml")]
        assert (CONF / f"{use_case}.yml").exists(), f"{job.name} has no conf/{use_case}.yml"


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_declared_use_case_matches_the_filename(path):
    """The notebook cross-checks these at runtime and refuses to continue if they
    disagree; catching it here means it never reaches a run."""
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["use_case"] == path.stem


# -- content -----------------------------------------------------------------

@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_every_config_is_valid(path):
    """Runs every framework validation over the committed parity definition."""
    plan = _load(path)
    assert plan.check_count > 0


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_both_layers_are_reconciled(path):
    """Checking only the mart hides a curated-layer defect that the mart happens
    to aggregate away."""
    layers = {t.layer for t in _load(path).targets}
    assert {"curated", "datamart"} <= layers, f"{path.stem} only reconciles {layers}"


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_every_target_has_a_row_count_check(path):
    """The cheapest check that catches the largest class of failure."""
    for target in _load(path).targets:
        assert any(c.check_type == "row_count" for c in target.checks), (
            f"{path.stem}.{target.name} has no row_count check"
        )


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_at_least_one_value_level_check_per_use_case(path):
    """Row counts match far more often than contents do. Same number of rows with
    different values is the classic silent migration defect, and only a hash or a
    sum finds it."""
    types = {c.check_type for t in _load(path).targets for c in t.checks}
    assert types & {"column_hash", "column_sum"}, (
        f"{path.stem} has no value-level check - a row count alone would pass a "
        "table whose every value was wrong"
    )


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_every_target_has_an_owner(path):
    for target in _load(path).targets:
        assert target.owner_email, f"{path.stem}.{target.name} has no owner_email"


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_every_tolerance_is_justified(path):
    """Enforced in edp_recon.model too, but asserted here so the failure names the
    file a reviewer has to open."""
    for target in _load(path).targets:
        for check in target.checks:
            if check.tolerance > 0:
                assert check.justification, (
                    f"{path.stem}.{target.name}.{check.name} has tolerance "
                    f"{check.tolerance} with no justification"
                )


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_tolerances_stay_within_a_sane_bound(path):
    """A tolerance above 1% is not a rounding allowance, it is a decision to
    accept different numbers. That needs a conversation, not a config edit."""
    for target in _load(path).targets:
        for check in target.checks:
            assert check.tolerance <= 0.01, (
                f"{path.stem}.{target.name}.{check.name} tolerance {check.tolerance} "
                "exceeds 1% - raise it with QA and the client, not in a PR"
            )


# -- resolution --------------------------------------------------------------

@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_targets_resolve_into_the_right_catalogs(path):
    ctx = build_context({"env": "prod", "use_case": path.stem})
    for target in _load(path).targets:
        resolved = target.resolve_target(ctx)
        assert resolved.startswith(f"edp_{target.layer}_prod.{path.stem}."), resolved


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_a_sandbox_run_defaults_to_the_shared_tables(path):
    """QA authoring a config is the primary sandbox user, and their own schema is
    empty - they never run ETL. Defaulting to the sandbox would compare an empty
    table and report a meaningless mismatch on every run."""
    ctx = build_context({"env": "nonprod", "use_case": path.stem, "schema_prefix": "sam_"})
    for target in _load(path).targets:
        assert ".sam_" not in target.resolve_target(ctx)


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_a_developer_can_opt_in_to_checking_their_own_port(path):
    """The other sandbox user. Explicit, because taking the wrong branch here
    silently reports a PASS about code the developer did not write - a false pass
    on the migration gate."""
    ctx = build_context({
        "env": "nonprod", "use_case": path.stem,
        "schema_prefix": "jsmith_", "upstream_mode": "sandbox",
    })
    for target in _load(path).targets:
        assert f".jsmith_{path.stem}." in target.resolve_target(ctx)


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_sandbox_results_stay_out_of_the_shared_evidence_base(path):
    """Whatever is READ, results are always WRITTEN to the sandbox recon schema,
    so a developer run can never count toward cutover."""
    ctx = build_context({
        "env": "nonprod", "use_case": path.stem,
        "schema_prefix": "jsmith_", "upstream_mode": "sandbox",
    })
    assert ctx.recon_table("parity_run") == "edp_ops_nonprod.jsmith_recon.parity_run"


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.stem)
def test_legacy_source_is_never_a_databricks_data_catalog(path):
    """source_ref must point at the LEGACY side. Pointing it at a Databricks data
    catalog would compare a table against itself and pass forever."""
    for target in _load(path).targets:
        for bad in ("edp_curated_", "edp_datamart_", "edp_landing_"):
            assert bad not in target.source_ref, (
                f"{path.stem}.{target.name}: source_ref {target.source_ref} is a "
                "Databricks data catalog - the comparison would be self-referential"
            )
