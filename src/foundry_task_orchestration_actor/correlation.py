"""Correlation ids — the one place this actor decides what identifies a unit of work.

WHY THIS EXISTS. In the hand-written instance this actor was extracted from, nothing it logged
carried an identity at all — worse, most of what it had to say about a run (a teardown's failures,
a readiness timeout's pod dump, a best-effort PR step that missed) went to `print()`, which reaches
`kubectl logs` and NOTHING else: no OTLP handler, so no Loki, so no dashboard. This module makes
carrying the ids the default rather than a thing each call site has to remember, and its
`stage`/`event` vocabulary is what those `print()`s became.

MOVED VERBATIM from `foundry-implementation-actor`, whose own copy was moved verbatim from the
instance repos before it. Everything below this docstring is byte-identical to that copy: the
record schema is the observability contract three actors' dashboards read with one query, and the
cheapest way to keep three emitters agreeing is to not let them differ.

TWO IDS, AND WHERE EACH COMES FROM.

  * `correlation_id` — THE TRACE ID, not a second identifier invented here. One `orchestrate-task`
    call is one trace: THIS actor's own `peers.call_door` injects W3C `traceparent`, and
    `HttpMailbox.do_POST` extracts it into the SERVER span it opens around `Actor.receive()` — so
    every actor in the pipeline, across every retry attempt, is already inside one trace before a
    line of this module runs. Reading `trace.get_current_span()` and formatting its
    trace id is therefore not "generating a correlation id", it is *naming the one that already
    crossed the wire*. It also means a `correlation_id` in Loki pastes straight into Tempo.
    A locally-minted uuid is the fallback for the no-SDK / no-active-span case only.
  * `task_id` — `TASK-NNN`, off the caller's own payload. Spans retries and every peer, where the
    trace id spans one orchestration run; neither subsumes the other, so both are carried.

A CALL THAT NEVER REACHES A HANDLER still gets a `correlation_id`. `Actor.receive()` refuses an
undeclared door or a schema-violating payload before any handler runs, and the binding answers an
unknown path or an unparseable body without ever calling `receive()` — so nothing of ours binds
for any of them. `CorrelationFilter` therefore falls back to the active span's trace id, which
since ADR-PASH-0005 (`papeete-actor-synchronous-messaging-http` 0.4.0) is open across every one of
those paths. That fallback is the whole reason the binding needed changing: a consumer can stamp
its own records, never the ones written on its behalf before its code ran.

WHY A HANDLER FILTER, NOT `extra=` AT EVERY CALL SITE. A `logging.Filter` on the ROOT LOGGER would
only see records logged directly to it — a record from any named logger reaches the root's
HANDLERS without ever being filtered by the root logger itself. So `install()` attaches to the
handlers, where every record does pass, including ones from code this repo does not own
(`papeete_actor_synchronous_messaging_http.mailbox`'s access log, most importantly). Nothing has
to opt in, which is the whole point: correlation ids everywhere means everywhere, not everywhere
someone remembered.

WHY `bind()` NEVER RESETS. `HttpMailbox` serves on a `ThreadingHTTPServer` — one fresh thread per
request, never pooled — and a `ContextVar` set inside a thread is invisible to every other thread
and dies with it. So request scope is thread scope here, for free, and NOT resetting is what lets
the ids reach the lines emitted *after* the handler returns: `do_POST`'s own access log sits in a
`finally` outside the span, and would otherwise be the one line in the whole request with no
identity. A second `bind()` in the same thread overwrites, which is what an attempt counter wants.
"""
from __future__ import annotations

import contextvars
import json
import logging
import time
import uuid
from contextlib import contextmanager

try:                                       # the same guard papeete-actor-synchronous-messaging-
    from opentelemetry import trace        # http's own _tracing.py keeps: the API is a no-op
except ImportError:                        # without an SDK, and absent entirely in a bare test
    trace = None                           # environment. Neither is an error here.

_FIELDS: contextvars.ContextVar[dict] = contextvars.ContextVar("papeete_correlation", default={})

STEP_LOGGER = "pipeline"


def _trace_id() -> str | None:
    """The active span's trace id, or None — no SDK, no span, or an invalid context."""
    if trace is None:
        return None
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None


def correlation_id() -> str:
    """The active trace id, or a fresh uuid when there is no SDK/span to read one from."""
    return _trace_id() or uuid.uuid4().hex


def current() -> dict:
    return dict(_FIELDS.get())


def bind(**fields) -> None:
    """Add ids to THIS THREAD's correlation context, for the rest of the thread's life.

    Deliberately not a context manager — see this module's own docstring for why the binding
    outliving the handler is the point, not an oversight."""
    _FIELDS.set({**_FIELDS.get(), **{k: v for k, v in fields.items() if v is not None}})


