"""Does this use still answer the same doors as the actor it claims to be?

WHY THIS EXISTS. This package ships the actor's DEFINITION — four cards under `cards/` saying what
a foundry task-orchestration actor is. A USE is one capability's own folder, carrying those four
beside its sidecar, named for the capability it serves.

MOVED FROM `foundry-implementation-actor`, WITH ITS NAMES CHANGED AND NOTHING ELSE (ADR-FTOA-0001).

SINCE THAT PACKAGE'S ADR-FIA-0005, A USE DOES NOT WRITE THEM. `instance.render_cards` produces
them from the definition at `docker build` time, and cards generated from the thing they are
compared against cannot disagree with it. This check remains for a use carrying a hand copy, or one
pinned to an older image, because those are exactly the ones that can be wrong, and because a gate
that has become cheap to pass is not a reason to remove it.

A hand copy drifts. Both folders pass `lint-card` independently — each is a conformant actor — and
neither gate has any opinion about the other. So the day the definition gains a field, renames a
message, or changes a door's completion set, a use that was not updated keeps linting green and
starts refusing callers at runtime, with the rejection surfacing at the door rather than here.

WHAT IS COMPARED, AND WHAT IS NOT. Only what a caller can observe: the set of doors, and for each
one its derived `request_schema`, its `completion_schema`, and the `engine` key it resolves
through. Those derivations already fold in the data dictionary and the message catalog — a renamed
data item, a changed type, a reference added to a message, all of it lands in the schema a caller is
validated against — so comparing them separately would be comparing the same fact twice.

PROSE IS NOT COMPARED, ON PURPOSE. A use's `means:` should name its real capability and its real
peer actors; the definition's cannot, because it serves no capability and its cards are grepped for
exactly such literals. The use's wording is the better one for anyone reading `describe`, and
flattening it to the definition's would delete information. Nor is `actor.yaml`'s `name:`: that is
the use's own identity, and it is REQUIRED to differ.
"""
from __future__ import annotations

from pathlib import Path

from papeete_actor_synchronous_messaging import card as pas_card

from .config import Report, cards_path

CARD_FILES = ("actor.yaml", "actor-data.yaml", "actor-message.yaml",
              "actor-synchronous-messaging.yaml")

# Slicing the plural gets "action" from "actions" and "querie" from "queries". These messages are
# what a use is told to act on, so the word they name the drift with has to be a word. The bug was
# unreachable in the package this was copied from until its definition declared a query
# (ADR-FIA-0004). This actor declares none today; the table stays so the day it does, the word
# is still a word.
_NOUN = {"actions": "action", "queries": "query"}


def check(folder: str | Path = ".") -> Report:
    """Compare one use's cards against the definition this package ships."""
    use = Path(folder)
    report = Report(oks=[], warns=[], errors=[])

    present = [name for name in CARD_FILES if (use / name).exists()]
    if not present:
        # THE NORMAL CASE (ADR-FIA-0005, ADR-FTOA-0001), not a shortfall. A use carries a sidecar;
        # its cards are rendered from the definition at `docker build` time, and a folder checked
        # before that step — in a source checkout, or in this package's own gates — has none to
        # compare. There is nothing to report against a set of cards that will be generated from
        # the very definition this check compares against.
        report.oks.append(
            f"{use}: no cards to compare — they are rendered from the definition "
            f"(`foundry-task-orchestration-actor render-cards`), so they cannot drift from it"
        )
        return report
    if len(present) != len(CARD_FILES):
        missing = [name for name in CARD_FILES if name not in present]
        report.errors.append(
            f"{use}: an incomplete card set — missing {', '.join(missing)}. `Actor.from_card` "
            f"opens exactly these four and never globs, so a use missing one does not boot."
        )
        return report

    definition = cards_path()
    if any(not (definition / name).exists() for name in CARD_FILES):
        # A wheel that lost its cards. `lint-card` in CI and in the release workflow exists to stop
        # that reaching PyPI; this turns the leftover case into a report rather than a traceback
        # from inside a gate a consumer is running.
        report.errors.append(
            f"{definition}: this package's own cards are missing, so there is nothing to compare "
            f"against. The build shipped without them — report it against the release."
        )
        return report

    try:
        theirs = pas_card.load(use)
    except ValueError as e:
        report.errors.append(f"{use}: its own cards do not pass the restriction, so they cannot "
                             f"be compared: {e}")
        return report
    ours = pas_card.load(definition)

    report.errors.extend(_compare(ours, theirs, "actions", use))
    report.errors.extend(_compare(ours, theirs, "queries", use))
    if not report.errors:
        doors = ", ".join(sorted(theirs.actions) + sorted(theirs.queries))
        report.oks.append(f"{use} answers the definition's doors, unchanged: {doors}")
    return report


def _compare(ours, theirs, kind: str, use: Path) -> list[str]:
    """The differences in one door family, as messages. Empty means they agree."""
    defined, used = getattr(ours, kind), getattr(theirs, kind)
    noun = _NOUN[kind]
    errors = []

    for extra in sorted(set(used) - set(defined)):
        errors.append(f"{use}: {noun} '{extra}' is not a door the definition declares — a use "
                      f"answers the actor's doors, it does not add its own")
    for absent in sorted(set(defined) - set(used)):
        errors.append(f"{use}: {noun} '{absent}' is missing — the definition declares it, so a "
                      f"caller addressing this actor may send it")

    for door in sorted(set(defined) & set(used)):
        mine, yours = defined[door], used[door]
        if mine.request_schema != yours.request_schema:
            errors.append(
                f"{use}: {noun} '{door}' accepts a different payload than the definition. "
                f"Its `door_schema` message and the data items that message references are what "
                f"derive this, so one of the two drifted:\n"
                f"      definition {mine.request_schema}\n"
                f"      this use   {yours.request_schema}")
        if mine.completion_schema != yours.completion_schema:
            errors.append(
                f"{use}: {noun} '{door}' replies against a different completion set than the "
                f"definition:\n"
                f"      definition {mine.completion_schema}\n"
                f"      this use   {yours.completion_schema}")
        if mine.engine != yours.engine:
            errors.append(
                f"{use}: {noun} '{door}' resolves through engine '{yours.engine}', the "
                f"definition through '{mine.engine}' — the entrypoint registers the engine under "
                f"the sidecar's `engine:` key, so these three must agree")
    return errors
