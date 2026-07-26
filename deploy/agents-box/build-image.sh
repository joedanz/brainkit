#!/bin/sh
# build-image.sh — build hermes-brain with the source commit stamped into it.
#
# Run from anywhere inside the repo:
#   deploy/agents-box/build-image.sh
#
#   IMAGE=hermes-brain          image name (default hermes-brain)
#
# Tags the result twice: :latest, so docker-compose.yml needs no edit, and
# :<short-sha>, so a rollback target exists and `docker images` shows what is
# on the box. A plain `docker build` also works — it just bakes GIT_SHA=unknown,
# and then nothing can say which commit the container is running.
#
# Why this matters here specifically: the image installs a *working tree*
# (COPY pyproject.toml + src), not a release. brainkit's version comes from
# package metadata, which reads the same number for every commit between
# releases — so the version alone cannot distinguish two images built weeks
# apart. The commit is the only thing that can.
set -eu

IMAGE=${IMAGE:-hermes-brain}

root=$(git rev-parse --show-toplevel)
sha=$(git -C "$root" rev-parse HEAD)
short=$(git -C "$root" rev-parse --short HEAD)

# A stamp that silently describes the wrong tree is worse than no stamp: the
# image would claim a commit whose code it does not contain.
if ! git -C "$root" diff --quiet HEAD -- pyproject.toml src; then
    echo "refusing to build: uncommitted changes in pyproject.toml or src/" >&2
    echo "the stamp would name $short but the image would not contain it" >&2
    exit 1
fi

echo "building $IMAGE:$short (and :latest) from $sha"
docker build \
    -f "$root/deploy/agents-box/Dockerfile" \
    --build-arg "GIT_SHA=$sha" \
    -t "$IMAGE:$short" \
    -t "$IMAGE:latest" \
    "$root"

echo
echo "stamped revision:"
docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$IMAGE:$short"
