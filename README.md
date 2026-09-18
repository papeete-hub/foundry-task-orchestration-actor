# foundry-task-orchestration-actor

Drives one task of one capability from agreement to pull request: a three amigos round between the
testing and implementation actors, then implement → deploy into an ephemeral namespace → black-box
test → retry, and the paired pull requests on a pass. It writes no code, writes no tests, judges
nothing and merges nothing.

An **actor, for one use**, with a [`papeete-actor`](https://github.com/papeete-hub/papeete-actor)
underneath. The `-actor` suffix is that claim (`ADR-ECO-0022`). It is the orchestration kind of the
shape [`foundry-implementation-actor`](https://github.com/papeete-hub/foundry-implementation-actor)
established; the machinery was copied from there with names changed (ADR-FTOA-0001).

```bash
pip install foundry-task-orchestration-actor
```

> **New here?** [`examples/`](examples/) walks through a complete, working use of this actor. Every
> command in it except `docker build` runs with no credentials and no network.

## What it is

The actor's **definition** — its four cards, and the machinery behind them. It carries **no
capability of its own**: the capability arrives in a sidecar the consuming repo writes:

```yaml
# actor-agentic-context.yaml
context: foundry-task-orchestration-actor/agentic-context/v1
capability: ACME.PARTS.CAP.SUP.007.WID
source_repo: acme-lab/ACME.PARTS.CAP.SUP.007.WID-task-orchestration
components: [backend, stub]
ephemeral:
  platform: platform-standin
  secrets:
    - name: "{workload}-db"
      data: {dsn: "postgresql://widgets:widgets@{run_id}-platform-postgres:5432/widgets"}
```

and, beside it, a Dockerfile of three lines:

```dockerfile
FROM ghcr.io/papeete-hub/foundry-task-orchestration-actor:0.4.0
COPY actor-agentic-context.yaml /actor/
COPY platform-standin /actor/platform-standin
RUN foundry-task-orchestration-actor render-cards /actor && foundry-task-orchestration-actor lint /actor
```

**That is the whole repository**, plus the platform stand-in folder if its components need one. No
cards, no `app.py`, no `entrypoint.sh`, no `handler.py`.

### Embedding it instead

```python
from foundry_task_orchestration_actor import CapabilityConfig, Settings, make_orchestrate_task

config = CapabilityConfig.load(".")
actor = Actor.from_card(".", mailbox=mailbox,
                        actions={"orchestrate-task": make_orchestrate_task(config, Settings())})
```

No `engines=`: the door names none. The mailbox and observability backend `serve` needs live in the
`[serve]` extra.

## The sidecar — `foundry-task-orchestration-actor/agentic-context/v1`

| field | required | what it is | default |
|---|---|---|---|
| `context` | yes | the contract string | — |
| `capability` | yes | `<ENT>.<DOMAIN>.CAP.<TYPE>.<NNN>.<CODE>` | — |
| `source_repo` | yes | `<owner>/<repo>` of this actor's own repo, and where both peers' are looked for | — |
| `components` | yes | component names, as the implementation actor's sidecar names them; round 0 sends them to the tester | — |
| `peers.implementation.repo` | no | where implementation is pushed and the PR is opened | `<owner>/<capability>-implementation` |
| `peers.implementation.url` | no | base URL of its doors | `http://foundry-<capability lowercased, dots→hyphens>-implementation` |
| `peers.testing.repo` | no | where tests are pushed and the paired PR is opened | `<owner>/<capability>-testing` |
| `peers.testing.url` | no | base URL of its doors | `http://foundry-<capability slug>-testing` |
| `ephemeral.platform` | no | folder beside the sidecar with `k8s/overlays/ephemeral/` — stand-ins applied before any component | none |
| `ephemeral.secrets[]` | no | `{name, data}` templates created for each touched component; `{run_id}`, `{component}`, `{workload}`, `{capability}` substituted | none |
| `ephemeral.test_env` | no | variables set on the test Job beside each `<COMPONENT>_URL` — by convention `AMQP_URL` for `via: event` datasets; `{run_id}`, `{capability}` substituted | none |

`IMPLEMENTATION_URL` / `TESTING_URL` in the environment override both the declared and the derived
URL. A misspelt role or key under `peers:` is refused rather than silently falling back to the
default. What is **not** in the sidecar, on purpose, is operational tuning — see *Settings*.

## One door

| door | verb | engine | what it does |
|---|---|---|---|
| `orchestrate-task` | request | none | round 0, then up to N attempts, then the PRs |

Request `orchestrate-task-cmd`: `task_id`, `title`, `definition_of_done`, optional `context`,
optional `report_to` (`owner/repo` where the task's owner hears about a stopped round).

Completions (closed; a reply matches exactly one):

- `orchestration-succeeded-result` — `accepted`, `pr_url`, `branch`, `test_branch`, `attempts`,
  `verdict`, `acceptance_surface`; optional `test_pr_url`.
- `orchestration-failed-result` — `accepted`, `because`, `stage`, `attempts`; optional `branch`,
  `test_branch`, `verdict`, `acceptance_surface`, `open_questions`, `objections`, `commitments`,
  `issue_url`. `stage` ∈ `round-0` | `implementation` | `testing` | `verdict`.

```
round 0      testing.propose-acceptance      → expectations, datasets (the tester's own), open_questions
             implementation.assess-task      → feasible, objections, commitments   (expectations only)
             stop (stage round-0) on a transport error, open questions, no expectations,
             infeasible, or any objection — carrying what was said
             report_to? open questions / objections → issue labelled task:<capability>/<task_id>
                        (or a comment on the one open) → issue_url
attempt n    implementation.implement-task   (acceptance_surface, remediation_context?)
             testing.test-task               (acceptance_surface, datasets, touched components)
             clone impl/<task_id> read-only → namespace test-<workload prefix>-<task_id> → pull Secret →
             platform stand-in → Secrets + each touched component → a test Job per test image,
             named for its component (every <COMPONENT>_URL + ephemeral.test_env) →
             teardown
             green → paired PRs, succeed      red → remediation context, next attempt
exhausted    stop (stage verdict)
```

The **agreed surface** is the tester's expectations, with each of the implementer's commitments that
names an expectation (`id`, `expectation` or `expectation_id`) attached under `commitments`, and
every other commitment appended as `{id: commitment-<n>, statement, handle, commitment}`. It goes to
both doors on every attempt and is rendered into the PR body. See ADR-FTOA-0002.

A **stopped round is sent, not written**: with `report_to`, open questions and objections become an
issue on that repository labelled `task:<capability>/<task_id>`, `needs-info` and
`from:<actor name>` — a checklist of who raised what about which expectation — or a comment on the
issue already open for the task. This actor never reads or edits the task's card; what the issue
means for it is the owning repository's rule. A peer that did not answer is not reported, and a
report that fails still returns the refusal. The token needs Issues: read and write there. See
ADR-FTOA-0004.

## Every rendering, from two fields

`foundry-task-orchestration-actor show` prints the table for a given sidecar:

| rendering | example |
|---|---|
| actor name | `ACME.PARTS.CAP.SUP.007.WID-task-orchestration` |
| registry path | `acme.parts/sup.007.wid` |
| component image | `<registry>/acme.parts/sup.007.wid/backend:<version>` |
| test image | `<registry>/acme.parts/sup.007.wid/backend/tests:<version>` |
| image name the base manifest carries | `acme.parts.cap.sup.007.wid-backend` |
| namespace / papeete-deploy product | `test-sup-007-wid-task-042` |
| component Deployment / Service | `test-sup-007-wid-task-042-sup-007-wid-backend` |
| test Job, one per component's test image | `test-sup-007-wid-task-042-test-backend` |
| what the test Job gets | `BACKEND_URL=http://test-sup-007-wid-task-042-sup-007-wid-backend` |

**The image ref is a three-way contract.** Both peers publish refs this actor parses back apart by
path segment; nothing is re-derived or looked up.

## Settings

Environment, read once at boot by `serve`; constructor keywords on `Settings` for an embedder.

| variable | default | |
|---|---|---|
| `IMAGE_REGISTRY` | — | where component and test images are pulled from; required once a run deploys |
| `IMPLEMENTATION_URL` / `TESTING_URL` | sidecar, else derived | |
| `MAX_ATTEMPTS` | `3` | |
| `DOOR_CALL_TIMEOUT_S` | `2400` | implement-task, test-task |
| `PROPOSE_TIMEOUT_S` / `ASSESS_TIMEOUT_S` | `1200` / `900` | round 0 |
| `DEPLOYMENT_READY_TIMEOUT_S` / `TEST_JOB_TIMEOUT_S` | `600` | |
| `KUBE_CONTEXT` | `in-cluster` | the context `serve` writes from the ServiceAccount |
| `IMAGE_PULL_SECRET` | `acr-pull` | copied from this Pod's namespace into each run's |
| `BASE_BRANCH` | `main` | what the PRs target |

`GITHUB_TOKEN` is the one credential: read on the implementation repo (the per-attempt clone),
`pull-requests:write` on both peer repos, `contents:write` on the testing repo (a long test log is
committed beside the tests and linked). No `CLAUDE_CODE_OAUTH_TOKEN` — nothing here runs a session.

## Running in a cluster

`serve` writes a kubeconfig from the Pod's projected ServiceAccount (token by path, so a rotation is
picked up) under the context `KUBE_CONTEXT` names, before the door opens. Outside a cluster it
writes nothing and says so. The ServiceAccount needs cluster-wide RBAC to create and delete
namespaces and manage Deployments, Services, Secrets, ServiceAccounts, Jobs and Pods/logs in them —
the namespace does not exist when the RBAC is written.

## CLI

```bash
foundry-task-orchestration-actor lint .                              # the sidecar, and the cards if rendered
foundry-task-orchestration-actor show . --registry reg.example.com   # every derived identifier
foundry-task-orchestration-actor render-cards .                      # the four cards, from the wheel
foundry-task-orchestration-actor serve .                             # boot it (needs the `serve` extra)
```

## Observability

The same record schema as the implementation actor — `correlation.py` is byte-identical below its
docstring: `{event: "step", step, phase, duration_ms}`, `{event: "event", step, **fields}`, with
`correlation_id` (the W3C trace id, propagated to both peers via `traceparent`), `task_id`,
`run_id` and `attempt` as structured metadata. Steps: `round-0`, `call-propose-acceptance`,
`call-assess-task`, `call-implement-task`, `call-test-task`, `clone-implementation`,
`deploy-pull-secret`, `deploy-platform-standin`, `deploy-component`, `run-test-job`, `teardown`,
`open-pr`. The step names are free to change; the record shape is not.

## Releasing, and which registry to pin

Identical to the implementation actor's (ADR-FIA-0006): a `v*` tag publishes the wheel to the feed
(Trusted Publishing) and one image build to `ghcr.io/papeete-hub/foundry-task-orchestration-actor`
and, when `vars.PRODUCT_IMAGE` names one, to a product's own registry. A manual run with a tag input
backfills an image without touching the feed or `latest`:

```bash
gh workflow run release.yml --ref main -f tag=v0.1.0
```

## Where this came from

Extracted from one capability's hand-written task-orchestration repository. Behaviour was ported,
with the capability's literals turned into derivations or sidecar declarations, round 0 added, and
known drift fixed (ADR-FTOA-0001). Decisions live in `adr/`.

## Development

```bash
uv run --extra dev pytest -q     # what CI runs
uv build
```

There is no separate lint/format command configured in this repo.
