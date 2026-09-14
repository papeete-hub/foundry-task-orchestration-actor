"""A kubeconfig from this Pod's own ServiceAccount — the bootstrap `entrypoint.sh` used to be.

WHY THERE IS A KUBECONFIG AT ALL. This actor deploys an ephemeral namespace per attempt,
reflexively, into the very cluster it runs in. `papeete_deploy.k8s` shells out as
`kubectl --context <name> …` on every call, so an in-cluster Pod — which has a projected token and
a CA but no kubeconfig and no context — needs one written before the first attempt.

WHY IT MOVED INTO THE PACKAGE, AND INTO `serve`. The instance this was extracted from ran a shell
entrypoint that called `kubectl config set-cluster/set-credentials/set-context/use-context` and
then `exec python app.py`. A shipped entrypoint script would have kept that shape; it was not
chosen, for three reasons:

- **A use's image keeps the base image's CMD.** `foundry-task-orchestration-actor serve /actor` is
  the whole story, as it is for the implementation actor. A wrapper script is a second thing a
  use could override, forget, or restate wrongly.
- **It is testable.** Writing a file from three inputs is a function; a shell script that execs
  into Python is something the suite could only run inside a container.
- **It can be better than the script was.** The script EMBEDDED the token it read at start. A
  projected ServiceAccount token is rotated by the kubelet, and an embedded copy is the one that
  eventually expires under a long-running Pod. The file written here names the token by PATH
  (`tokenFile`), which client-go re-reads, so the credential kubectl presents is always the one
  the kubelet last wrote.

WHEN IT DOES NOTHING. Outside a cluster (no `KUBERNETES_SERVICE_HOST`, no token on disk), or when
the operator has already pointed `KUBECONFIG` at a file that exists. Both are reported, never
raised: `serve` must still boot on a laptop and in CI, where there is no cluster and no attempt
will ever reach a kubectl call.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import yaml

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
NAMESPACE_FILE = SA_DIR / "namespace"


def bootstrap(context: str, *, sa_dir: Path = SA_DIR, environ: dict | None = None,
              dest: Path | None = None) -> tuple[Path | None, str]:
    """Write an in-cluster kubeconfig whose context is `context`, and point `KUBECONFIG` at it.

    Returns `(path written or None, why)`. `environ` defaults to `os.environ` and is MUTATED —
    that is the point: every `kubectl` this process spawns afterwards inherits `KUBECONFIG`.
    """
    environ = os.environ if environ is None else environ

    existing = environ.get("KUBECONFIG")
    if existing and all(Path(p).exists() for p in existing.split(os.pathsep) if p):
        return None, f"KUBECONFIG={existing} already names a kubeconfig; left alone"

    host = environ.get("KUBERNETES_SERVICE_HOST")
    port = environ.get("KUBERNETES_SERVICE_PORT")
    token, ca = Path(sa_dir) / "token", Path(sa_dir) / "ca.crt"
    if not (host and port and token.exists() and ca.exists()):
        return None, ("not running in a cluster (no service host, or no ServiceAccount token "
                      "mounted) — kubectl will use whatever kubeconfig the environment provides")

    # An IPv6 service host is a bare address; a URL needs it bracketed.
    server_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    document = {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [{"name": "in-cluster", "cluster": {
            "server": f"https://{server_host}:{port}",
            "certificate-authority": str(ca),
        }}],
        "users": [{"name": "service-account", "user": {"tokenFile": str(token)}}],
        "contexts": [{"name": context, "context": {
            "cluster": "in-cluster", "user": "service-account",
        }}],
        "current-context": context,
    }
    # /tmp, not $HOME: every deployment of this runs with a read-only root filesystem and an
    # emptyDir on /tmp.
    path = Path(dest) if dest is not None else Path(tempfile.gettempdir()) / "kubeconfig"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document, sort_keys=False))
    path.chmod(0o600)
    environ["KUBECONFIG"] = str(path)
    return path, f"in-cluster kubeconfig written to {path}, context '{context}'"
