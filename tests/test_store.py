import json
import sqlite3

import pytest

from brain import store
from brain.chunker import Chunk
from brain.embeddings import FakeEmbeddingProvider, pack_vector
from brain.store import SCHEMA_VERSION, IndexStore, StoreError
from tests.conftest import requires_vectors


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


@requires_vectors
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


@requires_vectors
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


def test_link_read_methods(tmp_path):
    s = IndexStore.open(tmp_path / "index.db", want_vectors=False)
    s.add_file("A.md", "sha", "Company", _chunks("A.md", 1), ["c0"], None,
               links=[("B.md", 1), ("Ghost", 0)])
    # self-loop must be excluded from traversal pairs
    s.add_file("B.md", "sha", "Company", _chunks("B.md", 1), ["c0"], None,
               links=[("B.md", 1)])
    assert s.has_file("A.md") and not s.has_file("Z.md")
    # A→Ghost drops out (target not a file), B→B drops out (self-loop)
    assert s.link_pairs() == [("A.md", "B.md")]
    assert s.links_to("B.md") == ["A.md"]
    assert s.links_from("A.md") == [("B.md", 1), ("Ghost", 0)]
    s.close()


def test_schema_v3_fact_tables_roundtrip(tmp_path):
    from brain.facts import Fact

    s = IndexStore.open(tmp_path / "index.db", want_vectors=False)
    fact = Fact(line=3, statement="Sarah Kim is our main contact",
                from_date="2026-01-01", until_date=None,
                sources=["[[2026-01-14-call]]"], targets=["2026-01-14-call"])
    s.add_file("Company/Intel/Acme.md", "sha", "Company", _chunks("Company/Intel/Acme.md", 1),
               ["c0"], None,
               entity=("client", ["Acme Corp", "ACME"]),
               facts=[(fact, ["People/alice/Inbox/2026-01-14-call.md"])])

    assert s.entities() == [("Company/Intel/Acme.md", "client", ["Acme Corp", "ACME"])]
    assert s.alias_map() == {"acme corp": "Company/Intel/Acme.md",
                             "acme": "Company/Intel/Acme.md"}
    rows = s.fact_rows()
    assert len(rows) == 1
    _id, rel, line, stmt, fdate, udate, sources = rows[0]
    assert (rel, line, stmt, fdate, udate) == (
        "Company/Intel/Acme.md", 3, "Sarah Kim is our main contact", "2026-01-01", None)
    assert json.loads(sources) == ["[[2026-01-14-call]]"]
    targets = [t for (t,) in s.conn.execute(
        "SELECT target_rel_path FROM fact_entities")]
    assert targets == ["People/alice/Inbox/2026-01-14-call.md"]

    # delete cascades through all three tables
    s.delete_file("Company/Intel/Acme.md")
    assert s.entities() == [] and s.fact_rows() == []
    assert s.conn.execute("SELECT count(*) FROM fact_entities").fetchone()[0] == 0
    s.close()


def test_fact_copairs_and_first_chunk(tmp_path):
    from brain.facts import Fact

    store = IndexStore.open(tmp_path / "index.db", want_vectors=False)
    mk = lambda rel, pos, text: Chunk(rel_path=rel, space="Company",
                                      heading_path="", pos=pos, text=text)
    fact = Fact(line=3, statement="Sarah is the contact", from_date="2026-01",
                until_date=None, sources=[], targets=[])
    # A closed fact (superseded, until_date set) on the same page co-mentioning
    # the same pair — closed facts contribute equally; duplicates are edge weight.
    closed_fact = Fact(line=5, statement="Sarah was the contact", from_date="2025-06",
                        until_date="2026-06", sources=[], targets=[])
    # Acme's page carries one fact co-mentioning Sarah and Deal.
    store.add_file("Company/Acme.md", "sha1", "Company",
                   [mk("Company/Acme.md", 0, "acme intro"),
                    mk("Company/Acme.md", 1, "acme more")],
                   ["cs1", "cs2"], None,
                   entity=("client", ["ACME"]),
                   facts=[(fact, ["Company/Acme.md", "Company/Sarah.md",
                                  "Company/Deal.md"]),
                          (closed_fact, ["Company/Acme.md", "Company/Sarah.md",
                                         "Company/Deal.md"])])
    store.add_file("Company/Sarah.md", "sha2", "Company",
                   [mk("Company/Sarah.md", 0, "sarah")], ["cs3"], None)
    # Deal.md is a fact target but NOT in files — its pairs must be excluded.

    pairs = sorted(store.fact_copairs())
    assert pairs == [("Company/Acme.md", "Company/Sarah.md"),
                      ("Company/Acme.md", "Company/Sarah.md")]

    cid = store.first_chunk("Company/Acme.md")
    row = store.chunk(cid)
    assert row is not None and row[3] == 0  # pos 0
    assert store.first_chunk("Company/Nope.md") is None
    store.close()


def _store_with_files(tmp_path, rels):
    store = IndexStore.open(tmp_path / "index.db", want_vectors=False)
    for rel in rels:
        store.add_file(rel, "sha", "Company", [], [], None)
    return store


def test_replace_edges_roundtrip_and_wholesale_replace(tmp_path):
    store = _store_with_files(tmp_path, ["a.md", "b.md"])
    store.replace_edges([("a.md", "b.md", "up", "explicit", 2.0)])
    store.replace_edges([("b.md", "a.md", "down", "inverse", 2.0)])
    rows = store.conn.execute("SELECT src_rel_path, dst_rel_path, rel, provenance, weight FROM edges").fetchall()
    assert rows == [("b.md", "a.md", "down", "inverse", 2.0)]  # old set fully replaced
    store.close()


def test_typed_edge_pairs_skips_inverse_and_missing_files(tmp_path):
    store = _store_with_files(tmp_path, ["a.md", "b.md"])
    store.replace_edges([
        ("a.md", "b.md", "up", "explicit", 2.0),
        ("b.md", "a.md", "down", "inverse", 2.0),      # mirror: skipped
        ("a.md", "gone.md", "same", "entity", 0.5),    # endpoint not in files: skipped
    ])
    assert store.typed_edge_pairs() == [("a.md", "b.md", 2.0)]
    store.close()


def test_edges_from_is_ordered(tmp_path):
    store = _store_with_files(tmp_path, ["a.md", "b.md", "c.md"])
    store.replace_edges([
        ("a.md", "c.md", "up", "explicit", 2.0),
        ("a.md", "b.md", "up", "folder", 0.5),
        ("a.md", "b.md", "same", "entity", 0.5),
    ])
    assert store.edges_from("a.md") == [
        ("b.md", "same", "entity", 0.5),
        ("b.md", "up", "folder", 0.5),
        ("c.md", "up", "explicit", 2.0),
    ]
    store.close()
