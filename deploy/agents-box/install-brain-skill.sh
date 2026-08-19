#!/bin/sh
# Provision brainkit's brain-protocol skill into a company skills repo checkout,
# so agents read it from there instead of from their own container.
#
# Why this exists. The skill ships in the image and is seeded into each
# container's own skills directory, which means updating it costs a rebuild and
# a recreate of every container -- and a recreate drops whatever conversation an
# agent is in. A company skills repo is already mounted read-only into every
# container and pulled on a timer, so a skill living there updates in place, for
# every agent at once, with nothing restarted. From brainkit 0.4.8 the boot hook
# defers to any skill that repo names (see scripts/03-brain-first-boot), which is
# what makes the handover stick across image rolls.
#
# The skill is provisioned, not committed. The pull job runs `fetch` and
# `reset --hard` and never `git clean`, so an untracked directory survives every
# pull; excluding it locally keeps it out of `git status` so nobody mistakes it
# for a stray edit. That also keeps ownership honest: this file belongs to
# brainkit and is re-provisioned from brainkit, while the repo stays the
# company's own.
#
# Usage: ./install-brain-skill.sh [company-skills-checkout]   (default /opt/company-skills)
#
# Re-run it after upgrading brainkit on the box. Then recreate the containers
# once so the boot hook reclaims the copies it had already seeded.
set -eu

DEST_ROOT="${1:-/opt/company-skills}"
SRC="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)/company-brain-profile/skills/brain-protocol"
DEST="$DEST_ROOT/brain-protocol"

[ -d "$SRC" ] || { echo "no brain-protocol skill at $SRC" >&2; exit 1; }
[ -d "$DEST_ROOT" ] || { echo "$DEST_ROOT does not exist — is the skills repo cloned?" >&2; exit 1; }

mkdir -p "$DEST"
cp -R "$SRC/." "$DEST/"
echo "installed $DEST/SKILL.md"

# Only meaningful when the destination really is a git checkout; a plain
# directory is a perfectly good destination and should not error here.
if [ -d "$DEST_ROOT/.git" ]; then
    exclude="$DEST_ROOT/.git/info/exclude"
    mkdir -p "$(dirname "$exclude")"
    if ! grep -qx 'brain-protocol/' "$exclude" 2>/dev/null; then
        echo 'brain-protocol/' >> "$exclude"
        echo "excluded brain-protocol/ from $DEST_ROOT's git status"
    fi
fi

echo
echo "next: recreate the agent containers once, so the boot hook drops the copy"
echo "it seeded into each one:  cd <compose dir> && docker compose up -d"
