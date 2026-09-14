"""Calling a peer actor's door — plain HTTP, not `HttpMailbox.deliver()`.

WHY NOT `deliver()`. `papeete_actor_synchronous_messaging_http.mailbox.HttpMailbox.deliver()`
hardcodes a 10s socket timeout — fine for a demo actor, wrong here: implement-task and test-task
each run a real `claude` session that can legitimately take many minutes, and round 0's two doors
each run a read-only one. This mirrors `deliver()`'s wire format exactly — `POST <base>/<door-id>`,
body `{"from": <caller>, "payload": {...}}`, a JSON reply — without its timeout. Queries are
POSTed the same way as requests; the verb is the route's, not the caller's.

WHAT A REFUSAL LOOKS LIKE. The binding answers a `Refusal` (an undeclared door, a payload the
card's `request_schema` rejects, a handler that raised) with HTTP 400 and `{"error": ...}`. That,
a connection that never opens, and a reply that is not a JSON object are all `PeerError` — the
caller decides what each means for its run, this module only says it happened and to which door.

WHAT CROSSES THE WIRE BESIDES THE PAYLOAD. The trace. A CLIENT span is opened around the call and
`propagate.inject()` writes it into `traceparent`; the peer's own `HttpMailbox.do_POST` extracts it
into the SERVER span it opens around `Actor.receive()`, and its `correlation.correlation_id()`
reads the same 32 hex digits back out. So one `orchestrate-task` call is one `correlation_id` across
all three actors and every retry, and nothing about correlation travels in a door's body.

The OpenTelemetry API is imported under the same guard `correlation.py` keeps: it arrives with the
`serve` extra's observability backend, and without it there is simply no trace to propagate.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from contextlib import nullcontext

from . import correlation

try:                                              # the same guard correlation.py keeps
    from opentelemetry import propagate, trace
except ImportError:                               # no API installed: nothing to propagate
    propagate = trace = None

# A peer's refusal text goes into a log record and into this door's own reply — bounded, so one
# diagnostic can never approach Loki's `max_line_size` (an oversized line is REJECTED, not trimmed).
DIAGNOSTIC_CHARS = 8000


class PeerError(RuntimeError):
    """A peer's door could not be reached, refused the call, or answered something unusable."""

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


def _span(door: str):
    if trace is None:
        return nullcontext()
    tracer = trace.get_tracer(__name__)
    return tracer.start_as_current_span(f"call {door}", kind=trace.SpanKind.CLIENT)


def call_door(base_url: str, door: str, payload: dict, from_: str, *, timeout: float) -> dict:
    """POST one payload to one peer door and return its reply, or raise `PeerError`."""
    url = f"{base_url.rstrip('/')}/{door}"
    with _span(door):
        headers = {"Content-Type": "application/json"}
        if propagate is not None:
            propagate.inject(headers)
        request = urllib.request.Request(
            url,
            data=json.dumps({"from": from_, "payload": payload}).encode(),
            method="POST",
            headers=headers,
        )
        with correlation.stage(f"call-{door}", url=base_url, timeout_s=timeout):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    body = response.read()
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:DIAGNOSTIC_CHARS]
                raise PeerError(f"{url} refused this call ({e.code}): {detail}",
                                status=e.code) from e
            except urllib.error.URLError as e:
                raise PeerError(f"{url} unreachable: {e.reason}") from e
            except (TimeoutError, OSError) as e:
                # A socket timeout mid-read surfaces as a bare TimeoutError, not a URLError.
                raise PeerError(f"{url} did not answer within {timeout}s: {e}") from e
            try:
                reply = json.loads(body)
            except json.JSONDecodeError as e:
                raise PeerError(f"{url} answered something that is not JSON: "
                                f"{body[:200]!r}") from e
            if not isinstance(reply, dict):
                raise PeerError(f"{url} answered a JSON {type(reply).__name__}, not an object")
            return reply
