---
id: ADR-FTOA-0002
title: "Round zero — this actor's half of the three amigos round"
status: Proposed
date: 2026-09-14
supersedes: []
references:
  - ../src/foundry_task_orchestration_actor/round0.py
  - ../src/foundry_task_orchestration_actor/handler.py
  - ../src/foundry_task_orchestration_actor/cards/actor-message.yaml
  - https://github.com/papeete-hub/foundry-implementation-actor/blob/main/adr/ADR-FIA-0004-the-three-amigos-round.md
---

# ADR-FTOA-0002 — Round zero

## Context

ADR-FIA-0004 decided a bounded negotiation before implementation — the tester proposes, the
implementer answers, a human breaks the tie — and built the implementation actor's half,
`assess-task`. It left the other two halves specified and unbuilt: the testing actor's
`propose-acceptance`, and *"the orchestrating actor's round 0 … after that actor unpacks its payload
and before its attempt loop begins."* The three packages now share one binding wire contract for
the round; this is the orchestrating actor's half of it.

## Decision

**1. Round 0 runs once per `orchestrate-task` call, before the attempt loop.**

```
POST testing        propose-acceptance {task_id, title, definition_of_done, components, context?}
POST implementation assess-task        {task_id, title, definition_of_done, acceptance_surface, context?}
```

**2. Propose, then assess, then refuse or proceed — never a second round.** The run stops, as an
`orchestration-failed-result` with `stage: round-0` and `attempts: 0`, when:

- `propose-acceptance` does not answer (transport error, or a 400 refusal);
- it answers with non-empty `open_questions` — carrying `acceptance_surface` (the expectations) and
  `open_questions`; **the implementation actor is not asked**;
- it proposes no expectations;
- `assess-task` does not answer — carrying the proposal as `acceptance_surface`;
- `feasible` is anything but `true`, or there is any objection — carrying `acceptance_surface`,
  `objections` and `commitments`.

**3. The failed result names its `stage`** — `round-0`, `implementation`, `testing` or `verdict` —
as a closed enum on a required field. Deployment and test-Job failures are `testing`; exhausted
attempts, and a green run whose implementation PR could not be opened, are `verdict`.

**4. The commitment attachment rule.** The agreed surface is the expectations, where each commitment
that is a mapping whose `id`, `expectation` or `expectation_id` equals an expectation's `id` is
appended to that expectation's `commitments` list; every other commitment is appended as
`{id: commitment-<n>, statement: <its text, else its JSON>, handle: <its handle or null>,
commitment: <raw>}`, numbered from 1 in order. Nothing the implementation actor promised is dropped
on the way to the tester.

**5. The agreed surface goes to `implement-task` AND `test-task` on every attempt**, and is returned
on success and rendered into the pull request body.

**6. `components` for `propose-acceptance` come from this actor's own sidecar.**

## Rationale

**Why stop on open questions before asking the implementer.** *"The task does not determine this"*
is the most useful answer round 0 can produce, and it is a human's to resolve by amending the task.
Asking the implementation actor to assess a surface the tester itself says is under-determined
spends a grounded session to produce objections nobody can act on until the human has answered.

**Why exactly one round.** A three-amigos meeting converges because a human is in the room. Here the
human is at the other end of the synchronous call that started the task, and escalation is this
door's own refusal with the objections (ADR-FIA-0004 §5). A negotiation loop between two sessions
would converge on whatever both could agree to, which is not the same as what the task meant.

**Why a `stage` field rather than prose.** The caller is most often a person deciding what to do
next, and the four stages call for four different actions: amend the task, look at the implementer,
look at the tests or the environment, or read the verdict. `because` stays for the detail; `stage`
is what a dashboard, a bot, or a human skimming a thread can branch on without parsing it.

**Why the surface travels on every attempt.** A retry is a second try at the same agreement, not a
new negotiation. Dropping the surface after attempt 1 would let `remediation_context` — a list of
failing test ids — quietly become the specification, and a tester re-deriving its assertions from
the last failure is exactly the "confirm the build" failure round 0 exists to prevent.

**Why components come from the sidecar.** Before implementation there is no completion to read
touched components from — the instance this was extracted from learned them from `implement-task`'s
`images`, which does not exist yet at round 0. The tester still needs to know what the capability
is made of to name handles. The sidecar's list is the whole capability; the attempt loop still
deploys and names to `test-task` only what implementation actually touched.

**Why commitments attach rather than ride beside the surface.** A commitment like "fixture ids are
0190…a1, a2, a3" is only useful to a tester next to the expectation it pins. Attaching it makes the
surface self-contained, so neither peer has to join two lists by a convention written only here; an
unattached commitment becomes an entry of its own so the tester still sees it.

## Consequences

- **Two extra door calls per task, each a read-only grounded session on a peer.** Their timeouts are
  `PROPOSE_TIMEOUT_S` / `ASSESS_TIMEOUT_S` (900s by default), separate from the implement/test doors'.
- **`attempts: 0`** is now a legal value, meaning round 0 stopped the run.
- **Every use's cards change** (a new required `stage`, new optional fields). Rendering removes the
  hand copy; `conformance.check` catches a use that still carries one.
- **Expectation ids are available to a verdict** but not yet used by one: the remediation context is
  still the failing pytest node ids. Mapping failures back to agreed expectation ids is left to the
  testing actor's test naming, and is not decided here.
- **Not decided here:** persisting the agreed surface anywhere durable beyond the PR body, and a
  human-in-the-loop door that could resume a run after open questions are answered.
