#!/bin/sh
# backup-agents.sh — back up every running agent-* container's hermes state.
#
# Run from host cron on the agents box, e.g. nightly:
#   15 3 * * * /opt/brain/deploy/agents-box/backup-agents.sh /srv/backups/agents
#
# Usage: backup-agents.sh [dest-dir]        (default /srv/backups/agents)
#   BACKUP_RETENTION_DAYS=14   days of zips to keep (default 14)
#   BACKUP_QUICK=1             quick state-only snapshot instead of full
#
# Each zip is produced by `hermes backup` INSIDE the container — it snapshots
# state.db via SQLite's backup() API, so it is consistent even while the
# gateway is running (a raw tar of the live volume is not). Only the
# /opt/data state volume needs backing up; /vault is derived — vault-sync
# re-clones it from the brain box and `brain index` rebuilds the index.
#
# The zips contain the deploy key, .env secrets, and chat history: treat the
# destination like the agents themselves — encrypt when shipping off-box
# (restic/borg do this natively) and never restore one person's zip into
# another person's container.
set -eu

DEST="${1:-/srv/backups/agents}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
STAMP=$(date -u +%Y%m%d-%H%M%S)
TMP_ZIP=/tmp/hermes-backup.zip

if [ -n "${BACKUP_QUICK:-}" ]; then
    set -- backup --quick -o "$TMP_ZIP"
else
    set -- backup -o "$TMP_ZIP"
fi

mkdir -p "$DEST"

containers=$(docker ps --format '{{.Names}}' | grep '^agent-' || true)
if [ -z "$containers" ]; then
    echo "backup-agents: no running agent-* containers" >&2
    exit 1
fi

failed=""
for c in $containers; do
    if docker exec "$c" hermes "$@" \
            && docker cp "$c:$TMP_ZIP" "$DEST/$c-$STAMP.zip"; then
        docker exec "$c" rm -f "$TMP_ZIP" || true
        echo "backup-agents: $c -> $DEST/$c-$STAMP.zip"
    else
        failed="$failed $c"
    fi
done

find "$DEST" -name 'agent-*.zip' -type f -mtime +"$RETENTION_DAYS" -delete

if [ -n "$failed" ]; then
    echo "backup-agents: FAILED:$failed" >&2
    exit 1
fi
