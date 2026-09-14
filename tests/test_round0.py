"""Round 0 — the rules as a table, then the whole door against real HTTP peers.

The first half pins `round0.py` as the pure function it is. The second half boots the actor from
its own cards, points it at `FakePeers`, and sends it `orchestrate-task` through `Actor.receive()`
— so the request schema, the handler, the real `peers.call_door` and the closed completion
schemas are all in the path. Only the Kubernetes run (`deploy.deploy_and_test`) and GitHub
(`pulls.open_prs`, and `pulls.github_request` under a stopped round's issue) are replaced.
"""
from __future__ import annotations

import pytest
from papeete_actor_synchronous_messaging.actor import Actor

from foundry_task_orchestration_actor import cards_path, correlation, deploy, pulls, round0
from foundry_task_orchestration_actor.handler import make_orchestrate_task
from foundry_task_orchestration_actor.peers import PeerError
from foundry_task_orchestration_actor.settings import Settings

TASK = {"task_id": "TASK-042", "title": "Supply the widget",
        "definition_of_done": ["GET /widgets/{id} returns 200 for each seeded widget"]}

EXPECTATIONS = [
    {"id": "E1", "statement": "three widgets are seeded, one per status",
     "handle": "BACKEND_URL", "component": "backend"},
    {"id": "E2", "statement": "GET /widgets/{id} returns 200 for each",
     "handle": "GET /widgets/{id}"},
]


def _never(_payload):
    raise AssertionError("this door must not be called")


# ── the pure rules ──────────────────────────────────────────────────────────────────────────

def test_the_proposal_names_the_sidecar_components_and_forwards_the_task_unchanged():
    seen = {}

    def propose(payload):
        seen.update(payload)
        return {"expectations": EXPECTATIONS}

    round0.run({**TASK, "context": "ctx"}, ("backend", "stub"), propose=propose,
               assess=lambda p: {"feasible": True})
    assert seen == {**TASK, "context": "ctx", "components": ["backend", "stub"]}


def test_an_absent_context_is_not_sent_as_null():
    """`assess-task-cmd` and `propose-acceptance-cmd` are closed and type `context` a string."""
    payload = round0.propose_payload({**TASK, "context": None}, ["backend"])
    assert "context" not in payload


def test_open_questions_stop_the_round_before_the_implementation_actor_is_asked():
    outcome = round0.run(TASK, ["backend"], propose=lambda p: {
        "expectations": EXPECTATIONS, "open_questions": ["which three statuses?"]},
        assess=_never)
    assert not outcome.agreed
    assert outcome.fields == {"acceptance_surface": EXPECTATIONS,
                              "open_questions": ["which three statuses?"]}


def test_an_empty_proposal_stops_the_round():
    outcome = round0.run(TASK, ["backend"], propose=lambda p: {"expectations": []},
                         assess=_never)
    assert not outcome.agreed
    assert "no expectations" in outcome.because


def test_a_proposal_that_is_not_a_list_stops_the_round():
    outcome = round0.run(TASK, ["backend"], propose=lambda p: {"open_questions": []},
                         assess=_never)
    assert not outcome.agreed


@pytest.mark.parametrize("assessment", [
    {"feasible": False},
    {"feasible": True, "objections": [{"id": "E1", "why": "the task does not determine it"}]},
    {},
])
def test_anything_but_a_clean_yes_stops_the_round(assessment):
    outcome = round0.run(TASK, ["backend"], propose=lambda p: {"expectations": EXPECTATIONS},
                         assess=lambda p: {**assessment, "commitments": ["widget ids 1, 2, 3"]})
    assert not outcome.agreed
    assert outcome.fields["acceptance_surface"] == EXPECTATIONS
    assert outcome.fields["commitments"] == ["widget ids 1, 2, 3"]
    assert outcome.fields.get("objections") == assessment.get("objections")


def test_the_implementation_actor_is_handed_the_proposal_as_the_surface():
    seen = {}

    def assess(payload):
        seen.update(payload)
        return {"feasible": True}

    round0.run(TASK, ["backend"], propose=lambda p: {"expectations": EXPECTATIONS}, assess=assess)
    assert seen == {**TASK, "acceptance_surface": EXPECTATIONS}


