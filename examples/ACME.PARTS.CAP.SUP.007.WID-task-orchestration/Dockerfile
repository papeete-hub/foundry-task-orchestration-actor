# A complete use of this actor, as an image. Three lines, one of which is a gate.
#
# Compare it with what a use used to carry: four hand-copied cards, an `app.py`, an `entrypoint.sh`
# rendering a kubeconfig, a `handler.py` of some eight hundred lines with the capability spelled
# through it, and a Dockerfile pinning the papeete stack package by package. All of that is the
# base image now (ADR-FTOA-0001). What is left below is the capability and nothing else.
#
# The tag is pinned deliberately. A use upgrades by changing this line, which is also how it takes
# a new definition of the cards: `render-cards` writes them from whatever wheel this image carries.
# The whole ref is one ARG so this repo's own CI can point it at the base image it just built.
#
# The default names the product registry rather than GHCR, for the reason FIA's example gives: it
# is the copy the in-cluster builder can pull (ADR-FIA-0006 publishes one digest to both). Override
# it if you are somewhere else:
#     docker build --build-arg ACTOR_IMAGE=ghcr.io/papeete-hub/foundry-task-orchestration-actor:0.2.0 .
ARG ACTOR_IMAGE=papeetefoundry.azurecr.io/foundry/foundry-task-orchestration-actor:0.2.0
FROM ${ACTOR_IMAGE}

# THE BINDING, and the platform stand-in it names. `ephemeral.platform` is resolved against the
# sidecar's own folder, and `lint` below refuses a sidecar naming a folder that is not there — so
# forgetting the second COPY fails this build rather than the first attempt.
COPY actor-agentic-context.yaml /actor/
COPY platform-standin /actor/platform-standin

# Render the cards from the definition in the image, then refuse to build if the result does not
# conform. `render-cards` cannot really produce a non-conformant card set; `lint` also checks the
# SIDECAR, which is hand-written and can be wrong about the capability it names.
RUN foundry-task-orchestration-actor render-cards /actor \
 && foundry-task-orchestration-actor lint /actor

# No CMD: the base image's is `foundry-task-orchestration-actor serve /actor`, and a use that
# restates it is a use that can get it wrong.
