#!/bin/sh
# Liveness ping for the brain box's long-running services: success when every
# expected systemd unit is active, /fail — with the failed names as the ping
# body — when any is not. The cycle cron's own dead-man ping already covers
# box/cron/cycle health; this covers the services whose death is otherwise
# silent (dashboards, webhook receiver, and whatever fronts them).
#
# Expected = every enabled brain-*.service unit, discovered from systemd, so
# adding a person's dashboard needs no monitoring change. Append non-brain
# units via $EXTRA_UNITS (e.g. "caddy cloudflared" when this box fronts its
# dashboards with them).
#
# Cron (root):
#   */5 * * * * HEALTHCHECK_URL=https://hc-ping.com/<uuid> \
#       EXTRA_UNITS="caddy cloudflared" \
#       /usr/local/sbin/brain-liveness.sh >> /var/log/brain-liveness.log 2>&1
set -eu

: "${HEALTHCHECK_URL:?set HEALTHCHECK_URL (hc-ping.com check for this box's services)}"

units="$(systemctl list-unit-files 'brain-*.service' --state=enabled --no-legend | awk '{print $1}')"

down=""
for u in $units ${EXTRA_UNITS:-}; do
    systemctl is-active --quiet "$u" || down="$down $u"
done

if [ -z "$down" ]; then
    curl -fsS -m 10 --retry 2 "$HEALTHCHECK_URL" >/dev/null
else
    echo "brain-liveness: DOWN$down ($(date -u +%FT%TZ))"
    curl -fsS -m 10 --retry 2 --data-raw "down:$down" "$HEALTHCHECK_URL/fail" >/dev/null
fi
