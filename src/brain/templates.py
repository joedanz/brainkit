"""Scaffolding content for a new company master vault."""

from __future__ import annotations

import difflib
import subprocess
from dataclasses import dataclass
from pathlib import Path

from brain.contextgen import render_charter
from brain.schemas import DEFAULT_SHARED, VaultConfig, load_config

_ORG_YAML_T = """\
people:
  # id: {name: Full Name, roles: [admin], teams: [sales], email: name@example.com}
  # email is optional and must be unique; it's the auth key for `brain ingest --from`.
  #
  # Keep the admin role on a dedicated curation identity, not on a real employee.
  # 'admin' writes the shared @SHARED@ space and approves promotions from the admin
  # dashboard / brain CLI — it needs no agent container. People who chat with an
  # agent are plain employees (read-only on @SHARED@), so no one can rewrite shared
  # knowledge without the human approval step. Add each employee like the example
  # below; grant the admin role to a person only if you want their own agent to
  # edit @SHARED@ directly.
  admin: {name: Admin, roles: [admin]}
  # alice: {name: Alice Example, teams: [sales], email: alice@example.com}
"""

_SPACES_YAML_T = """\
spaces:
  - {path: @SHARED_FIELD@read: [everyone],        write: ["role:admin"]}
  - {path: "Teams/*",   read: ["team:{name}"],   write: ["team:{name}"]}
  - {path: "People/*",  read: ["person:{name}"], write: ["person:{name}"]}

  # @ENTITIES@ are deny-by-default: only admins see a @ENTITY@ until it's assigned.
  # An exact per-@ENTITY@ rule below overrides this wildcard. Grant each @ENTITY@ to
  # its advisor(s) and any team that supports it, e.g.:
  #   - {path: "@ENTITIES@/Acme", read: ["role:admin", "person:alice", "team:concierge"], write: ["role:admin", "person:alice"]}
  - {path: "@ENTITIES@/*", read: ["role:admin"], write: ["role:admin"]}
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

_ASSISTANT_PROTOCOL_T = """\
# Assistant Protocol (server — full master vault)

This is the master vault. You are the company assistant with full
access. Personal agents see only their compiled slice; you maintain the whole.

@CHARTER_BLOCK@## What belongs in the brain

Routing decides where a fact goes; this decides whether it goes anywhere.
Apply it to every candidate before routing — record it only if all four hold:

- **Durable** — it outlasts the conversation. Passing status ("running late",
  "almost done", how someone feels today) is answered there and never written
  down. Something with an end date still counts: a decision, a deadline, a
  commitment is worth keeping until it is met.
- **Relevant** — it changes how we work: with a @ENTITY@, with a colleague, or
  as a company. A fact about a person that changes nothing about working with
  them is not a brain fact, however true it is.
- **New** — search with `brain_search` before writing. Something already here
  gets its page updated; it never becomes a second note saying the same thing.
- **Attributable** — you can say where it came from. Hearsay you cannot
  attribute stays out. Recording it as a claim ("X believes Y") is for a
  position someone took on the work, not a way to keep a rumour about a person.

Not recording is the ordinary outcome, not a failure. You process everyone's
transcripts, so what you let through compounds: most of a transcript is
conversation, and only some of it is knowledge, and a brain that stays small
stays searchable.

## Transcript pipeline

When a transcript appears in any `People/<person>/Inbox/`:
1. Summarize what was decided and what changed, not what was said.
2. Extract decisions, action items (owner + deadline), and changes to what the
   brain already says. `Inbox/doctor-digest.md` (`source: doctor`) is a
   generated integrity report, not a capture: never edit or archive it.
