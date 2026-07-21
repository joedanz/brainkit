#!/bin/sh
# backup-agents-offsite.sh — ship the nightly hermes backup zips to R2,
# encrypted, via restic. Runs AFTER backup-agents.sh (03:15) has produced
# the night's zips; this script changes nothing about that job.
#
# The zips contain secrets (deploy keys, .env, bot tokens, chat history);
# restic encrypts client-side, so R2 only ever sees ciphertext.
#
# Cron (root):  45 3 * * * /usr/local/sbin/backup-agents-offsite.sh >> /var/log/backup-agents-offsite.log 2>&1
#
# Needs: /etc/brain-backup/r2.env (0600; see deploy/backup/r2.env.example)
#        /etc/brain-backup/agents.pass (0600; openssl rand -base64 32)
# Pings $HEALTHCHECK_URL (/start, success, /fail) when set; silent when unset.
# Weekly `restic check` piggybacks on the Sunday run.
set -eu

ENV_FILE="${ENV_FILE:-/etc/brain-backup/r2.env}"
SRC="${SRC:-/srv/backups/agents}"
# shellcheck disable=SC1090
. "$ENV_FILE"
export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
export RESTIC_REPOSITORY="s3:${R2_ENDPOINT}/${R2_BUCKET}/agents"
export RESTIC_PASSWORD_FILE="${RESTIC_PASSWORD_FILE:-/etc/brain-backup/agents.pass}"

hc() {
    [ -n "${HEALTHCHECK_URL:-}" ] || return 0
    curl -fsS -m 10 --retry 3 "${HEALTHCHECK_URL}$1" >/dev/null 2>&1 || true
}
finish() {
    rc=$?
    if [ "$rc" -eq 0 ]; then hc ""; else hc /fail; fi
}
trap finish EXIT

hc /start
[ -d "$SRC" ] || { echo "backup-agents-offsite: $SRC missing" >&2; exit 1; }

restic cat config >/dev/null 2>&1 || restic init
restic unlock >/dev/null 2>&1 || true

restic backup "$SRC"

restic forget --prune \
    --keep-daily 14 --keep-weekly 8 --keep-monthly 6

if [ "$(date -u +%u)" = "7" ]; then
    restic check
fi
echo "backup-agents-offsite: ok $(date -u +%Y-%m-%dT%H:%M:%SZ)"
