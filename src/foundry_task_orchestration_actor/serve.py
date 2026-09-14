"""The entrypoint — so two uses cannot wire their observability, or their kubeconfig, differently.

WHAT THIS REPLACES. Two hand-written files in the instance repo this was extracted from: an
`app.py` of some sixty lines (observability, a console handler beside the OTLP one, the
correlation filter, an `HttpMailbox`, `Actor.from_card`, an `actor-started` event — not one line
about the capability), and an `entrypoint.sh` that rendered a kubeconfig from the Pod's
ServiceAccount before `exec`ing into it. Both now live once, here, the way `foundry-implementation-
actor serve` took the first of them for its own kind (ADR-FIA-0005, ADR-FTOA-0001).

NO ENGINES, ACTIONS ONLY. This actor's one door names no engine — every decision behind it is a
fixed rule — so `Actor.from_card` is given no `engines=` at all, and `orchestrate-task` is always
called with `judged=None`.

WHY THE KUBECONFIG IS WRITTEN HERE AND NOT BY A SHELL SCRIPT. See `kubeconfig.py`. In short: a use
keeps the base image's CMD, the bootstrap is a tested function, and it names the rotating token by
path instead of embedding the copy that eventually expires. It runs BEFORE `Actor.from_card`, and
never fails the boot: outside a cluster there is nothing to write, and saying so is enough.

WHY THE IMPORTS ARE LAZY. `papeete-actor-synchronous-messaging-http` and `papeete-observability`
are how this actor is CARRIED, and arrive through the `serve` extra. Importing them inside the
function is what lets `foundry-task-orchestration-actor lint` run in a venv that has neither.
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

from . import correlation, kubeconfig
from .config import CapabilityConfig
from .handler import make_orchestrate_task
from .instance import CARD_FILES, render_cards
from .settings import Settings

DEFAULT_PORT = 8080


class ServeError(RuntimeError):
    """Raised when the wire half of this actor is not installed."""


def _imports():
    try:
        from papeete_actor_synchronous_messaging.actor import Actor
        from papeete_actor_synchronous_messaging_http.mailbox import HttpMailbox
        from papeete_observability import configure
    except ImportError as e:  # pragma: no cover - exercised in the image, not the suite
        raise ServeError(
            f"the `serve` extra is not installed: {e}. `foundry-task-orchestration-actor serve` "
            f"needs a mailbox and an observability backend, which are wire concerns this package "
            f"does not depend on by default — install `foundry-task-orchestration-actor[serve]`, "
            f"or use the image this package publishes, which already has."
        ) from e
    return Actor, HttpMailbox, configure


def _cards_for(config: CapabilityConfig, folder: Path) -> Path:
    """The folder `Actor.from_card` is pointed at.

    Rendered at `docker build` time in the normal case, and this finds them already sitting beside
    the sidecar. A use that skipped that step gets them rendered into a tempdir now — the cards are
    derived from the definition either way, so there is no version of this that reads differently.
    """
    if all((folder / name).exists() for name in CARD_FILES):
        return folder
    rendered = Path(tempfile.mkdtemp(prefix=f"{config.actor_slug}-cards-"))
    render_cards(config, rendered)
    correlation.event(
        "cards-rendered", folder=str(rendered), actor=config.actor_name,
        because="the actor's folder carried no cards; render them at build time to have them "
                "beside the sidecar instead",
    )
    return rendered


def serve(folder: str | Path = ".", port: int | None = None) -> None:
    """Boot this actor and answer its door until the process is stopped."""
    Actor, HttpMailbox, configure = _imports()

    configure()
    # papeete_observability.configure() attaches an OTLP log handler to the root logger but never
    # raises its level off the stdlib default WARNING, so logging.info(...) calls (including
    # HttpMailbox's own access log) would otherwise never reach it.
    logging.getLogger().setLevel(logging.INFO)
    # ...and a console handler beside it. The OTLP handler alone is write-only from an operator's
    # seat: with nothing on stdout, `kubectl logs` shows this pod's startup line and nothing else —
    # so a record that never reaches the log backend is indistinguishable from a record that was
    # never emitted, exactly when the telemetry backend is the thing under suspicion. Found the
    # hard way, against an HTTP binding that predated its own OTel wiring and emitted no records at
    # all. `StreamHandler.emit()` flushes per record, so this needs no PYTHONUNBUFFERED.
    #
    # The formatter applies to THIS handler only — the OTLP `LoggingHandler` builds its body from
    # `record.getMessage()`, so what the log backend stores stays the bare message.
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(
        correlation.ConsoleFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logging.getLogger().addHandler(console)
    # ...and, on both handlers, the filter that stamps every record with this request's own
    # correlation ids. On the HANDLERS, not the root logger — see correlation.py's own docstring.
    correlation.install()

    folder = Path(folder)
    # The sidecar is the only thing in that folder this package did not put there (and the
    # platform stand-in it may name, which it resolves and checks against the same folder).
    config = CapabilityConfig.load(folder)
    settings = Settings.from_env()
    cards = _cards_for(config, folder)

    written, why = kubeconfig.bootstrap(settings.kube_context)
    correlation.event("kubeconfig", written=str(written) if written else None, detail=why)

    # PORT, not a hardcoded default: an env var is how an environment moves it without editing an
    # image, and it is what a Service's targetPort is set against.
    port = port if port is not None else int(os.environ.get("PORT", str(DEFAULT_PORT)))
    mailbox = HttpMailbox(port=port)  # 0.0.0.0:<port> — POST /orchestrate-task, plus GET /health
    actor = Actor.from_card(
        cards,
        mailbox=mailbox,
        actions={"orchestrate-task": make_orchestrate_task(config, settings)},
    )
    # An `event` record rather than a `print`: a restart in the middle of a run is one of the most
    # explanatory things a pipeline panel can show, and `print` never reaches the log backend.
    correlation.event("actor-started", actor=actor.name, port=port, capability=config.capability,
                      cards=str(cards),
                      implementation=settings.peer_url(config, "implementation"),
                      testing=settings.peer_url(config, "testing"))
    mailbox.serve_forever()
