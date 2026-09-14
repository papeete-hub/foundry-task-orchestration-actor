# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this
repository.

## What this is

`foundry-task-orchestration-actor` drives one capability's task through the three amigos round
(testing proposes, implementation assesses), then implement → ephemeral deploy → black-box test →
retry, and opens the paired pull requests on a pass. It is the **machinery** — it carries no
capability of its own and runs no engine. The capability arrives in a sidecar
(`actor-agentic-context.yaml`, contract `foundry-task-orchestration-actor/agentic-context/v1`).

Its shape is copied from `foundry-implementation-actor` (FIA) with names changed — see
`adr/ADR-FTOA-0001-*.md`. There is deliberately no shared kit between the two.

## Commands

```bash
uv run --extra dev pytest -q                                    # full suite (what CI runs)
uv run --extra dev pytest -q tests/test_round0.py               # one file
uv run --extra dev pytest -q tests/test_round0.py::test_name    # one test
uv build                                                        # sdist/wheel (hatchling)
uv run foundry-task-orchestration-actor lint <folder>           # validate a sidecar
uv run foundry-task-orchestration-actor show <folder> --registry r   # every derived rendering
```

There is no separate lint/format command configured in this repo.

## Architecture

Under `src/foundry_task_orchestration_actor/`:

- **`config.py`** — `CapabilityConfig`. Loads the sidecar and derives every identifier from
  `capability` and `source_repo`: peers' repos and URLs, registry path, workload names, image
  names, run ids, service URLs; renders `ephemeral.secrets` templates. Also `lint()` / `Report`.
- **`settings.py`** — `Settings`: operational tuning (attempts, timeouts, kube context, registry,
  pull secret, base branch, peer URL overrides), from constructor or environment. Never sidecar.
- **`round0.py`** — the three amigos round as a pure function over two injected door calls.
  All the wire contract's stop rules and the commitment attachment rule live here.
- **`peers.py`** — `call_door`: POST `{from, payload}` with a long timeout and `traceparent`;
  400/transport/non-object → `PeerError`.
- **`deploy.py`** — one attempt's ephemeral namespace: clone `impl/<task_id>` read-only, pull
  secret, platform stand-in, component secrets + components, test Jobs, best-effort teardown.
- **`pulls.py`** — the paired PRs; body renders the agreed surface. Implementation PR required,
  the rest best-effort.
- **`issues.py`** — a round stopped by open questions or objections, sent to the caller's
  `report_to` as an issue labelled `task:<capability>/<task_id>` (or a comment on the open one).
  Never reads or edits a task card; a failed report never costs the refusal (ADR-FTOA-0004).
- **`handler.py`** — `make_orchestrate_task(config, settings)`: round 0, the attempt loop, the
  `stage` of every failure.
- **`kubeconfig.py`** — in-cluster kubeconfig from the ServiceAccount (replaces `entrypoint.sh`).
- **`correlation.py`** — byte-identical to FIA's below the docstring.
- **`conformance.py`**, **`instance.py`**, **`serve.py`**, **`cli.py`** — FIA's, names changed;
  `serve` wires no engine and bootstraps the kubeconfig.

Committed contract, shipped in the wheel: `schemas/agentic-context.schema.yaml` and `cards/`.

## Core invariants that any change must preserve

- **The wire contract is shared with two other packages.** Door ids (`propose-acceptance`,
  `assess-task`, `implement-task`, `test-task`, `orchestrate-task`), message names, fields, and the
  round-0 rules are fixed across `foundry-implementation-actor`, `foundry-testing-actor` and this
  package. Do not change a field or a stop rule here alone.
- **Round 0 stops before implementation is asked** when the tester has open questions. Never
  "assess anyway".
- **The agreed surface goes to implement-task AND test-task on EVERY attempt.**
- **A failed result always names its `stage`**, and omits unknown fields rather than sending null
  — both completion schemas are closed and typed.
- **No capability literal, ever.** `tests/test_portability.py` greps `src/` for the originating
  instance's identifiers (id, workload prefix, database name, the hardcoded kube context). The
  platform stand-in and its credentials are the USE's (`ephemeral:`), never the wheel's.
- **The image ref is a three-way contract.** `<registry>/<capability_path>/<component>[/tests]:<v>`
  is parsed by path segment in `config.parse_images`; never re-derive or invent a tag.
- **Teardown is best-effort and always runs**, and deletes the namespace last.
- **The image is one build in two registries** (FIA's ADR-FIA-0006) — never a second `docker build`
  in `release.yml`, never a hardcoded registry.
- **`kubectl` is pinned** in `docker/Dockerfile`. Move it deliberately with the clusters it drives.

## Scope discipline

Generic across **capabilities**, not across actor *kinds*. No hooks or base classes for a
hypothetical second orchestration shape. A component layout other than
`<component>/deployment/dev/k8s/overlays/ephemeral` is not supported until a real capability needs
it — and then it is a sidecar declaration, not a strategy object.

Design rationale lives in `adr/`. Add a new ADR (copy `adr/template.md`) for any decision of
similar weight rather than only writing it into code comments.

## Releasing

Tag-triggered (`v*`) via `.github/workflows/release.yml`: PyPI through Trusted Publishing, the image
to GHCR and, when `vars.PRODUCT_IMAGE` names one, to a product's own registry. A manual run with a
tag input backfills an image only. `ci.yml` runs the suite, the wheel gates, and builds and boots
the example use — and reaches nothing outside its own checkout.
