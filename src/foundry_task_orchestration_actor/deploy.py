"""One attempt's ephemeral namespace — stood up, tested against, and torn down.

WHAT HAPPENS IN IT, IN ORDER.

    clone the implementation read-only, at the branch implement-task pushed
    namespace test-<task_id> + the registry pull Secret copied in from this Pod's own namespace
    the platform stand-in, if the sidecar declares one            (ephemeral.platform)
    for each touched component: its declared Secrets, then the component itself
    for each test image: a Job, its log read back and parsed
    teardown — by label, by name, then the namespace itself

NO DOCKER DAEMON, AND NO TAG IS EVER RE-DERIVED. Every image this module deploys comes straight
out of implement-task's and test-task's own completions, already the exact version each built;
`CapabilityConfig.parse_images` only reads component and kind out of the ref's path segments.
`papeete_deploy.k8s.apply()` is handed that version and the registry repository it lives in, and
rewrites the component's own base manifest onto it through a wrapper kustomization — the manifest
never learns which registry it was deployed against (papeete-deploy's ADR-PD-0006).

WHAT IS THE CAPABILITY'S, AND WHAT IS NOT. The instance this was extracted from hardcoded, in this
module, the Postgres user and database its backend expected, the names of the two Secrets each
component read, and the k8s name prefix every component carried. None of that is generic. The
names are now derived (`config.workload_name`), and the rest is declared by the use —
`ephemeral.platform` is a kustomize folder the use ships beside its sidecar, `ephemeral.secrets`
are templates rendered per component. A capability with no database declares neither.

WHY THE CLONE IS READ-ONLY AND OF `impl/<task_id>`. Only to read a touched component's own
`deployment/dev/k8s/overlays/ephemeral/` — HOW to run it, not WHAT the task is. implement-task
committed on its own branch and opened no PR, so that branch is the only place the overlay matching
the image exists. This actor never writes to the clone and never pushes.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from papeete_deploy import k8s

from . import correlation
from .config import COMPONENT_DEPLOY_FOLDER, EPHEMERAL_RECIPE, CapabilityConfig
from .kubeconfig import NAMESPACE_FILE
from .settings import Settings

# `kubectl describe`/`get pods` output goes into a log record as a field — bounded, so a single
# diagnostic can never approach Loki's own `max_line_size` (an oversized line is REJECTED rather
# than trimmed: the diagnostic would silently not exist).
DIAGNOSTIC_CHARS = 8000

CLONE_TIMEOUT_S = 120
TEST_JOB_POLL_S = 5


class DeployError(RuntimeError):
    """A step of the ephemeral run failed: kubectl, papeete-deploy, the clone, a missing input."""


@dataclass
class TestRun:
    """What one attempt's test Jobs reported, summed across every test image."""

    __test__ = False    # a name pytest would otherwise try to collect from any module importing it

    passed: int = 0
    total: int = 0
    criteria: list[str] = field(default_factory=list)
    logs: dict[str, str] = field(default_factory=dict)

    @property
    def green(self) -> bool:
        return self.total > 0 and self.passed == self.total

    @property
    def verdict(self) -> str:
        return f"{self.passed}/{self.total} criteria passed"


# ── kubectl, directly — for the pieces papeete_deploy.k8s doesn't already cover ─────────────

def kubectl(settings: Settings, *args: str, input: str | None = None,  # noqa: A002
            timeout: int = 60) -> str:
    try:
        result = subprocess.run(
            ["kubectl", "--context", settings.kube_context, *args],
            check=True, capture_output=True, text=True, input=input, timeout=timeout,
        )
    except FileNotFoundError as e:
        raise DeployError("kubectl is not installed in this environment") from e
    except subprocess.CalledProcessError as e:
        raise DeployError(f"kubectl {' '.join(args[:4])} failed: "
                          f"{e.stderr[:DIAGNOSTIC_CHARS]}") from e
    except subprocess.TimeoutExpired as e:
        raise DeployError(f"kubectl {' '.join(args[:4])} timed out after {timeout}s") from e
    return result.stdout


def apply_manifest(settings: Settings, namespace: str, manifest: dict) -> None:
    kubectl(settings, "-n", namespace, "apply", "-f", "-", input=yaml.safe_dump(manifest))


def _labels(run_id: str) -> dict:
    # The same two labels papeete-deploy's wrapper stamps, so `k8s.delete()`'s label sweep and a
    # human's `kubectl get -l` both see what this module created directly as part of the run.
    return {k8s.MANAGED_BY_LABEL: "papeete-deploy", k8s.PRODUCT_LABEL: run_id}