3. Admit each item against the tests above, then route what passed:
   - Action items -> that person's `People/<person>/Actions/Tracker.md`
   - Durable personal facts that passed the tests above -> that person's
     `People/<person>/Memory.md`,
     kept as a lean overview: topic-sized detail goes to
     `People/<person>/Notes/<Topic>.md` with a one-line link under the heading
   - @ENTITY_TITLE@ facts -> the matching `@ENTITIES@/<@ENTITY@>/` file. A named third party
     is a @ENTITY@/contact, never a `People/` note — even one sharing a person's
     surname. If a @ENTITY@ space does not exist yet, an employee agent requests
     one via `People/<person>/@REQUESTS@/` (server provisions it); you, with
     full access, may create `@ENTITIES@/<@ENTITY@>/` directly. Prefer the fullest
     reasonable identifier; when a name is ambiguous, ask before creating.
     One item can split into two homes — a @ENTITY@ note in
     `@ENTITIES@/<@ENTITY@>/` and, for a dated occurrence, a
     `@SHARED@/Intel/Events/<Name>.md` page — cross-linked. Create a
     @ENTITY@ space for someone the work actually involves; a name that came
     up once is a mention, not a @ENTITY@.
     Owners share or revoke access to their spaces via
     `People/<person>/ShareRequests/` (`space`/`share-with`/`access`/`action`
     frontmatter); shares wait for their decider's approval — the recipient
     for a person-share, a team lead for a team-share, an admin for
     company-wide — revokes apply automatically, and status lands in that
     person's `Shares.md`. Your
     own access is never blocked by pending shares — keep writing. You cannot
     revoke your own access; ask an admin.
     Recipients and team leads see an "Awaiting your decision" section in
     their Shares.md and decide via `People/<person>/Approvals/<share-id>.md`
     (decision: approve|reject, reason required to reject) — a decision note
     records only what its human explicitly decided. Company-wide shares
     (share-with: everyone, read-only) are decided by an admin with
     `brain shares approve`, never in-vault.
     Moving a personal note *into* a shared or team space is a promotion,
     not a share: draft it at `People/<person>/Promotions/<name>.md` with
     `target-path` and `mode: create|append|patch`, and the server queues it
     for approval. Team leads see their own "Awaiting your decision" section
     in Shares.md and decide via
     `People/<person>/PromotionApprovals/<promo-id>.md` (decision:
     approve|reject, reason required to reject). Promotions into `@SHARED@/`
     are decided by an admin at the dashboard, where the diff and the
     audience are visible — never in-vault, not even by an admin.
   - Company-wide decisions (a choice made, with its why) -> a new file in
     `@SHARED@/Decisions/`
   - Standing processes and standards -> a new file in `@SHARED@/Playbook/`
   - Knowledge read from articles, posts, PDFs, or screenshots -> distill
     into `@SHARED@/Intel/` (never archive the full text or file): a new page
     per subject (`mode: create`), or update an existing page with
     `mode: append` / `mode: patch`. What someone knows from working with a
     thing is not Intel — that cites the episode, not a URL, and belongs in
     the space this company names for it. Cite every claim
     `[source](URL), as of YYYY-MM`: source is the URL or the
     publication/title (or uploaded filename); use the source's date, or
     `captured YYYY-MM` (today) when it shows none. Keep `Intel/Home.md`
     linking to every page. When distilled content lands outside
     `@SHARED@/Intel/` instead — an entity page, a personal note — mark it
     `distilled: <URL or title>` in frontmatter and cite it the same way; the
     original never enters the vault, so the citation is the only route back
   - Session summary -> `People/<person>/Sessions/`, carrying what was
     decided and what passed the tests above, not a retelling — it is the
     record of the episode, so it is what a fact line cites
   - General insights that passed the tests above and bear on the whole
     company -> fold into `@SHARED@/Memory.md`, which stays a lean overview
     linking out to detail notes — not a running log
4. Delete the processed transcript. It is not archived: the summary from step 3
   is what the episode leaves behind. Delete it last, so an interrupted routing
   loses nothing.

An item that failed the tests above is not routed anywhere: drop it. Writing
it to `Needs-Routing.md` instead is how a brain fills with what nobody chose
to keep. Only an item that belongs here but has no confident home goes to
`@SHARED@/Needs-Routing.md`, one line saying what it is and why it did not
fit — then work that note down as homes become clear. Doing nothing is always
safer than routing wrongly.

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
    entity: @ENTITY@
    aliases: [Other Name, ABBR]
    ---

Types in use: @ENTITY@, person (someone we work with), provider (a vendor or
service), destination (a place we cover), event (a dated occurrence), tool
(software or equipment we use). Add a new type when none fits — lowercase,
singular — and use it consistently; prefer an existing type to a near-synonym,
because the relation miner treats each new type as a new group.

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
doesn't already — folder-index parents (`@ENTITIES@/Acme/Acme.md`), dated notes in
one folder, and same-`entity`-type pages are linked automatically, so don't
restate those. A target that doesn't resolve just yields no edge.

## Dashboards

Keep `@SHARED@/Home.md` current as the priority dashboard: open actions by
owner, pending promotions, recent decisions.

## Boundaries

- Never move content out of a `People/` space except via an approved
  promotion in `_meta/promotions/`.
- `_meta/` is operational state; never surface its contents in shared notes.
- Drafts only for anything outward-facing: a human sends every message and
  approves every commitment.
