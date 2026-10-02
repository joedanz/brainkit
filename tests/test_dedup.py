import struct

import pytest

from brain.dedup import (
    DUP_JACCARD,
    band_keys,
    jaccard_estimate,
    minhash_signature,
    normalize_text,
    shingles,
)


def test_normalize_strips_frontmatter_and_punctuation():
    words = normalize_text("---\ntitle: X\n---\n# Hello, World!\n\nBody text here.\n")
    assert "title" not in words
    assert words == ["hello", "world", "body", "text", "here"]


def test_shingles_below_k_words_is_empty():
    assert shingles(["a", "b", "c"]) == set()


def test_minhash_is_deterministic_and_none_on_empty():
    s = shingles([f"w{i}" for i in range(20)])
    assert minhash_signature(s) == minhash_signature(set(s))
    assert minhash_signature(set()) is None


def test_similar_texts_high_jaccard_and_shared_band():
    a_words = [f"w{i}" for i in range(60)]
    b_words = list(a_words)
    b_words[30] = "changed"
    sa = minhash_signature(shingles(a_words))
    sb = minhash_signature(shingles(b_words))
    assert jaccard_estimate(sa, sb) >= DUP_JACCARD
    assert set(band_keys(sa)) & set(band_keys(sb))


def test_dissimilar_texts_low_jaccard_no_shared_band():
    sa = minhash_signature(shingles([f"a{i}" for i in range(40)]))
    sb = minhash_signature(shingles([f"b{i}" for i in range(40)]))
    assert jaccard_estimate(sa, sb) < 0.1
    assert not (set(band_keys(sa)) & set(band_keys(sb)))


from brain.dedup import cosine, hamming, mean_pool, sign_bits, unpack_vector
from brain.embeddings import pack_vector


def test_unpack_is_inverse_of_pack():
    v = [0.5, -1.25, 2.0]
    assert unpack_vector(pack_vector(v)) == v


def test_mean_pool():
    assert mean_pool([[1.0, 0.0], [0.0, 1.0]]) == [0.5, 0.5]


