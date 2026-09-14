"""The in-cluster kubeconfig — what `entrypoint.sh` did, as a function the suite can run."""
from __future__ import annotations

import yaml

from foundry_task_orchestration_actor import kubeconfig


def _service_account(tmp_path):
    sa = tmp_path / "sa"
    sa.mkdir()
    (sa / "token").write_text("a-projected-token")
    (sa / "ca.crt").write_text("---CA---")
    return sa


def test_it_writes_a_context_papeete_deploy_can_name(tmp_path):
    environ = {"KUBERNETES_SERVICE_HOST": "10.0.0.1", "KUBERNETES_SERVICE_PORT": "443"}
    path, _ = kubeconfig.bootstrap("in-cluster", sa_dir=_service_account(tmp_path),
                                   environ=environ, dest=tmp_path / "kubeconfig")

    assert environ["KUBECONFIG"] == str(path)
    doc = yaml.safe_load(path.read_text())
    assert doc["current-context"] == "in-cluster"
    assert doc["contexts"][0]["name"] == "in-cluster"
    assert doc["clusters"][0]["cluster"]["server"] == "https://10.0.0.1:443"
    assert (path.stat().st_mode & 0o777) == 0o600


def test_the_token_is_named_by_path_so_a_rotation_is_picked_up(tmp_path):
    """The shell script embedded the token it read at start; a projected token rotates."""
    sa = _service_account(tmp_path)
    path, _ = kubeconfig.bootstrap(
        "c", sa_dir=sa, dest=tmp_path / "kubeconfig",
        environ={"KUBERNETES_SERVICE_HOST": "h", "KUBERNETES_SERVICE_PORT": "443"})
    user = yaml.safe_load(path.read_text())["users"][0]["user"]
    assert user == {"tokenFile": str(sa / "token")}
    assert "a-projected-token" not in path.read_text()


def test_an_ipv6_service_host_is_bracketed(tmp_path):
    path, _ = kubeconfig.bootstrap(
        "c", sa_dir=_service_account(tmp_path), dest=tmp_path / "kubeconfig",
        environ={"KUBERNETES_SERVICE_HOST": "fd00::1", "KUBERNETES_SERVICE_PORT": "443"})
    assert yaml.safe_load(path.read_text())["clusters"][0]["cluster"]["server"] == \
        "https://[fd00::1]:443"


def test_outside_a_cluster_it_does_nothing_and_says_so(tmp_path):
    environ: dict = {}
    path, why = kubeconfig.bootstrap("c", sa_dir=tmp_path / "absent", environ=environ,
                                     dest=tmp_path / "kubeconfig")
    assert path is None
    assert "not running in a cluster" in why
    assert "KUBECONFIG" not in environ
    assert not (tmp_path / "kubeconfig").exists()


def test_an_operators_own_kubeconfig_is_left_alone(tmp_path):
    own = tmp_path / "mine"
    own.write_text("apiVersion: v1\n")
    environ = {"KUBECONFIG": str(own), "KUBERNETES_SERVICE_HOST": "h",
               "KUBERNETES_SERVICE_PORT": "443"}
    path, why = kubeconfig.bootstrap("c", sa_dir=_service_account(tmp_path), environ=environ,
                                     dest=tmp_path / "kubeconfig")
    assert path is None
    assert environ["KUBECONFIG"] == str(own)
    assert "left alone" in why
