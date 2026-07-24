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
    assert minhash_signature(s) == minhash_signature(set(list(s)))
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
