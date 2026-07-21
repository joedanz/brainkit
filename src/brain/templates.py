"""Scaffolding content for a new company master vault."""

from __future__ import annotations

import subprocess
from pathlib import Path

ORG_YAML = """\
people:
  # id: {name: Full Name, roles: [admin], teams: [sales], email: name@example.com}
  # email is optional and must be unique; it's the auth key for `brain ingest --from`.
  #
  # Keep the admin role on a dedicated curation identity, not on a real employee.
  # 'admin' writes the shared Company space and approves promotions from the admin
  # dashboard / brain CLI — it needs no agent container. People who chat with an
  # agent are plain employees (read-only on Company), so no one can rewrite shared
  # knowledge without the human approval step. Add each employee like the example
  # below; grant the admin role to a person only if you want their own agent to
  # edit Company directly.
  admin: {name: Admin, roles: [admin]}
  # alice: {name: Alice Example, teams: [sales], email: alice@example.com}
"""

SPACES_YAML = """\
spaces:
  - {path: Company,     read: [everyone],        write: ["role:admin"]}
  - {path: "Teams/*",   read: ["team:{name}"],   write: ["team:{name}"]}
  - {path: "People/*",  read: ["person:{name}"], write: ["person:{name}"]}

  # Clients are deny-by-default: only admins see a client until it's assigned.
  # An exact per-client rule below overrides this wildcard. Grant each client to
  # its advisor(s) and any team that supports it, e.g.:
  #   - {path: "Clients/Acme", read: ["role:admin", "person:alice", "team:concierge"], write: ["role:admin", "person:alice"]}
  - {path: "Clients/*", read: ["role:admin"], write: ["role:admin"]}
"""

WEBHOOK_YAML_EXAMPLE = """\
# Webhook intake — let outside services push notes straight into Inboxes:
# meeting recorders (Fathom), automation platforms (Zapier, Make, n8n),
# Composio triggers watching email or chat. Recommended for any deployment
# that wants the brain to feed itself.
#
# To enable:
#   1. copy this file to webhook.yaml and edit the sources below
#   2. set each secret's environment variable (secrets never live in this file)
#   3. run the receiver behind a TLS reverse proxy:  brain webhook --master <master>
#      (deploy/brain-box/ ships a systemd unit and Caddy snippet)
#
# Each source is one URL path (POST /hook/<id>), one verify mode, one routing
# rule. Unknown senders are refused; every delivery lands via `brain ingest`.
# Sources accept up to 60 deliveries/minute by default (excess gets a
# retryable 429); set `rate_limit:` on a source to change that — raise it
# before a big backfill, lower it for a source that should stay quiet.
#
# sources:
#   # A meeting recorder delivering one person's transcripts.
#   # `standard-webhooks` verifies the HMAC signature Fathom/Composio send.
#   - id: fathom-founder
#     person: founder
#     verify: standard-webhooks
#     secret_env: WEBHOOK_FATHOM_SECRET
#     source: fathom
#     body_field: transcript
#
#   # A Zapier/Make automation. These can't sign requests, so `token` checks a
#   # shared secret in the Authorization or X-Brain-Token header, and
#   # `route: sender-email` files each note by the payload's email via org.yaml.
#   - id: zapier-intake
#     route: sender-email
#     verify: token
#     secret_env: WEBHOOK_ZAPIER_TOKEN
#     source: zapier
"""

ASSISTANT_PROTOCOL = """\
# Assistant Protocol (server — full master vault)

This is the master vault. You are the company assistant with full
access. Personal agents see only their compiled slice; you maintain the whole.

## Transcript pipeline

When a transcript appears in any `People/<person>/Inbox/`:
1. Summarize it.
2. Extract decisions, action items (owner + deadline), and context updates.
3. Route:
   - Action items -> that person's `People/<person>/Actions/Tracker.md`
   - Personal durable facts -> that person's `People/<person>/Memory.md`,
     kept as a lean overview: topic-sized detail goes to
     `People/<person>/Notes/<Topic>.md` with a one-line link under the heading
   - Client facts -> the matching `Clients/<client>/` file
   - Company-wide decisions (a choice made, with its why) -> a new file in
     `Company/Decisions/`
   - Standing processes and standards -> a new file in `Company/Playbook/`
   - Session summary -> `People/<person>/Sessions/`
   - General insights worth keeping -> fold into `Company/Memory.md`, which
     stays a lean overview linking out to detail notes — not a running log
4. Archive the processed transcript into `People/<person>/Sessions/`.

If you cannot place an item confidently, add it to `Company/Needs-Routing.md`
instead of guessing. Doing nothing is always safer than routing wrongly.

## Dashboards

Keep `Company/Home.md` current as the priority dashboard: open actions by
owner, pending promotions, recent decisions.

## Boundaries

- Never move content out of a `People/` space except via an approved
  promotion in `_meta/promotions/`.
- `_meta/` is operational state; never surface its contents in shared notes.
- Drafts only for anything outward-facing: a human sends every message and
  approves every commitment.
"""


def _home_md(company: str) -> str:
    return (
        f"# {company} — Home\n\n"
        "## Priorities\n\n(maintained by the company assistant)\n\n"
        "## Links\n\n- [[Memory]]\n- Decisions/\n- Playbook/\n- Templates/\n"
    )


def _memory_md(company: str) -> str:
    return (
        f"# {company} — Company Memory\n\n"
        "Business overview, positioning, offers, team structure. Maintained by\n"
        "the company assistant; substantive changes arrive via promotions or\n"
        "admin edits.\n"
    )


def scaffold_master(dest: Path, company: str) -> list[str]:
    files: dict[str, str] = {
        "AGENTS.md": ASSISTANT_PROTOCOL,
        "Company/Home.md": _home_md(company),
        "Company/Memory.md": _memory_md(company),
        "Company/Decisions/.gitkeep": "",
        "Company/Playbook/.gitkeep": "",
        "Company/Templates/.gitkeep": "",
        "Teams/.gitkeep": "",
        "People/.gitkeep": "",
        "Clients/.gitkeep": "",
        "_meta/org.yaml": ORG_YAML,
        "_meta/spaces.yaml": SPACES_YAML,
        "_meta/webhook.yaml.example": WEBHOOK_YAML_EXAMPLE,
        "_meta/promotions/pending/.gitkeep": "",
        "_meta/promotions/approved/.gitkeep": "",
        "_meta/promotions/rejected/.gitkeep": "",
    }
    created = []
    for rel, content in files.items():
        p = dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        created.append(rel)
    subprocess.run(["git", "-C", str(dest), "init", "-b", "main"],
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", str(dest), "add", "-A"],
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", str(dest), "-c", "user.name=Brain Init",
                    "-c", "user.email=init@brain.local",
                    "commit", "-m", f"init: {company} master vault"],
                   capture_output=True, check=True)
    return created
