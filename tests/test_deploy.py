"""The ephemeral run's mechanical parts — with kubectl, git and papeete-deploy replaced.

Nothing here reaches a cluster. What is pinned is what this module decides on its own: the order
the namespace is filled in, what the test Job is told, that a failure still tears down, and that
teardown never stops at its first error.
"""
from __future__ import annotations

import json
import logging
import subprocess

import pytest
import yaml

from foundry_task_orchestration_actor import correlation, deploy
from foundry_task_orchestration_actor.config import CapabilityConfig
from foundry_task_orchestration_actor.settings import Settings

SETTINGS = Settings(image_registry="reg.example.com", kube_context="ctx", test_job_timeout_s=5)


@pytest.fixture
def ephemeral_config(sidecar_dict, write_sidecar, platform_folder, tmp_path):
    sidecar_dict["ephemeral"] = {
        "platform": "platform-standin",
        "secrets": [{"name": "{workload}-broker",
                     "data": {"amqp_url": "amqp://g:g@{run_id}-platform-rabbitmq:5672/"}}],
    }
    folder = write_sidecar(sidecar_dict)
    platform_folder(root=folder)
    return CapabilityConfig.load(folder)


class Recorder:
    """Stands in for kubectl, git and papeete_deploy.k8s at once, in one ordered log.

    `job_log` is what `kubectl logs` answers: one string for any Job, or a dict keyed by the
    component a Job is named for. The dict form refuses a Job it has no entry for — otherwise a
    two-component run reading one shared log would count the same tests twice and still pass.
    `jobs` keeps every applied Job manifest, in order, and re-applying a Job under a name already
    used with another image is refused, as the API server refuses it: a pod template is immutable.
    """

    def __init__(self, monkeypatch, *, fail_on: str | None = None,
                 job_log: str | dict[str, str] = "2 passed"):
        self.log: list[str] = []
        self.jobs: list[dict] = []
        self.fail_on = fail_on
        self.job_log = job_log

        def kubectl(settings, *args, input=None, timeout=60):         # noqa: A002
            line = "kubectl " + " ".join(args)
            self.log.append(line)
            if self.fail_on and self.fail_on in line:
                raise deploy.DeployError(f"boom: {line}")
            if "get secret" in line:
                return json.dumps({"data": {".dockerconfigjson": "e30="}})
            if "get deployments" in line:
                return "test-sup-007-wid-task-042-platform-postgres"
            if args[-2:] == ("-o", "json") and "job/" in line:
                return json.dumps({"status": {"conditions": [{"type": "Complete",
                                                              "status": "True"}]}})
            if " logs " in f" {line} ":
                if isinstance(self.job_log, str):
                    return self.job_log
                component = line.rpartition("-test-")[2]
                assert component in self.job_log, f"no log for {line!r}"
                return self.job_log[component]
            if " apply " in f" {line} " and input:
                manifest = yaml.safe_load(input)
                self.log.append("  applied " + manifest["metadata"]["name"])
                if manifest["kind"] == "Job":
                    image = manifest["spec"]["template"]["spec"]["containers"][0]["image"]
                    for job in self.jobs:
                        if (job["metadata"]["name"] == manifest["metadata"]["name"]
                                and job["spec"]["template"]["spec"]["containers"][0]["image"]
                                != image):
                            raise deploy.DeployError(f"kubectl -n x apply failed: Job "
                                                     f"{job['metadata']['name']} is invalid: "
                                                     f"spec.template: field is immutable")
                    self.jobs.append(manifest)
            return ""

        def k8s_call(name):
            def _call(*args, **kwargs):
                self.log.append(f"k8s.{name} {' '.join(str(a) for a in args[1:4])}")
                if self.fail_on and self.fail_on == f"k8s.{name}":
                    raise subprocess.CalledProcessError(1, ["kubectl"])
            return _call

        monkeypatch.setattr(deploy, "kubectl", kubectl)
        for name in ("ensure_namespace", "apply", "apply_product", "delete", "delete_namespace"):
            monkeypatch.setattr(deploy.k8s, name, k8s_call(name))
        monkeypatch.setattr(deploy, "own_namespace", lambda: "foundry-local")

        def clone(config, branch, task_id, token):
            self.log.append(f"clone {config.implementation.repo}@{branch}")
            return deploy.Path(deploy.tempfile.mkdtemp())
        monkeypatch.setattr(deploy, "clone_implementation", clone)


