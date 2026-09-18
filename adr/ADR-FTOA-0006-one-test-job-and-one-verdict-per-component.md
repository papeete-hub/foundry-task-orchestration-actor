---
id: ADR-FTOA-0006
title: "One test Job and one verdict per component"
status: Proposed
date: 2026-09-18
supersedes: []
references:
  - ../src/foundry_task_orchestration_actor/deploy.py
  - ../src/foundry_task_orchestration_actor/config.py
  - ./ADR-FTOA-0005-an-attempts-namespace-carries-its-capability.md
---

# ADR-FTOA-0006 — One test Job and one verdict per component

## Context
The testing actor publishes one test image per touched component
(`<registry>/<capability_path>/<component>/tests:<version>`), and an attempt runs each of them
against the namespace. Until 0.6.0 every one of those Jobs was applied under the same name,
`{run_id}-test-job`. A Job's pod template is immutable, so applying the second component's Job,
with a different image under the name the first one already holds, is refused by the API server.
The attempt then failed with a `DeployError` carrying kubectl's `field is immutable`, which reads
like a cluster fault rather than this package's. No attempt that touched two components has ever
run. The code said "unverified"; it was broken.

The results were also summed. `5/5` from one component and `0/0` from another read as a green
`5/5`, and a failing criterion did not say which component it came from.

## Decision
- **The Job is named for its component:** `{run_id}-test-{component}`. `component` (the one
  whose test image the Job runs) is a new keyword-only parameter of `test_job_manifest` and
  `run_test_job`. It is separate from `components` (every touched component). Each of those
  still becomes a `<COMPONENT>_URL` on every Job, so a black-box test can still cross from one
  component to another.
- **Name budget.** `longest_name` counts the per-component name for each declared component
  alongside that component's workload. `test-` (5 characters) is shorter than any workload prefix
  (`sup-007-wid-`, 12), so each Job name is shorter than its own component's Deployment name. The
  budget from ADR-FTOA-0005 does not move, and a test asserts that.
- **Results are kept per component.** `TestRun` holds one `ComponentRun` (passed, total, failed
  lines, log) per test image. `passed`, `total`, `criteria` and `logs` are derived from those
  results, so the handler and the pull request bodies keep their types.
- **`green` is `all(...)`.** Every component must have collected at least one test and passed all
  of them. A run with no components is still not green.
- **`verdict` appends the breakdown:** `8/8 criteria passed — bff 5/5, frontend 3/3`. With one
  component it reads `8/8 criteria passed — stub 8/8`. The leading clause is unchanged, so anything
  reading it the old way still does. `verdict` is still a string.
- **Each failing criterion names its component:** `❌ [frontend] tests/…::test_x`. It still starts
  with `❌`, which is what the remediation context selects on.
- **Each component's log tail goes into its `component-tested` record**, capped at
  `DIAGNOSTIC_CHARS`. A red attempt opens no pull request, so that record is the only place its
  evidence outlives the namespace.

## Rationale
A per-component name removes the rejection without changing what a Job is. One Job still runs one
image with every touched component's address. The only other way to avoid the collision would be
to delete each Job before the next one, which throws away the first component's evidence before
the attempt has a verdict.

The verdict is a wire field: `orchestrate-task`'s `verdict`, the `## Verdict` section of both pull
requests, and the fallback text of the retry's remediation context. That is why the old clause
stays first and the breakdown comes after it, rather than a new format replacing it.

`{component}` could now be offered to `ephemeral.test_env` templates, because each Job has exactly
one component. It is deliberately not offered yet. Whether a test's environment may differ by
component belongs to the change that makes `<COMPONENT>_URL` conditional (`depends_on`). If the
placeholder were offered now, that change would inherit it instead of deciding it.

## Consequences
- **Behaviour change:** a component whose suite collects zero tests now fails the attempt. Before,
  it added nothing to a sum that could still pass. This is the same silent shrinking that
  `depends_on` is meant to close, and it can turn a shape that used to pass into a failing one.
- A consumer that compared `verdict` for equality with `N/N criteria passed` now sees the
  breakdown after it. A consumer that matched the prefix is unaffected.
- The new names differ from the old one, so a stopped 0.5.0 attempt's leftover `…-test-job` is
  not named by 0.6.0. Teardown's label sweep and namespace deletion remove it all the same.
- Not verified live until a capability with two components actually runs an attempt. The first
  such attempt is also the first time that capability's second component and its test image are
  ever published.
