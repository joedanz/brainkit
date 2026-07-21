#!/bin/sh
# provision-r2.sh — one-time, per-company R2 backup provisioning.
#
# Usage: provision-r2.sh <company-slug>
#
# Requires a Cloudflare API token in /root/.cf-provision-token (0600) with:
#   Account | Workers R2 Storage | Edit
#   Account | Account API Tokens | Edit     (to mint the bucket-scoped S3 token)
#
# Creates <slug>-backups bucket, enables object versioning, adds a lifecycle
# rule expiring noncurrent versions after 30 days, mints an S3 token scoped to
# ONLY that bucket, and writes /root/r2-<slug>.env (0600) ready to install as
# /etc/brain-backup/r2.env on each of the company's boxes.
#
# Nothing secret is printed to stdout. Delete /root/.cf-provision-token after
# every company you provision (this script reminds you at the end).
set -eu

SLUG="${1:?usage: provision-r2.sh <company-slug>}"
TOKEN_FILE=/root/.cf-provision-token
CF=https://api.cloudflare.com/client/v4
BUCKET="${SLUG}-backups"
OUT="/root/r2-${SLUG}.env"

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
    python3 -c "import json,sys; j=json.load(sys.stdin); print($1)"
}

ACCOUNT_ID=$(api GET /accounts | jget 'j["result"][0]["id"]')
echo "provision-r2: account resolved"

# --- bucket (idempotent) ---
if api GET "/accounts/$ACCOUNT_ID/r2/buckets/$BUCKET" >/dev/null 2>&1; then
    echo "provision-r2: bucket $BUCKET already exists"
else
    api POST "/accounts/$ACCOUNT_ID/r2/buckets" "{\"name\":\"$BUCKET\"}" >/dev/null
    echo "provision-r2: bucket $BUCKET created"
fi

# --- versioning ---
# If this PUT fails, enable versioning in the dashboard (bucket -> Settings ->
# Object versioning) and re-run: the block is idempotent and re-confirms state.
if api PUT "/accounts/$ACCOUNT_ID/r2/buckets/$BUCKET/versioning" \
        '{"enabled":true}' >/dev/null 2>&1; then
    echo "provision-r2: versioning enabled via API"
else
    echo "provision-r2: WARNING versioning API call failed — enable in" >&2
    echo "  dashboard (bucket Settings) then re-run to confirm" >&2
fi
VSTATE=$(api GET "/accounts/$ACCOUNT_ID/r2/buckets/$BUCKET/versioning" \
            2>/dev/null | jget 'j["result"].get("enabled")' 2>/dev/null || echo unknown)
echo "provision-r2: versioning state: $VSTATE"

# --- lifecycle: expire noncurrent versions after 30 days ---
if api PUT "/accounts/$ACCOUNT_ID/r2/buckets/$BUCKET/lifecycle" '{
  "rules": [{
    "id": "expire-noncurrent-30d",
    "enabled": true,
    "conditions": {"prefix": ""},
    "deleteObjectsNoncurrentTransition": {"condition": {"maxAge": 2592000, "type": "Age"}}
  }]
}' >/dev/null 2>&1; then
    echo "provision-r2: lifecycle rule set"
else
    echo "provision-r2: WARNING lifecycle PUT failed — check schema against" >&2
    echo "  current docs (GET .../lifecycle shows the accepted shape)" >&2
fi

# --- bucket-scoped S3 token ---
PG=$(api GET "/accounts/$ACCOUNT_ID/tokens/permission_groups")
READ_ID=$(echo "$PG"  | jget '[g["id"] for g in j["result"] if g["name"]=="Workers R2 Storage Bucket Item Read"][0]')
WRITE_ID=$(echo "$PG" | jget '[g["id"] for g in j["result"] if g["name"]=="Workers R2 Storage Bucket Item Write"][0]')

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
# r2.env for $SLUG — install as /etc/brain-backup/r2.env (0600 root:root)
R2_ENDPOINT=https://${ACCOUNT_ID}.r2.cloudflarestorage.com
R2_BUCKET=$BUCKET
AWS_ACCESS_KEY_ID=$TOK_ID
AWS_SECRET_ACCESS_KEY=$SECRET
# Per-box dead-man's-switch ping URL (healthchecks.io check for THIS box's job).
# Leave unset to disable pings.
#HEALTHCHECK_URL=
EOF
echo "provision-r2: wrote $OUT (0600) — scp to each box, then DELETE it here"
echo "provision-r2: done. Remove the provisioning token now:"
echo "  rm -f $TOKEN_FILE"
