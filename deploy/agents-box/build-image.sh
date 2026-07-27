#!/bin/sh
# build-image.sh — build hermes-brain from a published brainkit commit.
#
# Run from anywhere inside the repo:
#   deploy/agents-box/build-image.sh
#
#   IMAGE=hermes-brain          image name (default hermes-brain)
#   REF=main                    branch, tag, or commit to install (default main)
#
# Tags the result twice: :latest, so docker-compose.yml needs no edit, and
# :<short-sha>, so a rollback target exists and `docker images` shows what is
# on the box.
#
# The image installs brainkit from the public repo, not from the working tree,
# so what ends up inside it is always a *pushed* commit — code someone else can
# fetch and read. That replaces the old uncommitted-changes guard: local edits
# can no longer reach the image at all, so there is nothing to refuse. The new
# failure mode is the opposite one — building while your work is still local —
# and the script says so rather than letting you discover it on the box.
set -eu

IMAGE=${IMAGE:-hermes-brain}
REF=${REF:-main}
REPO=https://github.com/joedanz/brainkit

# Resolve the ref to a commit up front, so the image is built from an exact
# revision and the tag names it. ls-remote resolves branches and tags; a raw
# commit is passed through, since ls-remote cannot look one up.
sha=$(git ls-remote "$REPO" "$REF" 2>/dev/null | cut -f1)
if [ -z "$sha" ]; then
    case $REF in
        "" | *[!0-9a-fA-F]*)
            echo "cannot resolve '$REF' on $REPO" >&2
            exit 1
            ;;
        *) sha=$REF ;;
    esac
fi
short=$(printf '%s' "$sha" | cut -c1-12)

# Building the remote tip is correct, but it is not always what someone with
# local commits expects — say it plainly instead of shipping a surprise.
root=$(git rev-parse --show-toplevel)
head=$(git -C "$root" rev-parse HEAD 2>/dev/null || true)
if [ -n "$head" ] && [ "$head" != "$sha" ]; then
    echo "note: building $REF ($short); local HEAD is $(printf '%s' "$head" | cut -c1-12)" >&2
    echo "      anything not pushed to $REF is not in this image" >&2
fi

echo "building $IMAGE:$short (and :latest) from $sha"
docker build \
    -f "$root/deploy/agents-box/Dockerfile" \
    --build-arg "BRAINKIT_REF=$sha" \
    -t "$IMAGE:$short" \
    -t "$IMAGE:latest" \
    "$root"

echo
echo "installed revision:"
docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$IMAGE:$short"