@pytest.mark.parametrize("key", ["id", "expectation", "expectation_id"])
def test_a_commitment_naming_an_expectation_is_attached_to_it(key):
    commitment = {key: "E1", "statement": "ids 0190…a1, 0190…a2, 0190…a3"}
    surface = round0.attach_commitments(EXPECTATIONS, [commitment])
    assert surface[0]["commitments"] == [commitment]
    assert "commitments" not in surface[1]
    assert len(surface) == len(EXPECTATIONS)


def test_a_commitment_naming_nothing_becomes_an_entry_of_its_own():
    surface = round0.attach_commitments(EXPECTATIONS, [
        "the stub listens on 8000",
        {"statement": "events route on widget.seeded", "handle": "widget.seeded"},
        {"expectation": "E9", "what": "names an id nobody proposed"},
    ])
    assert surface[2] == {"id": "commitment-1", "statement": "the stub listens on 8000",
                          "handle": None, "commitment": "the stub listens on 8000"}
    assert surface[3]["id"] == "commitment-2"
    assert surface[3]["statement"] == "events route on widget.seeded"
    assert surface[3]["handle"] == "widget.seeded"
    assert surface[4]["id"] == "commitment-3"
    assert '"what": "names an id nobody proposed"' in surface[4]["statement"]


def test_a_prose_commitment_is_attached_by_the_ids_it_starts_with():
    """What the first live round actually sent: strings, prefixed with the ids they concern."""
    one = "E1: three widgets seeded with ids 0190…a1, 0190…a2, 0190…a3"
    both = "E1/E2: nothing else changes"
    surface = round0.attach_commitments(EXPECTATIONS, [one, both])
    assert surface[0]["commitments"] == [one, both]
    assert surface[1]["commitments"] == [both]
    assert len(surface) == len(EXPECTATIONS)


@pytest.mark.parametrize("text", [
    "BACKEND_URL / COMPONENT_PORT: confirmed as committed fact",   # names no proposed id
    "E1/E9: half of this names an id nobody proposed",             # all or nothing
    "Note: the stub listens on 8000",                               # a sentence, not an id list
    "the stub listens on 8000",
])
def test_a_prose_commitment_that_names_no_proposed_expectation_stays_its_own_entry(text):
    surface = round0.attach_commitments(EXPECTATIONS, [text])
    assert all("commitments" not in e for e in surface[:2])
    assert surface[2]["id"] == "commitment-1" and surface[2]["commitment"] == text


def test_the_shape_the_implementation_actor_is_now_asked_for_attaches_by_id():
    commitment = {"id": "E2", "commitment": "GET /widgets/{id} is mounted at the root"}
    surface = round0.attach_commitments(EXPECTATIONS, [commitment, {"id": None,
                                                                    "commitment": "port 8000"}])
    assert surface[1]["commitments"] == [commitment]
    assert surface[2]["statement"] == "port 8000"


def test_attaching_does_not_mutate_the_proposal():
    proposal = [dict(e) for e in EXPECTATIONS]
    round0.attach_commitments(proposal, [{"id": "E1"}])
    assert proposal == EXPECTATIONS


def test_a_peer_that_does_not_answer_stops_the_round():
    def propose(_payload):
        raise PeerError("unreachable")

    outcome = round0.run(TASK, ["backend"], propose=propose, assess=_never)
    assert not outcome.agreed
    assert "propose-acceptance did not answer" in outcome.because


# ── the whole door, against real HTTP peers ─────────────────────────────────────────────────

IMAGE = "reg.example.com/acme.parts/sup.007.wid/backend:0.1.0-task-042-abc1234"
TEST_IMAGE = "reg.example.com/acme.parts/sup.007.wid/backend/tests:0.1.0-task-042-def5678"


@pytest.fixture
def orchestrate(config, monkeypatch):
    """An actor booted from its own cards, whose door drives the given peer URLs."""
    monkeypatch.setenv("GITHUB_TOKEN", "t")

    def _boot(implementation_url: str, testing_url: str, *, payload: dict | None = None,
              **settings):
        handler = make_orchestrate_task(config, Settings(
            implementation_url=implementation_url, testing_url=testing_url,
            image_registry="reg.example.com", **settings))
        actor = Actor.from_card(cards_path(), actions={"orchestrate-task": handler})
        return lambda: actor.receive(verb="request", door="orchestrate-task",
                                     from_="a-human", payload={**TASK, **(payload or {})})
    return _boot


