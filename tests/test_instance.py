"""Rendered cards are a conformant use, without anyone checking.

The claim ADR-FIA-0005 rests on, adopted here by ADR-FTOA-0001. `conformance.check` exists because
a hand copy drifts; rendering is the construction that cannot, and these are the properties that
make that true rather than merely intended — the card set is complete, it parses as an actor, it
answers the definition's doors exactly, and the one file that differs per use differs only in
identity.
"""
from __future__ import annotations

import pytest
import yaml
from papeete_actor_synchronous_messaging import card as pas_card

from foundry_task_orchestration_actor import cards_path, conformance, render_cards
from foundry_task_orchestration_actor.instance import CARD_FILES


def test_it_writes_the_four_cards(config, tmp_path):
    written = render_cards(config, tmp_path / "use")
    assert [p.name for p in written] == list(CARD_FILES)
    assert all(p.exists() for p in written)


def test_the_result_is_an_actor(config, tmp_path):
    """`Actor.from_card` opens exactly these four and never globs. This is that load."""
    folder = tmp_path / "use"
    render_cards(config, folder)
    assert not pas_card.lint(folder).errors


def test_the_result_conforms_to_the_definition(config, tmp_path):
    folder = tmp_path / "use"
    render_cards(config, folder)
    report = conformance.check(folder)
    assert report.ok, report.errors


def test_the_result_answers_every_door_the_definition_declares(config, tmp_path):
    """Stated positively, so a release that adds a door cannot pass this by accident."""
    folder = tmp_path / "use"
    render_cards(config, folder)
    definition = pas_card.load(cards_path())
    rendered = pas_card.load(folder)
    assert set(rendered.actions) == set(definition.actions)
    assert set(rendered.queries) == set(definition.queries)


def test_identity_is_the_use_and_nothing_else_is(config, tmp_path):
    """`name:` is the use's own and is REQUIRED to differ; the rest of the actor is the actor."""
    folder = tmp_path / "use"
    render_cards(config, folder)
    rendered = yaml.safe_load((folder / "actor.yaml").read_text())
    definition = yaml.safe_load((cards_path() / "actor.yaml").read_text())

    assert rendered["name"] == config.actor_name
    assert rendered["name"] != definition["name"]
    assert rendered["manifest"] == definition["manifest"]
    # The description is the definition's own sentence with this instance's capability appended —
    # so the words a reader gets still come from the definition and cannot silently diverge.
    assert config.capability in rendered["description"]
    assert config.source_repo in rendered["description"]
    # Unlike the implementation actor's, this sidecar declares its peers, so a reader of `describe`
    # still learns which two repositories this instance drives.
    assert config.implementation.repo in " ".join(rendered["description"].split())
    assert config.testing.repo in " ".join(rendered["description"].split())
    assert definition["description"].split(".")[0] in rendered["description"]


@pytest.mark.parametrize("name", [n for n in CARD_FILES if n != "actor.yaml"])
def test_the_other_three_are_the_definition_exactly(config, tmp_path, name):
    """Data, messages and doors are what the actor IS. A use differing in any of them is another
    actor, so they are copied rather than templated — including their comments, which is where the
    reasoning for each door and each item is written down."""
    folder = tmp_path / "use"
    render_cards(config, folder)
    rendered = (folder / name).read_text()
    definition = (cards_path() / name).read_text()

    assert rendered.endswith(definition)
    assert yaml.safe_load(rendered) == yaml.safe_load(definition)
    # And the comments survived the copy, rather than a safe_dump round-trip eating them.
    assert "#" in definition.split("\n")[0]
    assert definition.split("\n")[0] in rendered


def test_every_card_says_it_was_rendered(config, tmp_path):
    """Otherwise someone edits one, and the next `docker build` silently discards the edit."""
    folder = tmp_path / "use"
    for path in render_cards(config, folder):
        first = path.read_text().split("\n")[0]
        assert first.startswith("# RENDERED — do not edit")


def test_no_line_runs_long(config, tmp_path):
    """A rendered card is read in review like any other. 100 columns is this repo's own width."""
    folder = tmp_path / "use"
    render_cards(config, folder)
    for path in [folder / "actor.yaml"]:
        for i, line in enumerate(path.read_text().split("\n"), start=1):
            assert len(line) <= 100, f"{path.name}:{i} is {len(line)} columns: {line!r}"


def test_rendering_twice_is_the_same_bytes(config, tmp_path):
    """`docker build` reruns it on every rebuild; a use that changes on every build is noise."""
    folder = tmp_path / "use"
    first = {p.name: p.read_text() for p in render_cards(config, folder)}
    second = {p.name: p.read_text() for p in render_cards(config, folder)}
    assert first == second


def test_it_overwrites_an_edited_card(config, tmp_path):
    """The banner says the next build discards an edit. This is that."""
    folder = tmp_path / "use"
    render_cards(config, folder)
    (folder / "actor-message.yaml").write_text("message: nonsense\n")
    render_cards(config, folder)
    assert conformance.check(folder).ok


def test_a_second_capability_differs_only_in_identity(config, sidecar_dict, write_sidecar,
                                                      tmp_path):
    """The claim the package's name makes: a second use is a sidecar, not a fork."""
    from foundry_task_orchestration_actor import CapabilityConfig

    other = dict(sidecar_dict,
                 capability="ZENITH.CARGO.CAP.SUP.001.MAN",
                 source_repo="zenith-lab/ZENITH.CARGO.CAP.SUP.001.MAN-task-orchestration")
    other_config = CapabilityConfig.load(write_sidecar(other, tmp_path / "other-sidecar"))

    first = tmp_path / "first"
    second = tmp_path / "second"
    render_cards(config, first)
    render_cards(other_config, second)

    for name in CARD_FILES:
        if name == "actor.yaml":
            assert (first / name).read_text() != (second / name).read_text()
        else:
            assert (first / name).read_text() == (second / name).read_text()
    assert conformance.check(second).ok
