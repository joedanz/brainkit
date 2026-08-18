#!/bin/sh
# Admin digest for the two human-gated queues: promotions and space shares.
#
# Runs on the BRAIN BOX, not in an agent container. Only this box has the
# master vault — agents hold a deploy key for their own compiled slice and
# nothing else, so `brain promotions list --master` has nowhere to point from
# inside a container. (An earlier draft of this put it in the admin's agent
# container; it cannot work there.)
#
# Prints nothing when both queues are empty, which is the entire design: cron
# mails only what a job writes to stdout, so an empty queue sends no mail. A
# digest that says "nothing pending" every morning trains its reader to filter
# the whole channel, and then the one that mattered gets filtered too.
#
# It deliberately does NOT approve anything and offers no way to. Company-wide
# promotions are decided at the dashboard, where the reviewer sees a live diff
# against the current page and a warning naming exactly who will be able to
# read the result. The server enforces that regardless — a shared-space target
# is refused in-vault even for an admin — so this is describing a boundary it
# cannot cross, not one it is trusted to respect.
#
# Cron (root), weekday mornings, after the per-person nudges so a lead's own
# decision has already cleared its item:
#   0 9 * * 1-5 MASTER=/srv/brain/master \
#       /usr/local/sbin/brain-queue-digest.sh
# Set MAILTO in the crontab to choose where the output lands.
set -eu

MASTER="${MASTER:-/srv/brain/master}"
BRAIN="${BRAIN_BIN:-brain}"
# Age at which a pending item stops being a queue and starts being a problem.
STALE_DAYS="${STALE_DAYS:-3}"

promotions=$("$BRAIN" promotions list --master "$MASTER" 2>/dev/null || true)
shares=$("$BRAIN" shares list --master "$MASTER" 2>/dev/null || true)

n_promo=$(printf '%s' "$promotions" | grep -c . || true)
n_share=$(printf '%s' "$shares" | grep -c . || true)

[ "$n_promo" -eq 0 ] && [ "$n_share" -eq 0 ] && exit 0

echo "Brain queues — $(date +%Y-%m-%d)"
echo

if [ "$n_promo" -gt 0 ]; then
    # Split by destination: a Teams/ item can be cleared by that team's lead
    # from their own vault, so it may never need you. Depth alone is not a
    # call to action; depth you personally own is.
    team=$(printf '%s\n' "$promotions" | grep -c 'target=Teams/' || true)
    yours=$((n_promo - team))
    echo "$n_promo promotion(s) pending — $yours need an admin, $team can be cleared by a team lead"
    printf '%s\n' "$promotions" | sed 's/^/  /'
    echo
fi

if [ "$n_share" -gt 0 ]; then
    echo "$n_share share request(s) pending"
    printf '%s\n' "$shares" | sed 's/^/  /'
    echo
fi

echo "Review and decide at the dashboard. Anything older than ${STALE_DAYS} days is"
echo "worth deciding or rejecting outright — a queue that only grows teaches"
echo "people that proposing achieves nothing, and a rejection with a reason is"
echo "a better answer than indefinite silence."
