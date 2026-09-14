---
id: ADR-FTOA-0004
title: "A stopped round is sent to the task's owner — as an issue, never written into its backlog"
status: Proposed
date: 2026-09-14
supersedes: []
references:
  - ../src/foundry_task_orchestration_actor/issues.py
  - ../src/foundry_task_orchestration_actor/handler.py
  - ../src/foundry_task_orchestration_actor/cards/actor-message.yaml
  - ./ADR-FTOA-0002-round-zero.md
---

# ADR-FTOA-0004 — A stopped round is sent to the task's owner

## Context

ADR-FTOA-0002 made round 0 stop on open questions or objections and hand them back as this door's
refusal — *"here the human is at the other end of the synchronous call that started the task."*
In practice the other end of the call is often not the task's owner: a script, a skill launching
tasks from a backlog, or a person who has since closed the terminal. The answer reached whoever
made the HTTP call and went no further. The backlog the task came from — task cards in a
repository this actor has never read — kept saying the task was ready. The first real case was a
task that stopped because the backend it touched had no ARCHIVE command; nothing but a response
body recorded that a human had something to decide.

The fix could not be for this actor to edit the card. It deals with the task it is handed and
nothing else; the backlog, its format and its states belong to the task-management skills of the
repository that holds it. An actor that writes into knowledge it does not own becomes a second
place for that knowledge's rules to drift.

## Decision

**1. The caller may name `report_to`** (`owner/repo`) on `orchestrate-task-cmd`: where the task's
owner wants to hear that round 0 stopped. Optional; without it nothing is sent, exactly as before.

**2. Only a round stopped by open questions or objections is sent** — what a human answers by
amending the task. A peer that did not answer, or a proposal with no expectations, is not: those
are for whoever operates the actors.

**3. It is sent as a GitHub issue, one per task.** Three labels: `task:<capability>/<task_id>` (the
join key, at most 50 characters), `needs-info`, and `from:<this actor's name, lowercased>`. Title
`[<task_id>] round 0 stopped: N open question(s), M objection(s)`; the body is a checklist item per
question and objection — who raised it, the expectation it concerns — then the proposed
expectations and the correlation id. If an issue with the task's label is already open, the stop
is a comment on it instead.

**4. The refusal carries `issue_url`** when the issue was sent. When sending fails, the round-0
refusal is returned unchanged, without `issue_url`, and the failure is logged as
`issue-report-failed`.

**5. This actor still never reads or edits a task card.** What an open issue means for the card —
`needs_info` until a human amends the card and closes the issue — is the owning repository's rule,
kept in its own skills.

## Rationale

**Why a message rather than an edit.** A message needs no ownership: anyone may open an issue on a
repository they can reach, and the receiver decides what it means. An edit to the card would bind
this package to one backlog's file format and state machine, and let two writers disagree about a
card's state.

**Why an issue, and why labelled by task.** The owning repository's skills already run `gh` against
it; an issue is a thing they can list, link from a board and close, with a history a human can
answer in. A label is the only field both sides can match exactly without either parsing the
other's prose, so it is the join key — for the skills finding a task's issue, and for this actor
finding the issue it already opened.

**Why comment rather than open another.** The owner of a task that stops twice before they answer
has one question, asked twice. One thread per open question keeps the board to one link per task.

**Why the caller names the repository.** This actor has no idea where a task came from; the sidecar
names the capability's code and test repositories, not a backlog, and one capability's tasks may be
launched from more than one place. Whoever sends the task knows where its owner listens.

**Why a failed report does not fail the door.** The refusal is the answer; the issue is a copy of it
sent elsewhere. Losing the copy must not cost the caller the original.

## Consequences

- **The actor's `GITHUB_TOKEN` needs Issues: read and write** on every repository a caller names in
  `report_to`, beyond the contents and pull-request permissions it already needed.
- **A caller that sends no `report_to` sees no change**; the request and failure schemas gain one
  optional field each.
- **Closing the issue is the owner's signal**, not this actor's: nothing here reopens, closes or
  reads an issue beyond finding an open one to comment on.
- **Not decided here:** reporting an infeasible assessment that names no objection, and a door that
  resumes a run once the questions are answered.
