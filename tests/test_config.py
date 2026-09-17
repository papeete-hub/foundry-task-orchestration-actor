"""The derivations, the peers, the ephemeral declarations, the settings — and the gate."""
from __future__ import annotations

import pytest

from foundry_task_orchestration_actor.config import (CONTRACT, CapabilityConfig, ConfigError,
                                                     lint)
from foundry_task_orchestration_actor.settings import Settings, SettingsError

from conftest import CAPABILITY, SOURCE_REPO


# ── the renderings ──────────────────────────────────────────────────────────────────────────

def test_every_rendering_derives_from_two_fields(config):
    assert config.capability == CAPABILITY
    assert config.source_repo == SOURCE_REPO
    assert config.actor_name == "ACME.PARTS.CAP.SUP.007.WID-task-orchestration"
    assert config.capability_path == "acme.parts/sup.007.wid"
    assert config.capability_slug == "acme-parts-cap-sup-007-wid"
    assert config.workload_prefix == "sup-007-wid"
    assert config.workload_name("backend") == "sup-007-wid-backend"
    assert config.image_name("backend") == "acme.parts.cap.sup.007.wid-backend"
    assert config.image_repository("reg.example.com/", "backend") == \
        "reg.example.com/acme.parts/sup.007.wid/backend"
    assert config.clone_prefix("TASK-042") == \
        "acme-parts-cap-sup-007-wid-task-orchestration-TASK-042-"
    assert config.run_id("TASK-042") == "test-sup-007-wid-task-042"
    assert config.service_url("test-sup-007-wid-task-042", "backend") == \
        "http://test-sup-007-wid-task-042-sup-007-wid-backend"


def test_parse_images_reads_the_three_way_contract_back_apart(config):
    images = [
        "reg.example.com:5000/acme.parts/sup.007.wid/backend:0.1.0-task-042-abc1234",
        "reg.example.com:5000/acme.parts/sup.007.wid/backend/tests:0.1.0-task-042-def5678",
        "reg.example.com/zenith.cargo/sup.001.man/backend:9.9.9",      # another capability
        "reg.example.com:5000/acme.parts/sup.007.wid/backend",           # no tag: a port colon
        "reg.example.com/acme.parts/sup.007.wid/backend/other:1",         # not a known kind
    ]
    assert config.parse_images(images) == {"backend": "0.1.0-task-042-abc1234"}
    assert config.parse_images(images, test=True) == {"backend": "0.1.0-task-042-def5678"}


def test_capability_without_a_cap_segment_is_refused(sidecar_dict, write_sidecar):
    sidecar_dict["capability"] = "ACME.PARTS.SUP.007.WID"
    with pytest.raises(ConfigError, match="no 'CAP' segment"):
        CapabilityConfig.load(write_sidecar(sidecar_dict))


def test_source_repo_must_be_owner_slash_repo(sidecar_dict, write_sidecar):
    sidecar_dict["source_repo"] = "just-a-name"
    with pytest.raises(ConfigError, match="<owner>/<repo>"):
        CapabilityConfig.load(write_sidecar(sidecar_dict))


# ── components ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("components, match", [
    ([], "empty"),
    ("backend", "list of component names"),
    ([{"name": "backend"}], "list of component names"),
    (["backend", "backend"], "twice"),
    (["Backend_1"], "DNS label"),
])
def test_components_are_a_list_of_distinct_dns_labels(sidecar_dict, write_sidecar,
                                                      components, match):
    sidecar_dict["components"] = components
    with pytest.raises(ConfigError, match=match):
        CapabilityConfig.load(write_sidecar(sidecar_dict))


# ── peers: derived, declared, overridden ────────────────────────────────────────────────────

def test_peers_are_derived_when_not_declared(config):
    assert config.implementation.repo == "acme-lab/ACME.PARTS.CAP.SUP.007.WID-implementation"
    assert config.testing.repo == "acme-lab/ACME.PARTS.CAP.SUP.007.WID-testing"
    assert config.implementation.url == "http://foundry-acme-parts-cap-sup-007-wid-implementation"
    assert config.testing.url == "http://foundry-acme-parts-cap-sup-007-wid-testing"
    assert config.implementation.actor_name == "ACME.PARTS.CAP.SUP.007.WID-implementation"
    assert not config.implementation.declared_repo and not config.implementation.declared_url


