"""Validate the real conf/<use_case>/sources.yml files.

This is the test that makes source onboarding safe: a malformed registry row is
caught by PR validation rather than by a failed 2am load. It reads the actual
files that ship, not fixtures.
"""

from pathlib import Path

import pytest
import yaml
from landing_module.seed import load_seed_file

CONF = Path(__file__).resolve().parents[1] / "conf"
SEED_FILES = sorted(CONF.glob("*/sources.yml"))
EXPECTED_USE_CASES = {"us1", "us2", "us3", "us4", "us5"}


def _load(path: Path):
    return load_seed_file(
        yaml.safe_load(path.read_text(encoding="utf-8")),
        expected_use_case=path.parent.name,
    )


def test_every_use_case_has_a_seed_file():
    """A use case with no sources file would land nothing, silently."""
    assert {p.parent.name for p in SEED_FILES} == EXPECTED_USE_CASES


@pytest.mark.parametrize("path", SEED_FILES, ids=lambda p: p.parent.name)
def test_every_seed_file_is_valid(path):
    """Runs every framework validation over the committed metadata."""
    assert _load(path), f"{path} produced no rows"


@pytest.mark.parametrize("path", SEED_FILES, ids=lambda p: p.parent.name)
def test_declared_use_case_matches_the_folder(path):
    """A mismatch would register sources against the wrong use case, landing one
    team's data in another team's schema."""
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert doc["use_case"] == path.parent.name


def test_source_ids_are_unique_across_all_use_cases():
    """source_id is the primary key of ops.config.landing_source, which is shared
    across use cases - a collision would silently overwrite one source."""
    seen: dict[str, str] = {}
    for path in SEED_FILES:
        for row in _load(path):
            sid = row["source_id"]
            assert sid not in seen, f"{sid} defined in both {seen[sid]} and {path.parent.name}"
            seen[sid] = path.parent.name


def test_target_tables_are_unique_within_a_use_case():
    """Two sources writing the same table in the same schema would race."""
    for path in SEED_FILES:
        targets: dict[str, str] = {}
        for row in _load(path):
            t = row["target_table"]
            assert t not in targets, (
                f"{path.parent.name}: {t} written by both {targets[t]} and {row['source_id']}"
            )
            targets[t] = row["source_id"]


def test_source_ids_are_namespaced_by_use_case():
    """Convention: <use_case>_<system>_<object>. Without the prefix, two use
    cases onboarding a table of the same name collide in the shared registry."""
    for path in SEED_FILES:
        for row in _load(path):
            assert row["source_id"].startswith(f"{path.parent.name}_"), (
                f"{row['source_id']} should start with {path.parent.name}_"
            )


def test_every_active_source_has_an_owner():
    """An unowned source is one nobody gets paged for."""
    for path in SEED_FILES:
        for row in _load(path):
            if row["is_active"]:
                assert row.get("owner_email"), f"{row['source_id']} has no owner_email"


def test_every_active_source_names_a_secret_scope():
    for path in SEED_FILES:
        for row in _load(path):
            if row["is_active"]:
                assert row.get("secret_scope"), f"{row['source_id']} has no secret_scope"


def test_kafka_sources_all_use_cdc_stream():
    """Kafka is an unbounded stream; no other strategy is meaningful."""
    for path in SEED_FILES:
        for row in _load(path):
            if row["source_system"] == "kafka":
                assert row["load_strategy"] == "cdc_stream", row["source_id"]


def test_incremental_sources_declare_a_watermark():
    for path in SEED_FILES:
        for row in _load(path):
            if row["load_strategy"] == "incremental":
                assert row["watermark_column"], f"{row['source_id']} is incremental with no watermark"


def test_secret_scopes_are_ones_the_platform_bundle_creates():
    """A scope the platform bundle does not declare will not exist at runtime."""
    declared = {"edp-kafka", "edp-oracle", "edp-legacy"}
    for path in SEED_FILES:
        for row in _load(path):
            if row.get("secret_scope"):
                assert row["secret_scope"] in declared, (
                    f"{row['source_id']} references undeclared scope {row['secret_scope']}"
                )
