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
# WHY THE MANIFEST (and why this is not a plain `cp -R`)
#
# The destination lives in a directory called `company-skills`, so it reads as
# the company's own file, and sooner or later somebody edits it there. This
# script used to be a bare `cp -R`: that edit died silently on the next run,
# with no warning and no copy kept. The risk is worse once a fleet job calls
# this after every image roll, because then the loss is automatic rather than
# waiting on a human to remember the command.
#
# So each install records an md5 per installed file. A later run recomputes it:
# equal means nobody has touched our copy and we may replace it; different means
# a human edited a brainkit-owned file, and we REFUSE (exit 2) rather than
# decide for them. `--force` overwrites, and still leaves a timestamped backup.
#
# Detection and not a 3-way merge (which is what SOUL.md gets, in
# scripts/03-brain-first-boot): SOUL.md is genuinely co-authored — the fleet
# writes a name declaration into every agent's copy — so a merge there is
# routine. Nothing is supposed to co-author this file, so the honest answer to
# a local edit is to stop and say so, not to blend it.
#
# Ownership is deliberately exclusive. If a company wants its OWN
# brain-protocol, the move is to commit it to the repo, drop the
# `brain-protocol/` line from .git/info/exclude, and stop running this script —
# all three. Tracked AND provisioned is the one combination that cannot work:
# the pull's `reset --hard` restores the committed copy every few minutes while
# this script puts brainkit's back.
#
# Usage: ./install-brain-skill.sh [company-skills-checkout] [--force]
#          (destination defaults to /opt/company-skills)
#
# Exit: 0 installed or already current; 1 usage/environment error;
#       2 refused — the destination carries local edits.
#
# Re-run it after upgrading brainkit on the box. Then recreate the containers
# once so the boot hook reclaims the copies it had already seeded.
set -eu

FORCE=0
DEST_ROOT=""
for arg in "$@"; do
    case "$arg" in
        --force) FORCE=1 ;;
        -*) echo "unknown option: $arg" >&2; exit 1 ;;
        *) DEST_ROOT="$arg" ;;
    esac
done
DEST_ROOT="${DEST_ROOT:-/opt/company-skills}"

SRC="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)/company-brain-profile/skills/brain-protocol"
DEST="$DEST_ROOT/brain-protocol"
# Beside the skill, not inside it: everything under $DEST is replaced wholesale
# on install, and a record that is deleted by the thing it audits records
# nothing. The leading dot plus the exclude entry below keep it out of sight
# and out of `git status`.
MANIFEST="$DEST_ROOT/.brain-protocol.manifest"

[ -d "$SRC" ] || { echo "no brain-protocol skill at $SRC" >&2; exit 1; }
[ -d "$DEST_ROOT" ] || { echo "$DEST_ROOT does not exist — is the skills repo cloned?" >&2; exit 1; }

# md5 per file, sorted by path, relative to the directory — so it is stable
# across machines and catches an added or deleted file as well as an edited
# one. `find | sort` and not `md5sum -r *`: the skill may carry
# subdirectories (references/, scripts/), and glob order is locale-dependent.
manifest_of() {
    _dir=$1
    [ -d "$_dir" ] || return 0
    ( cd "$_dir" && find . -type f ! -name '.*' | LC_ALL=C sort | while IFS= read -r f; do
        printf '%s  %s\n' "$(md5sum "$f" | cut -d' ' -f1)" "${f#./}"
    done )
}

if [ -d "$DEST" ]; then
    live="$(manifest_of "$DEST")"
    incoming="$(manifest_of "$SRC")"

    if [ -f "$MANIFEST" ]; then
        recorded="$(cat "$MANIFEST")"
        if [ "$live" = "$incoming" ] && [ "$recorded" = "$live" ]; then
            echo "$DEST/SKILL.md is already current"
            exit 0
        fi
        if [ "$live" != "$recorded" ]; then
            if [ "$FORCE" -eq 0 ]; then
                echo "REFUSING to overwrite $DEST — it has local edits." >&2
                echo >&2
                echo "This skill is owned by brainkit and re-provisioned from it, so an edit" >&2
                echo "made here is lost on every upgrade. Diverged files:" >&2
                printf '%s\n' "$live" | while IFS= read -r line; do
                    p=${line#*  }
                    printf '%s\n' "$recorded" | grep -q "  $p\$" || { echo "  + $p (added)" >&2; continue; }
                    printf '%s\n' "$recorded" | grep -qx "$line" || echo "  M $p" >&2
                done
                printf '%s\n' "$recorded" | while IFS= read -r line; do
                    p=${line#*  }
                    printf '%s\n' "$live" | grep -q "  $p\$" || echo "  - $p (deleted)" >&2
                done
                echo >&2
                echo "To keep the edits: commit them to the company skills repo, remove the" >&2
                echo "'brain-protocol/' line from $DEST_ROOT/.git/info/exclude, and stop running" >&2
                echo "this script — the repo then owns the file." >&2
                echo "To discard them: re-run with --force (a backup is kept either way)." >&2
                exit 2
            fi
            echo "--force: overwriting local edits in $DEST"
        fi
    fi

    # No manifest is the legacy state every already-provisioned box is in: the
    # copy predates this record, so nothing here can tell a stock copy from an
    # edited one. Back it up whenever it differs from what we are about to
    # write and say so — the guess that loses work is the one not to make.
    if [ "$live" != "$incoming" ]; then
        backup="$DEST_ROOT/.brain-protocol.bak-$(date -u +%Y%m%dT%H%M%SZ)"
        cp -R "$DEST" "$backup"
        echo "previous copy saved to $backup"
    fi
fi

mkdir -p "$DEST"
# Replace rather than merge: a file this script installed and no longer ships
# must not linger. $DEST is ours whole, which is also why MANIFEST lives beside
# it rather than in it.
rm -rf "$DEST"
mkdir -p "$DEST"
cp -R "$SRC/." "$DEST/"
manifest_of "$DEST" > "$MANIFEST"
echo "installed $DEST/SKILL.md"

# Only meaningful when the destination really is a git checkout; a plain
# directory is a perfectly good destination and should not error here.
if [ -d "$DEST_ROOT/.git" ]; then
    exclude="$DEST_ROOT/.git/info/exclude"
    mkdir -p "$(dirname "$exclude")"
    for pat in 'brain-protocol/' '.brain-protocol.manifest' '.brain-protocol.bak-*'; do
        if ! grep -qx "$pat" "$exclude" 2>/dev/null; then
            echo "$pat" >> "$exclude"
            echo "excluded $pat from $DEST_ROOT's git status"
        fi
    done
fi

echo
echo "next: recreate the agent containers once, so the boot hook drops the copy"
echo "it seeded into each one:  cd <compose dir> && docker compose up -d"
