---
id: ADR-FTOA-0005
title: "An attempt's namespace carries its capability, within the 63 characters a Service name has"
status: Proposed
date: 2026-09-15
supersedes: []
references:
  - ../src/foundry_task_orchestration_actor/config.py
  - ../src/foundry_task_orchestration_actor/handler.py
---

# ADR-FTOA-0005 — An attempt's namespace carries its capability, within the 63 characters a Service name has

## Context
`run_id` is both an attempt's namespace and the papeete-deploy product its objects are prefixed
with. Until 0.3.0 it was `test-<task_id>`. Task ids are numbered per capability, though: a
product running several uses of this actor has TASK-003 in more than one backlog. Two
orchestrators in the same cluster working on their own TASK-003 at the same time would share one
namespace, and each one's teardown would delete the other's run.

Every object an attempt creates is named `{run_id}-{name}`, and a Service name is a DNS-1035
label of at most 63 characters. papeete-deploy does not shorten names. With the full capability
slug, a pairing check's id already overflows:
`test-bnk-rlvr-cap-bsp-001-sco-task-pair-verify-007-bsp-001-sco-stub` is 67 characters.

## Decision
- `run_id(task_id)` is `test-{workload_prefix}-{task_id lowercased}`. `workload_prefix` is the
  tail of the capability id after its `CAP` segment, dots to hyphens (`bsp-001-sco`). It is the
  same token every component's workload name already starts with.
- Loading a sidecar refuses a capability whose longest attempt object
  (`{run_id}-{workload}` or `{run_id}-test-job`) would be over 63 characters for a task id as
  long as `TASK-PAIR-VERIFY-NNN`.
- `orchestrate-task` refuses a task id whose names would overflow. It does so as a refusal (HTTP
  400), before round 0, so no peer session is paid for an attempt that could never be created.
  The completion's `stage` enum is shared across three packages and gains no value.

## Rationale
The tail slug is unique among the capabilities one enterprise domain runs, which is the scope of
one product's cluster. It also keeps a pairing-check id with a long component name (58
characters for `…-task-pair-verify-007-chn-001-dsh-frontend`) inside the limit. The full slug
would make names unique across enterprises too, but at the cost of refusing the task ids actually
in use. Checking at load puts the failure in front of the person writing the sidecar, not in a
live attempt.

## Consequences
- The namespace changes for existing uses: `test-task-042` becomes `test-sup-007-wid-task-042`.
  A stopped 0.3.0 attempt's leftover namespace is not recognised by 0.4.0's teardown and has to be
  deleted by hand.
- Two capabilities in different enterprise domains that share a tail (`X.CAP.SUP.002.BEN` and
  `Y.CAP.SUP.002.BEN`) still collide if they run in one cluster. Nothing runs that way today. If
  it ever does, the fix is a declared prefix, not a longer derived one.
- The platform stand-in's object names (`{run_id}-platform-…`) come from the use's own manifests
  and are not checked here. They fall under the same limit.
