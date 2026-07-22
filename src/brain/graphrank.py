"""Personalized PageRank over the vault's file graph.

The graph is assembled at query time from tables the index already maintains
— resolved wikilinks (weight 1 per pair) and fact co-mentions (weight 1 per
co-mentioning fact), undirected, weights summed — so nothing here touches the
index path and nothing is persisted. Power iteration walks nodes in sorted
order with fixed damping/tolerance/cap: the same index yields bit-identical
scores on every machine. Where HippoRAG seeds PPR via LLM entity extraction,
brainkit's seeds (see search.py) come from deterministic string matching.

Degree-zero nodes are dangling: their outgoing mass teleports back to the
seed distribution each iteration, per standard PageRank — retention would
let an isolated seed hoard mass and outrank better-connected notes.
"""

from __future__ import annotations

from brain.store import IndexStore

_ALPHA = 0.85
_TOL = 1e-8
_MAX_ITER = 100


def build_graph(store: IndexStore) -> dict[str, dict[str, float]]:
    """Weighted undirected adjacency over files: wikilink pairs plus fact
    co-mention pairs, one unit of weight each, summed."""
    adj: dict[str, dict[str, float]] = {}

    def bump(a: str, b: str) -> None:
        if a == b:
            return
        for x, y in ((a, b), (b, a)):
            row = adj.setdefault(x, {})
            row[y] = row.get(y, 0.0) + 1.0

    for src, tgt in store.link_pairs():
        bump(src, tgt)
    for a, b in store.fact_copairs():
        bump(a, b)
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
