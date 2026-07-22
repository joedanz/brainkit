"""Hybrid retrieval: fuse FTS5 keyword ranking with vector KNN via RRF.

Reciprocal-rank fusion needs no score calibration between the two legs — it
combines their *rankings*, so a chunk that both legs surface outranks one only a
single leg found. When vectors are unavailable (no provider, or sqlite-vec
didn't load) search runs keyword-only and says so, rather than returning empty.

A third, graph leg runs whenever the query yields PPR seeds (entity/alias/stem
matches, the top text hits, or an explicit `center` note): personalized
PageRank over the wikilink + fact-co-mention graph ranks files, whose
representative chunks join the fusion as one more ranking — same RRF
treatment, no weight knob. Unlike the old BFS center boost, the graph leg may
*introduce* candidates the text legs missed; such hits carry sources ==
["graph"] so a non-textual match is visibly attributed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from brain.embeddings import EmbeddingProvider, pack_vector
from brain.graphrank import build_graph, extract_seeds, ppr
from brain.store import IndexStore

_LEG_DEPTH = 50
_MAX_PER_FILE = 2
_RRF_C = 60
_PPR_LEG = 20   # PPR files entering the fusion
_SEED_HITS = 5  # top text-fused chunks whose files become weak seeds


@dataclass
class Hit:
    rel_path: str
    space: str
    heading_path: str
    snippet: str
    score: float
    sources: list[str] = field(default_factory=list)  # subset of {"keyword","vector","graph"}


@dataclass
class SearchReport:
    query: str
    mode: str
    hits: list[Hit]
    warnings: list[str] = field(default_factory=list)


def rrf(rankings: list[list[int]], *, c: int = _RRF_C) -> dict[int, float]:
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
    center: str | None = None,
) -> SearchReport:
    vault = Path(vault)
    db = _index_db(vault)
    if not db.is_file():
        # No index yet: report the gap rather than *creating* an empty one as a
        # side effect (which would mask the "run brain index" hint everywhere).
        return SearchReport(
            query=query, mode="", hits=[],
            warnings=[f"no index at {db} — run: brain index --vault {vault}"],
        )
    store = IndexStore.open_readonly(db, want_vectors=not keyword_only)

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
    text_fused = rrf(legs)
    fts_set, vec_set = set(fts_rank), set(vec_rank)

    rows: dict[int, tuple[str, str, str, int, str]] = {}
    for cid in list(text_fused):
        row = store.chunk(cid)
        if row is not None:
            rows[cid] = row
    text_order = [cid for cid, _ in
                  sorted(text_fused.items(), key=lambda kv: (-kv[1], kv[0]))
                  if cid in rows]

    if center is not None and not store.has_file(center):
        warnings.append(f"center note not in index: {center}")
        center = None

    # Graph leg: PPR from deterministic seeds; may introduce new candidates.
    seed_files: list[str] = []
    for cid in text_order[:_SEED_HITS]:
        rel = rows[cid][0]
        if rel not in seed_files:
            seed_files.append(rel)
    seeds = extract_seeds(query, store, center=center, text_hit_files=seed_files)

    graph_rank: list[int] = []
    if seeds:
        ranked = ppr(build_graph(store), seeds)
        best_for_file: dict[str, int] = {}
        for cid in text_order:
            best_for_file.setdefault(rows[cid][0], cid)
        for rel, _score in ranked[:_PPR_LEG]:
            # representative chunk: the file's best text candidate if the
            # text legs saw it, else its first chunk (a note's opening is
            # its best blind summary)
            cid = best_for_file.get(rel)
            if cid is None:
                cid = store.first_chunk(rel)
            if cid is not None and cid not in graph_rank:
                graph_rank.append(cid)
        legs.append(graph_rank)
        for cid in graph_rank:
            if cid not in rows:
                row = store.chunk(cid)
                if row is not None:
                    rows[cid] = row

    fused = rrf(legs)
    graph_set = set(graph_rank)

    hits: list[Hit] = []
    per_file: dict[str, int] = {}
    # highest score first; tie-break on id for determinism
    for cid, score in sorted(fused.items(), key=lambda kv: (-kv[1], kv[0])):
        row = rows.get(cid)
        if row is None:
            continue
        rel, space, heading_path, _pos, text = row
        if per_file.get(rel, 0) >= _MAX_PER_FILE:
            continue
        per_file[rel] = per_file.get(rel, 0) + 1
        snippet = snippets.get(cid) or (text[:200] + ("…" if len(text) > 200 else ""))
        sources = [s for s, hit in (
            ("keyword", cid in fts_set),
            ("vector", cid in vec_set),
            ("graph", cid in graph_set),
        ) if hit]
        hits.append(Hit(rel, space, heading_path, snippet, round(score, 6), sources))
        if len(hits) >= k:
            break

    store.close()
    mode = ("hybrid" if use_vectors else "keyword-only") + \
        ("+graph" if seeds else "")
    return SearchReport(query=query, mode=mode, hits=hits, warnings=warnings)


def _index_db(vault: Path) -> Path:
    return vault / ".brain" / "index.db"