@pytest.fixture
def no_cluster(monkeypatch):
    """Fail loudly if an attempt reaches Kubernetes or GitHub unexpectedly."""
    def _unexpected(*args, **kwargs):
        raise AssertionError("round 0 should have stopped the run before this")
    monkeypatch.setattr(deploy, "deploy_and_test", _unexpected)
    monkeypatch.setattr(pulls, "open_prs", _unexpected)


def test_a_open_questions_refuse_at_round_0_and_nothing_is_built(fake_peers, orchestrate,
                                                                 no_cluster):
    fake_peers.answers["propose-acceptance"] = {
        "expectations": EXPECTATIONS,
        "open_questions": [{"about": "E1", "question": "which three statuses?"}]}

    reply = orchestrate(fake_peers.url, fake_peers.url)()

    assert reply["accepted"] is False
    assert reply["stage"] == "round-0"
    assert reply["attempts"] == 0
    assert reply["acceptance_surface"] == EXPECTATIONS
    assert reply["open_questions"] == [{"about": "E1", "question": "which three statuses?"}]
    assert fake_peers.doors() == ["propose-acceptance"], "nothing past round 0 may run"


def test_b_an_infeasible_surface_refuses_carrying_the_objections(fake_peers, orchestrate,
                                                                 no_cluster):
    objections = [{"id": "E1", "kind": "not-determined", "why": "the task never names the ids",
                   "counter_proposal": "seed ids 1..3"}]
    fake_peers.answers["propose-acceptance"] = {"expectations": EXPECTATIONS}
    fake_peers.answers["assess-task"] = {"feasible": False, "objections": objections,
                                         "commitments": [{"id": "E2", "path": "/widgets/{id}"}]}

    reply = orchestrate(fake_peers.url, fake_peers.url)()

    assert reply["accepted"] is False
    assert reply["stage"] == "round-0"
    assert reply["objections"] == objections
    assert reply["commitments"] == [{"id": "E2", "path": "/widgets/{id}"}]
    assert reply["acceptance_surface"] == EXPECTATIONS
    assert fake_peers.doors() == ["propose-acceptance", "assess-task"]
    assert "implement-task" not in fake_peers.doors()


