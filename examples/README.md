# A worked example — one actor, one capability

This folder teaches `foundry-task-orchestration-actor` by showing a complete, working **use** of it:
[`ACME.PARTS.CAP.SUP.007.WID-task-orchestration/`](ACME.PARTS.CAP.SUP.007.WID-task-orchestration/).

The capability is fictional. Everything else is real — this is the exact folder CI and the release
workflow run their gates against, so nothing here can quietly stop being true.

---

## What this actor does

One sentence: **it gets the tester and the implementer to agree on what a task means, then drives
build → deploy → test → retry until the agreed surface holds, and opens the pull requests.**

```
  a caller                  this actor                                 its peers / the world
  ────────                  ──────────                                 ─────────────────────
  POST /orchestrate-task ─▶ round 0
   task_id, title,            propose-acceptance (task, components) ──▶ testing actor
   definition_of_done,        assess-task (task, expectations)      ──▶ implementation actor
   context?                   disagreement → stop, stage round-0
                            attempt 1..3
                              implement-task (surface, remediation?) ──▶ implementation actor
                              test-task (surface, components)        ──▶ testing actor
                              namespace test-<task_id>:              ──▶ the cluster
                                stand-in, components, test Job,
                                teardown
                            green → paired pull requests              ──▶ GitHub
  ◀──────────────────────── {accepted, pr_url, …, acceptance_surface}
                            or {accepted: false, because, stage, …}
```

It **never writes code, never writes tests, and never merges.** Every decision is a fixed rule,
which is why its door names no engine.

---

## The one idea: a definition, and a use

This package **is** the actor: four cards and the machinery behind them. A **use** is one
capability's folder, holding what binds it to that capability — and nothing else:

```
  foundry-task-orchestration-actor     ACME.PARTS.CAP.SUP.007.WID-task-orchestration
  (the definition — this package)      (a use — this folder)
  ├── cards/                           ├── actor-agentic-context.yaml  ← THE BINDING. Written.
  ├── the machinery                    ├── platform-standin/           ← what a component needs
  └── the image                        │                                  beside it in a test run
                                       └── Dockerfile                  ← 3 lines, one a gate
```

The four cards are rendered into the image at `docker build` time.

---

## Read the sidecar

Open [`ACME.PARTS.CAP.SUP.007.WID-task-orchestration/actor-agentic-context.yaml`](ACME.PARTS.CAP.SUP.007.WID-task-orchestration/actor-agentic-context.yaml):

**`capability` and `source_repo`** — the only two identifiers anyone writes.

**`components`** — names only. Round 0 happens before anything is built, so the tester is told what
the capability is made of from here; an attempt deploys only what implementation actually touched.

**`peers`** — absent here, so both are derived: `acme-lab/ACME.PARTS.CAP.SUP.007.WID-implementation`
at `http://foundry-acme-parts-cap-sup-007-wid-implementation`, and the same for `-testing`.

**`ephemeral`** — the stand-in folder and the Secrets each component's manifest reads, pointing at
it. These name a database and credentials, so they are the capability's, not the package's.

---

## Try it

```bash
foundry-task-orchestration-actor lint examples/ACME.PARTS.CAP.SUP.007.WID-task-orchestration
foundry-task-orchestration-actor show examples/ACME.PARTS.CAP.SUP.007.WID-task-orchestration \
  --registry registry.example.com
```

`show` prints every name one attempt at `TASK-NNN` will use — the namespace, each component's
Deployment and URL, each Secret, both image refs — before a single peer session is spent.

To build it you need the base image; against one you built locally:

```bash
uv build && docker build -f docker/Dockerfile -t foundry-task-orchestration-actor:0.1.1 .
docker build --build-arg ACTOR_IMAGE=foundry-task-orchestration-actor:0.1.1 \
  -t acme-wid-orchestration examples/ACME.PARTS.CAP.SUP.007.WID-task-orchestration
docker run --rm -p 8080:8080 acme-wid-orchestration
curl localhost:8080/health
```

---

## What a real request looks like

```bash
curl -X POST http://<actor>/orchestrate-task -H 'Content-Type: application/json' \
  -d '{"from": "you", "payload": {
        "task_id": "TASK-014",
        "title": "Seed three widgets, one per status",
        "definition_of_done": ["GET /widgets/{id} returns 200 for each seeded widget"]
      }}'
```

When the tester cannot tell what to assert, it answers early, and nothing is built:

```json
{"accepted": false, "stage": "round-0", "attempts": 0,
 "because": "round 0: the task does not determine what the testing actor must assert — …",
 "acceptance_surface": [{"id": "E1", "statement": "…", "handle": "BACKEND_URL"}],
 "open_questions": [{"about": "E1", "question": "which widget ids must be stable?"}]}
```

When it all holds:

```json
{"accepted": true, "attempts": 2, "verdict": "4/4 criteria passed",
 "branch": "impl/TASK-014", "test_branch": "test/TASK-014",
 "pr_url": "https://github.com/acme-lab/…-implementation/pull/12",
 "test_pr_url": "https://github.com/acme-lab/…-testing/pull/9",
 "acceptance_surface": [{"id": "E1", "statement": "…", "handle": "BACKEND_URL",
                         "commitments": [{"expectation_id": "E1", "statement": "ids …"}]}]}
```

---

## Instantiate one for your own capability

1. **Copy this folder**, renamed after your capability's orchestration repo.
2. **Edit the sidecar**: `capability`, `source_repo`, `components`; `peers` only if they do not
   live where the convention says; `ephemeral` for whatever your components need.
3. **Replace `platform-standin/`** with your components' stand-ins, or delete it and the
   `ephemeral.platform` line and the second `COPY`.

Then `lint`, `show`, and `docker build`.