"""


def _render(template: str, config: VaultConfig) -> str:
    entity_title = config.entity[:1].upper() + config.entity[1:]
    # @SHARED_FIELD@ is the grant line's `<name>,` cell, padded to keep the
    # default's column alignment; a name too long for the column just gets
    # the single trailing space.
    field = (config.shared + ", ").ljust(13)
    return (template
            .replace("@CHARTER_BLOCK@", render_charter(config))
            .replace("@SHARED_FIELD@", field)
            .replace("@SHARED@", config.shared)
            .replace("@ENTITIES@", config.entities)
            .replace("@ENTITY_TITLE@", entity_title)
            .replace("@ENTITY@", config.entity)
            .replace("@REQUESTS@", config.requests_folder))


def org_yaml(config: VaultConfig = VaultConfig()) -> str:
    return _render(_ORG_YAML_T, config)


def spaces_yaml(config: VaultConfig = VaultConfig()) -> str:
    return _render(_SPACES_YAML_T, config)


def assistant_protocol(config: VaultConfig = VaultConfig()) -> str:
    return _render(_ASSISTANT_PROTOCOL_T, config)


ORG_YAML = org_yaml()
SPACES_YAML = spaces_yaml()
ASSISTANT_PROTOCOL = assistant_protocol()


def config_yaml(config: VaultConfig) -> str:
    """The `shared:` and `charter:` keys are written only when set, so a
    default vault's config.yaml is byte-identical to what shipped before
    either key existed.

    The charter is the one value here a human wrote as a sentence, so it is
    emitted as a quoted scalar rather than bare: an unquoted `charter: yes` is
    a bool to YAML, and one containing a colon would not round-trip at all.
    VaultConfig has already collapsed its whitespace, so escaping the two
    characters a double-quoted YAML scalar reserves is sufficient.
    """
    text = f"entities: {config.entities}\nentity: {config.entity}\n"
    if config.shared != DEFAULT_SHARED:
        text += f"shared: {config.shared}\n"
    if config.charter:
        escaped = config.charter.replace("\\", "\\\\").replace('"', '\\"')
        text += f'charter: "{escaped}"\n'
    return text


def _home_md(company: str, config: VaultConfig) -> str:
    return (
        f"# {company} — Home\n\n"
        "The company's live priority dashboard, kept current by the assistant.\n"
        # Path form here for the same reason as the Intel link below, and more
        # urgently: EVERY person's space holds a Memory.md, so a bare
        # [[Memory]] resolves first-match-wins in every compiled vault — the
        # scaffold was shipping the exact stem collision doctor warns about.
        f"See [[{config.shared}/Memory]] for the company overview and where\n"
        "knowledge lives, and"
        # Path form, not [[Home]]: this vault has two landing pages named Home
        # (the per-space convention doctor exempts from stem-collision), so a
        # bare stem would resolve to whichever sorts first. The link is also
        # what makes Intel reachable — the protocol connects Intel by links
        # rather than folder structure, and until something points at it the
        # wiki's own index is an island no graph walk can enter.
        f" [[{config.shared}/Intel/Home|Intel]] for knowledge\n"
        "distilled from outside sources.\n\n"
        "## Priorities\n\n_What needs attention now._\n\n"
        "## Open actions by owner\n\n_Outstanding action items, grouped by owner._\n\n"
        "## Pending promotions\n\n_Drafts awaiting approval._\n\n"
        "## Recent decisions\n\n_Latest entries in Decisions/._\n"
    )


def _memory_md(company: str, config: VaultConfig) -> str:
    return (
        f"# {company} — {config.shared} Memory\n\n"
        "Business overview, positioning, offers, team structure. Maintained by\n"
        "the company assistant; substantive changes arrive via promotions or\n"
        "admin edits.\n\n"
        "## Where knowledge lives\n\n"
        "_Name the spaces this company files knowledge in, and what belongs in\n"
        "each. The routing rules in AGENTS.md are generic on purpose; this is\n"
        "where a deployment records its own taxonomy, so an agent reads one map\n"
        "instead of inferring one. Leave it empty until there are spaces worth\n"
        "naming._\n"
    )


def _intel_home_md() -> str:
    """The Intel map, flat and in nobody's industry vocabulary.

    This used to scaffold Destinations/Providers/Events/Trends folders and
    headings, which shipped one company's taxonomy to every company — and put
    a worked subject list a thousand characters below the protocol's relevance
    test, where it was the only concrete vocabulary an agent had to calibrate
    against. A company's own subjects are its own to name.
    """
    return (
        "# Intel — knowledge distilled from outside sources\n\n"
        "Every Intel page is linked from here.\n\n"
        "Intel is what we read about the world: articles, posts, reports, PDFs.\n"
        "The source itself never enters the vault, so the citation is the only\n"
        "route back to it. Every claim cites `[source](URL), as of YYYY-MM` — a\n"
        "URL, or the publication/title when there is no link; use the source's\n"
        "date, or `captured YYYY-MM` (today's date) when it shows none.\n\n"
        "What we know from actually working with someone or something is not\n"
        "Intel. That is first-party knowledge: it cites the episode that\n"
        "established it rather than a URL, and it belongs in a space this\n"
        "company names for it.\n\n"
        "This page is flat on purpose. Add a folder when one subject outgrows\n"
        "this map, the same way a page splits when a section outgrows it.\n\n"
        "## Pages\n\n(none yet)\n"
    )


@dataclass(frozen=True)
class ProtocolStatus:
    """What `refresh_assistant_protocol` found, and what it did about it.

    `gate_differs` separates the one kind of drift that is not cosmetic. An
    out-of-date wording elsewhere in the protocol is untidy; an out-of-date
    admission gate is fail-open — the assistant is admitting things this
    version of brainkit decided do not belong — so doctor reports the two at
    different severities. `diff` is carried here rather than recomputed by the
    caller for the same reason the write lives in this function: two places
    deciding what "current" means is two places that can disagree.
    """
    path: Path
    missing: bool
    differs: bool
    written: bool
    gate_differs: bool = False
    diff: str = ""


_GATE_HEADING = "## What belongs in the brain"


def _gate_section(text: str | None) -> str:
    """The admission gate alone, or "" when the file has no gate at all.

    A master written before the gate existed returns "" here, which differs
    from the shipped section and so reports as the fail-open condition it is.
    """
    if not text or _GATE_HEADING not in text:
        return ""
    body = text.split(_GATE_HEADING, 1)[1]
    return body.split("\n## ", 1)[0]


def refresh_assistant_protocol(master: Path, config: VaultConfig | None = None,
                               *, write: bool = False) -> ProtocolStatus:
    """Bring a master vault's AGENTS.md back in line with the shipped protocol.

    `scaffold_master` writes this file once, at `brain init`, and nothing has
    ever rewritten it — so every improvement to the assistant protocol reached
    new vaults only, and a master deployed a year ago still instructs its
    assistant with the rules of a year ago. Compiled per-person vaults have no
    such problem: `generate_context_files` regenerates theirs on every cycle.

    Reporting and writing are one function so they cannot disagree about what
    "current" means, but `write` defaults to False: this file is generated
    content an admin may nonetheless have edited by hand, and the honest
    sequence is to say it differs and let a human decide, rather than to
    silently discard the edit the way a compile discards edits to a slice.
    """
    config = config or load_config(master)
    path = master / "AGENTS.md"
    expected = assistant_protocol(config)
    try:
        current = path.read_text()
    except FileNotFoundError:
        current = None

    differs = current != expected
    diff = "".join(difflib.unified_diff(
        (current or "").splitlines(keepends=True), expected.splitlines(keepends=True),
        fromfile=f"{path} (current)", tofile="shipped protocol")) if differs else ""
    if differs and write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(expected)
    return ProtocolStatus(path=path, missing=current is None,
                          differs=differs, written=differs and write,
                          gate_differs=_gate_section(current) != _gate_section(expected),
                          diff=diff)


def scaffold_master(dest: Path, company: str,
                     config: VaultConfig | None = None) -> list[str]:
    config = config or VaultConfig()
    files: dict[str, str] = {
        # the cycle's embedding cache lives under _meta/cache/ — binary,
        # rebuildable, must never enter the master's git history
        ".gitignore": "_meta/cache/\n",
        "AGENTS.md": assistant_protocol(config),
        f"{config.shared}/Home.md": _home_md(company, config),
        f"{config.shared}/Memory.md": _memory_md(company, config),
        f"{config.shared}/Decisions/.gitkeep": "",
        f"{config.shared}/Playbook/.gitkeep": "",
        f"{config.shared}/Templates/.gitkeep": "",
        f"{config.shared}/Intel/Home.md": _intel_home_md(),
        "Teams/.gitkeep": "",
        "People/.gitkeep": "",
        f"{config.entities}/.gitkeep": "",
        "_meta/org.yaml": org_yaml(config),
        "_meta/spaces.yaml": spaces_yaml(config),
        "_meta/config.yaml": config_yaml(config),
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
