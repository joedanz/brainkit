"""Build a per-vault search index from its compiled manifest, incrementally.

The compiler does a full rebuild every run, so a naive indexer would re-embed
everything each cycle. Instead we diff the manifest's per-file sha256 against
what the index already holds: unchanged files are skipped entirely, and even a
changed file only re-embeds chunks whose content hash misses the embedding
cache. That cache is shared across people, so a chunk that two vaults have in
common embeds exactly once.

Indexing reads only the compiled vault (never master), so the index inherits the
compiler's structural boundary: a per-person index can only ever contain content
that person may read.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from brain.chunker import chunk_markdown, embedding_input
from brain.embeddings import EmbeddingCache, EmbeddingProvider, pack_vector
from brain.resolver import space_of_path
from brain.store import IndexStore
from brain.writeback import _load_manifest


@dataclass
class IndexReport:
    files_indexed: int = 0
    files_removed: int = 0
    files_unchanged: int = 0
    chunks_embedded: int = 0
    chunks_from_cache: int = 0
    mode: str = "keyword-only"  # "hybrid" | "keyword-only"
    warnings: list[str] = field(default_factory=list)


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _index_path(vault: Path) -> Path:
    return vault / ".brain" / "index.db"


def build_index(
    vault: Path,
    *,
    provider: EmbeddingProvider | None,
    cache: EmbeddingCache | None,
    full: bool = False,
) -> IndexReport:
    vault = Path(vault)
    manifest = _load_manifest(vault)  # raises ManifestError if uncompiled
    generated = set(manifest["generated"])
    candidates = {
        rel: sha
        for rel, sha in manifest["compiled"].items()
        if rel.endswith(".md") and rel not in generated
    }

    report = IndexReport()
    index_path = _index_path(vault)

    store = IndexStore.open(index_path)
    # A model change invalidates every stored vector (and its dimension), so
    # rebuild from scratch — the embedding cache keeps that cheap.
    if provider is not None and store.get_meta("model") not in (None, provider.model):
        full = True
    if full:
        store.close()
        for p in (index_path, index_path.with_name(index_path.name + "-wal"),
                  index_path.with_name(index_path.name + "-shm")):
            p.unlink(missing_ok=True)
        store = IndexStore.open(index_path)

    want_vectors = provider is not None and store.vector_status == "ok"
    if provider is not None and store.vector_status != "ok":
        report.warnings.append(store.vector_status)
    report.mode = "hybrid" if want_vectors else "keyword-only"
    model = provider.model if provider else ""

    existing = store.files()
    removed = [rel for rel in existing if rel not in candidates]
    for rel in removed:
        store.delete_file(rel)
    report.files_removed = len(removed)

    changed = [rel for rel, sha in candidates.items() if existing.get(rel) != sha]
    report.files_unchanged = len(candidates) - len(changed)

    # Phase 1: chunk every changed file and gather the embedding inputs needed.
    per_file: dict[str, tuple[str, str, list, list[str]]] = {}
    needed: dict[str, str] = {}  # chunk_sha -> embedding input
    for rel in changed:
        space = space_of_path(rel)
        if space is None:  # compiled files are always in a space; defensive
            report.warnings.append(f"{rel}: outside any space, skipped")
            continue
        text = (vault / rel).read_text(encoding="utf-8", errors="replace")
        chunks = chunk_markdown(rel, text)
        cshas = [_sha_text(embedding_input(c)) for c in chunks]
        per_file[rel] = (candidates[rel], space, chunks, cshas)
        if want_vectors:
            for ch, csha in zip(chunks, cshas):
                needed.setdefault(csha, embedding_input(ch))

    # Phase 2: resolve vectors cache-first; embed only the misses, once.
    vecs: dict[str, bytes] = {}
    if want_vectors and needed:
        shas = list(needed)
        hits = cache.get_many(shas, model) if cache else {}
        vecs.update(hits)
        report.chunks_from_cache = len(hits)
        misses = [s for s in shas if s not in hits]
        if misses:
            packed = [pack_vector(v) for v in provider.embed([needed[s] for s in misses])]
            if cache:
                cache.put_many(list(zip(misses, packed)), model)
            vecs.update(zip(misses, packed))
            report.chunks_embedded = len(misses)

    # Phase 3: write each file atomically (delete + add is one transaction pair).
    for rel, (sha, space, chunks, cshas) in per_file.items():
        store.delete_file(rel)
        vectors = [vecs[s] for s in cshas] if want_vectors else None
        store.add_file(rel, sha, space, chunks, cshas, vectors)
    report.files_indexed = len(per_file)

    if provider is not None:
        store.set_meta("model", provider.model)
        store.set_meta("dim", str(provider.dim or ""))
    store.set_meta("built_at", datetime.now(timezone.utc).isoformat())
    store.close()
    return report
