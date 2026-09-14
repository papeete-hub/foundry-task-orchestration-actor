"""The paired pull requests — what the body says, and what is required versus best-effort."""
from __future__ import annotations

import pytest

from foundry_task_orchestration_actor import pulls

SURFACE = [
    {"id": "E1", "statement": "three widgets are seeded", "handle": "BACKEND_URL",
     "commitments": [{"expectation_id": "E1", "statement": "ids 1, 2, 3"}]},
    {"id": "commitment-1", "statement": "the stub answers on 8000", "handle": None,
     "commitment": "the stub answers on 8000"},
]


def test_the_body_renders_the_agreed_surface(config):
    body = pulls.pr_body(config, "TASK-042", "Supply the widget", SURFACE, [],
                         "2/2 criteria passed", {"backend": "0.1.0-a"}, {"backend": "0.1.0-b"})
    assert "Agreed acceptance surface" in body
    assert "- **E1** three widgets are seeded — handle: `BACKEND_URL`" in body
    assert "committed:" in body and "ids 1, 2, 3" in body
    assert "- **commitment-1** the stub answers on 8000" in body
    assert "ACME.PARTS.CAP.SUP.007.WID-testing" in body
    assert "- `backend`: `0.1.0-a`" in body


def test_only_the_implementation_pr_is_required(config, monkeypatch):
    calls = []

    def fake_request(url, token, *, method, body):
        calls.append(url)
        if "WID-testing/pulls" in url:
            raise pulls.PullRequestError("422 no commits between main and test/TASK-042")
        return {"html_url": "https://github.example/impl/pull/7", "number": 7}

    monkeypatch.setattr(pulls, "github_request", fake_request)
    urls = pulls.open_prs(config, task_id="TASK-042", title="t", branch="impl/TASK-042",
                          test_branch="test/TASK-042", base="main", surface=SURFACE,
                          criteria=[], verdict="1/1", code_images={}, test_images={},
                          full_log="1 passed", token="t")
    assert urls == {"pr_url": "https://github.example/impl/pull/7"}
    assert any(url.endswith("/issues/7/comments") for url in calls), "the log is still posted"


def test_a_failed_implementation_pr_raises(config, monkeypatch):
    def fake_request(url, token, *, method, body):
        raise pulls.PullRequestError("403")

    monkeypatch.setattr(pulls, "github_request", fake_request)
    with pytest.raises(pulls.PullRequestError):
        pulls.open_prs(config, task_id="TASK-042", title="t", branch="b", test_branch="tb",
                       base="main", surface=[], criteria=[], verdict="v", code_images={},
                       test_images={}, full_log="", token="t")