def test_c_the_agreed_surface_reaches_both_doors_on_every_attempt(fake_peers, orchestrate,
                                                                  monkeypatch):
    commitment = {"expectation_id": "E1", "statement": "widget ids 0190-a1, 0190-a2, 0190-a3"}
    loose = "the backend answers on port 8000"
    datasets = [{"expectation": "E1", "via": "seed", "because": "the seed is the subject",
                 "provided_by": "E1"},
                {"expectation": "E2", "via": "command", "steps": ["POST /widgets"]}]
    fake_peers.answers["propose-acceptance"] = {"expectations": EXPECTATIONS, "datasets": datasets,
                                                "open_questions": []}
    fake_peers.answers["assess-task"] = {"feasible": True, "commitments": [commitment, loose]}
    fake_peers.answers["implement-task"] = {"accepted": True, "branch": "impl/TASK-042",
                                            "images": [IMAGE]}
    fake_peers.answers["test-task"] = {"accepted": True, "branch": "test/TASK-042",
                                       "images": [TEST_IMAGE]}

    runs = iter([
        deploy.TestRun(passed=1, total=2, criteria=["❌ tests/test_widgets.py::test_e1 FAILED"],
                       logs={"backend": "1 failed, 1 passed"}),
        deploy.TestRun(passed=2, total=2, logs={"backend": "2 passed"}),
    ])
    deployed = []

    def fake_deploy(config, settings, **kwargs):
        deployed.append(kwargs)
        return next(runs)

    opened = {}

    def fake_open_prs(config, **kwargs):
        opened.update(kwargs)
        return {"pr_url": "https://github.example/impl/pull/1",
                "test_pr_url": "https://github.example/test/pull/1"}

    monkeypatch.setattr(deploy, "deploy_and_test", fake_deploy)
    monkeypatch.setattr(pulls, "open_prs", fake_open_prs)

    reply = orchestrate(fake_peers.url, fake_peers.url)()

    agreed = [
        {**EXPECTATIONS[0], "commitments": [commitment]},
        EXPECTATIONS[1],
        {"id": "commitment-1", "statement": loose, "handle": None, "commitment": loose},
    ]
    assert reply == {"accepted": True, "branch": "impl/TASK-042", "test_branch": "test/TASK-042",
                     "attempts": 2, "verdict": "2/2 criteria passed",
                     "acceptance_surface": agreed,
                     "pr_url": "https://github.example/impl/pull/1",
                     "test_pr_url": "https://github.example/test/pull/1"}

    assert fake_peers.doors() == ["propose-acceptance", "assess-task",
                                  "implement-task", "test-task",
                                  "implement-task", "test-task"]
    implemented, tested = fake_peers.payloads("implement-task"), fake_peers.payloads("test-task")
    for attempt in (0, 1):
        assert implemented[attempt]["acceptance_surface"] == agreed
        assert tested[attempt]["acceptance_surface"] == agreed
        assert tested[attempt]["components"] == ["backend"]
    # The datasets are the tester's own: relayed to its test door on every attempt, and never
    # shown to the implementer at either of its doors (ADR-FTA-0003, ADR-FTOA-0003).
    for attempt in (0, 1):
        assert tested[attempt]["datasets"] == datasets
        assert "datasets" not in implemented[attempt]
    assert "datasets" not in fake_peers.payloads("assess-task")[0]
    assert "datasets" not in reply
    assert "remediation_context" not in implemented[0]
    assert implemented[1]["remediation_context"] == "❌ tests/test_widgets.py::test_e1 FAILED"
    assert tested[1]["remediation_context"] == implemented[1]["remediation_context"]

    # Round 0 names the SIDECAR's components; the attempt names only what was touched.
    assert fake_peers.payloads("propose-acceptance")[0]["components"] == ["backend", "stub"]
    assert deployed[0]["code_images"] == {"backend": "0.1.0-task-042-abc1234"}
    assert deployed[0]["test_images"] == {"backend": "0.1.0-task-042-def5678"}
    # Every call carried this actor's own name as its sender.
    assert {sender for _, sender, _ in fake_peers.calls} == {"foundry-task-orchestration-actor"}
    # And the PR body is built from the agreed surface, not the raw proposal.
    assert opened["surface"] == agreed


def test_d_a_transport_error_on_propose_refuses_at_round_0(closed_port_url, fake_peers,
                                                           orchestrate, no_cluster):
    reply = orchestrate(fake_peers.url, closed_port_url)()

    assert reply["accepted"] is False
    assert reply["stage"] == "round-0"
    assert reply["attempts"] == 0
    assert "propose-acceptance" in reply["because"]
    assert "unreachable" in reply["because"]
    assert fake_peers.calls == [], "the implementation actor must not be asked"


def test_a_400_from_propose_is_a_refusal_at_round_0(fake_peers, orchestrate, no_cluster):
    fake_peers.answers["propose-acceptance"] = (400, {"error": "refuses this query: components"})
    reply = orchestrate(fake_peers.url, fake_peers.url)()
    assert reply["stage"] == "round-0"
    assert "(400)" in reply["because"]
    assert fake_peers.doors() == ["propose-acceptance"]


def test_a_refused_implementation_names_its_stage(fake_peers, orchestrate, no_cluster):
    fake_peers.answers["propose-acceptance"] = {"expectations": EXPECTATIONS}
    fake_peers.answers["assess-task"] = {"feasible": True}
    fake_peers.answers["implement-task"] = {"accepted": False, "because": "nothing staged"}

    reply = orchestrate(fake_peers.url, fake_peers.url)()

    assert reply["stage"] == "implementation"
    assert reply["attempts"] == 1
    assert "nothing staged" in reply["because"]
    assert "branch" not in reply, "an unknown branch is omitted, not sent as null"