def test_a_declared_peer_key_wins_and_the_other_is_still_derived(sidecar_dict, write_sidecar):
    sidecar_dict["peers"] = {"implementation": {"url": "http://impl.local:8080/"},
                             "testing": {"repo": "elsewhere/widget-tests"}}
    config = CapabilityConfig.load(write_sidecar(sidecar_dict))
    assert config.implementation.url == "http://impl.local:8080"
    assert config.implementation.repo == "acme-lab/ACME.PARTS.CAP.SUP.007.WID-implementation"
    assert config.testing.repo == "elsewhere/widget-tests"
    assert config.testing.url == "http://foundry-acme-parts-cap-sup-007-wid-testing"


def test_the_environment_overrides_the_sidecar_and_the_derivation(sidecar_dict, write_sidecar):
    sidecar_dict["peers"] = {"implementation": {"url": "http://declared"}}
    config = CapabilityConfig.load(write_sidecar(sidecar_dict))

    settings = Settings.from_env({"IMPLEMENTATION_URL": "http://from-env:8080/",
                                  "TESTING_URL": ""})
    assert settings.peer_url(config, "implementation") == "http://from-env:8080"
    # An empty variable is unset, not an override to the empty string.
    assert settings.peer_url(config, "testing") == config.testing.url

    assert Settings().peer_url(config, "implementation") == "http://declared"


@pytest.mark.parametrize("peers, match", [
    ({"implementaton": {}}, "unknown role"),
    ({"testing": {"repository": "x/y"}}, "unknown key"),
    ({"testing": {"repo": "no-owner"}}, "<owner>/<repo>"),
    ({"testing": {"url": "foundry-testing"}}, "http"),
    ({"testing": "http://x"}, "mapping"),
])
def test_a_peer_declaration_that_would_silently_fall_back_is_refused(sidecar_dict, write_sidecar,
                                                                     peers, match):
    """A typo'd role or key would otherwise be ignored and the derived default used instead."""
    sidecar_dict["peers"] = peers
    with pytest.raises(ConfigError, match=match):
        CapabilityConfig.load(write_sidecar(sidecar_dict))


# ── ephemeral ───────────────────────────────────────────────────────────────────────────────

SECRETS = [
    {"name": "{workload}-broker",
     "data": {"amqp_url": "amqp://guest:guest@{run_id}-platform-rabbitmq:5672/"}},
    {"name": "{workload}-db",
     "data": {"dsn": "postgresql://widgets:widgets@{run_id}-platform-postgres:5432/widgets"}},
]


def test_secrets_render_per_run_and_component(sidecar_dict, write_sidecar):
    sidecar_dict["ephemeral"] = {"secrets": SECRETS}
    config = CapabilityConfig.load(write_sidecar(sidecar_dict))
    rendered = config.render_secrets("test-task-042", "stub")
    assert [s.name for s in rendered] == ["sup-007-wid-stub-broker", "sup-007-wid-stub-db"]
    assert rendered[0].string_data == {
        "amqp_url": "amqp://guest:guest@test-task-042-platform-rabbitmq:5672/"}


def test_a_typod_placeholder_is_refused_at_load(sidecar_dict, write_sidecar):
    sidecar_dict["ephemeral"] = {"secrets": [{"name": "{workload}-db",
                                              "data": {"dsn": "postgresql://{runid}-pg"}}]}
    with pytest.raises(ConfigError, match=r"\{runid\}"):
        CapabilityConfig.load(write_sidecar(sidecar_dict))


def test_braces_that_are_not_a_placeholder_survive(sidecar_dict, write_sidecar):
    sidecar_dict["ephemeral"] = {"secrets": [{"name": "{workload}-cfg",
                                              "data": {"json": '{"a": 1}'}}]}
    config = CapabilityConfig.load(write_sidecar(sidecar_dict))
    assert config.render_secrets("r", "backend")[0].string_data == {"json": '{"a": 1}'}


def test_a_secret_name_that_is_not_a_k8s_name_is_refused(sidecar_dict, write_sidecar):
    sidecar_dict["ephemeral"] = {"secrets": [{"name": "{capability}-db", "data": {"k": "v"}}]}
    with pytest.raises(ConfigError, match="not a valid Kubernetes object name"):
        CapabilityConfig.load(write_sidecar(sidecar_dict))


