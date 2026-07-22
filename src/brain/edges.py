"""Typed edges over the vault's note graph, inspired by Obsidian's Breadcrumbs.

Five fixed relations (`up`/`down`/`same`/`prev`/`next`) declared as flat
frontmatter lines whose values hold wikilinks — `up: [[Retrieval]]` — plus
edges mined from structure that already exists: folder index notes, date
sequences, shared entity types. Every stored edge gets a mirrored twin with
provenance "inverse" (up⇄down, prev⇄next, same⇄same), which is the *only*
inference rule: each edge is explainable in one hop from something an author
wrote or a convention mined.

The edge set lives in the index's `edges` table and is rebuilt wholesale on
every `brain index` run — cheap derived data, and wholesale rebuild sidesteps
cross-file incremental invalidation (a new folder-index note changes its
siblings' edges). All candidate sets iterate in sorted order, so the same
vault yields byte-identical rows on every machine, matching graphrank's
determinism invariant. Boundary safety is inherited, not enforced here: the
compiler stubs frontmatter wikilinks a reader may not see, so a forbidden
edge's target never resolves inside their vault.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Iterable

from brain.compiler import extract_wikilinks

RELATION_KEYS = ("down", "next", "prev", "same", "up")
INVERSE = {"up": "down", "down": "up", "same": "same", "prev": "next", "next": "prev"}
W_EXPLICIT = 2.0
W_MINED = 0.5

# One edge row, everywhere: (src_rel_path, dst_rel_path, rel, provenance, weight)
Edge = tuple[str, str, str, str, float]
Resolver = Callable[[list[str]], list[tuple[str, int]]]


def explicit_edges(rel_path: str, meta: dict[str, str], resolve: Resolver) -> list[Edge]:
    """Edges an author declared in flat frontmatter. Values are scanned for
    wikilinks; non-wikilink text is ignored, unresolved targets and
    self-references produce no edge."""
    edges: list[Edge] = []
    for key in RELATION_KEYS:
        value = meta.get(key)
        if not value:
            continue
        for target, ok in resolve(extract_wikilinks(value)):
            if ok and target != rel_path:
                edges.append((rel_path, target, key, "explicit", W_EXPLICIT))
    return edges


_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def note_date(rel_path: str, meta: dict[str, str]) -> str | None:
    """YYYY-MM-DD used for sequencing: a filename date prefix wins, then a
    `date:` or `captured:` frontmatter value that starts with a date."""
    m = _DATE_RE.match(Path(rel_path).name)
    if m:
        return m.group(1)
    for key in ("date", "captured"):
        m = _DATE_RE.match(meta.get(key, "").strip())
        if m:
            return m.group(1)
    return None


def folder_edges(files: Iterable[str]) -> list[Edge]:
    """`up` from each note to its folder's index note — the note whose stem
    equals the folder name (`Clients/Acme/Acme.md`). Immediate folder only."""
    fileset = set(files)
    edges: list[Edge] = []
    for rel in sorted(fileset):
        folder = Path(rel).parent
        if folder == Path("."):
            continue
        index_note = (folder / f"{folder.name}.md").as_posix()
        if index_note != rel and index_note in fileset:
            edges.append((rel, index_note, "up", "folder", W_MINED))
    return edges


def date_edges(dated: dict[str, str]) -> list[Edge]:
    """`next` chains between dated notes of the same folder, ordered by
    (date, filename). Sequences never cross folder boundaries."""
    by_folder: dict[str, list[tuple[str, str]]] = {}
    for rel, day in dated.items():
        by_folder.setdefault(Path(rel).parent.as_posix(), []).append((day, rel))
    edges: list[Edge] = []
    for folder in sorted(by_folder):
        chain = sorted(by_folder[folder])
        for (_, a), (_, b) in zip(chain, chain[1:]):
            edges.append((a, b, "next", "date", W_MINED))
    return edges


def with_inverses(edges: list[Edge]) -> list[Edge]:
    """Every edge plus its mirror (INVERSE[rel], provenance "inverse", the
    twin's weight). Mirrors are always inverse-provenance regardless of what
    produced the original — that is what lets retrieval skip them wholesale
    without double-counting. Mirrors colliding on (src, dst, rel) keep the
    highest weight."""
    mirrors: dict[tuple[str, str, str], float] = {}
    for src, dst, rel, _prov, weight in edges:
        key = (dst, src, INVERSE[rel])
        mirrors[key] = max(mirrors.get(key, 0.0), weight)
    return list(edges) + [
        (src, dst, rel, "inverse", weight)
        for (src, dst, rel), weight in sorted(mirrors.items())
    ]


def rebuild_edges(vault: Path, store, resolve: Resolver) -> int:
    """Wholesale-rebuild the index's edges table from its files: explicit
    frontmatter relations, mined structure, and all mirrors. Returns the
    number of rows written."""
    from brain.frontmatter import split_frontmatter

    files = sorted(store.files())
    explicit: list[Edge] = []
    dated: dict[str, str] = {}
    for rel in files:
        try:
            text = (Path(vault) / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        meta, _body = split_frontmatter(text)
        explicit += explicit_edges(rel, meta, resolve)
        day = note_date(rel, meta)
        if day:
            dated[rel] = day
    mined = (folder_edges(files) + date_edges(dated)
             + entity_edges([(rel, etype) for rel, etype, _a in store.entities()]))
    rows = with_inverses(explicit + mined)
    store.replace_edges(rows)
    return len(rows)


def entity_edges(entities: Iterable[tuple[str, str]]) -> list[Edge]:
    """`same` between pages sharing an entity type, canonical direction
    a < b. Groups are expected small (a handful of pages per type)."""
    groups: dict[str, list[str]] = {}
    for rel, etype in entities:
        if etype:
            groups.setdefault(etype, []).append(rel)
    edges: list[Edge] = []
    for etype in sorted(groups):
        members = sorted(groups[etype])
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                edges.append((a, b, "same", "entity", W_MINED))
    return edges
