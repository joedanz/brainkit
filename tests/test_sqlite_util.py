import sqlite3

import pytest

from brain import sqlite_util


def test_connect_readonly_reads_but_never_writes(tmp_path):
    db = tmp_path / "a dir?#" / "x.db"
    db.parent.mkdir()
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (v)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()

    ro = sqlite_util.connect_readonly(db)
    assert ro.execute("SELECT v FROM t").fetchall() == [(1,)]
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("INSERT INTO t VALUES (2)")
    ro.close()


def test_connect_readonly_never_creates_a_file(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        sqlite_util.connect_readonly(tmp_path / "missing.db")
    assert not (tmp_path / "missing.db").exists()


def test_every_read_only_opener_goes_through_the_helper(tmp_path, monkeypatch):
    from brain.dedup import DEDUP_CACHE_REL, SignatureCache
    from brain.stats import ro_connect
    from brain.store import IndexStore

    db = tmp_path / "x.db"
    sqlite3.connect(db).close()
    cache = tmp_path / DEDUP_CACHE_REL
    cache.parent.mkdir(parents=True)
    sqlite3.connect(cache).close()

    seen = []
    real = sqlite_util.connect_readonly

    def spy(path):
        seen.append(path)
        return real(path)

    monkeypatch.setattr(sqlite_util, "connect_readonly", spy)
    ro_connect(db).close()
    IndexStore.open_readonly(db, want_vectors=False).close()
    SignatureCache.open_readonly(tmp_path).close()
    assert len(seen) == 3


def _err(code):
    e = sqlite3.DatabaseError("boom")
    e.sqlite_errorcode = code
    return e


def test_only_corrupt_or_not_a_database_counts_as_damaged():
    assert sqlite_util.is_damaged(_err(sqlite3.SQLITE_CORRUPT))
    assert sqlite_util.is_damaged(_err(sqlite3.SQLITE_NOTADB))
    assert not sqlite_util.is_damaged(_err(sqlite3.SQLITE_BUSY))
    assert not sqlite_util.is_damaged(_err(sqlite3.SQLITE_LOCKED))
    assert not sqlite_util.is_damaged(sqlite3.DatabaseError("no code"))


def test_rebuild_deletes_only_that_database_and_its_journals(tmp_path):
    db = tmp_path / "x.db"
    for name in ("x.db", "x.db-journal", "x.db-wal", "x.db-shm", "other.db"):
        (tmp_path / name).write_bytes(b"junk")
    conn = sqlite_util.rebuild(db, sqlite3.connect)
    conn.execute("CREATE TABLE t (v)")
    conn.close()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["other.db", "x.db"]
