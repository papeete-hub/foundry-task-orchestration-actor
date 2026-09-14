"""A stopped round, sent as an issue — the labels, what the body says, and no duplicates."""
from __future__ import annotations

import pytest

from foundry_task_orchestration_actor import issues, pulls

FIELDS = {
    "acceptance_surface": [{"id": "E1", "statement": "three widgets are seeded", "handle": "h"},
                           {"id": "E2", "statement": "GET /widgets/{id} returns 200"}],
    "open_questions": ["which three statuses?",
                       {"about": "E2", "question": "is 404 in scope?"}],
    "objections": [{"id": "E1", "kind": "not-determined", "why": "no ARCHIVE command exists",
                    "counter_proposal": "seed through the stub"}],
    "commitments": ["E2: mounted at the root"],
}


class FakeGitHub:
    """`pulls.github_request`, recording `(method, url, body)` and answering by route."""

    def __init__(self, open_issues=(), label_error=None):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.open_issues = list(open_issues)
        self.label_error = label_error

    def __call__(self, url, token, *, method, body=None):
        self.calls.append((method, url, body))
        if url.endswith("/labels"):
            if self.label_error:
                raise self.label_error
            return {"name": body["name"]}
        if method == "GET":
            return self.open_issues
        if url.endswith("/comments"):
            return {"html_url": "https://github.example/o/r/issues/5#issuecomment-1"}
        return {"html_url": "https://github.example/o/r/issues/9", "number": 9}

    def posted(self, suffix: str) -> list[dict]:
        return [b for m, u, b in self.calls if m == "POST" and u.endswith(suffix)]


@pytest.fixture
def github(monkeypatch):
    def _install(**kwargs) -> FakeGitHub:
        fake = FakeGitHub(**kwargs)
        monkeypatch.setattr(pulls, "github_request", fake)
        return fake
    return _install


def test_the_labels_are_the_join_key_the_sender_and_needs_info(config):
    assert issues.task_label(config, "TASK-042") == "task:ACME.PARTS.CAP.SUP.007.WID/TASK-042"
    assert issues.from_label(config) == "from:acme.parts.cap.sup.007.wid-task-orchestration"


def test_the_title_counts_questions_and_objections():
    assert issues.issue_title("TASK-042", FIELDS) == \
        "[TASK-042] round 0 stopped: 2 open question(s), 1 objection(s)"
    assert issues.issue_title("TASK-042", {"open_questions": ["q"]}) == \
        "[TASK-042] round 0 stopped: 1 open question(s), 0 objection(s)"


def test_the_body_is_a_checklist_saying_who_raised_what_about_which_expectation(config):
    body = issues.issue_body(config, "TASK-042", FIELDS, "abc123")
    assert ("- [ ] **open question** — raised by testing "
            "(`ACME.PARTS.CAP.SUP.007.WID-testing`): which three statuses?") in body
    assert "raised by testing (`ACME.PARTS.CAP.SUP.007.WID-testing`), on `E2`: is 404 in scope?" \
        in body
    assert ("- [ ] **objection** — raised by implementation "
            "(`ACME.PARTS.CAP.SUP.007.WID-implementation`), on `E1`: no ARCHIVE command exists"
            ) in body
    assert "counter-proposal: seed through the stub" in body
    assert "- **E1** three widgets are seeded" in body
    assert "- **E2** GET /widgets/{id} returns 200" in body
    assert "E2: mounted at the root" in body
    assert "Correlation id: `abc123`" in body


def test_a_new_issue_is_opened_with_the_three_labels(config, github):
    fake = github()
    url = issues.report_round0(config, "acme-lab/backlog", "TASK-042", FIELDS, "abc123", "t")

    assert url == "https://github.example/o/r/issues/9"
    assert [b["name"] for b in fake.posted("/labels")] == [
        "task:ACME.PARTS.CAP.SUP.007.WID/TASK-042", "needs-info",
        "from:acme.parts.cap.sup.007.wid-task-orchestration"]
    get = [(u, b) for m, u, b in fake.calls if m == "GET"]
    assert get == [("https://api.github.com/repos/acme-lab/backlog/issues?"
                    "labels=task%3AACME.PARTS.CAP.SUP.007.WID%2FTASK-042&state=open", None)]
    [created] = fake.posted("/repos/acme-lab/backlog/issues")
    assert created["title"] == "[TASK-042] round 0 stopped: 2 open question(s), 1 objection(s)"
    assert created["labels"] == ["task:ACME.PARTS.CAP.SUP.007.WID/TASK-042", "needs-info",
                                 "from:acme.parts.cap.sup.007.wid-task-orchestration"]
    assert "- [ ] **open question**" in created["body"]
    assert not fake.posted("/comments")


def test_an_open_issue_for_the_task_is_commented_on_not_duplicated(config, github):
    fake = github(open_issues=[
        {"number": 3, "html_url": "https://github.example/o/r/pull/3", "pull_request": {}},
        {"number": 5, "html_url": "https://github.example/o/r/issues/5"},
    ])
    url = issues.report_round0(config, "acme-lab/backlog", "TASK-042", FIELDS, "abc123", "t")

    assert url == "https://github.example/o/r/issues/5"
    assert not fake.posted("/repos/acme-lab/backlog/issues"), "no second issue"
    [comment] = fake.posted("/issues/5/comments")
    assert "round 0 stopped" in comment["body"] and "- [ ] **objection**" in comment["body"]


def test_a_label_that_already_exists_is_not_an_error(config, github):
    github(label_error=pulls.PullRequestError(
        'POST …/labels failed (422): {"errors":[{"code":"already_exists"}]}', status=422))
    assert issues.report_round0(config, "acme-lab/backlog", "TASK-042", FIELDS, "c", "t")


@pytest.mark.parametrize("error", [
    pulls.PullRequestError("POST …/labels failed (403): Resource not accessible", status=403),
    pulls.PullRequestError('POST …/labels failed (422): {"code":"invalid"}', status=422),
])
def test_any_other_label_failure_raises(config, github, error):
    github(label_error=error)
    with pytest.raises(pulls.PullRequestError):
        issues.report_round0(config, "acme-lab/backlog", "TASK-042", FIELDS, "c", "t")


def test_a_malformed_repo_or_an_over_long_label_raises_before_any_call(config, github):
    fake = github()
    with pytest.raises(pulls.PullRequestError, match="owner"):
        issues.report_round0(config, "not-a-repo", "TASK-042", FIELDS, "c", "t")
    with pytest.raises(pulls.PullRequestError, match="50 characters"):
        issues.report_round0(config, "acme-lab/backlog", "TASK-" + "9" * 30, FIELDS, "c", "t")
    assert fake.calls == []
