# Decision log (`ADR-FTOA-*`)

Decisions owned by **this repo**: what this package derives, what a capability declares, and where
the line between the two sits — for the task-orchestration kind of foundry actor.

Not here: decisions this package inherits from the implementation actor it was modelled on — those
are `ADR-FIA-*` in [`foundry-implementation-actor`](https://github.com/papeete-hub/foundry-implementation-actor)
and are cited, not copied. Not here either: how a capability models itself (its own maps own that),
and how the ecosystem's artifacts are named or placed (`ADR-ECO-*` in
[`ecosystem-governance`](https://github.com/papeete-foundry/ecosystem-governance)).

## The log

| ID | Title | Status |
|----|-------|--------|
| [ADR-FTOA-0001](./ADR-FTOA-0001-the-machinery-leaves-the-capability.md) | The machinery leaves the capability — the orchestration kind gets the implementation kind's shape | Proposed |
| [ADR-FTOA-0002](./ADR-FTOA-0002-round-zero.md) | Round zero — this actor's half of the three amigos round | Proposed |
| [ADR-FTOA-0003](./ADR-FTOA-0003-datasets-travel-to-the-tester-only.md) | Datasets travel to the tester only — relayed unread, and the test Job is told what else it can reach | Proposed |
| [ADR-FTOA-0004](./ADR-FTOA-0004-a-stopped-round-is-sent-to-the-tasks-owner.md) | A stopped round is sent to the task's owner — as an issue, never written into its backlog | Proposed |

## Authoring

Copy [`template.md`](./template.md), take the next `NNNN`, keep it short, and link the canonical
source where the decision is implemented rather than restating it.
