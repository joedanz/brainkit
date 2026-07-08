import sqlite3

import pytest

from brain import store
from brain.chunker import Chunk
from brain.embeddings import FakeEmbeddingProvider, pack_vector
from brain.store import IndexStore, SCHEMA_VERSION, StoreError


def _chunks(rel="Company/Doc.md", n=3):
    return [
        Chunk(rel_path=rel, space="Company", heading_path="H", pos=i, text=f"chunk text {i}")
        for i in range(n)
    ]


def test_open_initializes_and_is_idempotent(tmp_path):
    p = tmp_path / ".brain/index.db"
    s = IndexStore.open(p)
    assert s.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    s.close()
    s2 = IndexStore.open(p)  # reopening an existing index must not error
    assert s2.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    s2.close()


def test_version_guard_rejects_newer_index(tmp_path):
    p = tmp_path / "index.db"
    IndexStore.open(p).close()
    conn = sqlite3.connect(p)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5}")
    conn.commit()
    conn.close()
    with pytest.raises(StoreError):
        IndexStore.open(p)


def test_add_and_delete_keeps_all_tables_aligned(tmp_path):
    s = IndexStore.open(tmp_path / "index.db")
    chunks = _chunks(n=3)
    vecs = [pack_vector(v) for v in FakeEmbeddingProvider().embed([c.text for c in chunks])]
    s.add_file("Company/Doc.md", "sha1", "Company", chunks, ["cs0", "cs1", "cs2"], vecs)

    def counts():
        n_chunks = s.conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
        n_fts = s.conn.execute("SELECT count(*) FROM chunks_fts").fetchone()[0]
        n_vec = s.conn.execute("SELECT count(*) FROM chunks_vec").fetchone()[0]
        return n_chunks, n_fts, n_vec

    assert counts() == (3, 3, 3)
    s.delete_file("Company/Doc.md")
    assert counts() == (0, 0, 0)
    assert s.files() == {}
    s.close()


def test_fts_finds_phrase_and_sanitizes_operators(tmp_path):
    s = IndexStore.open(tmp_path / "index.db")
    chunks = [Chunk("Company/Doc.md", "Company", "Decision", 0, "we chose the acme option")]
    s.add_file("Company/Doc.md", "sha", "Company", chunks, ["cs"], None)
    hits = s.fts("acme option", 5)
    assert hits and hits[0][0] == 1  # rowid of the only chunk
    # raw FTS operators / quotes must not raise — sanitizer quotes each token
    assert s.fts('drop -table AND (x"', 5) == []
    s.close()


def test_knn_returns_nearest_first(tmp_path):
    s = IndexStore.open(tmp_path / "index.db")
    prov = FakeEmbeddingProvider()
    texts = ["quarterly revenue pipeline", "ops runbook restart", "cooking pasta recipe"]
    chunks = [Chunk("Company/D.md", "Company", "", i, t) for i, t in enumerate(texts)]
    vecs = [pack_vector(v) for v in prov.embed(texts)]
    s.add_file("Company/D.md", "sha", "Company", chunks, ["a", "b", "c"], vecs)
    q = pack_vector(prov.embed(["quarterly revenue forecast"])[0])
    hits = s.knn(q, 3)
    assert hits[0][0] == 1  # the revenue/pipeline chunk (rowid 1) is nearest
    s.close()


def test_null_backend_when_extension_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_try_load_vec", lambda conn: False)
    s = IndexStore.open(tmp_path / "index.db")
    assert s.vector_status != "ok"
    chunks = [Chunk("Company/D.md", "Company", "", 0, "keyword searchable text")]
    # add_file must still work (vectors silently dropped), FTS still queryable
    s.add_file("Company/D.md", "sha", "Company", chunks, ["cs"], [pack_vector([0.1])])
    assert s.fts("keyword", 5)
    assert s.knn(pack_vector([0.1]), 5) == []
    s.close()


def test_links_round_trip_and_delete(tmp_path):
    s = IndexStore.open(tmp_path / "index.db")
    s.add_file(
        "Company/A.md", "sha", "Company", _chunks("Company/A.md", 1), ["x"], None,
        links=[("Company/B.md", 1), ("Missing Note", 0), ("Company/B.md", 1)],
    )
    rows = s.conn.execute(
        "SELECT src_rel_path, target_rel_path, resolved FROM links ORDER BY target_rel_path"
    ).fetchall()
    # duplicate targets collapse via the composite primary key
    assert rows == [
        ("Company/A.md", "Company/B.md", 1),
        ("Company/A.md", "Missing Note", 0),
    ]
    s.delete_file("Company/A.md")
    assert s.conn.execute("SELECT count(*) FROM links").fetchone()[0] == 0
    s.close()


def test_v1_index_migrates_and_reports_migrated_from(tmp_path):
    p = tmp_path / "index.db"
    # Hand-roll a v1 index: current DDL minus links, user_version pinned to 1.
    conn = sqlite3.connect(p)
    conn.executescript(
        "CREATE TABLE index_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "CREATE TABLE files (rel_path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, space TEXT NOT NULL);"
    )
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()

    s = IndexStore.open(p)
    assert s.migrated_from == 1
    assert s.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert s.conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = 'links'").fetchone()
    s.close()
    # Reopening a current-version index is not a migration.
    s2 = IndexStore.open(p)
    assert s2.migrated_from is None
    s2.close()


def test_meta_round_trip_and_files_map(tmp_path):
    s = IndexStore.open(tmp_path / "index.db")
    s.set_meta("model", "fake-32")
    assert s.get_meta("model") == "fake-32"
    assert s.get_meta("missing") is None
    s.add_file("Company/A.md", "shaA", "Company", _chunks("Company/A.md", 1), ["x"], None)
    s.add_file("People/bob/B.md", "shaB", "People/bob", _chunks("People/bob/B.md", 1), ["y"], None)
    assert s.files() == {"Company/A.md": "shaA", "People/bob/B.md": "shaB"}
    s.close()