def test_a_declared_platform_must_carry_the_ephemeral_overlay(sidecar_dict, write_sidecar,
                                                              tmp_path, platform_folder):
    sidecar_dict["ephemeral"] = {"platform": "platform-standin"}
    folder = write_sidecar(sidecar_dict)
    with pytest.raises(ConfigError, match="k8s/overlays/ephemeral"):
        CapabilityConfig.load(folder)

    platform_folder(root=folder)
    config = CapabilityConfig.load(folder)
    assert config.platform_folder == folder / "platform-standin"


def test_a_platform_path_may_not_escape_the_sidecars_folder(sidecar_dict, write_sidecar):
    sidecar_dict["ephemeral"] = {"platform": "../elsewhere"}
    with pytest.raises(ConfigError, match="relative path"):
        CapabilityConfig.load(write_sidecar(sidecar_dict))


def test_nothing_ephemeral_is_a_valid_declaration(config):
    assert config.platform is None and config.platform_folder is None
    assert config.render_secrets("r", "backend") == []
    assert config.render_test_env("r") == []


def test_test_env_renders_per_run(sidecar_dict, write_sidecar):
    """What a test reaches besides the components — the broker an `event` dataset publishes to."""
    sidecar_dict["ephemeral"] = {"test_env": {
        "AMQP_URL": "amqp://guest:guest@{run_id}-platform-rabbitmq:5672/"}}
    config = CapabilityConfig.load(write_sidecar(sidecar_dict))
    assert config.render_test_env("test-task-042") == [
        ("AMQP_URL", "amqp://guest:guest@test-task-042-platform-rabbitmq:5672/")]


@pytest.mark.parametrize("test_env, match", [
    ({"AMQP_URL": "amqp://{workload}-mq"}, r"\{workload\}"),     # one Job, every component
    ({"amqp_url": "amqp://mq"}, "UPPER_SNAKE_CASE"),
    ({"BACKEND_URL": "http://elsewhere"}, "redefines"),
    ({"AMQP_URL": 5672}, "string template"),
])
def test_a_test_env_that_cannot_mean_one_thing_is_refused(sidecar_dict, write_sidecar, test_env,
                                                         match):
    sidecar_dict["ephemeral"] = {"test_env": test_env}
    with pytest.raises(ConfigError, match=match):
        CapabilityConfig.load(write_sidecar(sidecar_dict))


# ── settings ────────────────────────────────────────────────────────────────────────────────

def test_settings_default_to_the_extracted_instances_values():
    settings = Settings.from_env({})
    assert (settings.max_attempts, settings.door_timeout_s, settings.ready_timeout_s,
            settings.test_job_timeout_s) == (3, 2400, 600, 600)
    assert settings.kube_context == "in-cluster"


def test_settings_read_the_environment():
    settings = Settings.from_env({"MAX_ATTEMPTS": "5", "PROPOSE_TIMEOUT_S": "60",
                                  "KUBE_CONTEXT": "docker-desktop",
                                  "IMAGE_REGISTRY": "reg.example.com"})
    assert settings.max_attempts == 5
    assert settings.propose_timeout_s == 60
    assert settings.kube_context == "docker-desktop"
    assert settings.image_registry == "reg.example.com"


@pytest.mark.parametrize("value", ["three", "0"])
def test_a_bad_integer_fails_at_boot(value):
    with pytest.raises(SettingsError, match="MAX_ATTEMPTS"):
        Settings.from_env({"MAX_ATTEMPTS": value})


# ── the gate ────────────────────────────────────────────────────────────────────────────────

def test_lint_accepts_the_fixture(config, tmp_path):
    report = lint(tmp_path)
    assert report.ok, report.errors
    assert any(CONTRACT in line for line in report.oks)


def test_lint_warns_and_stops_on_an_unmigrated_sidecar(sidecar_dict, write_sidecar):
    sidecar_dict["context"] = "foundry-implementation-actor/agentic-context/v1"
    report = lint(write_sidecar(sidecar_dict))
    assert report.ok
    assert any("UNMIGRATED" in w for w in report.warns)


def test_lint_warns_on_secrets_with_no_platform(sidecar_dict, write_sidecar):
    sidecar_dict["ephemeral"] = {"secrets": SECRETS}
    report = lint(write_sidecar(sidecar_dict))
    assert report.ok
    assert any("no ephemeral.platform" in w for w in report.warns)


def test_lint_reports_a_missing_file(tmp_path):
    report = lint(tmp_path / "nowhere")
    assert not report.ok
    assert "no such file" in report.errors[0]


