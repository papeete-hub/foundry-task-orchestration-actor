---
id: ADR-FTOA-0001
title: "The machinery leaves the capability — the orchestration kind gets the implementation kind's shape"
status: Proposed
date: 2026-09-14
supersedes: []
references:
  - ../src/foundry_task_orchestration_actor/config.py
  - ../src/foundry_task_orchestration_actor/deploy.py
  - ../src/foundry_task_orchestration_actor/kubeconfig.py
  - ../docker/Dockerfile
  - https://github.com/papeete-hub/foundry-implementation-actor/blob/main/adr/ADR-FIA-0001-the-machinery-leaves-the-capability.md
  - https://github.com/papeete-hub/foundry-implementation-actor/blob/main/adr/ADR-FIA-0005-the-actor-ships-an-image.md
  - https://github.com/papeete-hub/foundry-implementation-actor/blob/main/adr/ADR-FIA-0006-the-image-is-published-to-two-registries.md
---

# ADR-FTOA-0001 — The machinery leaves the capability

## Context

`foundry-implementation-actor` turned one capability's hand-written implementation actor into a
published package and a base image; a use of it is now a sidecar and a four-line Dockerfile
(ADR-FIA-0001, -0005, -0006). The orchestration actor for that same capability was still a
hand-written repository: an `app.py`, an `entrypoint.sh` rendering a kubeconfig, an ~830-line
`handler.py`, a verbatim `correlation.py`, four cards, a platform stand-in, and a Dockerfile pinning
the papeete stack package by package.

Nothing about that machinery was capability-specific except the literals it was written with — and
there were more of them than in the implementation actor's case, because they sat inside f-strings
in the deploy steps: the id, both peer repositories, the registry path, the image prefix, the k8s
workload prefix every Deployment, Service and Secret name was built from, the Postgres user and
database the backend expected, and a kube context named after a developer's laptop.

It had also drifted in ways a package would have caught: its cards and README still described
`papeete-deploy resolve --registry local`, a step the code no longer took; its data dictionary named
the branch `feat/TASK-NNN-actor` while the implementation actor pushed `impl/TASK-NNN`; its
Dockerfile pinned `papeete-product`, which it never imported, and took `kubectl` from
`bitnami/kubectl:latest`.

## Decision

**1. A standalone package and base image, `foundry-task-orchestration-actor`**, with the same
shape as the implementation actor's: `config` / `instance` / `conformance` / `correlation` /
`serve` / `cli`, four cards in the wheel rendered into a use at `docker build`, a `docker/Dockerfile`
whose `CMD` is `serve /actor`, and the same CI and two-registry release. That machinery is
**copied, with names changed**, not shared: there is no common kit. `correlation.py` stays
byte-identical below its docstring across all three.

**2. Two declared fields, everything else derived.** `capability` and `source_repo`, as before;
plus, because an orchestrator has facts an implementer does not: `components` (names only — round
0 needs them before anything is built, ADR-FTOA-0002), optional `peers` (each role's `repo` and
`url` default to `<owner>/<capability>-<role>` and `http://foundry-<capability slug>-<role>`;
`IMPLEMENTATION_URL`/`TESTING_URL` still win), and optional `ephemeral` (a platform stand-in folder
and per-component Secret templates).

**3. What was capability-specific in the deploy steps becomes derived or declared, never
generic-by-assumption.** Workload names derive from the id's tail. The platform stand-in and the
Secrets pointing at it are the **use's** — they name a database and credentials that belong to one
capability — so they are declared (`ephemeral.platform` is a folder beside the sidecar;
`ephemeral.secrets` are templates) rather than shipped in the wheel. Platform readiness waits on
whatever Deployments the stand-in renders, discovered by papeete-deploy's product label.

**4. Operational tuning is `Settings`, not sidecar.** Attempts, door and round-0 timeouts,
readiness and Job timeouts, kube context, image registry, pull Secret name, PR base branch: a
constructor keyword each, filled from the environment by `serve` — the line ADR-FIA-0002/0004 drew
for that actor's own timeouts.

**5. The kubeconfig bootstrap moves into `serve`**, as a function (`kubeconfig.bootstrap`), not a
shipped shell entrypoint. It names the ServiceAccount token by path (`tokenFile`) so a rotated
projected token is picked up, where the script embedded the copy it read at start.

**6. The known drift is fixed in the move**: the `resolve` prose is gone, `branch` is documented as
`impl/TASK-NNN`, `papeete-product` is not a dependency, `kubectl` is pinned from
`registry.k8s.io/kubectl`.

## Rationale

**Why copy rather than extract a shared kit.** Three consumers of one shape is the point at which a
kit starts to look free, and it is not: the implementation actor's `serve` wires an engine, this
one wires none and writes a kubeconfig; its `lint` checks grounding, this one checks a platform
folder. A kit would need hooks for exactly those differences, and FIA's own scope rule —
*generic across capabilities, not across actor kinds* — says not to build them for a guess. A
copy that names its origin is cheap to diff and costs nothing to diverge.

**Why the stand-in is declared, not shipped.** A Postgres and a RabbitMQ with neutral credentials
could be shipped, and would be generic; the Secrets pointing a component at them would then have to
agree with credentials the package chose, and the database name a component migrates into would be
this package's opinion. The use already owns the component's manifests that read those Secrets; it
should own what they point at.

**Why `foundry-` is in the derived URL.** It is the product this family of actors is deployed as;
papeete-deploy prefixes every Service with it. A use deployed under another product declares
`peers.<role>.url`, or sets the environment — which is what every live deployment does anyway.

## Consequences

- **A use becomes a sidecar, a platform folder if it needs one, and a three-line Dockerfile.** The
  first is the capability this was extracted from; its migration is a separate change in that repo.
- **The failed result gained `stage` and the round-0 fields (ADR-FTOA-0002).** A use still carrying
  the old hand-copied cards fails `conformance.check`, which is the migration signal.
- **The component layout is a convention now written down once**: a component's kustomize folder
  is `<component>/deployment/dev` with a `k8s/overlays/ephemeral/` in the implementation repo, and
  its Deployment/Service is `<id tail, dots to hyphens>-<component>`. A capability that lays out its
  components otherwise cannot use this actor until that is declared rather than assumed — not done
  here, because no such capability exists.
- **Environment variable names for component URLs** turn a hyphen into an underscore
  (`API_GATEWAY_URL`); identical to before for every component name in use.
