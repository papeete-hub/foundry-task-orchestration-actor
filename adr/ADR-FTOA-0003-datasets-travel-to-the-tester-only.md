---
id: ADR-FTOA-0003
title: "Datasets travel to the tester only — relayed unread, and the test Job is told what else it can reach"
status: Proposed
date: 2026-09-14
supersedes: []
references:
  - ../src/foundry_task_orchestration_actor/round0.py
  - ../src/foundry_task_orchestration_actor/handler.py
  - ../src/foundry_task_orchestration_actor/deploy.py
  - ../src/foundry_task_orchestration_actor/config.py
  - https://github.com/papeete-hub/foundry-testing-actor/blob/main/adr/ADR-FTA-0003-every-expectation-says-how-its-data-comes-to-exist.md
---

# ADR-FTOA-0003 — Datasets travel to the tester only

## Context

ADR-FTA-0003 makes the testing actor's `propose-acceptance` answer a second list beside the
expectations: `datasets`, one per expectation, saying how the state its test runs against comes to
exist (`none`, `command`, `event`, or `seed`). It is private to the testing actor, and the
implementer must never see it — anything a dataset needs from the implementer is proposed as an
expectation instead.

This actor sits between the two. It is the only one that sees the proposal, and the only one that
builds the payloads each door receives, so the privacy of `datasets` is decided here. And an
`event` dataset publishes to a broker the test Job has, until now, had no address for: the Job was
told `<COMPONENT>_URL` and nothing else.

## Decision

**1. `datasets` is taken off the proposal and carried beside the surface, unread.** `round0.run`
returns it on the agreed `Outcome`; nothing in this actor inspects an entry.

**2. It goes to `test-task` on every attempt, and nowhere else.** Not to `assess-task` (which gets
the expectations, as before), not to `implement-task`, not into either result message, not into
the pull request body. The round-0 end-to-end test pins all four absences.

**3. The test Job gets `ephemeral.test_env`, declared by the use.** A mapping of variable name to
template, rendered with `{run_id}` and `{capability}` and set on the Job after every
`<COMPONENT>_URL`. By convention `AMQP_URL` names the broker an `event` dataset publishes to. Names
must be UPPER_SNAKE_CASE and may not redefine a `<COMPONENT>_URL`; a per-component placeholder is
refused, because one Job runs every touched component's tests. `show` prints the names, never the
values.

## Rationale

**Why relay rather than let the tester recompute.** The plan the tests are written against should
be the one that existed when the implementer agreed to the surface — before the build. Recomputed
at `test-task`, after the build, it would be free to read state off the artifact again.

**Why unread.** This actor has no opinion about how a test arranges its data, and every rule about
it lives in the actor that writes it (ADR-FTA-0003 §2). Reading it here would be a second place for
those rules to drift.

**Why the broker address is the use's declaration.** The stand-in is the use's own folder
(`ephemeral.platform`), and its Service names and credentials are the use's facts — the same reason
`ephemeral.secrets` already lives in the sidecar. A convention in code would have to guess them.

## Consequences

- **A proposal without `datasets`** — a testing actor older than foundry-testing-actor 0.2.0 — is
  still accepted; `test-task` simply receives none and builds state through the contract.
- **A use that wants event-driven tests** declares `ephemeral.test_env.AMQP_URL`; one that declares
  nothing gets exactly the Job it had before.
- **Not decided here**: whether a verdict should report, per expectation, which dataset failed to
  build. The failing test names its expectation id already.
