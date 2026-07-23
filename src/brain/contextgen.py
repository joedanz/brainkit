"""Generate AGENTS.md / CLAUDE.md so every compiled vault is self-describing.

Limits follow Hermes Agent context-file loading: ~20K chars for the root file,
~8K for progressively-discovered per-directory files. Copy is declarative so it
passes Hermes's prompt-injection scan.
"""

from __future__ import annotations

from pathlib import Path

from brain.schemas import Person, SpaceRule, VaultConfig

ROOT_LIMIT = 20_000
SPACE_LIMIT = 8_000

_ROOT_TEMPLATE = """\
# Brain Protocol — vault of {name} ({pid})

This vault is {name}'s slice of the company brain. It is compiled: it contains
only the spaces {name} may read. Anything not present here is not accessible.

## Spaces in this vault

{space_lines}

Read-only spaces are maintained by the company assistant. Edits belong in
writable spaces; the write-back service rejects changes to read-only paths.

## Routing rules (apply when processing new information)

- Action items (owner + deadline) -> `People/{pid}/Actions/Tracker.md`
- Session/meeting summaries -> `People/{pid}/Sessions/`
- Raw transcripts land in `People/{pid}/Inbox/` and are processed, then archived to `People/{pid}/Sessions/`
- Personal durable facts, preferences, lessons -> `People/{pid}/Memory.md`.
  Keep it a lean overview, not a running log: small facts live under its
  headings; when a topic outgrows a few lines, move the detail to
  `People/{pid}/Notes/<Topic>.md` and leave a one-line link under the heading
- A **named third party** (a person, family, or company you work with or track)
  is a {entity}/contact, not you — capture it as a {entity}, never in
  `People/{pid}/`. You are {pid}: a third party who happens to share your
  surname is still a third party. To create a {entity}, write a request to
  `People/{pid}/{requests}/<name>.md` with frontmatter `{name_key}: <full
  name>`, `owner: {pid}`, `entity: {entity}`; the server provisions a
  `{entities}/<name>/` space you own on the next cycle, then you write there
  directly. Name it with the fullest reasonable identifier (a full name, not a
  bare surname). Ask the user for one distinguishing detail before creating
  only when the name is thin or ambiguous — a bare common surname, a name that
  matches a {entity} you already have, or one that collides with your own
  household. One utterance can split into two homes: e.g. a family attending an
  event becomes a {entity} note AND a `Company/Intel/Events/` promotion,
  cross-linked.
- {entity_title} facts about a {entity} you already own -> write them into that
  `{entities}/<name>/` space directly
- To give a colleague or team access to a space you own (e.g. a {entity} you
  created): write `People/{pid}/ShareRequests/<name>.md` with frontmatter
  `space: <the space>`, `share-with: person:<id>` or `team:<name>`,
  `access: read|write`, `action: share` — the body is an optional note to the
  approver. An admin approves shares; status shows in `People/{pid}/Shares.md`,
  and your own access never blocks — keep writing while it's pending. To
  remove someone, use `action: revoke` (applies automatically; you cannot
  revoke your own access).
- Decisions of company-wide relevance (a choice made, with its why) -> draft a
  promotion targeting a new file in `Company/Decisions/`
- Standing processes, standards, or how-we-work facts -> draft a promotion
  targeting a new file in `Company/Playbook/`
- Articles, posts, links, PDFs, and screenshots: distill, never archive —
  read the source (fetch a URL, extract PDF text, read an image) and route
  destination, provider, event, or trend intel to `Company/Intel/` via a
  promotion (see below). The full text or file never enters the vault; your
  personal take stays in `People/{pid}/Notes/`
- If unsure where something belongs, add it to `People/{pid}/Needs-Routing.md`

## Company Intel (the shared travel wiki)

`Company/Intel/` holds shared reference knowledge, mapped in `Intel/Home.md`:
`Destinations/<Place>.md`, `Providers/<Name>.md` (hotels, DMCs, outfitters,
villas, cruise, aviation, guides), `Events/<Name>.md` (venues and
access-worthy events), `Trends/<YYYY-MM Topic>.md`. Conventions:
- First line of a page is a one-sentence summary; provider pages add
  `**Type:** ... · **Where:** [[Destination]]`.
- Every claim cites its provenance: `[source](URL), as of YYYY-MM`. The
  source is the URL, or the publication/title (or uploaded filename) when
  there is no link. Use the source's own date; when it shows none, use
  today's date and write `captured YYYY-MM` instead.
- Link related pages both ways; every page is linked from `Intel/Home.md`.
- Intel starts on the country page; when a city outgrows a few lines it
  becomes its own page with a one-line link left behind.
- New entity -> promote a new page (`mode: create`, the default). Page
  already exists -> promote with `mode: append` (your update is added under
  a divider) or `mode: patch` (body is the complete revised page; approval
  fails closed if the page changed since it was queued). Never draft a
  separate addendum file.
This vault is your only knowledge base — never build a wiki outside it.

## Typed relations

Notes can declare how they relate, in frontmatter — five keys holding
`[[wikilinks]]`: `up`/`down` (hierarchy), `same` (peers), `prev`/`next`
(sequence). Declare one direction only; the inverse is derived. They sharpen
retrieval and let you walk structure with `brain graph`. Add them only where
they carry signal structure doesn't already — folder-index parents, dated notes
in one folder, and same-`entity`-type pages are linked automatically, so don't
restate those. A target that doesn't resolve just yields no edge.

## Promotion protocol (moving knowledge to shared spaces)

Nothing in `People/{pid}/` is shared automatically. To share knowledge:
1. Draft a sanitized note (no private context beyond what is being shared).
2. Save it under `People/{pid}/Promotions/` with frontmatter:
   `target-path: <file in a shared space>`, `source: <originating note>`, and
   `mode: create|append|patch` (default `create`). The default `mode: create`
   requires a target that must not already exist. To update an existing
   shared page set `mode: append` (adds your note under a divider) or
   `mode: patch` (your body replaces the whole page — include ALL of it; the
   approver reviews a diff). Never target a running file like
   `Company/Memory.md` with `mode: create` — approval fails on any existing
   target.
3. {name} reviews and approves via `brain promotions approve`; only then does
   the note reach the shared space.
4. Track your proposals in `People/{pid}/Shares.md` (generated, read-only):
   pending, approved, and rejected — with the rejection reason. Answer
   "did my share go live?" from this note.

## Privacy rules

- Content in `People/{pid}/` is private to {pid}.
- Never copy content from a private space into a shared space directly; use a
  promotion.
- When drafting anything {entity}-facing, cite the source note.
"""


