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
   - Client facts -> the matching `Clients/<client>/` file. A named third party
     is a client/contact, never a `People/` note — even one sharing a person's
     surname. If a client space does not exist yet, an employee agent requests
     one via `People/<person>/ClientRequests/` (server provisions it); you, with
     full access, may create `Clients/<client>/` directly. Prefer the fullest
     reasonable identifier; when a name is ambiguous, ask before creating.
     A single mention can split into two homes — a client note in
     `Clients/<client>/` and, for a dated occurrence, a
     `Company/Intel/Events/<Name>.md` page — cross-linked.
     Owners share or revoke access to their spaces via
     `People/<person>/ShareRequests/` (`space`/`share-with`/`access`/`action`
     frontmatter); shares wait for admin approval (`brain shares`), revokes
     apply automatically, and status lands in that person's `Shares.md`. Your
     own access is never blocked by pending shares — keep writing. You cannot
     revoke your own access; ask an admin.
   - Company-wide decisions (a choice made, with its why) -> a new file in
     `Company/Decisions/`
   - Standing processes and standards -> a new file in `Company/Playbook/`
   - Destination, provider, event, or trend intel from articles, posts,
     PDFs, or screenshots -> distill into `Company/Intel/` (never archive the
     full text or file): a new page per entity (`mode: create`), or update an
     existing page with `mode: append` / `mode: patch`. Cite every claim
     `[source](URL), as of YYYY-MM`: source is the URL or the
     publication/title (or uploaded filename); use the source's date, or
     `captured YYYY-MM` (today) when it shows none. Keep `Intel/Home.md`
     linking to every page
   - Session summary -> `People/<person>/Sessions/`
   - General insights worth keeping -> fold into `Company/Memory.md`, which
     stays a lean overview linking out to detail notes — not a running log
4. Archive the processed transcript into `People/<person>/Sessions/`.

If you cannot place an item confidently, add it to `Company/Needs-Routing.md`
instead of guessing. Doing nothing is always safer than routing wrongly.

## Facts and entities

Durable state claims ("X is Y", "X works at Y") are written as fact lines so
they can be queried by time:

- A fact is a bullet with `[from:: YYYY-MM]` (or a full date) and a
  `[source:: [[note]]]` pointing at the episode that established it.
- When a new claim contradicts an existing fact: add `[until:: date]` to the
  old line and write the new fact line — both in the same commit. Never
  delete a fact line; history is the point.
- Prose keeps the existing `[source](URL), as of YYYY-MM` citation style.
  Fact lines are for queryable state; citations are for evidence in text.

Pages about a single thing get entity frontmatter:

    ---
    entity: client
    aliases: [Other Name, ABBR]
    ---

Types in use: client (a paying customer), person (someone we work with),
provider (a vendor or service), destination (a place we cover), event
(a dated occurrence), tool (software or equipment we use). Add a new type
when none fits — lowercase, singular — and use it consistently.

## Typed relations

A note can declare how it relates to others in frontmatter — five keys, each
holding `[[wikilinks]]`: `up`/`down` (hierarchy), `same` (peers), `prev`/`next`
(sequence):

    ---
    title: 2025-03-12 Kickoff call
    up: [[Acme]]
    next: [[2025-04-02 Check-in]]
    ---

Declare one direction only; the inverse is derived (a note you point `up` at
knows you as `down`). These sharpen retrieval and let agents walk structure
with `brain graph`. Add them only where they carry signal the vault's structure
doesn't already — folder-index parents (`Clients/Acme/Acme.md`), dated notes in
one folder, and same-`entity`-type pages are linked automatically, so don't
restate those. A target that doesn't resolve just yields no edge.

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
        "The company's live priority dashboard, kept current by the assistant.\n"
        "See [[Memory]] for the company overview and where knowledge lives.\n\n"
        "## Priorities\n\n_What needs attention now._\n\n"
        "## Open actions by owner\n\n_Outstanding action items, grouped by owner._\n\n"
        "## Pending promotions\n\n_Drafts awaiting approval._\n\n"
        "## Recent decisions\n\n_Latest entries in Decisions/._\n"
    )


def _memory_md(company: str) -> str:
    return (
        f"# {company} — Company Memory\n\n"
        "Business overview, positioning, offers, team structure. Maintained by\n"
        "the company assistant; substantive changes arrive via promotions or\n"
        "admin edits.\n"
    )


def _intel_home_md() -> str:
    return (
        "# Intel — the shared travel wiki\n\n"
        "A lean map: every Intel page is linked from here. Pages are distilled\n"
        "from articles, posts, and advisor knowledge — see the routing rules in\n"
        "AGENTS.md. Every claim cites `[source](URL), as of YYYY-MM` — a URL, or\n"
        "the publication/title when there is no link; use the source's date, or\n"
        "`captured YYYY-MM` (today's date) when it shows none.\n\n"
        "## Destinations\n\n(none yet)\n\n"
        "## Providers\n\n(none yet)\n\n"
        "## Events\n\n(none yet)\n\n"
        "## Trends\n\n(none yet)\n"
    )


def scaffold_master(dest: Path, company: str) -> list[str]:
    files: dict[str, str] = {
        # the cycle's embedding cache lives under _meta/cache/ — binary,
        # rebuildable, must never enter the master's git history
        ".gitignore": "_meta/cache/\n",
        "AGENTS.md": ASSISTANT_PROTOCOL,
        "Company/Home.md": _home_md(company),
        "Company/Memory.md": _memory_md(company),
        "Company/Decisions/.gitkeep": "",
        "Company/Playbook/.gitkeep": "",
        "Company/Templates/.gitkeep": "",
        "Company/Intel/Home.md": _intel_home_md(),
        "Company/Intel/Destinations/.gitkeep": "",
        "Company/Intel/Providers/.gitkeep": "",
        "Company/Intel/Events/.gitkeep": "",
        "Company/Intel/Trends/.gitkeep": "",
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
