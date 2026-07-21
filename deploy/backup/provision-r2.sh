#!/bin/sh
# provision-r2.sh — one-time, per-company R2 backup provisioning.
#
# Usage: provision-r2.sh <company-slug>
#
# Requires a Cloudflare API token in /root/.cf-provision-token (0600) with:
#   Account | Workers R2 Storage | Edit
#   Account | Account API Tokens | Edit     (to mint the bucket-scoped S3 tokens)
#
# Creates TWO buckets — <slug>-brain-backups and <slug>-agents-backups — and
# mints an S3 token scoped to each. The bucket is the isolation boundary: R2
# tokens scope to whole buckets (no prefix scoping) and R2 has no object
# versioning (GetBucketVersioning is an S3-compat stub, verified 2026-07), so
# per-box buckets are what keeps a compromised box from deleting the OTHER
# box's backups. Optional extra hardening later: R2 bucket locks (REST-managed,
# unreachable by the boxes' S3 tokens) — but they fight restic prune, so they
# are not applied here.
#
# Writes /root/r2-<slug>-brain.env and /root/r2-<slug>-agents.env (0600),
# each ready to install as /etc/brain-backup/r2.env on its box.
#
# Nothing secret is printed to stdout. Delete /root/.cf-provision-token (and
# the dashboard token it came from) after every company you provision.
set -eu

SLUG="${1:?usage: provision-r2.sh <company-slug>}"
TOKEN_FILE=/root/.cf-provision-token
CF=https://api.cloudflare.com/client/v4

[ -r "$TOKEN_FILE" ] || { echo "provision-r2: $TOKEN_FILE missing" >&2; exit 1; }
TOKEN=$(cat "$TOKEN_FILE")

api() { # api METHOD PATH [JSON_BODY]  -> response body on stdout
    _m=$1; _p=$2; _b=${3:-}
    if [ -n "$_b" ]; then
        curl -fsS -X "$_m" "$CF$_p" -H "Authorization: Bearer $TOKEN" \
             -H "Content-Type: application/json" --data "$_b"
    else
        curl -fsS -X "$_m" "$CF$_p" -H "Authorization: Bearer $TOKEN"
    fi
}

jget() { # jget PYEXPR  -> evaluate over parsed stdin JSON bound to j
    # strict=False: Cloudflare responses can carry control chars in strings
    python3 -c "import json,sys; j=json.loads(sys.stdin.read(), strict=False); print($1)"
}

ACCOUNT_ID=$(api GET /accounts | jget 'j["result"][0]["id"]')
echo "provision-r2: account resolved"

PG=$(api GET "/accounts/$ACCOUNT_ID/tokens/permission_groups")
READ_ID=$(echo "$PG"  | jget '[g["id"] for g in j["result"] if g["name"]=="Workers R2 Storage Bucket Item Read"][0]')
WRITE_ID=$(echo "$PG" | jget '[g["id"] for g in j["result"] if g["name"]=="Workers R2 Storage Bucket Item Write"][0]')

for KIND in brain agents; do
    BUCKET="${SLUG}-${KIND}-backups"
    OUT="/root/r2-${SLUG}-${KIND}.env"

    if api GET "/accounts/$ACCOUNT_ID/r2/buckets/$BUCKET" >/dev/null 2>&1; then
        echo "provision-r2: bucket $BUCKET already exists"
    else
        api POST "/accounts/$ACCOUNT_ID/r2/buckets" "{\"name\":\"$BUCKET\"}" >/dev/null
        echo "provision-r2: bucket $BUCKET created"
    fi

    TOK_JSON=$(api POST "/accounts/$ACCOUNT_ID/tokens" "{
      \"name\": \"${BUCKET}-restic\",
      \"policies\": [{
        \"effect\": \"allow\",
        \"permission_groups\": [{\"id\": \"$READ_ID\"}, {\"id\": \"$WRITE_ID\"}],
        \"resources\": {\"com.cloudflare.edge.r2.bucket.${ACCOUNT_ID}_default_${BUCKET}\": \"*\"}
      }]
    }")
    TOK_ID=$(echo "$TOK_JSON" | jget 'j["result"]["id"]')
    TOK_VALUE=$(echo "$TOK_JSON" | jget 'j["result"]["value"]')
    # R2 S3 credentials: access key id = token id, secret = SHA-256(token value)
    SECRET=$(printf '%s' "$TOK_VALUE" | sha256sum | cut -d' ' -f1)

    umask 077
    cat > "$OUT" <<EOF
# r2.env for $SLUG ($KIND box) — install as /etc/brain-backup/r2.env (0600 root:root)
R2_ENDPOINT=https://${ACCOUNT_ID}.r2.cloudflarestorage.com
R2_BUCKET=$BUCKET
AWS_ACCESS_KEY_ID=$TOK_ID
AWS_SECRET_ACCESS_KEY=$SECRET
# Per-box dead-man's-switch ping URL (healthchecks.io check for THIS box's job).
# Leave unset to disable pings.
#HEALTHCHECK_URL=
EOF
    echo "provision-r2: wrote $OUT (0600)"
done

echo "provision-r2: done. Install each env file on its box, then clean up:"
echo "  rm -f $TOKEN_FILE /root/r2-${SLUG}-*.env"
echo "  ...and delete the provisioning token in the Cloudflare dashboard."
