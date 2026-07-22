"""Personalized PageRank over the vault's file graph.

The graph is assembled at query time from tables the index already maintains
— resolved wikilinks (weight 1 per pair), fact co-mentions (weight 1 per
co-mentioning fact), and typed edges (their stored weight: explicit 2.0,
mined 0.5) — undirected, weights summed, so nothing here touches the index
path and nothing is persisted. Power iteration walks nodes in sorted
order with fixed damping/tolerance/cap: the same index yields bit-identical
scores on every machine. Where HippoRAG seeds PPR via LLM entity extraction,
brainkit's seeds (see search.py) come from deterministic string matching.

Degree-zero nodes are dangling: their outgoing mass teleports back to the
seed distribution each iteration, per standard PageRank — retention would
let an isolated seed hoard mass and outrank better-connected notes.
"""

from __future__ import annotations

import re

from brain.compiler import _stem
from brain.store import IndexStore

_ALPHA = 0.85
_TOL = 1e-8
_MAX_ITER = 100


def build_graph(store: IndexStore) -> dict[str, dict[str, float]]:
    """Weighted undirected adjacency over files: wikilink pairs and fact
    co-mention pairs at one unit of weight each, plus typed edges at their
    stored weight (explicit 2.0, mined 0.5), summed. A frontmatter relation
    is also an ordinary wikilink, so an explicit typed pair totals 3.0
    against 1.0 for a casual mention — intentional stacking.

    Self-loop-free by construction: link_pairs() excludes src == target in
    SQL, fact_copairs() joins on a strict < between targets, and edges.py
    never emits a self-edge. typed_edge_pairs() excludes inverse-provenance
    rows — mirrors of edges already counted, which would otherwise double
    every typed pair.
    """
    adj: dict[str, dict[str, float]] = {}

    def bump(a: str, b: str, w: float = 1.0) -> None:
        for x, y in ((a, b), (b, a)):
            row = adj.setdefault(x, {})
            row[y] = row.get(y, 0.0) + w

    for src, tgt in store.link_pairs():
        bump(src, tgt)
    for a, b in store.fact_copairs():
        bump(a, b)
    for src, tgt, w in store.typed_edge_pairs():
        bump(src, tgt, w)
    return adj


def ppr(
    adj: dict[str, dict[str, float]],
    seeds: dict[str, float],
    *,
    alpha: float = _ALPHA,
    tol: float = _TOL,
    max_iter: int = _MAX_ITER,
) -> list[tuple[str, float]]:
    """Ranked (rel_path, score) by personalized PageRank from `seeds`,
    nonzero scores only, ties broken by path for determinism."""
    if not seeds:
        return []
    total = sum(w for _, w in sorted(seeds.items()))
    s = {n: w / total for n, w in sorted(seeds.items())}
    nodes = sorted(set(adj) | set(s))
    p = {n: s.get(n, 0.0) for n in nodes}
    for _ in range(max_iter):
        dangling = sum(p[n] for n in nodes if not adj.get(n))
        base = 1.0 - alpha
        nxt = {n: (base + alpha * dangling) * s.get(n, 0.0) for n in nodes}
        for n in nodes:
            row = adj.get(n)
            if not row or p[n] == 0.0:
                continue
            share = alpha * p[n] / sum(row.values())
            for nb in sorted(row):
                nxt[nb] += share * row[nb]
        delta = sum(abs(nxt[n] - p[n]) for n in nodes)
        p = nxt
        if delta < tol:
            break
    ranked = [(n, sc) for n, sc in p.items() if sc > 0.0]
    ranked.sort(key=lambda kv: (-kv[1], kv[0]))
    return ranked


def extract_seeds(
    query: str,
    store: IndexStore,
    *,
    center: str | None = None,
    text_hit_files: tuple[str, ...] | list[str] = (),
) -> dict[str, float]:
    """Deterministic seed vector for PPR: entity names, aliases, and file
    stems matched in the query at weight 1.0 (word-boundary, longest match
    claims overlapping spans), `center` at 1.0, text-hit files at 0.5.

    Stems win over aliases on the same term, mirroring the indexer's link
    resolution; all candidate orders are sorted so ties are deterministic.
    """
    terms: dict[str, str] = {}
    for rel in sorted(store.files()):
        terms.setdefault(_stem(rel), rel)
    for alias, rel in sorted(store.alias_map().items()):
        terms.setdefault(alias, rel)

    q = query.lower()
    seeds: dict[str, float] = {}
    claimed: list[tuple[int, int]] = []
    for term in sorted(terms, key=lambda t: (-len(t), t)):
        if not term:
            continue
        for m in re.finditer(rf"(?<!\w){re.escape(term)}(?!\w)", q):
            span = (m.start(), m.end())
            if any(s0 < span[1] and span[0] < e0 for s0, e0 in claimed):
                continue
            claimed.append(span)
            seeds[terms[term]] = 1.0
    if center is not None:
        seeds[center] = 1.0
    for rel in text_hit_files:
        seeds.setdefault(rel, 0.5)
    return seeds
