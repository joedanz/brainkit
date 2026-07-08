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
from brain.compiler import _stem, extract_wikilinks
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


def _resolve_links(
    raw_targets: list[str], paths: set[str], by_stem: dict[str, str]
) -> list[tuple[str, int]]:
    """Map raw wikilink targets to (target_rel_path, resolved) pairs.

    Resolution matches the compiler's stem-based scheme (lowercased stem);
    path-form targets ("Company/Decisions/X") try an exact rel-path match
    first. On duplicate stems the lexicographically first path wins —
    deterministic, though Obsidian itself prefers the shortest path.
    Unresolved targets keep their raw text with resolved=0: "note doesn't
    exist" and "note exists in master but outside this vault" are
    indistinguishable from inside a compiled vault, by design.
    """
    out: list[tuple[str, int]] = []
    for target in raw_targets:
        if "/" in target:
            for candidate in (target, target + ".md"):
                if candidate in paths:
                    out.append((candidate, 1))
                    break
            else:
                hit = by_stem.get(_stem(target))
                out.append((hit, 1) if hit else (target, 0))
        else:
            hit = by_stem.get(_stem(target))
            out.append((hit, 1) if hit else (target, 0))
    return out


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
    # A schema migration leaves new tables (e.g. links) empty for files the
    # diff would skip as unchanged; rebuild so they populate.
    if store.migrated_from is not None:
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

    # Link targets resolve against every indexable file in the manifest, the
    # same universe the compiler used for stubbing. First path wins on
    # duplicate stems (sorted → deterministic).
    link_paths = set(candidates)
    by_stem: dict[str, str] = {}
    for rel in sorted(candidates):
        by_stem.setdefault(_stem(rel), rel)

    # Phase 1: chunk every changed file and gather the embedding inputs needed.
    per_file: dict[str, tuple[str, str, list, list[str], list[tuple[str, int]]]] = {}
    needed: dict[str, str] = {}  # chunk_sha -> embedding input
    for rel in changed:
        space = space_of_path(rel)
        if space is None:  # compiled files are always in a space; defensive
            report.warnings.append(f"{rel}: outside any space, skipped")
            continue
        text = (vault / rel).read_text(encoding="utf-8", errors="replace")
        chunks = chunk_markdown(rel, text)
        cshas = [_sha_text(embedding_input(c)) for c in chunks]
        links = _resolve_links(extract_wikilinks(text), link_paths, by_stem)
        per_file[rel] = (candidates[rel], space, chunks, cshas, links)
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
    for rel, (sha, space, chunks, cshas, links) in per_file.items():
        store.delete_file(rel)
        vectors = [vecs[s] for s in cshas] if want_vectors else None
        store.add_file(rel, sha, space, chunks, cshas, vectors, links=links)
    report.files_indexed = len(per_file)

    # Unchanged sources may still point at files removed this run; demote
    # those rows so `resolved` never claims a target the index no longer has.
    store.conn.execute(
        "UPDATE links SET resolved = 0 WHERE resolved = 1 "
        "AND target_rel_path NOT IN (SELECT rel_path FROM files)"
    )
    store.conn.commit()

    if provider is not None:
        store.set_meta("model", provider.model)
        store.set_meta("dim", str(provider.dim or ""))
    store.set_meta("built_at", datetime.now(timezone.utc).isoformat())
    store.close()
    return report
