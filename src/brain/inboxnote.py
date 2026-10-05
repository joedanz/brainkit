"""Write or remove one generated note in a person's Inbox, safely.

Triage's doctor digest and the weekly digest are both generated notes the brain
keeps current in `People/<id>/Inbox/`. Both go through here so the refusals are
the same: a symlinked folder or note is never touched, the person's own write
grant is re-checked, the path must stay inside their space, an unchanged note
(same fingerprint) is left alone, and an empty set deletes the note.
"""

from __future__ import annotations

from pathlib import Path

from brain.frontmatter import split_frontmatter
from brain.resolver import can_write_path, space_of_path
from brain.schemas import Person, SpaceRule


def sync_inbox_note(master: Path, person: Person, rules: tuple[SpaceRule, ...],
                    shared: str, name: str, *, content: str | None,
                    fingerprint: str | None, warnings: list[str]) -> str:
    """Returns "written", "removed", "unchanged" or "skipped" (a warning says why)."""
    rel = f"People/{person.id}/Inbox/{name}"
    ancestor = master
    for part in Path(rel).parent.parts:
        ancestor = ancestor / part
        if ancestor.is_symlink():
            warnings.append(f"{rel}: ancestor is a symlink — refusing to write")
            return "skipped"
    target = master / rel
    if space_of_path(rel, shared) != f"People/{person.id}":
        warnings.append(f"{rel}: resolves outside People/{person.id} — skipped")
        return "skipped"
    if not can_write_path(rel, person, rules, shared=shared):
        warnings.append(f"{person.id} has no write grant on their own space — skipped")
        return "skipped"
    if content is None:
        if target.is_symlink():
            warnings.append(f"{rel}: digest is a symlink — refusing to remove")
            return "skipped"
        if target.is_file():
            try:
                target.unlink()
            except OSError as e:
                warnings.append(f"{rel}: {e}")
                return "skipped"
            return "removed"
        return "unchanged"
    if target.is_file() and not target.is_symlink():
        try:
            meta, _body = split_frontmatter(target.read_text())
        except (KeyError, ValueError, UnicodeDecodeError):
            meta = {}  # malformed: fall through and rewrite it
        except OSError as e:
            warnings.append(f"{rel}: {e}")
            return "skipped"
        if meta and meta.get("fingerprint") == fingerprint:
            return "unchanged"
    if target.is_symlink():
        warnings.append(f"{rel}: digest is a symlink — refusing to write")
        return "skipped"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    except OSError as e:
        warnings.append(f"{rel}: {e}")
        return "skipped"
    return "written"
