"""Generate Map.md: what is in this vault right now.

The companion to contextgen's AGENTS.md. That file is instructions — stable
protocol, fixed size, raises when it exceeds its budget. This one is state —
regenerated every compile, data-driven, and it must NEVER raise: failing here
would fail the compile for the person with the largest vault, precisely the
person the map helps most.

Deliberately NOT an index. `brain_search` resolves aliases and has no size
cap; enumerating entity names here overruns the budget at ~127-322 entities
and answers the lookup worse. This file ships counts and shape so a cold
agent knows which searches are worth making.

Everything is computed from the compiler's building tree, which contains only
the spaces this person may read — so the map structurally cannot name a note
the person cannot see. No subprocess, no master handle, no filtering.
"""

from __future__ import annotations

from dataclasses import dataclass

from brain.compiler import extract_wikilinks
from brain.frontmatter import split_frontmatter


@dataclass(frozen=True)
class NoteFacts:
    """One note's contribution to the map, read from its SHIPPED text."""

    entity: str  # "" when the note is not an entity page
    targets: tuple[str, ...]  # raw wikilink targets, in document order


def scan_note(text: str) -> NoteFacts:
    from brain.facts import parse_entity

    meta, _body = split_frontmatter(text)
    parsed = parse_entity(meta)
    # Scan the whole file, not just the body: typed relations (up/down/same/
    # prev/next) are wikilinks in frontmatter, and indexer._resolve_links
    # counts them. Scanning the body only would undercount hub degree.
    return NoteFacts(
        entity=parsed[0] if parsed else "",
        targets=tuple(extract_wikilinks(text)),
    )


def link_degree(notes: dict[str, NoteFacts]) -> dict[str, int]:
    """In+out resolved wikilink degree per note.

    Resolution mirrors indexer._resolve_links: lowercased stem, and on a
    duplicate stem the lexicographically first path wins (sorted iteration +
    setdefault). Unresolvable targets and self-links contribute nothing —
    the latter matches stats._build_graph's `src != target` filter.
    """
    from brain.compiler import _stem

    by_stem: dict[str, str] = {}
    for rel in sorted(notes):
        by_stem.setdefault(_stem(rel), rel)

    degree: dict[str, int] = {rel: 0 for rel in notes}
    for src, facts in notes.items():
        for raw in facts.targets:
            target = by_stem.get(_stem(raw))
            if target is None or target == src:
                continue
            degree[src] += 1
            degree[target] += 1
    return degree


def rank_hubs(degree: dict[str, int], cap: int) -> list[tuple[str, int]]:
    """Most-connected notes first. Zero-degree notes are not hubs."""
    ranked = sorted(degree.items(), key=lambda kv: (-kv[1], kv[0]))
    return [(rel, n) for rel, n in ranked if n > 0][:cap]
