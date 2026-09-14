"""The `-actor` suffix, checked rather than asserted — and the wire contract, pinned.

`ADR-ECO-0022` makes the suffix an obligation: a package ending in `-actor` asserts a
`papeete-actor` underneath, which `lint-card` can check. This file is that check, plus the parts of
the three amigos wire contract this actor owns: one action, no engine, the request it accepts, and
two closed outcome shapes of which a reply must match exactly one.
"""
from __future__ import annotations

import pytest
from papeete_actor_synchronous_messaging import card
from papeete_actor_synchronous_messaging.actor import Actor, Refusal

from foundry_task_orchestration_actor import cards_path

CARD_FILES = ("actor.yaml", "actor-data.yaml", "actor-message.yaml",
              "actor-synchronous-messaging.yaml")


def test_the_package_ships_the_four_cards():
    missing = [name for name in CARD_FILES if not (cards_path() / name).exists()]
    assert not missing, f"the actor's definition is incomplete — missing: {', '.join(missing)}"


def test_the_shipped_cards_are_conformant():
    report = card.lint(cards_path())
    assert not report.errors, ("the cards this package ships are not conformant:\n  "
                               + "\n  ".join(report.errors))


def test_one_action_no_query_no_engine():
    """Every decision behind the door is a fixed rule, so nothing is judged."""
    loaded = card.load(cards_path())
    assert set(loaded.actions) == {"orchestrate-task"}
    assert not loaded.queries
    assert loaded.actions["orchestrate-task"].engine is None


def test_the_request_is_the_contracts():
    schema = card.load(cards_path()).actions["orchestrate-task"].request_schema
    assert set(schema["properties"]) == {"task_id", "title", "definition_of_done", "context"}
    assert set(schema["required"]) == {"task_id", "title", "definition_of_done"}


def test_the_outcomes_are_the_contracts():
    succeeded, failed = card.load(cards_path()).actions["orchestrate-task"].completion_schema
    assert set(succeeded["properties"]) == {"accepted", "pr_url", "test_pr_url", "branch",
                                            "test_branch", "attempts", "verdict",
                                            "acceptance_surface"}
    assert set(succeeded["required"]) == {"accepted", "pr_url", "branch", "test_branch",
                                          "attempts", "verdict", "acceptance_surface"}
    assert set(failed["properties"]) == {"accepted", "because", "stage", "attempts", "branch",
                                         "test_branch", "verdict", "acceptance_surface",
                                         "open_questions", "objections", "commitments"}
    assert set(failed["required"]) == {"accepted", "because", "stage", "attempts"}
    assert failed["properties"]["stage"]["enum"] == ["round-0", "implementation", "testing",
                                                     "verdict"]


def _actor_replying(reply: dict) -> Actor:
    return Actor.from_card(cards_path(), actions={
        "orchestrate-task": lambda actor, payload, from_, judged=None: reply})


PAYLOAD = {"task_id": "TASK-042", "title": "t", "definition_of_done": ["d"]}


@pytest.mark.parametrize("reply", [
    {"accepted": True, "pr_url": "u", "branch": "impl/TASK-042", "test_branch": "test/TASK-042",
     "attempts": 1, "verdict": "2/2 criteria passed", "acceptance_surface": [{"id": "E1"}]},
    {"accepted": False, "because": "b", "stage": "round-0", "attempts": 0,
     "acceptance_surface": [{"id": "E1"}], "open_questions": ["q"]},
    {"accepted": False, "because": "b", "stage": "verdict", "attempts": 3,
     "branch": "impl/TASK-042", "test_branch": "test/TASK-042", "verdict": "❌ t FAILED"},
])
def test_each_outcome_the_handler_produces_matches_exactly_one_shape(reply):
    assert _actor_replying(reply).receive(verb="request", door="orchestrate-task",
                                          from_="c", payload=PAYLOAD) == reply


@pytest.mark.parametrize("reply", [
    {"accepted": False, "because": "b", "stage": "somewhere-else", "attempts": 1},
    {"accepted": False, "because": "b", "attempts": 1},
    {"accepted": True, "pr_url": "u", "branch": "b", "test_branch": "t", "attempts": 1,
     "verdict": "v"},
    {"accepted": False, "because": "b", "stage": "testing", "attempts": 1, "branch": None},
])
def test_a_reply_outside_the_contract_is_refused_at_the_membrane(reply):
    """An unknown stage, a failure with no stage, a success with no surface, a null branch."""
    with pytest.raises(Refusal):
        _actor_replying(reply).receive(verb="request", door="orchestrate-task", from_="c",
                                       payload=PAYLOAD)


def test_a_payload_naming_no_definition_of_done_is_refused():
    with pytest.raises(Refusal, match="definition_of_done"):
        _actor_replying({}).receive(verb="request", door="orchestrate-task", from_="c",
                                    payload={"task_id": "TASK-042", "title": "t"})
