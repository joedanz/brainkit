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