def render_root_protocol(
    person: Person,
    spaces_rw: list[tuple[str, bool]],
    config: VaultConfig = VaultConfig(),
) -> str:
    space_lines = "\n".join(
        f"- `{space}/` — {'writable' if writable else 'read-only'}"
        for space, writable in spaces_rw
    )
    text = _ROOT_TEMPLATE.format(
        name=person.name, pid=person.id, space_lines=space_lines,
        entities=config.entities, entity=config.entity,
        entity_title=config.entity[:1].upper() + config.entity[1:],
        requests=config.requests_folder, name_key=config.name_key,
    )
    if len(text) > ROOT_LIMIT:
        raise ValueError(f"root protocol exceeds {ROOT_LIMIT} chars")
    return text


def render_space_note(space: str, writable: bool, owner: bool) -> str:
    if owner:
        text = (
            f"# {space} — private space\n\n"
            "Everything here is private to the vault owner. Nothing leaves this\n"
            "space without an approved promotion. Keep Memory.md a lean overview\n"
            "that links out to Notes/ for anything topic-sized; archive processed\n"
            "Inbox items into Sessions/.\n"
        )
    else:
        mode = "writable" if writable else "read-only"
        text = (
            f"# {space}\n\n"
            f"This space is {mode} for the vault owner. Follow the routing and\n"
            "promotion rules in the vault root AGENTS.md. Cite sources for\n"
            "facts recorded here.\n"
        )
    if len(text) > SPACE_LIMIT:
        raise ValueError(f"space note for {space} exceeds {SPACE_LIMIT} chars")
    return text


def _writable(space: str, person: Person, rules: tuple[SpaceRule, ...]) -> bool:
    from brain.resolver import can_write_path

    return can_write_path(f"{space}/x.md", person, rules)


def generate_context_files(
    vault: Path,
    person: Person,
    spaces: list[str],
    rules: tuple[SpaceRule, ...],
    config: VaultConfig = VaultConfig(),
) -> list[str]:
    written: list[str] = []
    spaces_rw = [(s, _writable(s, person, rules)) for s in spaces]

    root_text = render_root_protocol(person, spaces_rw, config)
    for fname in ("AGENTS.md", "CLAUDE.md"):
        (vault / fname).write_text(root_text)
        written.append(fname)

    # Keep machine-local state out of the vault's git history: the search index
    # (.brain/) is rebuilt locally and per-device, and Obsidian's workspace
    # config (.obsidian/) is personal. Both would otherwise be committed by the
    # compiler's `git add -A`.
    (vault / ".gitignore").write_text(".brain/\n.obsidian/\n")
    written.append(".gitignore")

    for space, writable in spaces_rw:
        owner = space == f"People/{person.id}"
        if not owner and not space.startswith(f"{config.entities}/"):
            continue
        note = render_space_note(space, writable, owner)
        for fname in ("AGENTS.md", "CLAUDE.md"):
            rel = f"{space}/{fname}"
            target = vault / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(note)
            written.append(rel)
    return written