def _run(config, components=("backend",)):
    return deploy.deploy_and_test(
        config, SETTINGS, task_id="TASK-042", branch="impl/TASK-042",
        code_images={c: "0.1.0-a" for c in components},
        test_images={c: "0.1.0-b" for c in components}, token="t")


def test_the_namespace_is_filled_in_order_and_torn_down(ephemeral_config, monkeypatch):
    recorder = Recorder(monkeypatch, job_log="tests/test_x.py::test_a PASSED\n2 passed in 1s")
    run = _run(ephemeral_config)

    assert run.green and run.verdict == "2/2 criteria passed — backend 2/2"
    steps = [line.split(" ")[0] + " " + " ".join(line.split(" ")[1:3])
             for line in recorder.log if not line.startswith("  applied")]
    order = [
        "clone acme-lab/ACME.PARTS.CAP.SUP.007.WID-implementation@impl/TASK-042",
        "k8s.ensure_namespace test-sup-007-wid-task-042",
        "kubectl -n foundry-local",                       # read the pull secret
        "kubectl -n test-sup-007-wid-task-042",                       # copy it in
        "k8s.apply_product test-sup-007-wid-task-042 ",               # the platform stand-in
    ]
    for expected, actual in zip(order, steps):
        assert actual.startswith(expected.strip()), (expected, recorder.log)
    joined = "\n".join(recorder.log)
    assert joined.index("k8s.apply_product") < joined.index("sup-007-wid-backend-broker") \
        < joined.index("k8s.apply test-sup-007-wid-task-042")
    assert "rollout status deployment/test-sup-007-wid-task-042-platform-postgres" in joined
    assert "rollout status deployment/test-sup-007-wid-task-042-sup-007-wid-backend" in joined
    assert joined.index("logs job/test-sup-007-wid-task-042-test-backend") \
        < joined.index("k8s.delete ")
    assert recorder.log[-1].startswith("k8s.delete_namespace test-sup-007-wid-task-042")


def test_a_failed_step_still_tears_down(ephemeral_config, monkeypatch):
    recorder = Recorder(monkeypatch, fail_on="k8s.apply")
    with pytest.raises(deploy.DeployError):
        _run(ephemeral_config)
    assert recorder.log[-1].startswith("k8s.delete_namespace")


def test_teardown_keeps_going_past_a_failure(ephemeral_config, monkeypatch):
    recorder = Recorder(monkeypatch, fail_on="delete secret")
    deploy.teardown(ephemeral_config, SETTINGS, "test-task-042", ["backend", "stub"])
    joined = "\n".join(recorder.log)
    assert "delete secret sup-007-wid-backend-broker" in joined
    assert "delete secret sup-007-wid-stub-broker" in joined
    assert "delete secret acr-pull" in joined
    assert recorder.log[-1].startswith("k8s.delete_namespace")


def test_the_test_job_is_told_where_every_touched_component_is(config):
    manifest = deploy.test_job_manifest(config, SETTINGS, "test-task-042",
                                        "reg/x/backend/tests:1", component="backend",
                                        components=["backend", "api-gateway"])
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    assert container["env"] == [
        {"name": "BACKEND_URL", "value": "http://test-task-042-sup-007-wid-backend"},
        {"name": "API_GATEWAY_URL", "value": "http://test-task-042-sup-007-wid-api-gateway"},
    ]
    assert manifest["metadata"]["name"] == "test-task-042-test-backend"
    assert manifest["spec"]["template"]["spec"]["imagePullSecrets"] == [{"name": "acr-pull"}]


def test_the_test_job_also_gets_the_declared_test_env(sidecar_dict, write_sidecar):
    sidecar_dict["ephemeral"] = {"test_env": {"AMQP_URL": "amqp://g:g@{run_id}-platform-mq:5672/"}}
    config = CapabilityConfig.load(write_sidecar(sidecar_dict))
    manifest = deploy.test_job_manifest(config, SETTINGS, "test-task-042", "img",
                                        component="backend", components=["backend"])
    assert manifest["spec"]["template"]["spec"]["containers"][0]["env"] == [
        {"name": "BACKEND_URL", "value": "http://test-task-042-sup-007-wid-backend"},
        {"name": "AMQP_URL", "value": "amqp://g:g@test-task-042-platform-mq:5672/"},
    ]


