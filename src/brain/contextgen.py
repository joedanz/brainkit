"""Generate AGENTS.md / CLAUDE.md so every compiled vault is self-describing.

Limits follow Hermes Agent context-file loading: ~20K chars for the root file,
~8K for progressively-discovered per-directory files. Copy is declarative so it
passes Hermes's prompt-injection scan.
"""

from __future__ import annotations

from pathlib import Path

from brain.schemas import Person, SpaceRule

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
- Personal durable facts, preferences, lessons -> `People/{pid}/Memory.md`
- Client facts you may share -> draft a promotion targeting the client's space
- Decisions of company-wide relevance (a choice made, with its why) -> draft a
  promotion targeting a new file in `Company/Decisions/`
- Standing processes, standards, or how-we-work facts -> draft a promotion
  targeting a new file in `Company/Playbook/`
- If unsure where something belongs, add it to `People/{pid}/Needs-Routing.md`

## Promotion protocol (moving knowledge to shared spaces)

Nothing in `People/{pid}/` is shared automatically. To share knowledge:
1. Draft a sanitized note (no private context beyond what is being shared).
2. Save it under `People/{pid}/Promotions/` with frontmatter:
   `target-path: <new file in a shared space>` and `source: <originating note>`.
   The target must not already exist: promotions add notes, they never rewrite
   one. Never target a running file like `Company/Memory.md` — approval fails
   on any existing target.
3. {name} reviews and approves via `brain promotions approve`; only then does
   the note reach the shared space.
4. Track your proposals in `People/{pid}/Shares.md` (generated, read-only):
   pending, approved, and rejected — with the rejection reason. Answer
   "did my share go live?" from this note.

## Privacy rules

- Content in `People/{pid}/` is private to {pid}.
- Never copy content from a private space into a shared space directly; use a
  promotion.
- When drafting anything client-facing, cite the source note.
"""


def render_root_protocol(person: Person, spaces_rw: list[tuple[str, bool]]) -> str:
    space_lines = "\n".join(
        f"- `{space}/` — {'writable' if writable else 'read-only'}"
        for space, writable in spaces_rw
    )
    text = _ROOT_TEMPLATE.format(name=person.name, pid=person.id, space_lines=space_lines)
    if len(text) > ROOT_LIMIT:
        raise ValueError(f"root protocol exceeds {ROOT_LIMIT} chars")
    return text


def render_space_note(space: str, writable: bool, owner: bool) -> str:
    if owner:
        text = (
            f"# {space} — private space\n\n"
            "Everything here is private to the vault owner. Nothing leaves this\n"
            "space without an approved promotion. Keep Memory.md curated;\n"
            "archive processed Inbox items into Sessions/.\n"
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
    vault: Path, person: Person, spaces: list[str], rules: tuple[SpaceRule, ...]
) -> list[str]:
    written: list[str] = []
    spaces_rw = [(s, _writable(s, person, rules)) for s in spaces]

    root_text = render_root_protocol(person, spaces_rw)
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
        if not owner and not space.startswith("Clients/"):
            continue
        note = render_space_note(space, writable, owner)
        for fname in ("AGENTS.md", "CLAUDE.md"):
            rel = f"{space}/{fname}"
            target = vault / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(note)
            written.append(rel)
    return written
