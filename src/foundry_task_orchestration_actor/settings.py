"""`Settings` — how this actor runs, as opposed to which capability it serves.

WHY THIS IS NOT THE SIDECAR. The sidecar declares facts about a capability: its id, its repo, its
components, where its peers live. How many attempts a run makes, how long a door may take to
answer, which kube context to drive and which registry to pull from are facts about an
ENVIRONMENT — the same capability deployed twice may want two answers, and neither answer is
something a capability's owner should have to write into the file that binds it. FIA drew the same
line for its own door timeouts (ADR-FIA-0004, "how long a door may think is operational tuning,
and it is a constructor keyword").

So every field here is a constructor keyword with a default, and `from_env` is how `serve` fills
them from the Pod spec. An embedder passes a `Settings` of its own and never touches the
environment.

WHY THE PEER URLS ARE ALSO HERE. The sidecar's `peers.<role>.url` (or its derived default) says
where a peer lives by convention. `IMPLEMENTATION_URL` / `TESTING_URL` still win when set, because
the deployment manifests of every live use already set them, and because "where is it in THIS
cluster" is the environment's call — the same reason they are env vars in the first place.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, fields, replace

from .config import CapabilityConfig

# Generous. implement-task and test-task each run a headless `claude` session with a 1800s budget
# of its own, plus clone and grounding; this door blocks for the whole call, synchronously, by this
# pipeline's own design (no callback door on either side).
DEFAULT_DOOR_TIMEOUT_S = 2400

# The round-0 doors are read-only sessions bounded well below the implementing ones (FIA's
# assess-task defaults to 600s of session, plus clone and grounding on top).
DEFAULT_PROPOSE_TIMEOUT_S = 900
DEFAULT_ASSESS_TIMEOUT_S = 900

# 600: two live end-to-end runs of the instance this was extracted from timed out waiting for a
# component's readiness at 180s and then 300s, while an isolated repro of the identical deploy
# sequence came up in well under a minute. Unexplained; `deploy.wait_ready` dumps pods and the
# deployment's description on timeout so the next occurrence is diagnosable from its own logs.
DEFAULT_READY_TIMEOUT_S = 600
DEFAULT_TEST_JOB_TIMEOUT_S = 600

DEFAULT_MAX_ATTEMPTS = 3

# The context `kubeconfig.bootstrap` writes when this runs in a Pod. papeete-deploy always shells
# out as `kubectl --context <name>`, so a name has to exist; outside a cluster, point KUBE_CONTEXT
# at one of your own.
DEFAULT_KUBE_CONTEXT = "in-cluster"

# The Secret, in this Pod's own namespace, copied into every ephemeral namespace so a private image
# can be pulled there. Its name is what the component manifests' `imagePullSecrets` already say.
DEFAULT_PULL_SECRET = "acr-pull"

DEFAULT_BASE_BRANCH = "main"


class SettingsError(ValueError):
    """An environment variable was set to something this actor cannot use."""


# field name → environment variable. One table, so `from_env` and the README cannot disagree.
ENV = {
    "max_attempts": "MAX_ATTEMPTS",
    "door_timeout_s": "DOOR_CALL_TIMEOUT_S",
    "propose_timeout_s": "PROPOSE_TIMEOUT_S",
    "assess_timeout_s": "ASSESS_TIMEOUT_S",
    "ready_timeout_s": "DEPLOYMENT_READY_TIMEOUT_S",
    "test_job_timeout_s": "TEST_JOB_TIMEOUT_S",
    "kube_context": "KUBE_CONTEXT",
    "image_registry": "IMAGE_REGISTRY",
    "pull_secret": "IMAGE_PULL_SECRET",
    "base_branch": "BASE_BRANCH",
    "implementation_url": "IMPLEMENTATION_URL",
    "testing_url": "TESTING_URL",
}


@dataclass(frozen=True)
class Settings:
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    door_timeout_s: int = DEFAULT_DOOR_TIMEOUT_S
    propose_timeout_s: int = DEFAULT_PROPOSE_TIMEOUT_S
    assess_timeout_s: int = DEFAULT_ASSESS_TIMEOUT_S
    ready_timeout_s: int = DEFAULT_READY_TIMEOUT_S
    test_job_timeout_s: int = DEFAULT_TEST_JOB_TIMEOUT_S
    kube_context: str = DEFAULT_KUBE_CONTEXT
    image_registry: str = ""
    pull_secret: str = DEFAULT_PULL_SECRET
    base_branch: str = DEFAULT_BASE_BRANCH
    implementation_url: str | None = None
    testing_url: str | None = None

    @classmethod
    def from_env(cls, environ: dict | None = None) -> Settings:
        """Defaults, overridden by whichever of `ENV`'s variables are set and non-empty.

        A non-integer where an integer is expected raises `SettingsError` — at boot, where it is a
        crash-loop with the reason on stdout, rather than at the first attempt.
        """
        environ = os.environ if environ is None else environ
        values: dict = {}
        for field in fields(cls):
            raw = environ.get(ENV[field.name])
            if raw is None or raw == "":
                continue
            if field.type in ("int", int):
                try:
                    value = int(raw)
                except ValueError as e:
                    raise SettingsError(f"{ENV[field.name]}={raw!r} is not an integer") from e
                if value < 1:
                    raise SettingsError(f"{ENV[field.name]}={raw!r} must be at least 1")
                values[field.name] = value
            else:
                values[field.name] = raw
        return replace(cls(), **values)

    def peer_url(self, config: CapabilityConfig, role: str) -> str:
        """Where a peer's doors answer: the environment, else the sidecar, else the convention."""
        override = {"implementation": self.implementation_url, "testing": self.testing_url}[role]
        if override:
            return override.rstrip("/")
        return getattr(config, role).url
