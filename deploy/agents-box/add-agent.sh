#!/bin/sh
# Print everything needed to onboard one person: their compose stanza and the
# operator checklist (brain box + agents box + chat pairing). Print-only —
# paste the stanza into docker-compose.yml yourself.
#
# Usage: ./add-agent.sh <person> [brain-box-host]
set -eu

PERSON="${1:?usage: add-agent.sh <person> [brain-box-host]}"
BRAIN_HOST="${2:-brain-box}"

# person → ALICE_BOT_TOKEN-style env var name
TOKEN_VAR="$(printf '%s' "$PERSON" | tr '[:lower:]-' '[:upper:]_')_BOT_TOKEN"

cat <<EOF
# ---- 1. paste into docker-compose.yml under services: ----------------------

  agent-$PERSON:
    <<: *agent-base
    container_name: agent-$PERSON
    environment:
      - BRAIN_PERSON=$PERSON
      - BRAIN_GIT_REMOTE=brain-sync@$BRAIN_HOST:/srv/brain/compiled/$PERSON
      - TELEGRAM_BOT_TOKEN=\${$TOKEN_VAR:?set $TOKEN_VAR in .env}
      - ANTHROPIC_API_KEY=\${LLM_KEY:?set LLM_KEY in .env}
      - BRAIN_EMBED_BASE_URL=\${EMBED_URL:-}
      - BRAIN_EMBED_API_KEY=\${EMBED_API_KEY:-}
      - BRAIN_EMBED_MODEL=\${EMBED_MODEL:-}
      - BRAIN_EMBED_DIM=\${EMBED_DIM:-}
    volumes:
      - $PERSON-state:/opt/data
      - $PERSON-vault:/vault

# ---- and under volumes: ----------------------------------------------------

  $PERSON-state:
  $PERSON-vault:

# ---- 2. operator checklist -------------------------------------------------
#
# brain box:
#   a. add '$PERSON' to /srv/brain/master/_meta/org.yaml
#      (the next cycle compiles /srv/brain/compiled/$PERSON)
#   b. let the compiled repo accept the agent's pushes:
#        git -C /srv/brain/compiled/$PERSON config receive.denyCurrentBranch updateInstead
#
# chat:
#   c. Telegram: @BotFather -> /newbot -> put the token in .env as $TOKEN_VAR
#
# agents box:
#   d. docker compose up -d agent-$PERSON
#   e. docker logs agent-$PERSON        # shows the container's deploy key
#   f. append ONE line to /home/brain-sync/.ssh/authorized_keys on the brain
#      box (key from step e; the forced command locks it to this one repo):
#
#        command="/usr/local/bin/brain-serve-repo /srv/brain/compiled/$PERSON",restrict <key from step e>
#
#      (brain-serve-repo ships in deploy/brain-box/ — install it once)
#      vault-sync clones within 30s of the key landing.
#
# employee:
#   g. they message the bot; approve the one-time code:
#        docker exec agent-$PERSON hermes pairing approve telegram <CODE>
#   h. send them one line: "This is your assistant - tell it things, ask it things."
EOF
