"""`orchestrate_task` — the deterministic, auditable whole of this actor's one door.

Purely mechanical coordination: no code-writing and no test-writing judgement here, so this actor
names no engine and there is no `engine.py`. Every decision this module makes is a fixed rule —
agree the surface or stop, call implement-task, call test-task, deploy an ephemeral namespace, run
the test image against it, retry or stop, open the PRs on a pass — never a `claude` session's.

    round 0            propose-acceptance → assess-task → agreed surface, or refuse (round0.py)
    attempt 1..N       implement-task  (surface, remediation?)          stage `implementation`
                       test-task       (surface, touched components)    stage `testing`
                       deploy + run the test Jobs + teardown (deploy.py) stage `testing`
                       green → open the paired PRs (pulls.py), succeed
                       red   → remediation context from the failing criteria, next attempt
    exhausted          refuse, stage `verdict`

WHAT A FAILURE CARRIES. `orchestration-failed-result` always names its `stage` — `round-0`,
`implementation`, `testing` or `verdict` — so a caller (a human, most often) can tell "the task is
under-specified" from "the implementation actor refused" from "the tests kept failing" without
parsing prose. Whatever is known by then rides along: the branch, the test branch, the agreed
surface; and, for round 0, the open questions, objections and commitments that stopped it. A key
whose value is unknown is OMITTED rather than sent as null — the door's completion schema types
each one, and a null branch is not a string.

WHAT THIS DOES NOT DO. It never writes code or tests — those stay the implementation and testing
actors' jobs, called over their own HTTP doors. It never merges the PRs it opens. It clones the
implementation repository read-only, once per attempt, purely to read a touched component's own
ephemeral overlay (see `deploy.py`).

WHAT IDENTIFIES A RUN. This door mints nothing of its own: `correlation.bind()` names the trace id
`HttpMailbox.do_POST` already opened a SERVER span on, and `peers.call_door` propagates that same
trace to every peer via W3C `traceparent` — so one `orchestrate-task` call is one `correlation_id`
across all three actors, round 0 and every retry, while `task_id` spans them and `attempt`
distinguishes the retries.

A REFUSAL VERSUS A FAILED RESULT. A missing `GITHUB_TOKEN` raises `HandlerError`, which
`Actor.receive()` turns into a `Refusal` (HTTP 400): the actor is misconfigured, nothing about the
task was learned, and nothing should be spent finding that out. Everything that happens to a task
once it is underway is an `orchestration-failed-result` instead — an answer, with a stage.
"""
from __future__ import annotations

import logging
import os

from . import correlation, deploy, peers, pulls, round0
from .config import CapabilityConfig
from .settings import Settings

try:                                              # the same guard correlation.py keeps
    from opentelemetry import trace
except ImportError:
    trace = None

STAGE_IMPLEMENTATION = "implementation"
STAGE_TESTING = "testing"
STAGE_VERDICT = "verdict"


class HandlerError(RuntimeError):
    """Raised for a misconfiguration no fixed rule covers — `Actor.receive()` turns it into a
    `Refusal` (HTTP 400), the same path an undeclared door or a schema violation takes."""


def _github_token() -> str:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise HandlerError("no GITHUB_TOKEN available in the environment")
    return token


def _failed(because: str, *, stage: str, attempts: int, **fields) -> dict:
    """This door's one shape of failure — the reply, and the record that says so, in one place.

    Every early return routes through here, so the terminal verdict of a run is never something
    to reconstruct from the last successful step's absence."""
    carried = {k: v for k, v in fields.items() if v is not None}
    correlation.event("orchestration-failed", level=logging.WARNING, because=because,
                      stage=stage, attempts=attempts,
                      **{k: v for k, v in carried.items() if k != "acceptance_surface"})
    return {"accepted": False, "because": because, "stage": stage, "attempts": attempts,
            **carried}


def _remediation_context(criteria: list[str], verdict: str) -> str:
    failing = [c for c in criteria if c.startswith("❌")]
    return "\n".join(failing or criteria) or verdict