def test_a_deployment_failure_is_the_testing_stage(fake_peers, orchestrate, monkeypatch):
    fake_peers.answers["propose-acceptance"] = {"expectations": EXPECTATIONS}
    fake_peers.answers["assess-task"] = {"feasible": True}
    fake_peers.answers["implement-task"] = {"accepted": True, "branch": "impl/TASK-042",
                                            "images": [IMAGE]}
    fake_peers.answers["test-task"] = {"accepted": True, "branch": "test/TASK-042",
                                       "images": [TEST_IMAGE]}

    def broken(*args, **kwargs):
        raise deploy.DeployError("sup-007-wid-backend never became ready")

    monkeypatch.setattr(deploy, "deploy_and_test", broken)
    reply = orchestrate(fake_peers.url, fake_peers.url)()
    assert reply["stage"] == "testing"
    assert reply["branch"] == "impl/TASK-042"
    assert reply["test_branch"] == "test/TASK-042"


def test_exhausted_attempts_are_the_verdict_stage(fake_peers, orchestrate, monkeypatch):
    fake_peers.answers["propose-acceptance"] = {"expectations": EXPECTATIONS}
    fake_peers.answers["assess-task"] = {"feasible": True}
    fake_peers.answers["implement-task"] = {"accepted": True, "branch": "impl/TASK-042",
                                            "images": [IMAGE]}
    fake_peers.answers["test-task"] = {"accepted": True, "branch": "test/TASK-042",
                                       "images": [TEST_IMAGE]}
    monkeypatch.setattr(deploy, "deploy_and_test", lambda *a, **k: deploy.TestRun(
        passed=0, total=1, criteria=["❌ t FAILED"]))
    monkeypatch.setattr(pulls, "open_prs", lambda *a, **k: pytest.fail("no PR on a red verdict"))

    reply = orchestrate(fake_peers.url, fake_peers.url, max_attempts=2)()

    assert reply["stage"] == "verdict"
    assert reply["attempts"] == 2
    assert reply["verdict"] == "❌ t FAILED"
    assert fake_peers.doors().count("implement-task") == 2
    assert fake_peers.doors().count("propose-acceptance") == 1, "round 0 runs once per call"


# ── a stopped round, sent to the task's owner (ADR-FTOA-0004) ───────────────────────────────

REPORT_TO = {"report_to": "acme-lab/backlog"}
TASK_LABEL = "task:ACME.PARTS.CAP.SUP.007.WID/TASK-042"


class GitHub:
    """`pulls.github_request` as the issues API — recording every call, never raising unless told.

    Records rather than asserts: the handler deliberately swallows anything the report raises, so
    an `AssertionError` thrown from in here would vanish into an `issue-report-failed` log line."""

    def __init__(self, open_issues=(), fail=None):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.open_issues, self.fail = list(open_issues), fail

    def __call__(self, url, token, *, method, body=None):
        self.calls.append((method, url, body))
        if self.fail:
            raise self.fail
        if url.endswith("/labels"):
            return {"name": body["name"]}
        if method == "GET":
            return self.open_issues
        if url.endswith("/comments"):
            return {"html_url": "https://github.example/acme-lab/backlog/issues/5#c1"}
        return {"html_url": "https://github.example/acme-lab/backlog/issues/9", "number": 9}

    def posted(self, suffix: str) -> list[dict]:
        return [b for m, u, b in self.calls if m == "POST" and u.endswith(suffix)]


@pytest.fixture
def github(monkeypatch):
    def _install(**kwargs) -> GitHub:
        fake = GitHub(**kwargs)
        monkeypatch.setattr(pulls, "github_request", fake)
        return fake
    return _install


@pytest.fixture
def events(monkeypatch):
    seen = []
    real = correlation.event
    monkeypatch.setattr(correlation, "event",
                        lambda name, **fields: (seen.append((name, fields)), real(name, **fields)))
    return seen