class CorrelationFilter(logging.Filter):
    """Stamps every record passing a handler with the current context's ids.

    Never overwrites: a call site that passed its own `extra={"task_id": ...}` keeps it, so this
    is additive to existing behaviour rather than a replacement for it. The stamped attributes
    are ordinary record attributes, which is precisely what OTel's own `LoggingHandler` turns
    into OTLP log attributes — and Loki, in turn, into structured metadata a `| task_id = "…"`
    matcher filters on with no parser in front of it."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in _FIELDS.get().items():
            if not hasattr(record, key):
                setattr(record, key, value)
        if not hasattr(record, "correlation_id"):
            # NOTHING BOUND — which is precisely the shape of a call the framework turned away
            # before any handler of ours could run: an undeclared door, a payload the card's own
            # `request_schema` rejects, a body that is not JSON. Since ADR-PASH-0005 the HTTP
            # binding holds its SERVER span open across all of those, parented on the caller's
            # own `traceparent`, so the active trace id here is the very value `bind()` would
            # have used. Falling back to it files those records under the run that caused them
            # instead of under nothing at all. `task_id` genuinely cannot be recovered this way
            # and is left absent: a payload the schema rejected may not carry one.
            fallback = _trace_id()
            if fallback:
                record.correlation_id = fallback
        return True


# The ids Loki gets as structured metadata are invisible in `kubectl logs`, which is still the
# first place anyone looks when the telemetry backend itself is what's under suspicion.
CONSOLE_IDS = ("correlation_id", "task_id", "attempt")


class ConsoleFormatter(logging.Formatter):
    """Appends whichever correlation ids a record actually carries.

    A plain `%(task_id)s` in the format string would instead raise `Formatting field not found`
    for every record emitted outside a request — the startup line, anything on a thread that never
    bound — which is exactly how a logging change takes a process down. Nothing is defaulted onto
    the record either: an id that isn't there stays absent, so the OTLP side never carries a
    placeholder value the dashboard would then have to filter back out."""

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        ids = " ".join(
            f"{key}={getattr(record, key)}" for key in CONSOLE_IDS if hasattr(record, key)
        )
        return f"{line} [{ids}]" if ids else line


def install() -> None:
    """Attach the filter to every handler on the root logger.

    Call AFTER `papeete_observability.configure()` and after any console handler is added — this
    walks the handlers that exist at the moment it runs."""
    correlation_filter = CorrelationFilter()
    for handler in logging.getLogger().handlers:
        handler.addFilter(correlation_filter)


# ── the step vocabulary ───────────────────────────────────────────────────────────────────────
#
# One JSON object per step, so the dashboard reads them with the same `| json` it already uses for
# the session transcript, and tells the two apart by `event`. `phase` is start / ok / failed —
# a start line with no matching ok is a step that was still running when the pod went away, which
# a duration-only record could never show.

def _emit(level: int, record: dict) -> None:
    # `level` goes INTO the JSON body as well as onto the record. Severity does reach Loki through
    # OTLP, but exactly which field it lands in is the ingester's business, not something a
    # dashboard query should be pinned to; a `level` the emitter wrote itself is one `| json` away
    # in every backend, which is what the product dashboard's own failure panel filters on.
    body = {"level": logging.getLevelName(level).lower(), **record}
    logging.getLogger(STEP_LOGGER).log(level, json.dumps(body, default=str, ensure_ascii=False))


def event(name: str, *, level: int = logging.INFO, **fields) -> None:
    """A point in the pipeline with no duration — a verdict, a published ref, a PR url.

    `level` is keyword-only — a best-effort step that failed without stopping the run
    (`logging.WARNING`) still reads as one `event` to the dashboard's `| json`, and lands in the
    body as `level` (see `_emit`), so `**fields` must not carry a key of that name."""
    _emit(level, {"event": "event", "step": name, **fields})


@contextmanager
def stage(name: str, **fields):
    """A step with a beginning and an end, logged as both — and as `failed` with the exception's
    own text if it raises, before the exception continues on its way untouched."""
    started = time.monotonic()
    _emit(logging.INFO, {"event": "step", "step": name, "phase": "start", **fields})
    try:
        yield
    except BaseException as e:                                    # noqa: BLE001 — re-raised below
        _emit(logging.ERROR, {
            "event": "step", "step": name, "phase": "failed",
            "duration_ms": round((time.monotonic() - started) * 1000),
            "error": f"{type(e).__name__}: {e}", **fields,
        })
        raise
    _emit(logging.INFO, {
        "event": "step", "step": name, "phase": "ok",
        "duration_ms": round((time.monotonic() - started) * 1000), **fields,
    })
