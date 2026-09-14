"""The pair of pull requests — opened only once testing has passed, and never merged.

WHY THIS ACTOR, AND WHY ONLY NOW. The implementation actor pushes a branch and publishes images;
the testing actor pushes a branch and publishes a test image. Neither opens a pull request,
because neither knows whether the other's half passed. This actor is the one place both verdicts
meet, so it is the one place a PR may be opened — and it opens one only on a green verdict.

TWO REPOS, TWO PRs, ONE PAIRING KEY. Two independent commits, two independent
`papeete_version.compute()` calls: a shared version is neither guaranteed nor meaningful across
them. What IS shared, by construction, is `task_id` — both publishing actors pass it as the feature
name, so every version on both sides already carries it. The pairing convention makes that
visible rather than inventing another: both PR titles start with the identical `[<task_id>]` tag,
both bodies list both repos' images side by side, and once both exist each is commented with a
link to the other.

THE AGREED SURFACE IS IN THE BODY. Round 0 is the reason a reviewer can trust the tests are not a
description of the build: they were proposed before it existed (ADR-FTOA-0002). A PR that showed
only a pass count would hide the one artefact that says what was agreed, so the body renders it —
each expectation's id, statement and handle, and the commitments the implementation actor made.

WHAT IS REQUIRED AND WHAT IS BEST-EFFORT. The implementation PR is required —
`orchestration-succeeded-result` requires `pr_url` — so its failure raises. The testing PR
(`test_pr_url` is optional precisely so its failure does not cost the one that opened), the
cross-link comments and the test-log comment are best-effort, each a correlated WARNING if missed.
"""
from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.request

from . import correlation
from .config import CapabilityConfig

# A PR comment over this many characters gets the full pytest log swapped for a link to a committed
# copy instead. GitHub's own cap is far higher; this is where a raw log stops being something a
# reviewer scans inline.
INLINE_LOG_LIMIT = 4000

GITHUB_API = "https://api.github.com"


class PullRequestError(RuntimeError):
    """A GitHub API call this actor needed failed.

    `status` is the HTTP status when GitHub answered at all, None when it could not be reached —
    so a caller can tell "that label already exists" (422) from "the token may not do this" (403)
    without matching on the message."""

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }


def github_request(url: str, token: str, *, method: str, body: dict | None = None) -> dict | list:
    """One GitHub REST call, its JSON answer returned. `body=None` sends no body at all — a GET —
    and a list endpoint answers a list, which is why the return type is not only `dict`."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=_headers(token))
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as e:
        raise PullRequestError(f"{method} {url} failed ({e.code}): "
                               f"{e.read().decode(errors='replace')[:2000]}",
                               status=e.code) from e
    except urllib.error.URLError as e:
        raise PullRequestError(f"could not reach GitHub for {method} {url}: {e}") from e


def _surface_lines(surface: list) -> str:
    if not surface:
        return "(none)"
    lines = []
    for entry in surface:
        if not isinstance(entry, dict):
            lines.append(f"- {entry}")
            continue
        handle = f" — handle: `{entry['handle']}`" if entry.get("handle") else ""
        lines.append(f"- **{entry.get('id', '?')}** {entry.get('statement', '')}{handle}")
        for commitment in entry.get("commitments") or ():
            text = commitment if isinstance(commitment, str) else json.dumps(
                commitment, sort_keys=True, ensure_ascii=False, default=str)
            lines.append(f"  - committed: {text}")
    return "\n".join(lines)


def pr_body(config: CapabilityConfig, task_id: str, title: str, surface: list,
            criteria: list[str], verdict: str, code_images: dict[str, str],
            test_images: dict[str, str]) -> str:
    def images(found: dict[str, str]) -> str:
        return "\n".join(f"- `{c}`: `{v}`" for c, v in sorted(found.items())) or "(none)"

    implementation, testing = config.implementation.actor_name, config.testing.actor_name
    return (
        f"Task: {task_id} — {title}\n\n"
        f"## Agreed acceptance surface (round 0)\n\n"
        f"Proposed by {testing} before anything was built, assessed and committed to by "
        f"{implementation}:\n\n{_surface_lines(surface)}\n\n"
        f"## Verdict ({testing})\n\n{verdict}\n"
        + ("\n".join(criteria) + "\n" if criteria else "")
        + f"\n## Published component images ({implementation})\n\n{images(code_images)}\n\n"
        f"## Published test images ({testing})\n\n{images(test_images)}\n\n---\n"
        f"Opened automatically by `{config.actor_name}` (`{config.capability}`), once {testing} "
        f"reported every criterion passing. No actor merges — a human reviews and merges this PR "
        f"and its `[{task_id}]` twin in the other repository, together."
    )


def _create_pr(repo: str, title: str, head: str, base: str, body: str, token: str) -> dict:
    return github_request(f"{GITHUB_API}/repos/{repo}/pulls", token, method="POST",
                          body={"title": title, "head": head, "base": base, "body": body})


def _comment(repo: str, number: int, body: str, token: str) -> None:
    github_request(f"{GITHUB_API}/repos/{repo}/issues/{number}/comments", token, method="POST",
                   body={"body": body})


def _commit_log_blob(config: CapabilityConfig, branch: str, task_id: str, content: str,
                     token: str) -> str:
    """Commit the full test-job log onto the testing branch via the Contents API — no clone needed —
    and return a COMMIT-pinned blob URL: a branch tip moves, and the branch itself disappears once
    its PR merges, while a commit sha never does."""
    repo = config.testing.repo
    path = f"test-results/{task_id}.log"
    result = github_request(
        f"{GITHUB_API}/repos/{repo}/contents/{path}", token, method="PUT",
        body={"message": f"test({task_id}): record test-job log ({config.capability})",
              "content": base64.b64encode(content.encode()).decode(), "branch": branch},
    )
    return f"https://github.com/{repo}/blob/{result['commit']['sha']}/{path}"


def _log_comment(full_log: str, blob_url: str | None) -> str:
    if blob_url:
        return ("**Test run log** — too long to inline; the full output was committed alongside "
                f"the test artifacts: {blob_url}")
    return ("**Test run log**\n\n<details><summary>pytest output</summary>\n\n"
            f"```\n{full_log}\n```\n\n</details>")


def open_prs(config: CapabilityConfig, *, task_id: str, title: str, branch: str,
             test_branch: str, base: str, surface: list, criteria: list[str], verdict: str,
             code_images: dict[str, str], test_images: dict[str, str], full_log: str,
             token: str) -> dict:
    """Open the pair. Returns `{"pr_url": ..., "test_pr_url"?: ...}`; raises `PullRequestError`
    only when the implementation PR itself could not be opened."""
    body = pr_body(config, task_id, title, surface, criteria, verdict, code_images, test_images)
    implementation, testing = config.implementation, config.testing

    def best_effort(what: str, step):
        try:
            return step()
        except PullRequestError as e:
            correlation.event("pr-step-failed", level=logging.WARNING, what=what, error=str(e))
            return None

    with correlation.stage("open-pr", repo=implementation.repo, branch=branch):
        impl_pr = _create_pr(implementation.repo,
                             f"[{task_id}] {title} — implemented by {implementation.actor_name}",
                             branch, base, body, token)
    correlation.event("pr-opened", repo=implementation.repo, url=impl_pr["html_url"])
    result = {"pr_url": impl_pr["html_url"]}

    test_pr = best_effort("open the testing PR", lambda: _create_pr(
        testing.repo, f"[{task_id}] {title} — tested by {testing.actor_name}",
        test_branch, base, body, token))
    if test_pr:
        result["test_pr_url"] = test_pr["html_url"]
        correlation.event("pr-opened", repo=testing.repo, url=test_pr["html_url"])
        best_effort("cross-link the paired PRs", lambda: (
            _comment(implementation.repo, impl_pr["number"],
                     f"Paired PR: {test_pr['html_url']}", token),
            _comment(testing.repo, test_pr["number"], f"Paired PR: {impl_pr['html_url']}", token),
        ))

    blob_url = None
    if len(full_log) > INLINE_LOG_LIMIT:
        blob_url = best_effort("commit the test log blob", lambda: _commit_log_blob(
            config, test_branch, task_id, full_log, token))
    log_body = _log_comment(full_log, blob_url)
    for repo, pr in ((implementation.repo, impl_pr), (testing.repo, test_pr)):
        if pr:
            best_effort(f"post the test-log comment on {repo}#{pr['number']}",
                        lambda repo=repo, pr=pr: _comment(repo, pr["number"], log_body, token))
    return result
