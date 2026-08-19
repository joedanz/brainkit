"""Generate AGENTS.md / CLAUDE.md so every compiled vault is self-describing.

Limits follow Hermes Agent context-file loading: ~20K chars for the root file,
~8K for progressively-discovered per-directory files. Copy is declarative so it
passes Hermes's prompt-injection scan.
"""

from __future__ import annotations

from pathlib import Path

from brain.schemas import DEFAULT_SHARED, Person, SpaceRule, VaultConfig

ROOT_LIMIT = 20_000
SPACE_LIMIT = 8_000

_ROOT_TEMPLATE = """\
# Brain Protocol — vault of {name} ({pid})

This vault is {name}'s slice of the company brain. It is compiled: it contains
only the spaces {name} may read. Anything not present here is not accessible.

## Start here

`Map.md` in this folder is generated state: spaces with note counts, entity
types with their most-linked pages, the most-connected notes, and what is
pending. Read it first to learn what kinds of things this vault holds. It is
an orientation summary, not an index — to find a specific note or entity, use
`brain_search`, which resolves aliases. It is rewritten on every compile;
edits to it are discarded.

## Spaces in this vault

{space_lines}

Read-only spaces are maintained by the company assistant. Edits belong in
writable spaces; the write-back service rejects changes to read-only paths.

{charter_block}## What belongs in this vault

Routing decides where a fact goes; this decides whether it goes anywhere.
Apply it first, to every candidate — record it only if all four hold:

- **Durable** — it outlasts this conversation. Passing status ("running
  late", "almost done", how someone feels today) is answered here and never
  written down. Something with an end date still counts: a decision, a
  deadline, a commitment is worth keeping until it is met.
- **Relevant** — it changes how we work: with a {entity}, with a colleague, or
  as a company. The spaces listed above are what this vault is about. A fact
  about a person that changes nothing about working with them is not a vault
  fact, however true it is.
- **New** — search with `brain_search` before writing. Something already here
  gets its page updated; it never becomes a second note saying the same thing.
- **Attributable** — you can say where it came from: who told you, or a source
  you actually read. Hearsay you cannot attribute stays out. Recording it as a
  claim ("X believes Y") is for a position someone took on the work, not a way
  to keep a rumour about a person.

Not recording is the ordinary outcome, not a failure. Most of what is said in
a day is conversation, not knowledge. When something fails these tests, use it
to answer well and write nothing — a vault that stays small stays searchable.
If you are unsure whether something passes, it does not.

{corrections_block}## Routing rules (where a fact that passed goes)

- Action items (owner + deadline) -> `People/{pid}/Actions/Tracker.md`
- Session/meeting summaries -> `People/{pid}/Sessions/`. A summary carries
  what was decided and what passed the tests above, not a retelling of the
  conversation
- Raw transcripts land in `People/{pid}/Inbox/` and are processed, then
  archived to `People/{pid}/Sessions/`. The archive records what was said; it
  is not somewhere to file things to be found later. If it is worth finding,
  it has a home above
- `People/{pid}/Inbox/doctor-digest.md` (`source: doctor`) is a generated
  integrity report, not a capture: fix what you can in writable spaces,
  submit shared-page fixes as `mode: patch` promotions, and record a one-line
  reason in `People/{pid}/Needs-Routing.md` for items only a human can
  decide. Never edit or archive the digest — it maintains itself.
- Durable facts, working preferences, and lessons that passed the tests above
  -> `People/{pid}/Memory.md`.
  Keep it a lean overview, not a running log: small facts live under its
  headings; when a topic outgrows a few lines, move the detail to
  `People/{pid}/Notes/<Topic>.md` and leave a one-line link under the heading
- When your human tells you an answer was wrong, write the correction to
  `People/{pid}/Corrections/<short-slug>.md` with frontmatter `rule:` (ONE
  imperative sentence — it is rendered into this protocol verbatim on the next
  compile) and `from:` (today, YYYY-MM-DD). Put what went wrong in the body;
  the body is never rendered, it is there for whoever reads the rule months
  later. Record only a correction your human actually made — never infer one.
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
  household. A name that merely came up is not yet a {entity}: create the space
  when there is something durable to keep in it. One utterance can split into
  two homes: e.g. a family attending an event becomes a {entity} note AND a
  `{shared}/Intel/Events/` promotion, cross-linked.
- {entity_title} facts about a {entity} you already own -> write them into that
  `{entities}/<name>/` space directly. The same tests apply there: a {entity}
  page holds what we need in order to work with them, not everything true
  about them
- To give a colleague or team access to a space you own (e.g. a {entity} you
  created): write `People/{pid}/ShareRequests/<name>.md` with frontmatter
  `space: <the space>`, `share-with: person:<id>` or `team:<name>`,
  `access: read|write`, `action: share` — the body is an optional note to the
  approver. The share's decider approves it — the recipient for `person:`, a
  team lead for `team:`, an admin for `everyone`; status shows in
  `People/{pid}/Shares.md`,
  and your own access never blocks — keep writing while it's pending. To
  remove someone, use `action: revoke` (applies automatically; you cannot
  revoke your own access).
- If your `People/{pid}/Shares.md` has an **Awaiting your decision** section,
  those shares name you (or a team you lead) as recipient. Decide by writing
  `People/{pid}/Approvals/<share-id>.md` with `decision: approve` or
  `decision: reject` (rejections need a `reason:`), `owner: {pid}`. Record
  only a decision your human has explicitly made — never decide on your own.
  Company-wide shares (to `everyone`) always need an admin, not you.
- If your `People/{pid}/Shares.md` has a **Promotions awaiting your decision**
  section, those promotions target a team you lead. The section shows what
  would be published (the body, or a diff for `patch`) — read it. Decide by
  writing `People/{pid}/PromotionApprovals/<promo-id>.md` with
  `decision: approve` or `decision: reject` (rejections need a `reason:`),
  `owner: {pid}`. Record only a decision your human has explicitly made —
  never decide on your own. Promotions into `{shared}/` always need an admin
  at the dashboard, not you.
- Decisions of company-wide relevance (a choice made, with its why) -> draft a
  promotion targeting a new file in `{shared}/Decisions/`
- Standing processes, standards, or how-we-work facts -> draft a promotion
  targeting a new file in `{shared}/Playbook/`
- Articles, posts, links, PDFs, and screenshots: distill, never archive —
  read the source (fetch a URL, extract PDF text, read an image) and route
  destination, provider, event, or trend intel to `{shared}/Intel/` via a
  promotion (see below). The full text or file never enters the vault; your
  personal take, when it is worth keeping, stays in `People/{pid}/Notes/`. Because the original is gone,
  the citation is the only way back to it: on any distilled page **outside**
  `{shared}/Intel/`, add frontmatter `distilled: <URL or title>` and cite the
  claims `[source](URL), as of YYYY-MM` — inside Intel the folder already
  says it, so no marker is needed
- If something failed the tests above, it is not routed anywhere — writing it
  to `Needs-Routing.md` instead of dropping it is the mistake that fills a
  vault with what nobody chose to keep
- If it passed but you cannot place it confidently, add one line to
  `People/{pid}/Needs-Routing.md` saying what it is and why it did not fit.
  Nothing drains that note but you: when you next work it, file each line and
  delete it, so the note tends toward empty

## {shared} Intel (the shared reference wiki)

`{shared}/Intel/` holds shared reference knowledge, mapped in `Intel/Home.md`:
`Destinations/<Place>.md`, `Providers/<Name>.md`, `Events/<Name>.md`,
`Trends/<YYYY-MM Topic>.md`. Only what the company needs in order to work
belongs here. Conventions:
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

## Fact lines and entity pages

A durable state claim ("X is Y", "X works at Y") is written as a bullet
carrying its dates and its provenance: the claim itself, then
`[from:: YYYY-MM]` to open it and `[source:: [[the note]]]` to cite the
episode that established it. Both are required, and a fact line without a
source is reported back to you. (Written out here in prose, not shown as a
bullet, because a literal example would parse as a real fact about a
company you have never heard of — the same reason applies to your notes.)
When a newer claim replaces an older one, add `[until:: 2026-06]` to the old
line and write the new line; never delete the old one, because knowing when
something stopped being true is the point. Prose keeps the
`[source](URL), as of YYYY-MM` style instead — fact lines are for queryable
state, citations are for evidence in text.

A page about a single thing declares what it is, and every other name it goes
by, so `brain_search` resolves them to this page:

    ---
    entity: {entity}
    aliases: [Other Name, ABBR]
    ---

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
   `{shared}/Memory.md` with `mode: create` — approval fails on any existing
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


def render_charter(config: VaultConfig) -> str:
    """The "what this brain is for" block, or "" when no charter is set.

    Empty means empty, as with corrections: a heading over an invented purpose
    would be worse than none, because the relevance test below it would then
    be measured against something nobody in the company actually said.
    """
    if not config.charter:
        return ""
    return (f"## What this brain is for\n\n{config.charter}\n\n"
            "That is the subject this vault collects. A fact bearing on none\n"
            "of it does not belong here, however interesting it is.\n\n")


def render_root_protocol(
    person: Person,
    spaces_rw: list[tuple[str, bool]],
    config: VaultConfig = VaultConfig(),
    corrections_block: str = "",
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
        shared=config.shared, charter_block=render_charter(config),
        corrections_block=(corrections_block + "\n") if corrections_block else "",
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


def writable_spaces(
    spaces: list[str], person: Person, rules: tuple[SpaceRule, ...],
    shared: str = DEFAULT_SHARED,
) -> list[tuple[str, bool]]:
    """Pair each space with whether this person may write it. The caller owns
    the result: the compiler needs the same list for the vault map, and
    deriving it twice would mean two sources of truth for one fact."""
    from brain.resolver import can_write_path

    return [(s, can_write_path(f"{s}/x.md", person, rules, shared=shared))
            for s in spaces]


def generate_context_files(
    vault: Path,
    person: Person,
    spaces_rw: list[tuple[str, bool]],
    config: VaultConfig = VaultConfig(),
) -> list[str]:
    written: list[str] = []

    from brain.corrections import load_corrections, render_corrections

    # The compiler has already copied this person's spaces into `vault`
    # (compiler.py) before calling us, so their Corrections/ are on disk here.
    block = render_corrections(load_corrections(vault, person.id))
    root_text = render_root_protocol(person, spaces_rw, config, corrections_block=block)
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
