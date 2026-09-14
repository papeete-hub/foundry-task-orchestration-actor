# foundry-task-orchestration-actor — the runnable half of what this package ships (ADR-FTOA-0001).
#
# NOT a capability's image. This one carries the MACHINERY and nothing else: it names no
# capability and has no sidecar. It is a base a use derives from:
#
#     FROM ghcr.io/papeete-hub/foundry-task-orchestration-actor:<version>
#     COPY actor-agentic-context.yaml /actor/
#     COPY platform-standin /actor/platform-standin        # only if the sidecar declares one
#     RUN foundry-task-orchestration-actor render-cards /actor \
#      && foundry-task-orchestration-actor lint /actor
#
# That is a use's whole image — see ../examples/ for a complete one that builds.
#
# BUILD CONTEXT IS THE REPO ROOT, not this folder:
#     uv build && docker build -f docker/Dockerfile -t foundry-task-orchestration-actor:$(version) .
# The wheel is COPY'd from `dist/` rather than resolved from PyPI on purpose — see below.

# kubectl, PINNED, from the Kubernetes project's own image. The instance this was extracted from
# copied it out of `bitnami/kubectl:latest` — an unpinned tag from a publisher that has since
# stopped maintaining free tags, which is two ways for a rebuild of the same commit to produce a
# different actor. kubectl supports one minor version of skew either side of the API server; move
# this line with the clusters it drives, deliberately, and nowhere else.
FROM registry.k8s.io/kubectl:v1.36.1 AS kubectl

FROM python:3.12-slim

# WHAT THIS ACTOR SHELLS OUT TO. Each line is here because a specific piece of the machinery
# execs it; nothing is here "in case".
#   git             — each attempt clones the implementation branch, read-only, to read a touched
#                     component's own ephemeral overlay (deploy.py).
#   ca-certificates — https to GitHub (the clone, the pull requests) and to the API server.
# Deliberately NOT here: node, the `claude` CLI, buildctl. This actor judges nothing and builds
# nothing — every image it deploys was built and pushed by a peer.
RUN apt-get update && apt-get install -y --no-install-recommends \
      git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# kubectl — a client only. papeete-deploy shells out to it (`kubectl kustomize` to render, `apply`,
# `delete`), and so does deploy.py for what papeete-deploy does not cover. No cluster config is
# baked in: `serve` writes one from the Pod's own ServiceAccount at start (kubeconfig.py).
COPY --from=kubectl /bin/kubectl /usr/local/bin/kubectl

# THE WHEEL, FROM THE BUILD THAT PRODUCED THIS IMAGE — never `pip install
# foundry-task-orchestration-actor==<tag>` from PyPI. The image and the wheel are two artifacts of
# one release, and resolving the wheel over the network would let an image tagged 0.1.0 contain
# some other 0.1.0 — or build green before the upload, then never again.
#
# `[serve]` is the wire half — a mailbox and an observability backend. An extra rather than a
# dependency so an embedder importing `CapabilityConfig` is not handed an HTTP server; this image
# is the consumer that wants them.
COPY dist/*.whl /tmp/wheels/
RUN set -eu; \
    wheels="$(ls /tmp/wheels/*.whl)"; \
    # One wheel, or the `[serve]` below would silently apply to whichever `ls` returned last.
    [ "$(echo "$wheels" | wc -l)" -eq 1 ] || { echo "expected exactly one wheel in dist/, found:"; echo "$wheels"; exit 1; }; \
    pip install --no-cache-dir "${wheels}[serve]"; \
    rm -rf /tmp/wheels

# WHERE A USE'S SIDECAR GOES, its platform stand-in beside it, and its cards rendered next to both.
# Empty in this image: there is no capability here to render them for.
WORKDIR /actor

# NON-ROOT IN THE IMAGE, not only in each deployment's securityContext. A manifest that forgets
# `runAsNonRoot` should still get a non-root container. 10001 matches the manifests this actor's
# uses deploy with.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin actor \
    && chown -R 10001:10001 /actor
USER 10001

# Every deployment of this runs with a read-only root filesystem and a writable /tmp — which is
# where the kubeconfig is written and the implementation is cloned.
ENV HOME=/home/actor \
    PORT=8080

# Passed at run time, never baked in:
#   GITHUB_TOKEN        read on the implementation repo (the per-attempt clone), pull-requests:write
#                       on both peer repos, contents:write on the testing repo (a long test log is
#                       committed beside the tests and linked from the PR).
#   IMAGE_REGISTRY      where the capability's component and test images are pulled from.
#   IMPLEMENTATION_URL / TESTING_URL   override the sidecar's (or the derived) peer URLs.
#   KUBE_CONTEXT, IMAGE_PULL_SECRET, MAX_ATTEMPTS, DOOR_CALL_TIMEOUT_S, PROPOSE_TIMEOUT_S,
#   ASSESS_TIMEOUT_S, DEPLOYMENT_READY_TIMEOUT_S, TEST_JOB_TIMEOUT_S, BASE_BRANCH
#                       operational tuning with defaults — see settings.py.
# No CLAUDE_CODE_OAUTH_TOKEN and no ANTHROPIC_API_KEY: nothing here runs a session.
EXPOSE 8080

# `serve`, not a copied-in app.py and not an entrypoint.sh. It wires observability once, writes the
# in-cluster kubeconfig the shell script used to, and boots the one door.
CMD ["foundry-task-orchestration-actor", "serve", "/actor"]
