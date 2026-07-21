"""The per-vault SQLite index: chunk store + FTS5 keyword table + vec0 vectors.

One file, ``<vault>/.brain/index.db``. The relational half (chunks, files, FTS5)
uses only stdlib sqlite3 and always works. The vector half loads the sqlite-vec
extension; if that extension can't load (a Python built without
``enable_load_extension``, a missing wheel), the store degrades to a
`NullVectorBackend` and search runs keyword-only — never a crash, never a
silently empty result.

`VectorBackend` is the seam that keeps the vector engine swappable: chunks, FTS,
and every caller talk to `add`/`delete`/`knn` and nothing else, so moving from
sqlite-vec brute-force to int8 quantization or an ANN backend touches only this
file.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Protocol

import sqlite_vec

from brain.chunker import Chunk

SCHEMA_VERSION = 2

_DDL = """
CREATE TABLE IF NOT EXISTS index_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS files (
    rel_path TEXT PRIMARY KEY,
    sha256   TEXT NOT NULL,
    space    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS links (
    src_rel_path    TEXT NOT NULL,
    target_rel_path TEXT NOT NULL,
    resolved        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (src_rel_path, target_rel_path)
);
CREATE INDEX IF NOT EXISTS links_by_target ON links(target_rel_path);
CREATE TABLE IF NOT EXISTS chunks (
    id           INTEGER PRIMARY KEY,
    rel_path     TEXT NOT NULL,
    space        TEXT NOT NULL,
    heading_path TEXT NOT NULL DEFAULT '',
    pos          INTEGER NOT NULL,
    chunk_sha    TEXT NOT NULL,
    text         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_by_path ON chunks(rel_path);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, heading_path);
"""


class StoreError(RuntimeError):
    """The index is unusable (e.g. built by a newer brainkit schema)."""


class VectorBackend(Protocol):
    def add(self, ids: list[int], vectors: list[bytes]) -> None: ...
    def delete(self, ids: list[int]) -> None: ...
    def knn(self, vector: bytes, k: int) -> list[tuple[int, float]]: ...


class NullVectorBackend:
    """No-op backend used when sqlite-vec cannot load. Search stays keyword-only."""

    def add(self, ids: list[int], vectors: list[bytes]) -> None:
        return None

    def delete(self, ids: list[int]) -> None:
        return None

    def knn(self, vector: bytes, k: int) -> list[tuple[int, float]]:
        return []


class SqliteVecBackend:
    """vec0-backed KNN. The vec table is created lazily on first write, once the
    embedding dimension is known (it must match across a rebuild)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._ready = self._table_exists()

    def _table_exists(self) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'chunks_vec'"
        ).fetchone()
        return row is not None

    def _ensure(self, dim: int) -> None:
        if not self._ready:
            self.conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(embedding float[{dim}])"
            )
            self._ready = True

    def add(self, ids: list[int], vectors: list[bytes]) -> None:
        if not vectors:
            return
        self._ensure(len(vectors[0]) // 4)
        self.conn.executemany(
            "INSERT INTO chunks_vec(rowid, embedding) VALUES (?, ?)",
            list(zip(ids, vectors)),
        )

    def delete(self, ids: list[int]) -> None:
        if not self._ready or not ids:
            return
        self.conn.executemany("DELETE FROM chunks_vec WHERE rowid = ?", [(i,) for i in ids])

    def knn(self, vector: bytes, k: int) -> list[tuple[int, float]]:
        if not self._ready:
            return []
        rows = self.conn.execute(
            "SELECT rowid, distance FROM chunks_vec WHERE embedding MATCH ? AND k = ?",
            (vector, k),
        ).fetchall()
        return [(int(r), float(d)) for r, d in rows]


def _try_load_vec(conn: sqlite3.Connection) -> bool:
    """Attempt to load sqlite-vec on `conn`. Return True on success. Isolated in
    a function so tests can force the degraded path by monkeypatching it."""
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except Exception:
        # Any failure (no enable_load_extension, missing wheel, OperationalError)
        # means we run keyword-only. Never let it crash indexing or search.
        return False


def _sanitize_fts(query: str) -> str:
    """Quote each whitespace token so raw FTS5 operators/quotes in a user query
    can never produce a syntax error."""
    return " ".join(f'"{tok.replace(chr(34), chr(34) * 2)}"' for tok in query.split())


class IndexStore:
    def __init__(self, conn: sqlite3.Connection, vectors: VectorBackend, vector_status: str,
                 migrated_from: int | None = None) -> None:
        self.conn = conn
        self.vectors = vectors
        self.vector_status = vector_status
        # Set when open() upgraded an older-schema index in place. New tables
        # start empty for already-indexed files, so the indexer treats this as
        # a full rebuild (cheap: the embedding cache absorbs the cost).
        self.migrated_from = migrated_from

    @classmethod
    def open(cls, path: Path, *, want_vectors: bool = True) -> IndexStore:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA journal_mode = WAL")

        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            conn.close()
            raise StoreError(
                f"index at {path} was built by a newer brainkit "
                f"(schema {version} > {SCHEMA_VERSION}); rebuild with: brain index --full"
            )
        conn.executescript(_DDL)
        migrated_from = version if 0 < version < SCHEMA_VERSION else None
        if version < SCHEMA_VERSION:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()

        if want_vectors and _try_load_vec(conn):
            vectors: VectorBackend = SqliteVecBackend(conn)
            status = "ok"
        else:
            vectors = NullVectorBackend()
            status = "sqlite-vec unavailable — keyword-only search" if want_vectors else "vectors disabled"
        return cls(conn, vectors, status, migrated_from)

    @classmethod
    def open_readonly(cls, path: Path, *, want_vectors: bool = True) -> IndexStore:
        """Open an existing index for reading only.

        Unlike :meth:`open`, this never creates the file, mkdir's its parent,
        switches journal mode, runs DDL, or bumps the schema version — it opens
        the ``file:...?mode=ro`` URI, so a search or status request can never
        mutate a vault (or contend for the write lock a concurrent ``brain
        index`` holds). The caller must ensure the database already exists;
        a missing file raises ``sqlite3.OperationalError``.
        """
        from urllib.parse import quote

        path = Path(path)
        uri = f"file:{quote(str(path), safe='/:')}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            conn.close()
            raise StoreError(
                f"index at {path} was built by a newer brainkit "
                f"(schema {version} > {SCHEMA_VERSION}); rebuild with: brain index --full"
            )
        if want_vectors and _try_load_vec(conn):
            vectors: VectorBackend = SqliteVecBackend(conn)
            status = "ok"
        else:
            vectors = NullVectorBackend()
            status = "sqlite-vec unavailable — keyword-only search" if want_vectors else "vectors disabled"
        return cls(conn, vectors, status, None)

    # ---- reads -------------------------------------------------------------
    def files(self) -> dict[str, str]:
        return {rel: sha for rel, sha in self.conn.execute("SELECT rel_path, sha256 FROM files")}

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM index_meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def has_file(self, rel_path: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM files WHERE rel_path = ?", (rel_path,)
        ).fetchone()
        return row is not None

    def link_pairs(self) -> list[tuple[str, str]]:
        """Resolved wikilink pairs whose target exists in this vault, self-loops
        excluded — the edge set for graph traversal (same join stats.py uses)."""
        return self.conn.execute(
            "SELECT l.src_rel_path, l.target_rel_path FROM links l "
            "JOIN files f ON f.rel_path = l.target_rel_path "
            "WHERE l.src_rel_path != l.target_rel_path"
        ).fetchall()

    def links_to(self, rel_path: str) -> list[str]:
        """Backlinks: notes whose resolved wikilinks point at `rel_path`
        (self-references excluded, matching link_pairs)."""
        return [r[0] for r in self.conn.execute(
            "SELECT src_rel_path FROM links "
            "WHERE target_rel_path = ? AND resolved = 1 AND src_rel_path != ? "
            "ORDER BY src_rel_path",
            (rel_path, rel_path),
        )]

    def links_from(self, rel_path: str) -> list[tuple[str, int]]:
        """Outgoing wikilinks of `rel_path` as (target, resolved) pairs.
        Unresolved targets keep their raw link text."""
        return [(t, int(r)) for t, r in self.conn.execute(
            "SELECT target_rel_path, resolved FROM links "
            "WHERE src_rel_path = ? ORDER BY target_rel_path",
            (rel_path,),
        )]

    def chunk(self, chunk_id: int) -> tuple[str, str, str, int, str] | None:
        return self.conn.execute(
            "SELECT rel_path, space, heading_path, pos, text FROM chunks WHERE id = ?",
            (chunk_id,),
        ).fetchone()

    def fts(self, query: str, k: int) -> list[tuple[int, float, str]]:
        q = _sanitize_fts(query)
        if not q:
            return []
        rows = self.conn.execute(
            "SELECT rowid, bm25(chunks_fts), "
            "snippet(chunks_fts, 0, '**', '**', '…', 12) "
            "FROM chunks_fts WHERE chunks_fts MATCH ? "
            "ORDER BY bm25(chunks_fts) LIMIT ?",
            (q, k),
        ).fetchall()
        return [(int(r), float(score), snip) for r, score, snip in rows]

    def knn(self, vector: bytes, k: int) -> list[tuple[int, float]]:
        return self.vectors.knn(vector, k)

    # ---- writes ------------------------------------------------------------
    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO index_meta(key, value) VALUES (?, ?)", (key, value)
        )
        self.conn.commit()

    def delete_file(self, rel_path: str) -> None:
        ids = [r[0] for r in self.conn.execute(
            "SELECT id FROM chunks WHERE rel_path = ?", (rel_path,))]
        self.vectors.delete(ids)
        self.conn.executemany("DELETE FROM chunks_fts WHERE rowid = ?", [(i,) for i in ids])
        self.conn.execute("DELETE FROM chunks WHERE rel_path = ?", (rel_path,))
        self.conn.execute("DELETE FROM files WHERE rel_path = ?", (rel_path,))
        self.conn.execute("DELETE FROM links WHERE src_rel_path = ?", (rel_path,))
        self.conn.commit()

    def add_file(
        self,
        rel_path: str,
        sha256: str,
        space: str,
        chunks: list[Chunk],
        chunk_shas: list[str],
        vectors: list[bytes] | None,
        links: list[tuple[str, int]] | None = None,
    ) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO files(rel_path, sha256, space) VALUES (?, ?, ?)",
            (rel_path, sha256, space),
        )
        if links:
            cur.executemany(
                "INSERT OR REPLACE INTO links(src_rel_path, target_rel_path, resolved) "
                "VALUES (?, ?, ?)",
                [(rel_path, target, resolved) for target, resolved in links],
            )
        ids: list[int] = []
        for ch, csha in zip(chunks, chunk_shas):
            cur.execute(
                "INSERT INTO chunks(rel_path, space, heading_path, pos, chunk_sha, text) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ch.rel_path, ch.space, ch.heading_path, ch.pos, csha, ch.text),
            )
            cid = cur.lastrowid
            ids.append(cid)
            cur.execute(
                "INSERT INTO chunks_fts(rowid, text, heading_path) VALUES (?, ?, ?)",
                (cid, ch.text, ch.heading_path),
            )
        if vectors:
            self.vectors.add(ids, vectors)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
