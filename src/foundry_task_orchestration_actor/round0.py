"""Round 0 — the three amigos round, this actor's half of it (ADR-FTOA-0002).

WHAT IT IS FOR. Before anything is built, the actor that will black-box test the increment says
what it intends to assert, and the actor that will build it says whether that can be delivered:

    orchestration ──▶ testing:        propose-acceptance   (task + component names)
                  ◀──                 expectations[] + open_questions[]
    orchestration ──▶ implementation: assess-task          (task + those expectations)
                  ◀──                 feasible + objections[] + commitments[]

      agreed     → the surface goes to implement-task AND test-task, on every attempt
      otherwise  → this door refuses to its own caller, stage `round-0`, carrying what was said

Exactly one round, then a person. No counter-proposal and no negotiation loop: a three-amigos
meeting converges because a human is in the room, and here the human is at the other end of the
call that started the task. The failure this prevents is real — an e2e suite hardcoding fixture
ids "discovered black-box against the running container", against a task card that never named
them (ADR-FIA-0004). A tester deriving its assertions from the built artifact can only confirm the
build; one that proposes first, before anything exists, can fail it.

WHY THIS MODULE IS PURE. No HTTP, no logging, no config: the two door calls are handed in as
callables, and the outcome is a value. Every rule the wire contract fixes — when to stop, what a
refusal carries, how a commitment attaches to an expectation — is therefore testable as a table of
inputs and outputs, and `handler.py` stays a sequence of steps rather than a sequence of steps with
a negotiation protocol tangled into it.

THE RULES, AS THE CONTRACT FIXES THEM.

1. `propose-acceptance` failing to answer (transport error, a 400) stops the run. So does a
   non-empty `open_questions` — carrying the expectations as `acceptance_surface` and the
   questions themselves, and WITHOUT asking the implementation actor anything: "the task does not
   determine this" is a legitimate answer, and the most useful one, and it is a human's to resolve
   by amending the task. So does an empty `expectations` list: there is nothing to agree on.
2. `assess-task` failing to answer stops the run. So does `feasible` being anything but `true`, or
   any objection at all — carrying the surface, the objections and the commitments.
3. Agreed: each commitment that names an expectation (a mapping whose `id`, `expectation` or
   `expectation_id` equals that expectation's `id`) is attached to it under `commitments`; every
   other commitment is appended as an expectation of its own, so nothing the implementation actor
   promised is dropped on the way to the tester.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

from .peers import PeerError

STAGE = "round-0"

# The keys by which a commitment may name the expectation it pins. Any one matching is enough.
_NAMING_KEYS = ("id", "expectation", "expectation_id")


@dataclass(frozen=True)
class Outcome:
    """What round 0 decided. `agreed` or not; `fields` is what a refusal carries."""

    agreed: bool
    surface: list = field(default_factory=list)
    because: str | None = None
    fields: dict = field(default_factory=dict)


def _task_fields(task: dict) -> dict:
    """The part of the caller's payload both round-0 doors are handed, unchanged."""
    out = {"task_id": task["task_id"], "title": task["title"],
           "definition_of_done": task["definition_of_done"]}
    if task.get("context"):
        out["context"] = task["context"]
    return out


def propose_payload(task: dict, components: list[str] | tuple[str, ...]) -> dict:
    """`propose-acceptance-cmd`. `components` is the sidecar's own list: nothing has been built
    yet, so which components the task will touch is not known — see ADR-FTOA-0002 §4."""
    payload = _task_fields(task)
    payload["components"] = list(components)
    return payload


def assess_payload(task: dict, expectations: list) -> dict:
    """`assess-task-cmd`: the task, and the tester's proposal as `acceptance_surface`."""
    payload = _task_fields(task)
    payload["acceptance_surface"] = expectations
    return payload


def _as_list(value) -> list:
    if value is None or value == "":
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _commitment_text(commitment) -> str:
    if isinstance(commitment, str):
        return commitment
    if isinstance(commitment, dict):
        for key in ("statement", "text"):
            if isinstance(commitment.get(key), str):
                return commitment[key]
    return json.dumps(commitment, sort_keys=True, ensure_ascii=False, default=str)


def attach_commitments(expectations: list, commitments: list) -> list:
    """The agreed surface: the expectations, each carrying the commitments that name it.

    Returns new objects — the proposal as the tester sent it is not mutated, so a refusal and a
    log record built from it later still say what was actually proposed.
    """
    surface = [dict(e) if isinstance(e, dict) else e for e in expectations]
    by_id = {str(e["id"]): e for e in surface if isinstance(e, dict) and "id" in e}

    unattached = 0
    for commitment in commitments:
        target = None
        if isinstance(commitment, dict):
            for key in _NAMING_KEYS:
                value = commitment.get(key)
                if value is not None and str(value) in by_id:
                    target = by_id[str(value)]
                    break
        if target is not None:
            target["commitments"] = [*_as_list(target.get("commitments")), commitment]
            continue
        unattached += 1
        surface.append({
            "id": f"commitment-{unattached}",
            "statement": _commitment_text(commitment),
            "handle": commitment.get("handle") if isinstance(commitment, dict) else None,
            "commitment": commitment,
        })
    return surface


def run(task: dict, components, *, propose: Callable[[dict], dict],
        assess: Callable[[dict], dict]) -> Outcome:
    """One round. `propose` and `assess` POST a payload to their door and return the reply, or
    raise `PeerError` — they are `peers.call_door`, bound, in production."""
    try:
        proposal = propose(propose_payload(task, components))
    except PeerError as e:
        return Outcome(False, because=f"round 0: the testing actor's propose-acceptance did not "
                                      f"answer: {e}")

    expectations = proposal.get("expectations")
    if not isinstance(expectations, list):
        return Outcome(False, because="round 0: propose-acceptance answered no `expectations` "
                                      "list")
    open_questions = _as_list(proposal.get("open_questions"))
    if open_questions:
        return Outcome(
            False, surface=expectations,
            because=(f"round 0: the task does not determine what the testing actor must assert — "
                     f"{len(open_questions)} open question(s) for a human to answer by amending "
                     f"the task; the implementation actor was not asked"),
            fields={"acceptance_surface": expectations, "open_questions": open_questions},
        )
    if not expectations:
        return Outcome(False, because="round 0: propose-acceptance proposed no expectations — "
                                      "there is nothing to agree on, and nothing to test")

    try:
        assessment = assess(assess_payload(task, expectations))
    except PeerError as e:
        return Outcome(False, surface=expectations,
                       because=f"round 0: the implementation actor's assess-task did not answer: "
                               f"{e}",
                       fields={"acceptance_surface": expectations})

    objections = _as_list(assessment.get("objections"))
    commitments = _as_list(assessment.get("commitments"))
    if assessment.get("feasible") is not True or objections:
        carried = {"acceptance_surface": expectations}
        if objections:
            carried["objections"] = objections
        if commitments:
            carried["commitments"] = commitments
        return Outcome(
            False, surface=expectations,
            because=(f"round 0: the implementation actor did not agree to the proposed surface — "
                     f"feasible={assessment.get('feasible')!r}, {len(objections)} objection(s); "
                     f"a human breaks the tie"),
            fields=carried,
        )

    return Outcome(True, surface=attach_commitments(expectations, commitments))
