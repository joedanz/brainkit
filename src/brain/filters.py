"""Structured, read-only listing of a vault's notes for the dashboard's query
tab — the "SQL-ish filters" that complement free-text search.

Where `search` ranks chunks by relevance, this answers set questions: which
notes are in a space, which have unresolved links, which are awaiting reindex,
which changed recently. It reads the index read-only (via `stats.ro_connect`)
and never opens it read-write, so it can run against a vault a cron `brain
index` is concurrently rebuilding. A missing index yields an empty list, not a
crash — mirroring how stats and search degrade.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from brain.resolver import space_of_path
from brain.stats import pending_reindex, ro_connect


@dataclass
class NoteRow:
    rel_path: str
    space: str
    chunks: int
    inbound: int  # links pointing at this note
    unresolved_out: int  # dangling links out of this note
    mtime: str  # filesystem mtime as YYYY-MM-DD, "" if the file is gone


@dataclass
class LinkRef:
    rel_path: str
    title: str


@dataclass
class NoteLinks:
    inbound: list[LinkRef]        # notes that link to this one
    outbound: list[LinkRef]       # resolved notes this one links to
    unresolved_out: list[str]     # dangling link targets out of this note


@dataclass
class InboxItem:
    rel_path: str
    title: str
    mtime: str


@dataclass
class ActionItem:
    rel_path: str
    title: str
    line: int
    text: str  # the checkbox line, marker stripped


_ROW_SQL = """
SELECT f.rel_path, f.space,
  (SELECT count(*) FROM chunks c WHERE c.rel_path = f.rel_path) AS chunks,
  (SELECT count(*) FROM links l WHERE l.target_rel_path = f.rel_path) AS inbound,
  (SELECT count(*) FROM links l
     WHERE l.src_rel_path = f.rel_path AND l.resolved = 0) AS unresolved_out
FROM files f
WHERE (:space IS NULL OR f.space = :space)
  AND (:contains IS NULL OR f.rel_path LIKE :like ESCAPE '\\')
ORDER BY f.rel_path
"""


def _mtime_date(vault: Path, rel_path: str) -> str:
    try:
        ts = (vault / rel_path).stat().st_mtime
    except OSError:
        return ""
    return datetime.fromtimestamp(ts, tz=UTC).date().isoformat()


def _like_pattern(contains: str) -> str:
    # Escape LIKE wildcards in the user's substring so '%' and '_' are literal.
    escaped = contains.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def list_notes(
    vault: Path,
    *,
    space: str | None = None,
    path_contains: str | None = None,
    unresolved_only: bool = False,
    pending_only: bool = False,
    modified_after: str | None = None,
    limit: int = 200,
) -> list[NoteRow]:
    vault = Path(vault)
    db = vault / ".brain" / "index.db"
    if not db.is_file():
        return []

    after: date | None = None
    if modified_after:
        try:
            after = date.fromisoformat(modified_after)
        except ValueError:
            after = None  # ignore an unparseable date rather than fail the query

    conn = ro_connect(db)
    try:
        params = {
            "space": space,
            "contains": path_contains,
            "like": _like_pattern(path_contains) if path_contains else None,
        }
        raw = conn.execute(_ROW_SQL, params).fetchall()
        pending = set(pending_reindex(vault, conn)) if pending_only else None
    except sqlite3.Error:
        return []
    finally:
        conn.close()

    rows: list[NoteRow] = []
    for rel_path, sp, chunks, inbound, unresolved_out in raw:
        if unresolved_only and not unresolved_out:
            continue
        if pending is not None and rel_path not in pending:
            continue
        mtime = _mtime_date(vault, rel_path)
        if after is not None and (not mtime or date.fromisoformat(mtime) < after):
            continue
        rows.append(NoteRow(
            rel_path=rel_path,
            space=sp or (space_of_path(rel_path) or ""),
            chunks=chunks,
            inbound=inbound,
            unresolved_out=unresolved_out,
            mtime=mtime,
        ))
        if len(rows) >= limit:
            break
    return rows


def note_links(vault: Path, rel_path: str) -> NoteLinks:
    """Resolved inbound/outbound links and dangling out-links for one note.

    Reading a note is exactly when its connections matter, so the note view
    surfaces the same adjacency the graph computes. Empty everywhere when there
    is no index. Titles are the file stem (matching `_build_graph`)."""
    empty = NoteLinks(inbound=[], outbound=[], unresolved_out=[])
    vault = Path(vault)
    db = vault / ".brain" / "index.db"
    if not db.is_file():
        return empty
    conn = ro_connect(db)
    try:
        inbound = [
            LinkRef(rel_path=src, title=Path(src).stem)
            for (src,) in conn.execute(
                "SELECT DISTINCT src_rel_path FROM links "
                "WHERE target_rel_path = ? AND src_rel_path != ? ORDER BY src_rel_path",
                (rel_path, rel_path))
        ]
        out_rows = conn.execute(
            "SELECT target_rel_path, resolved FROM links "
            "WHERE src_rel_path = ? AND target_rel_path != ? ORDER BY target_rel_path",
            (rel_path, rel_path)).fetchall()
    except sqlite3.Error:
        return empty
    finally:
        conn.close()

    outbound, unresolved = [], []
    seen_out, seen_unres = set(), set()
    for target, resolved in out_rows:
        if resolved:
            if target not in seen_out:
                seen_out.add(target)
                outbound.append(LinkRef(rel_path=target, title=Path(target).stem))
        elif target not in seen_unres:
            seen_unres.add(target)
            unresolved.append(target)
    return NoteLinks(inbound=inbound, outbound=outbound, unresolved_out=unresolved)


def list_inbox(vault: Path, person: str) -> list[InboxItem]:
    """Markdown notes in the person's Inbox, newest first — the triage worklist.
    Reads the filesystem (Inbox items may not be indexed yet), never escaping the
    person's own Inbox directory."""
    vault = Path(vault)
    if not person:
        return []
    inbox = vault / "People" / person / "Inbox"
    if not inbox.is_dir():
        return []
    items: list[tuple[float, InboxItem]] = []
    for f in inbox.rglob("*.md"):
        if f.is_symlink() or not f.is_file():
            continue
        try:
            ts = f.stat().st_mtime
        except OSError:
            continue
        rel = f.relative_to(vault).as_posix()
        items.append((ts, InboxItem(
            rel_path=rel, title=f.stem,
            mtime=datetime.fromtimestamp(ts, tz=UTC).date().isoformat())))
    items.sort(key=lambda t: t[0], reverse=True)
    return [it for _, it in items]


def list_actions(vault: Path, person: str, *, limit: int = 200) -> list[ActionItem]:
    """Open ``- [ ]`` checkbox lines across the person's Actions directory, so the
    dashboard can list what's outstanding, not just count it (mirrors
    `stats._count_open_actions`)."""
    vault = Path(vault)
    if not person:
        return []
    actions = vault / "People" / person / "Actions"
    if not actions.is_dir():
        return []
    out: list[ActionItem] = []
    for f in sorted(actions.rglob("*.md")):
        if f.is_symlink() or not f.is_file():
            continue
        rel = f.relative_to(vault).as_posix()
        text = f.read_text(encoding="utf-8", errors="replace")
        for i, ln in enumerate(text.splitlines(), start=1):
            stripped = ln.lstrip()
            if stripped.startswith("- [ ]"):
                out.append(ActionItem(rel_path=rel, title=f.stem, line=i,
                                      text=stripped[5:].strip()))
                if len(out) >= limit:
                    return out
    return out