def wait_ready(settings: Settings, namespace: str, name: str) -> None:
    timeout = settings.ready_timeout_s
    try:
        # +30s of Python-level slack over kubectl's own --timeout, so kubectl's deadline is always
        # what actually fires first, not the subprocess's.
        kubectl(settings, "-n", namespace, "rollout", "status", f"deployment/{name}",
                f"--timeout={timeout}s", timeout=timeout + 30)
    except DeployError as e:
        # Captured BEFORE teardown erases the evidence — a bare "never became ready" with no pod or
        # event detail is nearly impossible to debug after the fact. As one correlated record
        # rather than a `print`: `print` reaches `kubectl logs` and stops there, and `kubectl logs`
        # is gone with the pod, which is precisely the moment this dump matters.
        pods = describe = "(unavailable)"
        try:
            pods = kubectl(settings, "-n", namespace, "get", "pods", "-o", "wide")
            describe = kubectl(settings, "-n", namespace, "describe", f"deployment/{name}")
        except DeployError:
            pass
        correlation.event(
            "deployment-diagnostics", level=logging.ERROR, deployment=name, namespace=namespace,
            pods=pods[:DIAGNOSTIC_CHARS], describe=describe[:DIAGNOSTIC_CHARS],
        )
        raise DeployError(f"{name} never became ready in {namespace}: {e}") from e


# ── the clone ────────────────────────────────────────────────────────────────────────────────

def clone_implementation(config: CapabilityConfig, branch: str, task_id: str, token: str) -> Path:
    repo = config.implementation.repo
    dest = Path(tempfile.mkdtemp(prefix=config.clone_prefix(task_id)))
    url = f"https://x-access-token:{token}@github.com/{repo}.git"
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", branch, url, str(dest)],
            check=True, capture_output=True, text=True, timeout=CLONE_TIMEOUT_S,
        )
    except subprocess.CalledProcessError as e:
        shutil.rmtree(dest, ignore_errors=True)
        raise DeployError(f"could not clone {repo}@{branch}: "
                          f"{e.stderr.replace(token, '***')}") from e
    except subprocess.TimeoutExpired as e:
        shutil.rmtree(dest, ignore_errors=True)
        raise DeployError(f"cloning {repo}@{branch} timed out after {CLONE_TIMEOUT_S}s") from e
    return dest


# ── the namespace, and what has to be in it before anything can pull ─────────────────────────

def own_namespace() -> str:
    """This Pod's own namespace — where the registry pull credential lives."""
    try:
        return NAMESPACE_FILE.read_text().strip()
    except OSError as e:
        raise DeployError(f"could not read this pod's own namespace from {NAMESPACE_FILE}: "
                          f"{e}") from e


def deploy_pull_secret(settings: Settings, run_id: str) -> None:
    """Create the namespace, then copy this Pod's own pull Secret into it.

    A Secret is namespaced and has no cluster-wide form, so every `test-<task_id>` needs its own —
    and copying means no second place to rotate: the credential still has exactly one origin.
    Runs before anything else touches the namespace precisely so no pod can start without it, which
    is why it cannot assume the namespace exists. `ensure_namespace()` is idempotent.
    """
    k8s.ensure_namespace(settings.kube_context, run_id)
    source = own_namespace()
    raw = kubectl(settings, "-n", source, "get", "secret", settings.pull_secret, "-o", "json")
    data = json.loads(raw).get("data") or {}
    if not data:
        raise DeployError(f"secret/{settings.pull_secret} in {source} carries no data")
    apply_manifest(settings, run_id, {
        "apiVersion": "v1", "kind": "Secret", "type": "kubernetes.io/dockerconfigjson",
        "metadata": {"name": settings.pull_secret, "namespace": run_id,
                     "labels": _labels(run_id)},
        "data": data,
    })


def deploy_platform(config: CapabilityConfig, settings: Settings, run_id: str) -> list[str]:
    """Apply the use's declared platform stand-in, then wait for every Deployment it rendered.

    The Deployments are discovered by the product label papeete-deploy stamps, not named here: the
    stand-in is the use's folder, and which services it holds is the use's fact. This runs before
    any component is applied, so everything carrying the run's label at this point is platform.
    """
    folder = config.platform_folder
    if folder is None:
        return []
    k8s.apply_product(settings.kube_context, run_id, folder, EPHEMERAL_RECIPE, run_id)
    names = kubectl(settings, "-n", run_id, "get", "deployments",
                    "-l", f"{k8s.PRODUCT_LABEL}={run_id}",
                    "-o", "jsonpath={.items[*].metadata.name}").split()
    for name in names:
        wait_ready(settings, run_id, name)
    return names