# ── two components: one Job and one verdict each (ADR-FTOA-0006) ─────────────────────────────

def test_two_components_run_in_two_differently_named_jobs(config, monkeypatch):
    """The bug this pins: one shared Job name, re-applied with the second component's image, is an
    immutable-field rejection from the API server — the second component never ran."""
    recorder = Recorder(monkeypatch, job_log={"backend": "5 passed in 1s", "stub": "3 passed"})
    run = _run(config, components=("backend", "stub"))

    names = [job["metadata"]["name"] for job in recorder.jobs]
    assert names == ["test-sup-007-wid-task-042-test-backend",
                     "test-sup-007-wid-task-042-test-stub"]
    images = [job["spec"]["template"]["spec"]["containers"][0]["image"] for job in recorder.jobs]
    assert images == ["reg.example.com/acme.parts/sup.007.wid/backend/tests:0.1.0-b",
                      "reg.example.com/acme.parts/sup.007.wid/stub/tests:0.1.0-b"]
    # Each Job is still told where EVERY touched component is — a cross-component test needs both.
    for job in recorder.jobs:
        env = {e["name"] for e in job["spec"]["template"]["spec"]["containers"][0]["env"]}
        assert {"BACKEND_URL", "STUB_URL"} <= env

    assert run.green
    assert (run.passed, run.total) == (8, 8)
    assert run.verdict == "8/8 criteria passed — backend 5/5, stub 3/3"


def test_one_red_component_fails_the_attempt_and_both_logs_survive(config, monkeypatch, caplog):
    red = "tests/test_s.py::test_a FAILED\n==== 1 failed, 2 passed in 0.4s ===="
    recorder = Recorder(monkeypatch, job_log={"backend": "5 passed in 1s", "stub": red})
    with caplog.at_level(logging.INFO, logger=correlation.STEP_LOGGER):
        run = _run(config, components=("backend", "stub"))

    assert not run.green
    assert run.verdict == "7/8 criteria passed — backend 5/5, stub 2/3"
    assert run.criteria == ["❌ [stub] tests/test_s.py::test_a"]
    assert run.logs == {"backend": "5 passed in 1s", "stub": red}
    assert len(recorder.jobs) == 2                    # the red one did not stop the other
    # A red attempt opens no pull request, so the correlation record is where its logs survive.
    tested = [json.loads(r.getMessage()) for r in caplog.records
              if r.name == correlation.STEP_LOGGER and '"component-tested"' in r.getMessage()]
    assert {t["component"]: t["log"] for t in tested} == run.logs


def test_a_component_whose_suite_collected_nothing_fails_the_attempt():
    """A behaviour change from 0.5.0, where `5/5 + 0/0` summed to a green `5/5`: a component with
    no tests proves nothing about itself, so it no longer vanishes into the sum."""
    run = deploy.TestRun([deploy.ComponentRun("backend", 5, 5),
                          deploy.ComponentRun("stub", 0, 0)])
    assert run.passed == run.total == 5
    assert not run.green
    assert run.verdict == "5/5 criteria passed — backend 5/5, stub 0/0"


def test_an_empty_run_is_not_green():
    assert not deploy.TestRun().green
    assert deploy.TestRun().verdict == "0/0 criteria passed"


def test_no_platform_declared_means_no_platform_step(config, monkeypatch):
    recorder = Recorder(monkeypatch)
    _run(config)
    assert not any("apply_product" in line for line in recorder.log)


def test_no_registry_is_a_deploy_error(config):
    with pytest.raises(deploy.DeployError, match="IMAGE_REGISTRY"):
        deploy.image_repository(config, Settings(), "backend")


@pytest.mark.parametrize("log, expected", [
    ("a PASSED\nb PASSED\n==== 2 passed in 0.1s ====", (2, 2, [])),
    ("tests/t.py::a FAILED\nb PASSED\n==== 1 failed, 1 passed in 0.4s ====",
     (1, 2, ["❌ tests/t.py::a"])),
    ("collected 0 items", (0, 0, [])),
])
def test_the_pytest_summary_is_read(log, expected):
    assert deploy.parse_pytest_log(log) == expected
