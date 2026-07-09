"""Server-side intake: write one provenance-stamped note into a person's Inbox.

This is the only code path that creates a new note in a person's private space
in master from outside their synced vault. Channels (email, chat, voice, upload)
are thin wrappers that shell out to `brain ingest`; the security work lives here,
not in any channel:

- The target path is CONSTRUCTED (``People/<pid>/Inbox/<slug>.md``), never taken
  from caller input, so path traversal is structurally impossible.
- Every write is still re-checked with ``can_write_path`` (fail closed if
  spaces.yaml doesn't grant the person their own space).
- Provenance metadata is sanitized so untrusted channel content can't inject
  frontmatter keys or close the block early.
- Symlinked Inbox components are refused (same invariant as the compiler and
  write-back).
- Each intake is its own git commit under a distinct identity, so ``git log``
  tells authenticated write-backs apart from channel intake.

Routing the note out of the Inbox stays the chief-of-staff agent's job; ingest
only lands the raw note where that agent will find it.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from brain.resolver import can_write_path, space_of_path
from brain.schemas import Person, SpaceRule

_MAX_SLUG = 60


class IngestError(ValueError):
    """Invalid metadata, an unwritable target, or a failed commit.

    Callers surface this as a handled error (a message on stderr and a non-zero
    exit), never a raw traceback.
    """


@dataclass
class IngestResult:
    person_id: str
    rel_path: str  # e.g. "People/bob/Inbox/2026-07-08-standup.md"
    title: str
    source: str
    committed: bool  # False only when the write netted no change to commit


def _slug(text: str) -> str:
    # Same shape as promotions._slug: output is [a-z0-9-] only, so it can never
    # contribute a path separator or a "..".
    return "-".join("".join(c if c.isalnum() else " " for c in text.lower()).split())


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )


def _clean(value: str, field: str) -> str:
    """Reject values that could break out of a flat `key: value` frontmatter line.

    split_frontmatter parses line-by-line, so a newline (or carriage return) in a
    value would inject or corrupt keys. Fail closed rather than silently stripping.
    """
    if "\n" in value or "\r" in value:
        raise IngestError(f"{field} must be a single line")
    return value


@dataclass
class BuiltNote:
    rel_path: str  # People/<pid>/Inbox/<created>-<slug>.md
    text: str      # sanitized frontmatter + body, ready to write
    title: str     # sanitized, defaulted
    source: str    # sanitized


def build_inbox_note(
    root: Path,
    person_id: str,
    body: str,
    *,
    title: str,
    source: str,
    sender: str,
    created: str,
    original_name: str = "",
) -> BuiltNote:
    """Construct a provenance-stamped Inbox note under ``root`` without writing it.

    ``root`` is the tree the note lands in — the master vault (server-side ingest,
    committed immediately) or a compiled slice (dashboard capture, carried to
    master by write-back on the next cycle). The path is CONSTRUCTED from
    ``person_id`` (never caller input), a collision-free name is chosen against
    ``root``'s Inbox, symlinked ancestors are refused, and provenance is
    sanitized so channel content can't inject frontmatter. Returns the relative
    path and text; the caller decides how to persist it.
    """
    title = _clean(title, "title").strip()
    source = _clean(source, "source").strip()
    sender = _clean(sender, "from").strip()
    original_name = _clean(original_name, "original-name").strip()
    if not body.strip():
        raise IngestError("empty note — nothing to ingest")
    if not title:
        title = "note"

    slug = _slug(title)[:_MAX_SLUG].strip("-") or "note"

    inbox_rel = f"People/{person_id}/Inbox"
    # Refuse if any ancestor of the Inbox is a symlink — a planted link would let
    # bytes land outside the person's space. Checked before we construct a name.
    ancestor = root
    for part in Path(inbox_rel).parts:
        ancestor = ancestor / part
        if ancestor.is_symlink():
            raise IngestError(f"{inbox_rel} contains a symlink — refusing to write")

    inbox = root / inbox_rel
    fname = f"{created}-{slug}.md"
    n = 2
    while (inbox / fname).exists() or (inbox / fname).is_symlink():
        fname = f"{created}-{slug}-{n}.md"
        n += 1

    rel_path = f"{inbox_rel}/{fname}"
    # Belt and braces: the path is constructed, but confirm it resolves to the
    # person's own space before anyone touches disk.
    if space_of_path(rel_path) != f"People/{person_id}":
        raise IngestError(f"refusing to write outside {inbox_rel}")

    front = [
        "---",
        f"title: {title}",
        f"source: {source}",
        f"from: {sender}",
        f"created: {created}",
    ]
    if original_name:
        front.append(f"original-name: {original_name}")
    front.append("---\n")
    return BuiltNote(rel_path=rel_path, text="\n".join(front) + body,
                     title=title, source=source)


def ingest_note(
    master: Path,
    person: Person,
    rules: tuple[SpaceRule, ...],
    body: str,
    *,
    title: str,
    source: str,
    sender: str,
    created: str,
    original_name: str = "",
) -> IngestResult:
    """Write ``body`` as a note in ``person``'s Inbox in ``master`` and commit it.

    ``created`` is an ISO date supplied by the caller (the CLI edge defaults it to
    today) — never read from the clock here, matching the rest of the codebase.
    """
    built = build_inbox_note(master, person.id, body, title=title, source=source,
                             sender=sender, created=created, original_name=original_name)
    rel_path, title, source = built.rel_path, built.title, built.source
    if not can_write_path(rel_path, person, rules):
        raise IngestError(f"{person.id} has no write access to People/{person.id}/Inbox")

    fname = Path(rel_path).name
    target = master / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(built.text)

    committed = False
    try:
        _git(master, "add", "--", rel_path)  # only this file — never a bare -A
        if _git(master, "status", "--porcelain", "--", rel_path).stdout.strip():
            _git(
                master,
                "-c", "user.name=Brain Ingest",
                "-c", "user.email=ingest@brain.local",
                "commit", "-m", f"ingest: {person.id}/{fname} (source={source})",
            )
            committed = True
    except subprocess.CalledProcessError as e:
        # The note is on disk; re-running is safe (collision suffix gives a new
        # name) or an operator can commit by hand. Don't leak a raw traceback.
        raise IngestError(f"git commit failed: {e.stderr.strip()}") from e

    return IngestResult(
        person_id=person.id, rel_path=rel_path, title=title,
        source=source, committed=committed,
    )
