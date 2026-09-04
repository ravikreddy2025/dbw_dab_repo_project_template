"""Environment detection: the workspace is the authority on where you are.

The failures these lock down are all silent ones. Nothing here throws a stack
trace in production today - it writes to the wrong catalog and looks fine.
"""

import pytest
from dab_common.config import ConfigError, build_context
from dab_common.environments import (
    catalog_prefix,
    clear_cache,
    detect_environment,
    environment_for_host,
    environments,
    load_config,
    require_environment,
    target_environment,
)

NONPROD = "https://adb-0000000000000001.1.azuredatabricks.net"
PREPROD = "https://adb-0000000000000002.2.azuredatabricks.net"
PROD = "https://adb-0000000000000003.3.azuredatabricks.net"


@pytest.fixture(autouse=True)
def _fresh_config():
    clear_cache()
    yield
    clear_cache()


# -- the config file itself --------------------------------------------------
def test_all_three_environments_are_declared():
    assert set(environments()) == {"nonprod", "preprod", "prod"}


def test_catalog_prefix_comes_from_the_config_not_a_constant():
    assert catalog_prefix() == "edp"


def test_every_environment_has_a_distinct_workspace():
    hosts = [e.workspace_host for e in environments().values()]
    assert len(set(hosts)) == len(hosts), "two environments share a workspace"


def test_no_secret_shaped_values_have_crept_in():
    # A workspace URL is not a secret. Anything that looks like one must not be
    # here - this file is in git and ships inside a wheel.
    raw = str(load_config()).lower()
    for smell in ("password", "secret", "client_secret", "sas=", "token"):
        assert smell not in raw, f"{smell!r} in environments.yml"


# -- host -> environment -----------------------------------------------------
def test_each_host_maps_to_its_environment():
    assert environment_for_host(NONPROD).name == "nonprod"
    assert environment_for_host(PREPROD).name == "preprod"
    assert environment_for_host(PROD).name == "prod"


@pytest.mark.parametrize(
    "variant",
    [
        "adb-0000000000000003.3.azuredatabricks.net",          # no scheme
        "https://adb-0000000000000003.3.azuredatabricks.net/",  # trailing slash
        "https://ADB-0000000000000003.3.AzureDatabricks.net",   # case
    ],
)
def test_host_matching_survives_formatting(variant):
    # spark.conf returns a bare host; DATABRICKS_HOST usually has the scheme.
    assert environment_for_host(variant).name == "prod"


def test_an_undeclared_workspace_raises_rather_than_guessing():
    # The whole point. A default here writes to the wrong catalog while every
    # log line looks completely normal.
    with pytest.raises(ConfigError, match="not declared in environments.yml"):
        environment_for_host("https://adb-9999999999999999.9.azuredatabricks.net")


def test_the_error_names_the_workspaces_it_does_know():
    with pytest.raises(ConfigError) as exc:
        environment_for_host("https://adb-9999999999999999.9.azuredatabricks.net")
    assert "adb-0000000000000003" in str(exc.value)


# -- bundle targets ----------------------------------------------------------
def test_dev_is_a_target_in_nonprod_not_an_environment_of_its_own():
    assert target_environment("dev") == "nonprod"
    assert "dev" not in environments()


def test_each_production_target_maps_to_its_own_environment():
    assert target_environment("nonprod") == "nonprod"
    assert target_environment("preprod") == "preprod"
    assert target_environment("prod") == "prod"


def test_an_unknown_target_raises():
    with pytest.raises(ConfigError, match="Unknown bundle target"):
        target_environment("staging")


# -- detection ---------------------------------------------------------------
class FakeConf:
    def __init__(self, host):
        self._host = host

    def get(self, key):
        if key == "spark.databricks.workspaceUrl":
            return self._host
        raise KeyError(key)


class FakeSpark:
    def __init__(self, host):
        self.conf = FakeConf(host)


def test_detect_reads_the_workspace_from_spark():
    assert detect_environment(FakeSpark(PREPROD)).name == "preprod"


def test_detect_returns_none_when_there_is_no_workspace(monkeypatch):
    # A unit test. Not a misconfiguration - callers fall back to what was
    # declared, never to a default environment.
    monkeypatch.delenv("DATABRICKS_HOST", raising=False)
    assert detect_environment(spark=None) is None


def test_detect_falls_back_to_the_databricks_host_env_var(monkeypatch):
    monkeypatch.setenv("DATABRICKS_HOST", PROD)
    assert detect_environment(spark=None).name == "prod"


def test_detect_still_raises_on_a_reachable_but_undeclared_workspace():
    with pytest.raises(ConfigError, match="not declared"):
        detect_environment(FakeSpark("https://adb-9999999999999999.9.azuredatabricks.net"))


def test_require_environment_refuses_to_proceed_without_an_answer(monkeypatch):
    monkeypatch.delenv("DATABRICKS_HOST", raising=False)
    with pytest.raises(ConfigError, match="Could not determine the workspace"):
        require_environment(spark=None)


# -- the deploy-to-the-wrong-workspace guard ---------------------------------
def test_a_job_declaring_the_wrong_environment_is_refused(monkeypatch):
    # A preprod bundle deployed into the prod workspace. Today this runs to
    # completion and writes preprod catalogs from the prod workspace.
    monkeypatch.setenv("DATABRICKS_HOST", PROD)
    with pytest.raises(ConfigError, match="deployed to the wrong target"):
        build_context({"env": "preprod", "use_case": "us1"})


def test_a_job_in_the_right_workspace_is_fine(monkeypatch):
    monkeypatch.setenv("DATABRICKS_HOST", PROD)
    ctx = build_context({"env": "prod", "use_case": "us1"})
    assert ctx.catalog("curated") == "edp_curated_prod"


def test_the_guard_is_silent_when_there_is_no_workspace(monkeypatch):
    # Every other test in this repo builds a context this way.
    monkeypatch.delenv("DATABRICKS_HOST", raising=False)
    ctx = build_context({"env": "prod", "use_case": "us1"})
    assert ctx.env == "prod"


def test_the_guard_does_not_fire_on_an_undeclared_workspace(monkeypatch):
    # environments.py raises a good error for anyone who asks it directly. Every
    # job failing here instead would bury that message.
    monkeypatch.setenv("DATABRICKS_HOST", "https://adb-9999999999999999.9.azuredatabricks.net")
    ctx = build_context({"env": "prod", "use_case": "us1"})
    assert ctx.env == "prod"


def test_the_guard_can_be_switched_off_explicitly(monkeypatch):
    monkeypatch.setenv("DATABRICKS_HOST", PROD)
    ctx = build_context({"env": "preprod", "use_case": "us1"}, verify_workspace=False)
    assert ctx.env == "preprod"
