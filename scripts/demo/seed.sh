#!/usr/bin/env bash
# Seed a freshly `brain init`-ed master vault with a two-person demo company.
# Usage: seed.sh <master-path>
set -euo pipefail
MASTER="${1:?usage: seed.sh <master-path>}"

cat > "$MASTER/_meta/org.yaml" <<'EOF'
people:
  alice: {name: Alice Nguyen, roles: [admin], teams: [sales], email: alice@acme.com}
  bob:   {name: Bob Rivera, teams: [ops], email: bob@acme.com}
EOF

mkdir -p "$MASTER/People/alice/Notes" "$MASTER/People/bob/Notes" \
         "$MASTER/Teams/sales" "$MASTER/Teams/ops"

cat > "$MASTER/Company/Decisions/2026-07-pricing.md" <<'EOF'
# Decision: usage-based pricing
We price per active seat, billed monthly. Reasoning: aligns cost with value.
EOF

cat > "$MASTER/Teams/sales/q3-pipeline.md" <<'EOF'
# Q3 pipeline
Twelve qualified leads; focus on the two enterprise renewals first.
EOF

cat > "$MASTER/People/alice/Notes/client-call.md" <<'EOF'
# Northwind call notes
They want the rollout by October. Draft proposal by Friday.
EOF

cat > "$MASTER/People/bob/Notes/salary-negotiation.md" <<'EOF'
# My salary negotiation notes  (PRIVATE)
Asking for $145k. Fallback: $138k + extra PTO.
EOF

git -C "$MASTER" add -A
git -C "$MASTER" commit -qm "seed demo company"
echo "seeded: alice (sales) + bob (ops), 4 notes — including bob's private salary note"
