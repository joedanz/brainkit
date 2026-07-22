#!/bin/sh
# Liveness ping for the agent fleet: success when every compose service has a
# running container, /fail — with the missing names as the ping body — when
# any is down. One healthchecks.io check covers the box (missed pings) and
# every container (active /fail), and the alert says which agent died.
#
# The expected list comes from docker-compose.yml itself, so onboarding a new
# agent needs no monitoring changes.
#
# Cron (root):
#   */5 * * * * HEALTHCHECK_URL=https://hc-ping.com/<uuid> \
#       /usr/local/sbin/agents-liveness.sh >> /var/log/agents-liveness.log 2>&1
set -eu

COMPOSE_DIR="${COMPOSE_DIR:-/opt/brain/deploy/agents-box}"
: "${HEALTHCHECK_URL:?set HEALTHCHECK_URL (hc-ping.com check for this fleet)}"

cd "$COMPOSE_DIR"
all="$(docker compose config --services)"
running="$(docker compose ps --services --status running)"

down=""
for svc in $all; do
    printf '%s\n' "$running" | grep -qx "$svc" || down="$down $svc"
done

if [ -z "$down" ]; then
    curl -fsS -m 10 --retry 2 "$HEALTHCHECK_URL" >/dev/null
else
    echo "agents-liveness: DOWN$down ($(date -u +%FT%TZ))"
    curl -fsS -m 10 --retry 2 --data-raw "down:$down" "$HEALTHCHECK_URL/fail" >/dev/null
fi
