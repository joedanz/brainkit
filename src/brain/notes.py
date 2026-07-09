"""Safe, read-only access to a single markdown file inside a vault.

One function, one boundary. A vault (compiled per-person or master) contains
only what its reader may see, but any code that turns an outside-supplied path
into a file read needs defense-in-depth so a crafted ``rel_path`` cannot escape
that vault. `read_note` is that single gate: the MCP server, the dashboard's
``/api/note`` endpoint, and anything else read through it instead of
reimplementing the checks. It refuses paths outside a readable space, paths that
resolve outside the vault, symlinks, and non-files — raising `NoteAccessError`
rather than returning partial results, so callers map one exception to one
"refused" response.
"""

from __future__ import annotations

from pathlib import Path

from brain.resolver import space_of_path


class NoteAccessError(Exception):
    """A path is not a readable file inside this vault (refused, not missing)."""


def read_note(vault: Path, rel_path: str) -> str:
    """Read one markdown file by its vault-relative path.

    Raises `NoteAccessError` if the path is outside any readable space, escapes
    the vault (via ``..`` or a symlink), is a symlink, or is not a regular file.
    """
    vault = Path(vault)
    if space_of_path(rel_path) is None:
        raise NoteAccessError(f"{rel_path!r} is not inside any readable space")
    target = vault / rel_path
    try:
        inside = target.resolve().is_relative_to(vault.resolve())
    except OSError:
        inside = False
    if not inside or target.is_symlink() or not target.is_file():
        raise NoteAccessError(f"{rel_path!r} is not a readable file in this vault")
    return target.read_text(encoding="utf-8", errors="replace")
