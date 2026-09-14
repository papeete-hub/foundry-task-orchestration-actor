"""foundry-task-orchestration-actor — drive one capability's task from agreement to pull request.

An actor, for one use, with a `papeete-actor` underneath. It holds a three amigos round between
the testing and implementation actors, then drives implement → deploy → black-box test → retry,
and opens the paired pull requests on a pass. It judges nothing: every step is a fixed rule, so its
one door names no engine. The capability it serves is supplied by a sidecar
(`actor-agentic-context.yaml`, `foundry-task-orchestration-actor/agentic-context/v1`), never by
this package.

Wiring one up is three lines:

    from foundry_task_orchestration_actor import CapabilityConfig, make_orchestrate_task

    config = CapabilityConfig.load(".")
    actor = Actor.from_card(".", mailbox=mailbox,
                            actions={"orchestrate-task": make_orchestrate_task(config)})

`make_orchestrate_task` takes an optional `Settings` too; without one it reads the environment.

Those lines are for embedding. A use that just wants the actor writes no Python at all: the image
this package publishes renders the cards from the sidecar and runs `serve`, which is those lines
plus the observability wiring and the in-cluster kubeconfig that used to be copied into every repo.
"""
from .config import (CapabilityConfig, ConfigError, Peer, Report, Secret, SecretTemplate,
                     cards_path, lint, version)
from .deploy import DeployError
from .handler import HandlerError, make_orchestrate_task
from .instance import render_cards
from .peers import PeerError
from .pulls import PullRequestError
from .serve import ServeError, serve
from .settings import Settings, SettingsError
from . import conformance, correlation, deploy, instance, kubeconfig, peers, pulls, round0

__all__ = [
    "CapabilityConfig",
    "ConfigError",
    "DeployError",
    "HandlerError",
    "Peer",
    "PeerError",
    "PullRequestError",
    "Report",
    "Secret",
    "SecretTemplate",
    "ServeError",
    "Settings",
    "SettingsError",
    "cards_path",
    "conformance",
    "correlation",
    "deploy",
    "instance",
    "kubeconfig",
    "lint",
    "make_orchestrate_task",
    "peers",
    "pulls",
    "render_cards",
    "round0",
    "serve",
    "version",
]