def make_orchestrate_task(config: CapabilityConfig, settings: Settings | None = None):
    """Bind one capability's config, and one environment's settings, to `orchestrate-task`.

    A factory rather than a module-level handler reading constants: which repositories, which
    peers, which components — all of it comes from the sidecar, and a module-level constant is
    exactly the literal this package exists to remove. `settings` defaults to the process
    environment, read once, here.
    """
    settings = settings if settings is not None else Settings.from_env()

    def orchestrate_task(actor, payload: dict, from_: str, judged: dict | None = None) -> dict:
        task_id = payload["task_id"]
        title = payload["title"]
        definition_of_done = payload["definition_of_done"]
        context = payload.get("context")
        run_id = config.run_id(task_id)

        # Bound before round 0 and never reset: `run_id` and `task_id` hold for the whole door
        # call, `attempt` is re-bound as the loop turns.
        correlation.bind(correlation_id=correlation.correlation_id(), task_id=task_id,
                         run_id=run_id, door="orchestrate-task", caller=from_)
        if trace is not None:
            trace.get_current_span().set_attribute("task_id", task_id)

        token = _github_token()
        implementation_url = settings.peer_url(config, "implementation")
        testing_url = settings.peer_url(config, "testing")
        me = actor.name
        correlation.event("orchestration-started", title=title, run_id=run_id,
                          criteria=len(definition_of_done), max_attempts=settings.max_attempts,
                          implementation=implementation_url, testing=testing_url)

        # ── round 0 ───────────────────────────────────────────────────────────────────────
        task = {"task_id": task_id, "title": title, "definition_of_done": definition_of_done,
                "context": context}
        with correlation.stage("round-0", components=list(config.components)):
            outcome = round0.run(
                task, config.components,
                propose=lambda p: peers.call_door(testing_url, "propose-acceptance", p, me,
                                                  timeout=settings.propose_timeout_s),
                assess=lambda p: peers.call_door(implementation_url, "assess-task", p, me,
                                                 timeout=settings.assess_timeout_s),
            )
        if not outcome.agreed:
            return _failed(outcome.because, stage=round0.STAGE, attempts=0, **outcome.fields)
        surface = outcome.surface
        correlation.event("round-0-agreed", expectations=len(surface),
                          datasets=len(outcome.datasets),
                          ids=[e.get("id") for e in surface if isinstance(e, dict)])

        # ── the attempts ──────────────────────────────────────────────────────────────────
        remediation_context: str | None = None
        for attempt in range(1, settings.max_attempts + 1):
            correlation.bind(attempt=attempt)

            # The agreed surface goes to BOTH doors on EVERY attempt. A retry is a second try at
            # the same agreement, not a new negotiation — dropping it on attempt 2 would let the
            # remediation context quietly become the specification.
            impl_payload: dict = {"task_id": task_id, "title": title,
                                  "definition_of_done": definition_of_done,
                                  "acceptance_surface": surface}
            if context:
                impl_payload["context"] = context
            if remediation_context:
                impl_payload["remediation_context"] = remediation_context

            try:
                impl = peers.call_door(implementation_url, "implement-task", impl_payload, me,
                                       timeout=settings.door_timeout_s)
            except peers.PeerError as e:
                return _failed(str(e), stage=STAGE_IMPLEMENTATION, attempts=attempt,
                               acceptance_surface=surface)
            if not impl.get("accepted"):
                return _failed(f"implement-task refused: {impl.get('because', 'unknown reason')}",
                               stage=STAGE_IMPLEMENTATION, attempts=attempt,
                               acceptance_surface=surface)

            branch = impl.get("branch")
            code_images = config.parse_images(impl.get("images") or [])
            if not branch or not code_images:
                return _failed(
                    "implement-task accepted but its completion named no branch, or no touched "
                    "component of this capability (images was empty) — nothing to test",
                    stage=STAGE_IMPLEMENTATION, attempts=attempt, branch=branch,
                    acceptance_surface=surface)
            components = sorted(code_images)
            correlation.event("implementation-accepted", branch=branch, components=components,
                              images=code_images)

            test_payload: dict = {"task_id": task_id, "title": title,
                                  "definition_of_done": definition_of_done,
                                  "components": components, "acceptance_surface": surface}
            if outcome.datasets:
                # Relayed unread, to the actor that wrote them and to nobody else: how each test
                # builds its state, planned before the build (ADR-FTA-0003, ADR-FTOA-0003).
                test_payload["datasets"] = outcome.datasets
            if context:
                test_payload["context"] = context
            if remediation_context:
                # A failing criterion could mean the test was wrong, not the implementation — the
                # testing actor gets the same context, and judges which side needs the fix.
                test_payload["remediation_context"] = remediation_context

            try:
                tested = peers.call_door(testing_url, "test-task", test_payload, me,
                                         timeout=settings.door_timeout_s)
            except peers.PeerError as e:
                return _failed(str(e), stage=STAGE_TESTING, attempts=attempt, branch=branch,
                               acceptance_surface=surface)
            if not tested.get("accepted"):
                return _failed(f"test-task refused: {tested.get('because', 'unknown reason')}",
                               stage=STAGE_TESTING, attempts=attempt, branch=branch,
                               acceptance_surface=surface)

            test_branch = tested.get("branch")
            test_images = config.parse_images(tested.get("images") or [], test=True)
            if not test_branch or not test_images:
                return _failed(
                    "test-task accepted but its completion named no branch, or published no test "
                    "image of this capability — nothing to run",
                    stage=STAGE_TESTING, attempts=attempt, branch=branch, test_branch=test_branch,
                    acceptance_surface=surface)
            correlation.event("testing-accepted", test_branch=test_branch, images=test_images)

            try:
                run = deploy.deploy_and_test(config, settings, task_id=task_id, branch=branch,
                                             code_images=code_images, test_images=test_images,
                                             token=token)
            except deploy.DeployError as e:
                return _failed(str(e), stage=STAGE_TESTING, attempts=attempt, branch=branch,
                               test_branch=test_branch, acceptance_surface=surface)

            correlation.event("attempt-verdict", passed=run.passed, total=run.total,
                              verdict=run.verdict, criteria=run.criteria)

            if run.green:
                full_log = "\n\n".join(f"=== {c} ===\n{run.logs[c]}" for c in sorted(run.logs))
                try:
                    urls = pulls.open_prs(
                        config, task_id=task_id, title=title, branch=branch,
                        test_branch=test_branch, base=settings.base_branch, surface=surface,
                        criteria=run.criteria, verdict=run.verdict, code_images=code_images,
                        test_images=test_images, full_log=full_log, token=token)
                except pulls.PullRequestError as e:
                    return _failed(f"testing passed ({run.verdict}) but the pull request could "
                                   f"not be opened: {e}",
                                   stage=STAGE_VERDICT, attempts=attempt, branch=branch,
                                   test_branch=test_branch, verdict=run.verdict,
                                   acceptance_surface=surface)
                correlation.event("orchestration-succeeded", branch=branch,
                                  test_branch=test_branch, attempts=attempt, verdict=run.verdict,
                                  **urls)
                return {"accepted": True, "branch": branch, "test_branch": test_branch,
                        "attempts": attempt, "verdict": run.verdict,
                        "acceptance_surface": surface, **urls}

            if attempt < settings.max_attempts:
                remediation_context = _remediation_context(run.criteria, run.verdict)
                correlation.event("retrying", next_attempt=attempt + 1,
                                  remediation_context=remediation_context)
                continue

            return _failed(f"testing kept failing after {settings.max_attempts} attempt(s)",
                           stage=STAGE_VERDICT, attempts=attempt, branch=branch,
                           test_branch=test_branch,
                           verdict="\n".join(run.criteria) or run.verdict,
                           acceptance_surface=surface)

        # Unreachable while max_attempts >= 1, which `Settings.from_env` enforces — but a
        # `Settings(max_attempts=0)` built by hand would otherwise fall off the end and return None.
        return _failed("no attempt was made: max_attempts is 0", stage=STAGE_IMPLEMENTATION,
                       attempts=0, acceptance_surface=surface)

    return orchestrate_task
