"""A stopped round 0, sent to the task's owner as a GitHub issue (ADR-FTOA-0004).

WHY A MESSAGE AT ALL. Round 0 stopping is the most useful thing it can do — "the task does not
determine this", "the backend has no ARCHIVE command" — and it is a human's to resolve. Until now
the only place that answer went was the reply to whoever made the HTTP call. A caller that was a
script, a skill, or a person who closed the terminal took it with them, and the backlog the task
came from went on believing the task was ready. The card said nothing had changed, because from
where the card sat, nothing had.

WHY AN ISSUE, AND NEVER AN EDIT TO THE CARD. The backlog is not this actor's. Its cards are owned
by the task-management skills of the repository that holds them, and this actor deals only with
the task it was handed — it never reads that backlog, and so it has no business writing into it.
What it can do without owning anything is SEND something: an issue on the repository the caller
names in `report_to`, which that repository's own skills read as "this task needs information".
The owner resolves it the way they resolve anything else about the task — amend the card, close
the issue — and nothing in this package needs to know how.

ONE ISSUE PER TASK, NOT PER STOP. The join key is a label, `task:<capability>/<task_id>`: a card's
skills find the issue by it, and this module finds it by the same label before opening another. A
task that stops again while its issue is still open gets a comment on it, so an owner who has not
answered yet reads one thread growing, not a pile of duplicates. Once the issue is closed, the next
stop is a new question and opens a new issue.

    task:<capability>/<task_id>   the join key — GitHub caps a label at 50 characters, checked here
    needs-info                    what the owner's skills branch on
    from:<actor name, lowercased> who sent it, so a board can tell this actor's issues from a human's

WHAT IT IS NOT FOR. Only a round that stopped on something a human can answer is sent: open
questions from the testing actor, or objections from the implementation actor. A peer that did not
answer is an operator's problem, not the task owner's, and amending the card would not fix it.
The caller decides that rule (`handler.py`); this module only renders and sends.

WHAT A FAILURE HERE COSTS. Nothing but the message. Every step raises `PullRequestError` — the one
error type of the GitHub client this module shares with `pulls.py` — and the handler still returns
the round-0 refusal, without `issue_url`, logging that the report was not delivered.
"""
from __future__ import annotations

import json
import re
import urllib.parse

from . import pulls
from .config import CapabilityConfig
from .pulls import GITHUB_API, PullRequestError

# GitHub refuses a longer label name with a 422 of its own; checked before any call is made, so
# the refusal names the label rather than surfacing as an opaque validation error mid-report.
LABEL_MAX = 50

NEEDS_INFO = "needs-info"

_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

# The keys by which an open question or an objection names the expectation it concerns — the
# testing actor has sent `about` and `id`, the implementation actor sends `id`; any one is enough.
_CONCERNS_KEYS = ("id", "about", "expectation", "expectation_id")
_QUESTION_TEXT_KEYS = ("question", "text", "statement")
_OBJECTION_TEXT_KEYS = ("why", "objection", "text", "statement")

_LABEL_COLOURS = {"task": "1d76db", NEEDS_INFO: "d93f0b", "from": "c5def5"}


def task_label(config: CapabilityConfig, task_id: str) -> str:
    return f"task:{config.capability}/{task_id}"


def from_label(config: CapabilityConfig) -> str:
    return f"from:{config.actor_name.lower()}"


def _text(entry, keys: tuple[str, ...]) -> str:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        for key in keys:
            if isinstance(entry.get(key), str):
                return entry[key]
    return json.dumps(entry, sort_keys=True, ensure_ascii=False, default=str)


def _concerns(entry) -> str | None:
    if isinstance(entry, dict):
        for key in _CONCERNS_KEYS:
            if entry.get(key) is not None:
                return str(entry[key])
    return None


def _item(kind: str, raised_by: str, entry, keys: tuple[str, ...]) -> str:
    concerns = _concerns(entry)
    where = f", on `{concerns}`" if concerns else ""
    line = f"- [ ] **{kind}** — raised by {raised_by}{where}: {_text(entry, keys)}"
    if isinstance(entry, dict) and entry.get("counter_proposal"):
        line += f"\n  - counter-proposal: {_text(entry['counter_proposal'], ('text',))}"
    return line


def issue_title(task_id: str, fields: dict) -> str:
    return (f"[{task_id}] round 0 stopped: {len(fields.get('open_questions') or [])} open "
            f"question(s), {len(fields.get('objections') or [])} objection(s)")


