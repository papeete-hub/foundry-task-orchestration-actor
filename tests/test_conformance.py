"""A use answers the actor's doors, or it is not a use of that actor.

The definition and a use are two folders, each independently conformant, and neither gate has any
opinion about the other. `conformance.check` is the one that does — see `conformance.py` for what
it compares and, just as deliberately, what it does not.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from foundry_task_orchestration_actor import (CapabilityConfig, cards_path, conformance,
                                          render_cards)

FIXTURE = "examples/ACME.PARTS.CAP.SUP.007.WID-task-orchestration"


def _use(tmp_path, source=None):
    """A complete use folder, copied so a test can bend one thing in it."""
    folder = tmp_path / "use"
    folder.mkdir()
    for name in conformance.CARD_FILES:
        shutil.copy((source or cards_path()) / name, folder / name)
    return folder


def test_a_verbatim_copy_conforms(tmp_path):
    assert conformance.check(_use(tmp_path)).ok


def test_the_committed_fixture_conforms_once_its_cards_are_rendered(tmp_path):
    """The fixture is a sidecar; its cards arrive at `docker build` time.

    This is that build step, run in the suite: `render-cards` then `check`. It is the property
    ADR-FTOA-0001 adopts from ADR-FIA-0005 — that what the image generates for a use is, without
    anyone checking, a conformant use of this actor.
    """
    folder = tmp_path / "use"
    # The whole folder, not only the sidecar: it declares a platform stand-in beside it, and
    # loading checks that stand-in is there.
    shutil.copytree(Path(FIXTURE), folder, ignore=shutil.ignore_patterns("Dockerfile"))
    render_cards(CapabilityConfig.load(folder), folder)

    report = conformance.check(folder)
    assert report.ok, report.errors
    assert any("answers the definition's doors" in line for line in report.oks)


def test_a_sidecar_on_its_own_is_the_normal_case(tmp_path):
    """A use carries a sidecar. Cards it has not rendered yet are not a shortfall."""
    (tmp_path / "empty").mkdir()
    report = conformance.check(tmp_path / "empty")
    assert report.ok
    assert not report.errors
    assert any("rendered from the definition" in line for line in report.oks)


def test_an_incomplete_card_set_fails(tmp_path):
    folder = _use(tmp_path)
    (folder / "actor-message.yaml").unlink()
    report = conformance.check(folder)
    assert not report.ok
    assert "actor-message.yaml" in report.errors[0]


def test_a_drifted_payload_is_caught(tmp_path):
    """The whole point: the use still lints, and still answers a different door.

    A reference dropped from the door's own message is the cheapest realistic drift — the card set
    remains internally valid, so every existing gate stays green.
    """
    folder = _use(tmp_path)
    path = folder / "actor-message.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["messages"][0]["references"].remove("title")
    path.write_text(yaml.safe_dump(doc, sort_keys=False))

    report = conformance.check(folder)
    assert not report.ok
    assert any("accepts a different payload" in e for e in report.errors)


def test_a_dropped_completion_outcome_is_caught(tmp_path):
    folder = _use(tmp_path)
    path = folder / "actor-synchronous-messaging.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["actions"][0]["completion_schema"] = ["orchestration-succeeded-result"]
    path.write_text(yaml.safe_dump(doc, sort_keys=False))

    report = conformance.check(folder)
    assert not report.ok
    assert any("different completion set" in e for e in report.errors)


def test_a_different_engine_is_caught(tmp_path):
    folder = _use(tmp_path)
    path = folder / "actor-synchronous-messaging.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["actions"][0]["engine"] = "some-other-engine"
    path.write_text(yaml.safe_dump(doc, sort_keys=False))

    report = conformance.check(folder)
    assert not report.ok
    assert any("resolves through engine" in e for e in report.errors)


def test_a_use_whose_door_gained_an_engine_is_caught(tmp_path):
    """This actor's door names NO engine — every decision behind it is a fixed rule. A copy that
    grew one would boot, and then refuse every call for want of an engine nobody registers."""
    folder = _use(tmp_path)
    path = folder / "actor-synchronous-messaging.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["actions"][0]["engine"] = "claude-code"
    path.write_text(yaml.safe_dump(doc, sort_keys=False))

    report = conformance.check(folder)
    assert not report.ok
    assert any("resolves through engine 'claude-code'" in e for e in report.errors)


def test_a_use_that_never_gained_the_stage_is_caught(tmp_path):
    """The migration signal for a release that changes an outcome's shape.

    A use carrying an older hand copy — the failed result without `stage` — still lints green on
    its own, and replies with a shape the definition would refuse. The instance repo this package
    was extracted from is exactly such a copy.
    """
    folder = _use(tmp_path)
    path = folder / "actor-message.yaml"
    doc = yaml.safe_load(path.read_text())
    failed = next(m for m in doc["messages"] if m["name"] == "orchestration-failed-result")
    failed["references"].remove("stage")
    path.write_text(yaml.safe_dump(doc, sort_keys=False))

    from papeete_actor_synchronous_messaging import card as pas_card
    assert not pas_card.lint(folder).errors

    report = conformance.check(folder)
    assert not report.ok
    assert any("action 'orchestrate-task'" in e and "different completion set" in e
               for e in report.errors), report.errors


def test_a_use_that_invents_a_door_of_its_own_is_caught(tmp_path):
    """A use answers the actor's doors; it does not add its own."""
    folder = _use(tmp_path)
    path = folder / "actor-synchronous-messaging.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["actions"].append({**doc["actions"][0], "id": "orchestrate-everything"})
    path.write_text(yaml.safe_dump(doc, sort_keys=False))

    report = conformance.check(folder)
    assert not report.ok
    assert any("orchestrate-everything" in e for e in report.errors)


def test_prose_and_identity_are_not_compared(tmp_path):
    """A use SHOULD name its own capability and its own peers. That is not drift."""
    folder = _use(tmp_path)

    identity = folder / "actor.yaml"
    doc = yaml.safe_load(identity.read_text())
    doc["name"] = "ACME.PARTS.CAP.SUP.007.WID-task-orchestration"
    doc["description"] = "Orchestrates TASK-NNN cards for ACME.PARTS.CAP.SUP.007.WID."
    identity.write_text(yaml.safe_dump(doc, sort_keys=False))

    doors = folder / "actor-synchronous-messaging.yaml"
    doc = yaml.safe_load(doors.read_text())
    doc["actions"][0]["means"] = "the door for 'drive TASK-NNN of ACME.PARTS.CAP.SUP.007.WID'."
    doors.write_text(yaml.safe_dump(doc, sort_keys=False))

    assert conformance.check(folder).ok
