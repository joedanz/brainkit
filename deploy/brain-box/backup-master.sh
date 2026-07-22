#!/bin/sh
# backup-master.sh — hourly encrypted offsite backup of the brain box to R2.
#
# Backs up the master vault plus reconstruction-critical config:
#   /srv/brain/master  /etc/caddy  /etc/cloudflared  /etc/brain
#   /etc/systemd/system  /root/.cloudflared (tunnel-mgmt cert, browser-only
#   to reobtain)  /home/brain-sync/.ssh (authorized_keys carrying every
#   agent deploy key's forced-command entry — losing it means re-authorizing
#   each agent by hand)
# Never /etc/brain-backup — the keys that unlock the backup must not be in it.
#
# Cron (root):  7 * * * * /usr/local/sbin/backup-master.sh >> /var/log/backup-master.log 2>&1
#
# Needs: /etc/brain-backup/r2.env (0600; see deploy/backup/r2.env.example)
#        /etc/brain-backup/brain-master.pass (0600; openssl rand -base64 32)
# Pings $HEALTHCHECK_URL (/start, success, /fail) when set; silent when unset.
# Weekly `restic check` piggybacks on the Sunday 03:xx run.
set -eu

ENV_FILE="${ENV_FILE:-/etc/brain-backup/r2.env}"
# shellcheck disable=SC1090
. "$ENV_FILE"
export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
export RESTIC_REPOSITORY="s3:${R2_ENDPOINT}/${R2_BUCKET}/brain-master"
export RESTIC_PASSWORD_FILE="${RESTIC_PASSWORD_FILE:-/etc/brain-backup/brain-master.pass}"

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
restic cat config >/dev/null 2>&1 || restic init
restic unlock >/dev/null 2>&1 || true

restic backup \
    /srv/brain/master \
    /etc/caddy /etc/cloudflared /etc/brain \
    /etc/systemd/system \
    /root/.cloudflared /home/brain-sync/.ssh \
    --exclude /etc/brain-backup

restic forget --prune \
    --keep-hourly 24 --keep-daily 14 --keep-weekly 8 --keep-monthly 12

if [ "$(date -u +%u%H)" = "703" ]; then
    restic check
fi
echo "backup-master: ok $(date -u +%Y-%m-%dT%H:%M:%SZ)"
