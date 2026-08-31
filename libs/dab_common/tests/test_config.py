"""Config resolution is the mechanism that keeps 10 developers and 5 use cases
out of each other's way, so it gets the most thorough tests in the repo."""

import pytest
from dab_common.config import (
    LAYERS,
    OPS_SCHEMAS,
    ConfigError,
    RuntimeContext,
    build_context,
    current_user_prefix,
    interactive_context,
    validate_identifier,
)

SANDBOX = {"env": "nonprod", "use_case": "us1", "schema_prefix": "jsmith_", "bundle_target": "dev"}
SHARED = {"env": "prod", "use_case": "us1", "bundle_target": "prod"}


# -- construction ------------------------------------------------------------

def test_build_context_from_job_parameters():
    ctx = build_context(SANDBOX)
    assert ctx.env == "nonprod"
    assert ctx.use_case == "us1"
    assert ctx.is_sandbox is True


def test_missing_required_parameter_names_the_parameter():
    with pytest.raises(ConfigError, match="use_case"):
        build_context({"env": "nonprod"})


def test_dev_is_a_bundle_target_not_an_environment():
    """A sandbox lives INSIDE nonprod. Accepting env='dev' would imply a fourth
    set of catalogs that does not exist."""
    with pytest.raises(ConfigError, match="bundle target, not an environment"):
        build_context({"env": "dev", "use_case": "us1"})


def test_unknown_parameters_are_kept_in_extra():
    ctx = build_context({**SANDBOX, "source_id": "ora_customers"})
    assert ctx.extra["source_id"] == "ora_customers"


# -- the four catalogs -------------------------------------------------------

def test_each_layer_resolves_to_its_own_catalog():
    ctx = build_context(SHARED)
    assert ctx.catalog("landing") == "edp_landing_prod"
    assert ctx.catalog("curated") == "edp_curated_prod"
    assert ctx.catalog("datamart") == "edp_datamart_prod"
    assert ctx.catalog("ops") == "edp_ops_prod"


def test_catalogs_carry_the_environment_suffix():
    """One metastore serves all three workspaces, so catalog names must be
    unique metastore-wide. Dropping the suffix would collide."""
    names = {
        env: build_context({"env": env, "use_case": "us1"}).catalog("curated")
        for env in ("nonprod", "preprod", "prod")
    }
    assert len(set(names.values())) == 3


def test_catalog_prefix_is_configurable():
    ctx = build_context({**SHARED, "catalog_prefix": "acme"})
    assert ctx.catalog("curated") == "acme_curated_prod"


def test_unknown_layer_is_rejected():
    with pytest.raises(ConfigError, match="Unknown layer"):
        build_context(SHARED).catalog("bronze")


def test_catalog_can_be_overridden_for_a_legacy_name():
    ctx = build_context({**SHARED, "catalog_landing": "legacy_raw_zone"})
    assert ctx.catalog("landing") == "legacy_raw_zone"
    assert ctx.catalog("curated") == "edp_curated_prod"   # others unaffected


def test_override_for_an_unknown_layer_is_rejected():
    with pytest.raises(ConfigError, match="Unknown layer"):
        RuntimeContext(env="prod", use_case="us1", catalog_overrides={"silver": "x"})


# -- use-case schemas --------------------------------------------------------

def test_shared_environment_uses_the_bare_use_case_schema():
    ctx = build_context(SHARED)
    assert ctx.table("curated", "customer") == "edp_curated_prod.us1.customer"


def test_sandbox_prefixes_the_use_case_schema():
    ctx = build_context(SANDBOX)
    assert ctx.table("curated", "customer") == "edp_curated_nonprod.jsmith_us1.customer"


def test_two_developers_on_the_same_use_case_never_collide():
    """The property the whole shared-dev-workspace strategy depends on."""
    a = build_context({**SANDBOX, "schema_prefix": "jsmith_"})
    b = build_context({**SANDBOX, "schema_prefix": "apatel_"})
    assert a.table("curated", "customer") != b.table("curated", "customer")


def test_two_use_cases_never_collide():
    """The property that lets us1 and us5 share a catalog safely."""
    a = build_context({**SHARED, "use_case": "us1"})
    b = build_context({**SHARED, "use_case": "us5"})
    assert a.table("curated", "customer") != b.table("curated", "customer")


def test_reading_another_use_cases_data_is_possible_but_explicit():
    """Cross-use-case reads are legitimate (a shared dimension) but must be
    visible at the call site rather than implied."""
    ctx = build_context(SHARED)
    assert ctx.table("curated", "calendar", use_case="us3") == "edp_curated_prod.us3.calendar"