def test_cosine_basics():
    assert cosine([1.0, 0.0], [2.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0  # zero vector, no crash


def test_sign_bits_and_hamming():
    assert hamming(sign_bits([1.0, -1.0, 2.0]), sign_bits([1.0, 1.0, 2.0])) == 1
    assert hamming(sign_bits([1.0, -1.0]), sign_bits([1.0, -1.0])) == 0


# ---------------------------------------------------------------------------
# The semantic tier's cosine: each vector's norm once, a dot product per pair.

import random


def _generator_cosine(a, b):
    """Verbatim copy of dedup.cosine before it moved to math.sumprod (0.7.1)."""
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def test_cosine_with_precomputed_norms_matches_the_per_pair_formula():
    from brain.dedup import cosine_with_norms, norm

    rng = random.Random(7)
    for _ in range(300):
        dim = rng.choice([3, 32, 512])
        a = [rng.gauss(0.0, 1.0) for _ in range(dim)]
        b = [x + rng.gauss(0.0, 0.3) for x in a]
        got = cosine_with_norms(a, b, norm(a), norm(b))
        assert got == pytest.approx(_generator_cosine(a, b), rel=0, abs=1e-12)
        assert cosine(a, b) == got  # one arithmetic, whichever entry point
    assert cosine_with_norms([0.0, 0.0], [1.0, 0.0], 0.0, 1.0) == 0.0


# ---------------------------------------------------------------------------
# SignatureCache — MinHash signatures remembered between cycles.


def test_signature_version_moves_with_every_parameter(monkeypatch):
    import brain.dedup as dedup

    base = dedup.signature_version()
    assert dedup.signature_version() == base  # stable within a process
    monkeypatch.setattr(dedup, "SIGNATURE_SCHEME", dedup.SIGNATURE_SCHEME + 1)
    assert dedup.signature_version() != base
    monkeypatch.undo()
    monkeypatch.setattr(dedup, "SHINGLE_WORDS", dedup.SHINGLE_WORDS + 1)
    assert dedup.signature_version() != base
    monkeypatch.undo()
    monkeypatch.setattr(dedup, "_PERMS", dedup._perm_params(dedup.NUM_PERMS - 1))
    assert dedup.signature_version() != base


def test_writable_cache_is_refused_where_gitignore_does_not_cover_it(tmp_path):
    from brain.dedup import SignatureCache

    (tmp_path / ".gitignore").write_text("node_modules/\n")
    assert SignatureCache.open_writable(tmp_path) is None
    assert not (tmp_path / "_meta").exists()


def test_cache_round_trip_and_pruning(tmp_path):
    from brain.dedup import SignatureCache

    (tmp_path / ".gitignore").write_text("_meta/cache/\n")
    sig_a = minhash_signature(shingles([f"a{i}" for i in range(30)]))
    sig_b = minhash_signature(shingles([f"b{i}" for i in range(30)]))

    cache = SignatureCache.open_writable(tmp_path)
    assert cache.get_many(["sha-a", "sha-b"]) == {}
    cache.put("sha-a", sig_a)
    cache.put("sha-b", sig_b)
    cache.save()
    cache.close()

    ro = SignatureCache.open_readonly(tmp_path)
    assert ro.get_many(["sha-a", "sha-b", "sha-c"]) == {"sha-a": sig_a, "sha-b": sig_b}
    ro.put("sha-c", sig_a)  # a read-only cache takes the value and keeps nothing
    ro.close()

    cache = SignatureCache.open_writable(tmp_path)
    assert cache.get_many(["sha-b"]) == {"sha-b": sig_b}  # sha-a unseen this run
    cache.save()
    cache.close()

    ro = SignatureCache.open_readonly(tmp_path)
    assert ro.get_many(["sha-a", "sha-b", "sha-c"]) == {"sha-b": sig_b}
    ro.close()


def test_readonly_cache_is_absent_rather_than_created(tmp_path):
    from brain.dedup import SignatureCache

    (tmp_path / ".gitignore").write_text("_meta/cache/\n")
    assert SignatureCache.open_readonly(tmp_path) is None
    assert not (tmp_path / "_meta").exists()


def test_an_unreadable_cache_reads_as_empty(tmp_path):
    from brain.dedup import DEDUP_CACHE_REL, SignatureCache

    db = tmp_path / DEDUP_CACHE_REL
    db.parent.mkdir(parents=True)
    db.write_bytes(b"this is not a database" * 64)
    ro = SignatureCache.open_readonly(tmp_path)
    try:
        assert ro is None or ro.get_many(["sha-a"]) == {}
    finally:
        if ro is not None:
            ro.close()


def test_clusters_are_connected_components():
    from brain.dedup import clusters

    edges = [("b", "c"), ("a", "b"), ("x", "y"), ("d", "e"), ("e", "c")]
    assert clusters(edges) == [("a", "b", "c", "d", "e"), ("x", "y")]
    assert clusters([]) == []
    # Order-independent: the same graph, however its edges arrive.
    assert clusters(reversed(edges)) == clusters(edges)


def test_signature_version_notices_a_change_to_the_algorithm(monkeypatch):
    """A probe signature is part of the version, so changing how shingles
    are hashed (or normalized, or min-hashed) invalidates the cache even if
    nobody remembers to bump SIGNATURE_SCHEME."""
    import brain.dedup as dedup

    base = dedup.signature_version()
    monkeypatch.setattr(dedup, "_shingle_hash", lambda s: len(s))
    assert dedup.signature_version() != base
    monkeypatch.undo()
    monkeypatch.setattr(dedup, "_WORD_RE", dedup.re.compile(r"[^\w\s']+"))
    assert dedup.signature_version() != base


# ---------------------------------------------------------------------------
# semantic_pairs — the semantic tier's pairs, remembered between cycles.


def _blob(vec):
    return struct.pack(f"<{len(vec)}f", *vec)


def _all_pairs(notes):
    """The semantic tier as it was before anything was remembered: every pair
    of pooled vectors, prefilter then cosine."""
    from brain.dedup import (
        DUP_COSINE,
        DUP_HAMMING_FRAC,
        cosine,
        hamming,
        mean_pool,
        sign_bits,
        unpack_vector,
    )

    vecs = {k: mean_pool([unpack_vector(b) for b in bs]) for k, bs in notes.items()}
    max_ham = int(len(vecs[min(vecs)]) * DUP_HAMMING_FRAC)
    keys = sorted(vecs)
    return {(a, b) for i, a in enumerate(keys) for b in keys[i:]
            if hamming(sign_bits(vecs[a]), sign_bits(vecs[b])) <= max_ham
            and cosine(vecs[a], vecs[b]) >= DUP_COSINE}


def test_remembered_pairs_always_equal_the_all_pairs_answer(tmp_path):
    """Notes come and go at random across many writable runs, with read-only
    runs in between: every answer is the all-pairs one."""
    import random

    from brain.dedup import SignatureCache, semantic_pairs, vector_key

    (tmp_path / ".gitignore").write_text("_meta/cache/\n")
    rng = random.Random(5)
    centres = [[rng.gauss(0, 1) for _ in range(48)] for _ in range(6)]

    def make():
        c = rng.choice(centres)
        chunks = rng.randint(1, 3)
        return [_blob([x + rng.gauss(0, 0.35) for x in c]) for _ in range(chunks)]

    pool = [make() for _ in range(60)]
    pool.append([b"\0" * (4 * 48)])  # a zero vector: near nothing, not even itself
    present = set(rng.sample(range(len(pool)), 25))
    for step in range(25):
        for i in rng.sample(range(len(pool)), 6):
            present ^= {i}  # add or remove
        if step in (8, 17):  # a damaged file: the writer rebuilds it from this run
            db = tmp_path / "_meta/cache/dedup.db"
            db.write_bytes(b"this is not a database" * 64)
        notes = {vector_key(pool[i]): pool[i] for i in present}
        expected = _all_pairs(notes)
        assert expected and any(a != b for a, b in expected)
        assert semantic_pairs(notes) == expected
        cache = SignatureCache.open_writable(tmp_path)
        assert semantic_pairs(notes, cache) == expected
        if step in (5, 13):  # a good read, then a write that finds damage
            _fail_first_write(cache)
        cache.save()
        cache.close()
        ro = SignatureCache.open_readonly(tmp_path)
        assert semantic_pairs(notes, ro) == expected
        ro.close()


def _fail_first_write(cache):
    """Make cache.save()'s first write fail as on a corrupt file, so it
    rebuilds the file from what this run computed alone."""
    import sqlite3

    real = cache._write
    state = {"failed": False}

    def write():
        if not state["failed"]:
            state["failed"] = True
            e = sqlite3.DatabaseError("database disk image is malformed")
            e.sqlite_errorcode = sqlite3.SQLITE_CORRUPT
            raise e
        real()

    cache._write = write


def test_semantic_rows_round_trip_and_prune(tmp_path):
    from brain.dedup import SignatureCache

    (tmp_path / ".gitignore").write_text("_meta/cache/\n")
    cache = SignatureCache.open_writable(tmp_path)
    assert cache.get_vector_bits(["k1", "k2"]) == {}
    assert cache.get_near(["k1", "k2"], 8) == {}
    cache.put_vector_bits("k1", 12, 0b101100000001)
    cache.put_vector_bits("k2", 12, 0)
    cache.put_near("k1", ["k1", "k2"])
    cache.put_near("k2", [])
    cache.save()
    cache.close()

    ro = SignatureCache.open_readonly(tmp_path)
    assert ro.get_vector_bits(["k1", "k2", "k3"]) == {"k1": (12, 0b101100000001), "k2": (12, 0)}
    assert ro.get_near(["k1", "k2"], 8) == {"k1": ["k1", "k2"], "k2": []}
    assert ro.get_near(["k1", "k2"], 9) == {}  # another prefilter margin
    ro.put_near("k3", ["k1"])  # read-only: taken and kept nowhere
    ro.close()

    cache = SignatureCache.open_writable(tmp_path)
    cache.get_vector_bits(["k2"])
    cache.get_near(["k2"], 8)
    cache.save()  # k1 was not asked for: pruned
    cache.close()
    ro = SignatureCache.open_readonly(tmp_path)
    assert ro.get_vector_bits(["k1", "k2"]) == {"k2": (12, 0)}
    assert ro.get_near(["k1", "k2", "k3"], 8) == {"k2": []}
    ro.close()


def test_semantic_versions_move_with_every_parameter(monkeypatch):
    import brain.dedup as dedup

    bits, near = dedup.vector_bits_version(), dedup.near_version(32)
    assert dedup.near_version(33) != near
    for name, value in (("DUP_COSINE", 0.91), ("DUP_HAMMING_FRAC", 0.3)):
        monkeypatch.setattr(dedup, name, value)
        assert dedup.near_version(32) != near
        assert dedup.vector_bits_version() == bits
        monkeypatch.undo()
    monkeypatch.setattr(dedup, "NEAR_SCHEME", dedup.NEAR_SCHEME + 1)
    assert dedup.near_version(32) != near
    assert dedup.vector_bits_version() != bits


def test_semantic_versions_notice_a_change_to_the_vector_math(monkeypatch):
    """A probe runs fixed vectors through the functions the remembered rows
    depend on, so changing one invalidates them without a NEAR_SCHEME bump."""
    import math

    import brain.dedup as dedup

    bits, near = dedup.vector_bits_version(), dedup.near_version(32)
    changes = {
        "unpack_vector": lambda blob: [x * 2 for x in dedup.struct.unpack(
            f"<{len(blob) // 4}f", blob)],
        "mean_pool": lambda vs: [max(col) for col in zip(*vs)],
        "sign_bits": lambda v: sum(1 << i for i, x in enumerate(v) if x >= 0),
    }
    for name, fn in changes.items():
        monkeypatch.setattr(dedup, name, fn)
        assert dedup.vector_bits_version() != bits, name
        assert dedup.near_version(32) != near, name
        monkeypatch.undo()
    pair_only = {
        "hamming": lambda a, b: (a & b).bit_count(),
        "norm": lambda v: sum(abs(x) for x in v),
        "cosine_with_norms": lambda a, b, na, nb: math.sumprod(a, b) / (na + nb),
    }
    for name, fn in pair_only.items():
        monkeypatch.setattr(dedup, name, fn)
        assert dedup.near_version(32) != near, name
        monkeypatch.undo()
    assert (dedup.vector_bits_version(), dedup.near_version(32)) == (bits, near)


def test_partner_keys_are_stored_short_and_a_shared_prefix_recomputes(tmp_path):
    """Rows hold PARTNER_HEX-character prefixes of partner keys. Two current
    keys that share a prefix would make a row ambiguous, so then nothing
    remembered is trusted and every pair is compared again."""
    import json
    import sqlite3

    from brain.dedup import DEDUP_CACHE_REL, PARTNER_HEX, SignatureCache, semantic_pairs

    (tmp_path / ".gitignore").write_text("_meta/cache/\n")
    v = _blob([1.0, 2.0, -1.0, 0.5])
    notes = {"a" * 64: [v], "b" * 64: [v]}
    cache = SignatureCache.open_writable(tmp_path)
    assert semantic_pairs(notes, cache) == {
        ("a" * 64, "a" * 64), ("a" * 64, "b" * 64), ("b" * 64, "b" * 64)}
    cache.save()
    cache.close()
    with sqlite3.connect(tmp_path / DEDUP_CACHE_REL) as conn:
        rows = dict(conn.execute("SELECT sha, partners FROM near_pairs").fetchall())
    conn.close()
    assert json.loads(rows["a" * 64]) == ["a" * PARTNER_HEX, "b" * PARTNER_HEX]

    # Same prefix, different key, different vector: must not inherit "a"'s pairs.
    far = _blob([-1.0, -2.0, 1.0, -0.5])
    twin = "a" * PARTNER_HEX + "c" * (64 - PARTNER_HEX)
    notes = {"a" * 64: [v], twin: [far]}
    cache = SignatureCache.open_writable(tmp_path)
    assert semantic_pairs(notes, cache) == {
        ("a" * 64, "a" * 64), (twin, twin)}
    cache.save()
    cache.close()


def test_a_cache_without_the_semantic_tables_reads_as_empty(tmp_path):
    import sqlite3

    from brain.dedup import DEDUP_CACHE_REL, SignatureCache

    (tmp_path / ".gitignore").write_text("_meta/cache/\n")
    SignatureCache.open_writable(tmp_path).close()
    with sqlite3.connect(tmp_path / DEDUP_CACHE_REL) as conn:
        conn.execute("DROP TABLE vector_bits")
        conn.execute("DROP TABLE near_pairs")
    conn.close()
    ro = SignatureCache.open_readonly(tmp_path)
    assert ro.get_vector_bits(["k"]) == {} and ro.get_near(["k"], 8) == {}
    ro.close()
    cache = SignatureCache.open_writable(tmp_path)  # puts the tables back
    cache.get_near(["k"], 8)
    cache.put_near("k", [])
    cache.save()
    cache.close()
    ro = SignatureCache.open_readonly(tmp_path)
    assert ro.get_near(["k"], 8) == {"k": []}
    ro.close()