def _secret_manifest(run_id: str, name: str, string_data: dict) -> dict:
    return {
        "apiVersion": "v1", "kind": "Secret", "type": "Opaque",
        "metadata": {"name": name, "namespace": run_id, "labels": _labels(run_id)},
        "stringData": string_data,
    }


def deploy_component_secrets(config: CapabilityConfig, settings: Settings, run_id: str,
                             component: str) -> None:
    """Every `ephemeral.secrets` entry, rendered for this component — created directly, never
    through the kustomize wrapper, so each keeps the plain, unprefixed name the component's own
    base manifest references."""
    for secret in config.render_secrets(run_id, component):
        apply_manifest(settings, run_id, _secret_manifest(run_id, secret.name, secret.string_data))


def image_repository(config: CapabilityConfig, settings: Settings, component: str) -> str:
    if not settings.image_registry:
        raise DeployError("no IMAGE_REGISTRY set — cannot say where this component's image should "
                          "be pulled from")
    return config.image_repository(settings.image_registry, component)


def deploy_component(config: CapabilityConfig, settings: Settings, run_id: str, code_clone: Path,
                     component: str, version: str) -> None:
    deploy_folder = code_clone / COMPONENT_DEPLOY_FOLDER.format(component=component)
    k8s.apply(
        settings.kube_context, run_id, deploy_folder, EPHEMERAL_RECIPE,
        config.image_name(component), version, run_id,
        image_repository(config, settings, component),
    )
    wait_ready(settings, run_id, config.prefixed(run_id, config.workload_name(component)))


# ── the test Job ──────────────────────────────────────────────────────────────────────────────

def component_url_env(component: str) -> str:
    """`<COMPONENT>_URL` — the one dev↔test addressing convention in use today. An environment
    variable name cannot carry a hyphen, so one becomes an underscore."""
    return f"{component.upper().replace('-', '_')}_URL"


def test_job_manifest(config: CapabilityConfig, settings: Settings, run_id: str, image: str,
                      components: list[str]) -> dict:
    return {
        "apiVersion": "batch/v1", "kind": "Job",
        "metadata": {"name": config.prefixed(run_id, "test-job"), "namespace": run_id,
                     "labels": _labels(run_id)},
        "spec": {
            "backoffLimit": 0,
            "template": {"spec": {
                "restartPolicy": "Never",
                "imagePullSecrets": [{"name": settings.pull_secret}],
                "containers": [{
                    "name": "test", "image": image,
                    "env": [{"name": component_url_env(c), "value": config.service_url(run_id, c)}
                            for c in components],
                }],
            }},
        },
    }


# pytest's final summary line ("===== 1 failed, 6 passed in 0.42s =====") carries the counts, in
# a punctuation and order that varies by which categories are present — matching each "<N> <word>"
# independently is more robust than one combined pattern. Lowercase "N passed"/"N failed" only
# appears in that summary line; `pytest -v` prints each test's status UPPERCASE.
_SUMMARY_COUNT_RE = re.compile(r"(\d+) (passed|failed)\b")
_FAILED_LINE_RE = re.compile(r"^(\S+) FAILED", re.MULTILINE)


def parse_pytest_log(log: str) -> tuple[int, int, list[str]]:
    """`(passed, total, failed_lines)` off `pytest -v --tb=line` output; each `<nodeid> FAILED`
    line is a criterion the next attempt's remediation context names."""
    counts = {"passed": 0, "failed": 0}
    for count, kind in _SUMMARY_COUNT_RE.findall(log):
        counts[kind] = int(count)
    failed_lines = [f"❌ {m.group(1)}" for m in _FAILED_LINE_RE.finditer(log)]
    return counts["passed"], counts["passed"] + counts["failed"], failed_lines


def run_test_job(config: CapabilityConfig, settings: Settings, run_id: str, image: str,
                 components: list[str]) -> tuple[int, int, list[str], str]:
    """`(passed, total, failed_lines, raw_log)` — the raw log is carried out so a passing run's
    pull requests can show the evidence that earned the pass, not only the count."""
    manifest = test_job_manifest(config, settings, run_id, image, components)
    job = manifest["metadata"]["name"]
    apply_manifest(settings, run_id, manifest)

    deadline = time.monotonic() + settings.test_job_timeout_s
    while time.monotonic() < deadline:
        status = json.loads(kubectl(settings, "-n", run_id, "get", f"job/{job}", "-o", "json"))
        conditions = {c["type"]: c["status"]
                      for c in status.get("status", {}).get("conditions", [])}
        if conditions.get("Complete") == "True" or conditions.get("Failed") == "True":
            break
        time.sleep(TEST_JOB_POLL_S)
    else:
        raise DeployError(f"test Job {job} did not finish within {settings.test_job_timeout_s}s")

    log = kubectl(settings, "-n", run_id, "logs", f"job/{job}")
    passed, total, failed = parse_pytest_log(log)
    return passed, total, failed, log