def test_cross_use_case_read_still_respects_the_sandbox_prefix():
    ctx = build_context(SANDBOX)
    assert ctx.fq_schema("curated", use_case="us3") == "edp_curated_nonprod.jsmith_us3"


# -- ops catalog -------------------------------------------------------------

def test_ops_schemas_are_functional_not_per_use_case():
    ctx = build_context(SHARED)
    assert ctx.audit_table("job_run") == "edp_ops_prod.audit.job_run"
    assert ctx.config_table("landing_source") == "edp_ops_prod.config.landing_source"
    assert ctx.recon_table("parity_run") == "edp_ops_prod.recon.parity_run"


def test_ops_schemas_are_prefixed_in_a_sandbox_too():
    """THE ONE RULE. If ops.config were shared, a developer seeding their own
    landing sources would overwrite the registry shared nonprod jobs read."""
    ctx = build_context(SANDBOX)
    assert ctx.config_table("landing_source") == "edp_ops_nonprod.jsmith_config.landing_source"


def test_a_sandbox_cannot_reach_the_shared_config_registry():
    sandbox = build_context(SANDBOX).config_table("landing_source")
    shared = build_context({"env": "nonprod", "use_case": "us1"}).config_table("landing_source")
    assert sandbox != shared


def test_unknown_ops_schema_is_rejected():
    with pytest.raises(ConfigError, match="Unknown ops schema"):
        build_context(SHARED).ops_table("scratch", "x")


def test_every_declared_ops_schema_resolves():
    ctx = build_context(SHARED)
    for name in OPS_SCHEMAS:
        assert ctx.ops_table(name, "t").startswith("edp_ops_prod.")


# -- volumes -----------------------------------------------------------------

def test_volume_path_is_scoped_to_layer_and_sandbox():
    ctx = build_context(SANDBOX)
    assert ctx.volume_path("landing", "_checkpoints", "kafka", "orders") == (
        "/Volumes/edp_landing_nonprod/jsmith_us1/_checkpoints/kafka/orders"
    )


def test_volume_paths_differ_per_developer():
    a = build_context({**SANDBOX, "schema_prefix": "jsmith_"}).volume_path("landing", "_checkpoints")
    b = build_context({**SANDBOX, "schema_prefix": "apatel_"}).volume_path("landing", "_checkpoints")
    assert a != b


# -- injection safety --------------------------------------------------------

@pytest.mark.parametrize("bad", ["drop table", "edp-dev", "1catalog", "", "a;b", "cat.alog"])
def test_injection_shaped_identifiers_are_refused(bad):
    with pytest.raises(ConfigError):
        validate_identifier(bad)


def test_use_case_is_validated_at_construction():
    with pytest.raises(ConfigError):
        RuntimeContext(env="prod", use_case="us1; DROP DATABASE x")


def test_table_name_is_validated():
    with pytest.raises(ConfigError):
        build_context(SHARED).table("curated", "t; DROP TABLE y")


# -- tags --------------------------------------------------------------------

def test_tags_identify_the_use_case_and_the_sandbox():
    tags = build_context(SANDBOX).tags()
    assert tags["use_case"] == "us1"
    assert tags["sandbox"] == "true"
    assert tags["bundle_target"] == "dev"


def test_layers_constant_matches_the_catalogs_that_resolve():
    ctx = build_context(SHARED)
    assert {layer: ctx.catalog(layer) for layer in LAYERS}.keys() == set(LAYERS)


# -- writes isolated, reads shared -------------------------------------------
# The asymmetry that makes sandboxes workable on real data volumes. Without it a
# developer curated job reads its own EMPTY landing schema and silently produces
# nothing - which looks exactly like a successful run.

def test_sandbox_reads_upstream_from_the_shared_schema():
    """jsmith has not run the landing pipeline. Reading jsmith_us1 would return
    an empty table and the job would 'succeed' having produced nothing."""
    ctx = build_context(SANDBOX)
    assert ctx.upstream("landing", "kfk_orders") == "edp_landing_nonprod.us1.kfk_orders"


def test_sandbox_writes_to_its_own_schema():
    assert build_context(SANDBOX).table("curated", "orders") == (
        "edp_curated_nonprod.jsmith_us1.orders"
    )


