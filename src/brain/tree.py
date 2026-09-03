"""Folder tree of a compiled vault's indexed pages, for the dashboard's Pages
tab (``GET /api/tree``).

Built from the sqlite index's ``files`` table and nothing else. The index
holds exactly the notes the compiler gave this lens, so the tree is by
construction the readable set: a note outside the person's spaces is not in
the index and cannot appear here, and the protocol files on disk
(``AGENTS.md``, ``Map.md``) that ``read_note`` refuses are never listed
because the indexer never stored them. Read-only via ``ro_connect``; a missing
index yields an empty root, mirroring how stats and search degrade.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from brain.filters import _mtime_date
from brain.stats import ro_connect


@dataclass
class TreePage:
    rel_path: str
    title: str
    mtime: str  # YYYY-MM-DD, "" if the file is gone


@dataclass
class TreeDir:
    name: str
    path: str  # "" for the root, "Company/Decisions" below it (no trailing slash)
    dirs: list[TreeDir] = field(default_factory=list)
    pages: list[TreePage] = field(default_factory=list)
    count: int = 0  # pages in the whole subtree


def _title(rel_path: str) -> str:
    # Strip only a trailing ".md": a period inside a title is part of the
    # title (``Amendment No. 3``), the same rule the compiler keys links by.
    name = rel_path.rsplit("/", 1)[-1]
    return name[:-3] if name.endswith(".md") else name


def _hidden(rel_path: str) -> bool:
    return any(part.startswith(".") or part == "_meta" for part in rel_path.split("/"))


def build_tree(vault: Path) -> TreeDir:
    vault = Path(vault)
    root = TreeDir(name="", path="")
    db = vault / ".brain" / "index.db"
    if not db.is_file():
        return root
    conn = ro_connect(db)
    try:
        rows = conn.execute("SELECT rel_path FROM files").fetchall()
    except sqlite3.Error:
        return root
    finally:
        conn.close()

    dirs: dict[str, TreeDir] = {"": root}

    def dir_for(path: str) -> TreeDir:
        found = dirs.get(path)
        if found is not None:
            return found
        parent, _, name = path.rpartition("/")
        made = TreeDir(name=name, path=path)
        dir_for(parent).dirs.append(made)
        dirs[path] = made
        return made

    for (rel,) in rows:
        if _hidden(rel):
            continue
        parent = rel.rpartition("/")[0]
        dir_for(parent).pages.append(
            TreePage(rel_path=rel, title=_title(rel), mtime=_mtime_date(vault, rel)))

    _finish(root)
    return root


def _finish(d: TreeDir) -> int:
    """Sort each group with casefold (the JS side uses localeCompare — the
    same answer for the ASCII-and-accents vault names in practice) and roll
    the page counts up. Returns the subtree count."""
    d.dirs.sort(key=lambda x: x.name.casefold())
    d.pages.sort(key=lambda p: p.title.casefold())
    d.count = len(d.pages) + sum(_finish(c) for c in d.dirs)
    return d.count