# ── teardown ─────────────────────────────────────────────────────────────────────────────────

def teardown(config: CapabilityConfig, settings: Settings, run_id: str,
             components: list[str]) -> None:
    """Best-effort throughout — a failed teardown step must never mask this attempt's own verdict,
    and must never stop the rest of teardown from at least being attempted. Each failure is one
    correlated WARNING record, so it reaches Loki under this run's ids and survives the pod."""
    def attempt(what: str, step) -> None:
        try:
            step()
        except Exception as e:  # noqa: BLE001 — deliberately broad, see the docstring
            correlation.event("teardown-failed", level=logging.WARNING, what=what, error=str(e))

    context = settings.kube_context
    attempt(f"k8s.delete({run_id})", lambda: k8s.delete(context, run_id, run_id))
    # k8s.delete()'s label sweep covers all,configmap,ingress — not secrets, which is why the
    # rendered component Secrets and the pull credential are deleted by name.
    names = [s.name for c in components for s in config.render_secrets(run_id, c)]
    for name in [*dict.fromkeys(names), settings.pull_secret]:
        attempt(f"delete secret {name}",
                lambda name=name: kubectl(settings, "-n", run_id, "delete", "secret", name,
                                          "--ignore-not-found"))
    # And the namespace itself. It is genuinely ephemeral — created by this actor, for this
    # attempt, named after the task — which is exactly the case papeete-deploy's ADR-PD-0007 makes
    # deletion opt-in for. Deliberately last: the by-name deletions above are what make a partial
    # teardown legible in the logs, and they cost nothing once the namespace goes.
    attempt(f"delete namespace {run_id}", lambda: k8s.delete_namespace(context, run_id))


# ── one attempt, end to end ──────────────────────────────────────────────────────────────────

def deploy_and_test(config: CapabilityConfig, settings: Settings, *, task_id: str, branch: str,
                    code_images: dict[str, str], test_images: dict[str, str],
                    token: str) -> TestRun:
    """Stand the namespace up, run every test image against it, and always tear it down.

    Raises `DeployError` for any step that fails. papeete-deploy's own `apply()`/`apply_product()`
    raise `subprocess.CalledProcessError` (a failed kubectl) or `ValueError` (a missing overlay)
    directly; both are folded into `DeployError` here so the caller has one thing to catch. Their
    kubectl stderr is not captured by papeete-deploy — it inherits this Pod's stdout — so the
    message says WHICH step failed, and the Pod's logs say why.
    """
    run_id = config.run_id(task_id)
    components = sorted(code_images)
    try:
        with correlation.stage("clone-implementation", repo=config.implementation.repo,
                               branch=branch):
            code_clone = clone_implementation(config, branch, task_id, token)
        try:
            with correlation.stage("deploy-pull-secret", namespace=run_id):
                deploy_pull_secret(settings, run_id)
            if config.platform is not None:
                with correlation.stage("deploy-platform-standin", namespace=run_id):
                    deploy_platform(config, settings, run_id)
            for component in components:
                with correlation.stage("deploy-component", component=component,
                                       namespace=run_id, version=code_images[component]):
                    deploy_component_secrets(config, settings, run_id, component)
                    deploy_component(config, settings, run_id, code_clone, component,
                                     code_images[component])

            # One test image per component the testing actor published for. Each runs against
            # every touched component's URL; a single-component task is the only shape exercised
            # live so far, so the multi-image behaviour is unverified.
            run = TestRun()
            for component, test_version in sorted(test_images.items()):
                image = f"{image_repository(config, settings, component)}/tests:{test_version}"
                with correlation.stage("run-test-job", component=component, image=image):
                    passed, total, failed, log = run_test_job(config, settings, run_id, image,
                                                              components)
                run.passed += passed
                run.total += total
                run.criteria.extend(failed)
                run.logs[component] = log
                correlation.event("component-tested", component=component, passed=passed,
                                  total=total, failed=failed)
            return run
        finally:
            shutil.rmtree(code_clone, ignore_errors=True)
            with correlation.stage("teardown", namespace=run_id):
                teardown(config, settings, run_id, components)
    except (subprocess.CalledProcessError, ValueError) as e:
        raise DeployError(str(e)) from e
