"""The ephemeral run's mechanical parts — with kubectl, git and papeete-deploy replaced.

Nothing here reaches a cluster. What is pinned is what this module decides on its own: the order
the namespace is filled in, what the test Job is told, that a failure still tears down, and that
teardown never stops at its first error.
"""
from __future__ import annotations

import json
import subprocess

import pytest
import yaml

from foundry_task_orchestration_actor import deploy
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
    """Stands in for kubectl, git and papeete_deploy.k8s at once, in one ordered log."""

    def __init__(self, monkeypatch, *, fail_on: str | None = None, job_log: str = "2 passed"):
        self.log: list[str] = []
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
                return "test-task-042-platform-postgres"
            if args[-2:] == ("-o", "json") and "job/" in line:
                return json.dumps({"status": {"conditions": [{"type": "Complete",
                                                              "status": "True"}]}})
            if " logs " in f" {line} ":
                return self.job_log
            if " apply " in f" {line} " and input:
                self.log.append("  applied " + yaml.safe_load(input)["metadata"]["name"])
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


def _run(config):
    return deploy.deploy_and_test(
        config, SETTINGS, task_id="TASK-042", branch="impl/TASK-042",
        code_images={"backend": "0.1.0-a"}, test_images={"backend": "0.1.0-b"}, token="t")


def test_the_namespace_is_filled_in_order_and_torn_down(ephemeral_config, monkeypatch):
    recorder = Recorder(monkeypatch, job_log="tests/test_x.py::test_a PASSED\n2 passed in 1s")
    run = _run(ephemeral_config)

    assert run.green and run.verdict == "2/2 criteria passed"
    steps = [line.split(" ")[0] + " " + " ".join(line.split(" ")[1:3])
             for line in recorder.log if not line.startswith("  applied")]
    order = [
        "clone acme-lab/ACME.PARTS.CAP.SUP.007.WID-implementation@impl/TASK-042",
        "k8s.ensure_namespace test-task-042",
        "kubectl -n foundry-local",                       # read the pull secret
        "kubectl -n test-task-042",                       # copy it in
        "k8s.apply_product test-task-042 ",               # the platform stand-in
    ]
    for expected, actual in zip(order, steps):
        assert actual.startswith(expected.strip()), (expected, recorder.log)
    joined = "\n".join(recorder.log)
    assert joined.index("k8s.apply_product") < joined.index("sup-007-wid-backend-broker") \
        < joined.index("k8s.apply test-task-042")
    assert "rollout status deployment/test-task-042-platform-postgres" in joined
    assert "rollout status deployment/test-task-042-sup-007-wid-backend" in joined
    assert joined.index("logs job/test-task-042-test-job") < joined.index("k8s.delete ")
    assert recorder.log[-1].startswith("k8s.delete_namespace test-task-042")


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
                                        "reg/x/backend/tests:1", ["backend", "api-gateway"])
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    assert container["env"] == [
        {"name": "BACKEND_URL", "value": "http://test-task-042-sup-007-wid-backend"},
        {"name": "API_GATEWAY_URL", "value": "http://test-task-042-sup-007-wid-api-gateway"},
    ]
    assert manifest["metadata"]["name"] == "test-task-042-test-job"
    assert manifest["spec"]["template"]["spec"]["imagePullSecrets"] == [{"name": "acr-pull"}]


def test_the_test_job_also_gets_the_declared_test_env(sidecar_dict, write_sidecar):
    sidecar_dict["ephemeral"] = {"test_env": {"AMQP_URL": "amqp://g:g@{run_id}-platform-mq:5672/"}}
    config = CapabilityConfig.load(write_sidecar(sidecar_dict))
    manifest = deploy.test_job_manifest(config, SETTINGS, "test-task-042", "img", ["backend"])
    assert manifest["spec"]["template"]["spec"]["containers"][0]["env"] == [
        {"name": "BACKEND_URL", "value": "http://test-task-042-sup-007-wid-backend"},
        {"name": "AMQP_URL", "value": "amqp://g:g@test-task-042-platform-mq:5672/"},
    ]


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
