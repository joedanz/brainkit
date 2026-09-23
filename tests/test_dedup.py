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