@pytest.mark.parametrize("key", ["capability", "source_repo", "components"])
def test_each_required_key_is_required(sidecar_dict, write_sidecar, key):
    del sidecar_dict[key]
    assert not lint(write_sidecar(sidecar_dict)).ok
    # (`context` is required too, but a sidecar without one is UNMIGRATED — warned, not failed.)


# ── one attempt's names ─────────────────────────────────────────────────────────────────────

def test_the_same_task_id_in_two_capabilities_gets_two_namespaces(sidecar_dict, write_sidecar):
    first = CapabilityConfig.load(write_sidecar(sidecar_dict))
    sidecar_dict["capability"] = "ACME.PARTS.CAP.BSP.001.SCO"
    sidecar_dict["source_repo"] = "acme-lab/ACME.PARTS.CAP.BSP.001.SCO-task-orchestration"
    second = CapabilityConfig.load(write_sidecar(sidecar_dict))
    assert first.run_id("TASK-003") != second.run_id("TASK-003")
    assert second.run_id("TASK-003") == "test-bsp-001-sco-task-003"


def test_a_task_id_too_long_to_name_its_objects_is_refused(config):
    config.check_task_id("TASK-PAIR-VERIFY-007")
    with pytest.raises(ConfigError, match="at most 63"):
        config.check_task_id("TASK-" + "X" * 60)


def test_a_capability_leaving_no_room_for_a_long_task_id_is_refused(sidecar_dict, write_sidecar):
    sidecar_dict["components"] = ["a-component-name-long-enough-to-exhaust-the-budget"]
    with pytest.raises(ConfigError, match="leave no room"):
        CapabilityConfig.load(write_sidecar(sidecar_dict))


# ── identity: capability + role, not the repo half ──────────────────────────────────────────

def test_the_actor_name_is_exactly_what_the_repo_half_used_to_be(config):
    """The no-op half of the change, pinned — for this actor AND for both of its peers.

    All three names were `repo.partition("/")[2]`. They are `{capability}-{role}` now. Every
    sidecar that exists satisfies `repo == "<owner>/" + capability + "-" + role`, so the two
    derivations agree on all of them — which is what makes this releasable on its own, ahead of
    any repository moving.
    """
    assert config.source_repo == f"acme-lab/{config.capability}-task-orchestration"
    assert config.actor_name == config.source_repo.partition("/")[2]
    for peer in (config.implementation, config.testing):
        assert peer.actor_name == peer.repo.partition("/")[2]


def test_neither_this_actor_nor_its_peers_are_named_after_a_shared_repository(
        sidecar_dict, write_sidecar):
    """The point of the change, and this actor is where it would first be noticed.

    In a consolidated capability repository all three actors share one repo name, so the repo
    half would call all three `ACME.PARTS.CAP.SUP.007.WID`. This actor puts both peers' names in
    the pull request titles it opens (`pulls.py`), which is where "implemented by …WID / tested
    by …WID" would have shown up.
    """
    shared = "acme-lab/ACME.PARTS.CAP.SUP.007.WID"
    sidecar_dict["source_repo"] = shared
    sidecar_dict["peers"] = {"implementation": {"repo": shared}, "testing": {"repo": shared}}
    config = CapabilityConfig.load(write_sidecar(sidecar_dict))

    assert config.actor_name == "ACME.PARTS.CAP.SUP.007.WID-task-orchestration"
    assert config.implementation.actor_name == "ACME.PARTS.CAP.SUP.007.WID-implementation"
    assert config.testing.actor_name == "ACME.PARTS.CAP.SUP.007.WID-testing"
    assert config.implementation.repo == config.testing.repo == shared
    assert len({config.actor_name, config.implementation.actor_name,
                config.testing.actor_name}) == 3


def test_a_peer_name_is_its_role_even_when_its_repo_is_declared_elsewhere(sidecar_dict,
                                                                          write_sidecar):
    """A declared `repo` says where that peer's code lives, not what that peer is called.

    The peer derives its own name from `capability` + its role, so this actor has to agree with
    it rather than read a name back out of a repository it was handed.
    """
    sidecar_dict["peers"] = {"testing": {"repo": "elsewhere/widget-tests"}}
    config = CapabilityConfig.load(write_sidecar(sidecar_dict))
    assert config.testing.repo == "elsewhere/widget-tests"
    assert config.testing.actor_name == "ACME.PARTS.CAP.SUP.007.WID-testing"
