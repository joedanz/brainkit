#!/bin/sh
# Liveness ping for the agent fleet: success when every compose service has a
# running container, /fail — with the missing names as the ping body — when
# any is down. /fail also fires when a running agent's protocol would be
# refused by its own hermes; the body then names the blocked marker(s)
# instead of (or alongside) the down names. One healthchecks.io check covers
# the box (missed pings) and every container (active /fail), and the alert
# says which agent died or is blocked.
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

# A running agent whose protocol hermes refuses to load is as good as down:
# it answers, but with no admission gate, routing or privacy rules. vault-sync
# leaves /opt/data/.brain-context-blocked when the container's own hermes
# would drop a context file. The marker is agent-writable, so it's flattened
# to one line and capped before it reaches the ping body. A hung container
# must not stall the root cron ping, so each read is bounded by timeout.
blocked=""
for svc in $running; do
    m="$(timeout 10 docker compose exec -T "$svc" cat /opt/data/.brain-context-blocked 2>/dev/null \
        | tr -d '\r\n' | cut -c1-200 || true)"
    if [ -n "$m" ]; then
        blocked="$blocked $svc($m)"
    fi
done

body=""
[ -z "$down" ] || body="down:$down"
[ -z "$blocked" ] || body="${body:+$body; }blocked:$blocked"

if [ -z "$body" ]; then
    curl -fsS -m 10 --retry 2 "$HEALTHCHECK_URL" >/dev/null
else
    echo "agents-liveness: $body ($(date -u +%FT%TZ))"
    curl -fsS -m 10 --retry 2 --data-raw "$body" "$HEALTHCHECK_URL/fail" >/dev/null
fi
