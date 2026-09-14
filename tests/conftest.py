"""Fixtures.

THE FIXTURE CAPABILITY IS FICTIONAL, ON PURPOSE. `ACME.PARTS.CAP.SUP.007.WID` is not any real
capability in any real instance. This package is published publicly and carries no reference to
the instance it was extracted from — a test suite is source too, and `tests/test_portability.py`
would be a strange gate to enforce over `src/` while the fixtures next door named a client.

It has the same SHAPE as a real id (`<ENT>.<DOMAIN>.CAP.<TYPE>.<NNN>.<CODE>`), which is what the
derivation tests actually pin.

THE PEERS ARE REAL HTTP. `FakePeers` is a `ThreadingHTTPServer` on a loopback port answering the
four peer doors this actor calls, recording every payload it was sent. The round-0 and attempt-loop
tests therefore exercise the real `peers.call_door` — the wire body, the 400-as-refusal mapping,
the connection error — and only the Kubernetes and GitHub halves are replaced.
"""
from __future__ import annotations

import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

CAPABILITY = "ACME.PARTS.CAP.SUP.007.WID"
SOURCE_REPO = "acme-lab/ACME.PARTS.CAP.SUP.007.WID-task-orchestration"

SIDECAR = {
    "context": "foundry-task-orchestration-actor/agentic-context/v1",
    "capability": CAPABILITY,
    "source_repo": SOURCE_REPO,
    "components": ["backend", "stub"],
}


@pytest.fixture
def sidecar_dict() -> dict:
    """A deep copy, so a test may mutate one key without leaking into the next."""
    return copy.deepcopy(SIDECAR)


@pytest.fixture
def write_sidecar(tmp_path):
    """Write a sidecar into a folder and return that folder."""
    def _write(doc: dict, folder: Path | None = None) -> Path:
        folder = folder or tmp_path
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "actor-agentic-context.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
        return folder
    return _write


@pytest.fixture
def config(sidecar_dict, write_sidecar):
    from foundry_task_orchestration_actor.config import CapabilityConfig
    return CapabilityConfig.load(write_sidecar(sidecar_dict))


@pytest.fixture
def platform_folder(tmp_path):
    """A minimal kustomize layout with the overlay `ephemeral.platform` must carry."""
    def _make(name: str = "platform-standin", root: Path | None = None) -> Path:
        folder = (root or tmp_path) / name
        (folder / "k8s" / "overlays" / "ephemeral").mkdir(parents=True)
        (folder / "k8s" / "overlays" / "ephemeral" / "kustomization.yaml").write_text(
            "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\n")
        return folder
    return _make


class FakePeers:
    """The implementation and testing actors' doors, on one loopback server.

    `answers[door]` is either a dict (replied 200), a `(status, dict)` pair, or a callable taking
    the payload and returning either. `calls` records `(door, from, payload)` in arrival order.
    """

    def __init__(self):
        self.answers: dict = {}
        self.calls: list[tuple[str, str, dict]] = []
        peers = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):                                        # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                door = self.path.lstrip("/")
                peers.calls.append((door, body.get("from"), body.get("payload")))
                answer = peers.answers.get(door)
                if callable(answer):
                    answer = answer(body.get("payload"))
                if answer is None:
                    status, reply = 404, {"error": f"no route /{door}"}
                elif isinstance(answer, tuple):
                    status, reply = answer
                else:
                    status, reply = 200, answer
                data = json.dumps(reply).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):                              # quiet
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()

    def payloads(self, door: str) -> list[dict]:
        return [payload for called, _, payload in self.calls if called == door]

    def doors(self) -> list[str]:
        return [door for door, _, _ in self.calls]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake_peers():
    peers = FakePeers()
    yield peers
    peers.close()


@pytest.fixture
def closed_port_url():
    """A loopback URL nothing listens on — a transport error, not a refusal."""
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return f"http://127.0.0.1:{port}"
