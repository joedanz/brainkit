"""Hybrid retrieval: fuse FTS5 keyword ranking with vector KNN via RRF.

Reciprocal-rank fusion needs no score calibration between the two legs — it
combines their *rankings*, so a chunk that both legs surface outranks one only a
single leg found. When vectors are unavailable (no provider, or sqlite-vec
didn't load) search runs keyword-only and says so, rather than returning empty.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from brain.embeddings import EmbeddingProvider, pack_vector
from brain.store import IndexStore

_LEG_DEPTH = 50
_MAX_PER_FILE = 2


@dataclass
class Hit:
    rel_path: str
    space: str
    heading_path: str
    snippet: str
    score: float
    sources: list[str] = field(default_factory=list)  # subset of {"keyword","vector"}


@dataclass
class SearchReport:
    query: str
    mode: str
    hits: list[Hit]
    warnings: list[str] = field(default_factory=list)


def rrf(rankings: list[list[int]], *, c: int = 60) -> dict[int, float]:
    """Reciprocal-rank fusion: score(id) = Σ_legs 1 / (c + rank), rank 1-based."""
    scores: dict[int, float] = {}
    for leg in rankings:
        for pos, cid in enumerate(leg):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (c + pos + 1)
    return scores


def search_index(
    vault: Path,
    query: str,
    *,
    k: int = 8,
    provider: EmbeddingProvider | None = None,
    keyword_only: bool = False,
) -> SearchReport:
    vault = Path(vault)
    store = IndexStore.open(_index_db(vault), want_vectors=not keyword_only)

    fts_hits = store.fts(query, _LEG_DEPTH)
    fts_rank = [cid for cid, _, _ in fts_hits]
    snippets = {cid: snip for cid, _, snip in fts_hits}

    warnings: list[str] = []
    use_vectors = not keyword_only and provider is not None and store.vector_status == "ok"
    if not keyword_only and provider is not None and store.vector_status != "ok":
        warnings.append(store.vector_status)

    vec_rank: list[int] = []
    if use_vectors:
        qvec = pack_vector(provider.embed([query])[0])
        vec_rank = [cid for cid, _ in store.knn(qvec, _LEG_DEPTH)]

    legs = [fts_rank, vec_rank] if use_vectors else [fts_rank]
    fused = rrf(legs)
    fts_set, vec_set = set(fts_rank), set(vec_rank)

    hits: list[Hit] = []
    per_file: dict[str, int] = {}
    # highest score first; tie-break on id for determinism
    for cid, score in sorted(fused.items(), key=lambda kv: (-kv[1], kv[0])):
        row = store.chunk(cid)
        if row is None:
            continue
        rel, space, heading_path, _pos, text = row
        if per_file.get(rel, 0) >= _MAX_PER_FILE:
            continue
        per_file[rel] = per_file.get(rel, 0) + 1
        snippet = snippets.get(cid) or (text[:200] + ("…" if len(text) > 200 else ""))
        sources = [s for s, hit in (("keyword", cid in fts_set), ("vector", cid in vec_set)) if hit]
        hits.append(Hit(rel, space, heading_path, snippet, round(score, 6), sources))
        if len(hits) >= k:
            break

    store.close()
    mode = "hybrid" if use_vectors else "keyword-only"
    return SearchReport(query=query, mode=mode, hits=hits, warnings=warnings)


def _index_db(vault: Path) -> Path:
    return vault / ".brain" / "index.db"