def test_upstream_and_table_differ_only_inside_a_sandbox():
    """In every shared environment the two are the same string, so the same code
    is correct in production with no branch."""
    shared = build_context(SHARED)
    assert shared.upstream("landing", "x") == shared.table("landing", "x")

    sandbox = build_context(SANDBOX)
    assert sandbox.upstream("landing", "x") != sandbox.table("landing", "x")


def test_upstream_mode_sandbox_chains_your_own_output():
    """For when you HAVE materialised your own landing and want to test the
    whole chain end to end."""
    ctx = build_context({**SANDBOX, "upstream_mode": "sandbox"})
    assert ctx.upstream("landing", "kfk_orders") == "edp_landing_nonprod.jsmith_us1.kfk_orders"


def test_upstream_mode_defaults_to_shared():
    assert "jsmith" not in build_context(SANDBOX).upstream("landing", "x")


def test_unknown_upstream_mode_is_rejected():
    """A typo must not silently fall back to reading the wrong schema."""
    with pytest.raises(ConfigError, match="upstream_mode"):
        build_context({**SANDBOX, "upstream_mode": "mine"}).upstream("landing", "x")


def test_upstream_can_cross_use_cases():
    """Reading another use case's shared output - a conformed dimension, say."""
    ctx = build_context(SANDBOX)
    assert ctx.upstream("curated", "calendar", use_case="us3") == (
        "edp_curated_nonprod.us3.calendar"
    )


def test_upstream_validates_identifiers():
    with pytest.raises(ConfigError):
        build_context(SHARED).upstream("curated", "t; DROP TABLE y")


# -- sampling ----------------------------------------------------------------

class _FakeDF:
    def __init__(self, limited=None):
        self.limited = limited

    def limit(self, n):
        return _FakeDF(limited=n)


def test_sample_bounds_a_read_in_a_sandbox():
    ctx = build_context({**SANDBOX, "dev_sample_rows": "100000"})
    assert ctx.sample(_FakeDF()).limited == 100000


def test_sample_is_a_no_op_in_a_shared_environment():
    """A row cap that leaked into prod would silently truncate real output - a
    far worse failure than a slow sandbox."""
    ctx = build_context({**SHARED, "dev_sample_rows": "100000"})
    assert ctx.sample(_FakeDF()).limited is None


def test_sample_is_a_no_op_when_unset():
    assert build_context(SANDBOX).sample(_FakeDF()).limited is None


def test_sample_of_zero_disables_the_cap():
    ctx = build_context({**SANDBOX, "dev_sample_rows": "0"})
    assert ctx.sample(_FakeDF()).limited is None


# ---------------------------------------------------------------------------
# interactive_context - the notebook inner loop, where no job parameters exist
# ---------------------------------------------------------------------------
def test_current_user_prefix_matches_the_bundle_and_the_powershell():
    # All three derivations must agree or a developer tears down the wrong sandbox.
    assert current_user_prefix(user="jsmith@contoso.com") == "jsmith_"
    assert current_user_prefix(user="j.smith@contoso.com") == "j_smith_"
    assert current_user_prefix(user="sp-runner") == "sp_runner_"


def test_current_user_prefix_survives_a_leading_digit():
    assert current_user_prefix(user="7up@contoso.com") == "u_7up_"


def test_interactive_context_isolates_writes_by_default():
    ctx = interactive_context("us1", user="jsmith@contoso.com")
    assert ctx.is_sandbox
    assert ctx.table("curated", "orders") == "edp_curated_nonprod.jsmith_us1.orders"
    # Reads stay shared even in an isolated interactive session.
    assert ctx.upstream("landing", "orders") == "edp_landing_nonprod.us1.orders"


def test_interactive_context_isolated_false_writes_shared():
    ctx = interactive_context("us1", isolated=False)
    assert not ctx.is_sandbox
    assert ctx.table("curated", "orders") == "edp_curated_nonprod.us1.orders"
    assert ctx.upstream("landing", "orders") == "edp_landing_nonprod.us1.orders"


def test_interactive_context_explicit_prefix_wins_over_isolated():
    ctx = interactive_context("us1", isolated=True, schema_prefix="shared_test_")
    assert ctx.table("curated", "orders") == "edp_curated_nonprod.shared_test_us1.orders"


def test_interactive_context_refuses_prod():
    with pytest.raises(ConfigError, match="Refusing to build an interactive context"):
        interactive_context("us1", env="prod", user="jsmith@contoso.com")


def test_interactive_context_allows_reading_preprod():
    ctx = interactive_context("us1", env="preprod", isolated=False)
    assert ctx.upstream("curated", "orders") == "edp_curated_preprod.us1.orders"