def issue_body(config: CapabilityConfig, task_id: str, fields: dict, correlation_id: str) -> str:
    """The checklist a human works through, then what was proposed, then how to trace the run.

    Every question and objection is its own checkbox, saying which actor raised it and the
    expectation it concerns — a tick is the owner's own note of "answered in the card", nothing
    this actor reads back."""
    testing = f"testing (`{config.testing.actor_name}`)"
    implementation = f"implementation (`{config.implementation.actor_name}`)"
    items = [_item("open question", testing, q, _QUESTION_TEXT_KEYS)
             for q in fields.get("open_questions") or ()]
    items += [_item("objection", implementation, o, _OBJECTION_TEXT_KEYS)
              for o in fields.get("objections") or ()]

    expectations = [
        f"- **{e.get('id', '?')}** {e.get('statement', '')}" if isinstance(e, dict) else f"- {e}"
        for e in fields.get("acceptance_surface") or ()
    ] or ["(none)"]

    commitments = ""
    if fields.get("commitments"):
        commitments = ("\n\n## Commitments offered (implementation)\n\n"
                       + "\n".join(f"- {_text(c, ('commitment', 'statement', 'text'))}"
                                   for c in fields["commitments"]))

    return (
        f"Round 0 for `{task_id}` of `{config.capability}` stopped before anything was built. "
        f"What follows is for the task's owner to decide.\n\n"
        f"## To resolve\n\n" + "\n".join(items) + "\n\n"
        f"## Proposed expectations (testing)\n\n" + "\n".join(expectations)
        + commitments + "\n\n---\n"
        f"Resolve by amending the task card (its definition of done or context), then close this "
        f"issue with a comment linking the change. While an issue labelled "
        f"`{task_label(config, task_id)}` is open, the task is `needs_info`.\n\n"
        f"Sent by `{config.actor_name}`. Correlation id: `{correlation_id}`"
    )


def _ensure_label(repo: str, name: str, colour: str, token: str) -> None:
    try:
        pulls.github_request(f"{GITHUB_API}/repos/{repo}/labels", token, method="POST",
                             body={"name": name, "color": colour})
    except PullRequestError as e:
        # 422 is how GitHub says "already_exists" — the normal case after the first issue. Any
        # other 422 (an invalid name) is not, and is not swallowed.
        if not (e.status == 422 and "already_exists" in str(e)):
            raise


def _open_issue(repo: str, label: str, token: str) -> dict | None:
    query = urllib.parse.urlencode({"labels": label, "state": "open"})
    found = pulls.github_request(f"{GITHUB_API}/repos/{repo}/issues?{query}", token, method="GET")
    # The issues endpoint lists pull requests too; a PR somebody labelled is not this thread.
    issues = [i for i in found or () if isinstance(i, dict) and "pull_request" not in i]
    return min(issues, key=lambda i: i["number"]) if issues else None


def report_round0(config: CapabilityConfig, repo: str, task_id: str, fields: dict,
                  correlation_id: str, token: str) -> str:
    """Open an issue for this stopped round on `repo`, or comment on the one already open for the
    task — and return that issue's `html_url`. Raises `PullRequestError` on any failure."""
    if not _REPO.match(repo or ""):
        raise PullRequestError(f"report_to '{repo}' is not '<owner>/<repo>'")
    task, sender = task_label(config, task_id), from_label(config)
    too_long = [label for label in (task, sender) if len(label) > LABEL_MAX]
    if too_long:
        raise PullRequestError(f"label(s) over GitHub's {LABEL_MAX} characters: "
                               + ", ".join(too_long))

    for name, colour in ((task, _LABEL_COLOURS["task"]), (NEEDS_INFO, _LABEL_COLOURS[NEEDS_INFO]),
                         (sender, _LABEL_COLOURS["from"])):
        _ensure_label(repo, name, colour, token)

    body = issue_body(config, task_id, fields, correlation_id)
    existing = _open_issue(repo, task, token)
    if existing:
        pulls.github_request(f"{GITHUB_API}/repos/{repo}/issues/{existing['number']}/comments",
                             token, method="POST",
                             body={"body": f"**{issue_title(task_id, fields)}** — again.\n\n{body}"})
        return existing["html_url"]

    created = pulls.github_request(f"{GITHUB_API}/repos/{repo}/issues", token, method="POST",
                                   body={"title": issue_title(task_id, fields), "body": body,
                                         "labels": [task, NEEDS_INFO, sender]})
    return created["html_url"]