def test_e_open_questions_open_an_issue_for_the_task(fake_peers, orchestrate, no_cluster, github):
    fake = github()
    fake_peers.answers["propose-acceptance"] = {
        "expectations": EXPECTATIONS,
        "open_questions": [{"about": "E1", "question": "which three statuses?"}]}

    reply = orchestrate(fake_peers.url, fake_peers.url, payload=REPORT_TO)()

    assert reply["stage"] == "round-0"
    assert reply["issue_url"] == "https://github.example/acme-lab/backlog/issues/9"
    assert reply["open_questions"] == [{"about": "E1", "question": "which three statuses?"}]
    assert "report_to" not in fake_peers.payloads("propose-acceptance")[0], \
        "where the owner hears back is this actor's business, not the tester's"
    [created] = fake.posted("/repos/acme-lab/backlog/issues")
    assert created["labels"] == [TASK_LABEL, "needs-info",
                                 "from:acme.parts.cap.sup.007.wid-task-orchestration"]
    assert created["title"] == "[TASK-042] round 0 stopped: 1 open question(s), 0 objection(s)"
    assert ("- [ ] **open question** — raised by testing (`ACME.PARTS.CAP.SUP.007.WID-testing`), "
            "on `E1`: which three statuses?") in created["body"]
    assert "- **E2** GET /widgets/{id} returns 200 for each" in created["body"]
    assert "Correlation id: `" in created["body"]


def test_f_objections_on_a_task_with_an_open_issue_are_a_comment(fake_peers, orchestrate,
                                                                 no_cluster, github):
    fake = github(open_issues=[{"number": 5,
                                "html_url": "https://github.example/acme-lab/backlog/issues/5"}])
    fake_peers.answers["propose-acceptance"] = {"expectations": EXPECTATIONS}
    fake_peers.answers["assess-task"] = {"feasible": False, "objections": [
        {"id": "E1", "kind": "not-determined", "why": "no ARCHIVE command exists"}]}

    reply = orchestrate(fake_peers.url, fake_peers.url, payload=REPORT_TO)()

    assert reply["issue_url"] == "https://github.example/acme-lab/backlog/issues/5"
    assert not fake.posted("/repos/acme-lab/backlog/issues"), "one issue per task, not per stop"
    [comment] = fake.posted("/issues/5/comments")
    assert ("- [ ] **objection** — raised by implementation "
            "(`ACME.PARTS.CAP.SUP.007.WID-implementation`), on `E1`: no ARCHIVE command exists"
            ) in comment["body"]


def test_g_without_report_to_nothing_is_sent(fake_peers, orchestrate, no_cluster, github):
    fake = github()
    fake_peers.answers["propose-acceptance"] = {"expectations": EXPECTATIONS,
                                                "open_questions": ["which three statuses?"]}

    reply = orchestrate(fake_peers.url, fake_peers.url)()

    assert reply["stage"] == "round-0"
    assert "issue_url" not in reply
    assert fake.calls == []


def test_h_a_github_failure_still_returns_the_refusal(fake_peers, orchestrate, no_cluster,
                                                     github, events):
    github(fail=pulls.PullRequestError("POST …/labels failed (403): not accessible", status=403))
    fake_peers.answers["propose-acceptance"] = {"expectations": EXPECTATIONS,
                                                "open_questions": ["which three statuses?"]}

    reply = orchestrate(fake_peers.url, fake_peers.url, payload=REPORT_TO)()

    assert reply["accepted"] is False
    assert reply["stage"] == "round-0"
    assert reply["open_questions"] == ["which three statuses?"]
    assert "issue_url" not in reply
    [(_, failed)] = [e for e in events if e[0] == "issue-report-failed"]
    assert failed["repo"] == "acme-lab/backlog" and "403" in failed["error"]


@pytest.mark.parametrize("which", ["propose", "assess"])
def test_i_a_peer_that_did_not_answer_is_not_reported(which, closed_port_url, fake_peers,
                                                      orchestrate, no_cluster, github):
    """An operator's problem: amending the task card would not make a peer answer."""
    fake = github()
    fake_peers.answers["propose-acceptance"] = {"expectations": EXPECTATIONS}
    if which == "propose":
        reply = orchestrate(fake_peers.url, closed_port_url, payload=REPORT_TO)()
    else:
        reply = orchestrate(closed_port_url, fake_peers.url, payload=REPORT_TO)()

    assert reply["stage"] == "round-0"
    assert "did not answer" in reply["because"]
    assert "issue_url" not in reply
    assert fake.calls == []
