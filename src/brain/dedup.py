"""Duplicate-detection primitives — pure functions over text and vectors.

Doctor's `_check_duplicates` is the only consumer. Everything here is
deterministic: MinHash permutations derive from fixed blake2b constants, not
`random`, so findings are bit-identical on every machine — the same contract
graphrank keeps ("persists nothing, same result everywhere").
"""

from __future__ import annotations

import hashlib
import re
import struct

from brain.frontmatter import split_frontmatter

SHINGLE_WORDS = 5
NUM_PERMS = 128
LSH_BANDS = 32
LSH_ROWS = 4  # NUM_PERMS == LSH_BANDS * LSH_ROWS
DUP_JACCARD = 0.5
DUP_COSINE = 0.90
DUP_HAMMING_FRAC = 0.25  # sign-bit prefilter margin for the cosine pass
DUP_MIN_WORDS = 20  # below this a note is a stub, not a duplicate worth flagging

_MERSENNE = (1 << 61) - 1
_WORD_RE = re.compile(r"[^\w\s]+")


def _perm_params(n: int = NUM_PERMS) -> list[tuple[int, int]]:
    """n fixed (a, b) pairs for universal hashing: h' = (a*h + b) % p."""
    params: list[tuple[int, int]] = []
    for i in range(n):
        d = hashlib.blake2b(b"brain-dedup-%d" % i, digest_size=16).digest()
        a = (int.from_bytes(d[:8], "big") % (_MERSENNE - 1)) + 1  # a != 0
        b = int.from_bytes(d[8:], "big") % _MERSENNE
        params.append((a, b))
    return params


_PERMS = _perm_params()


def normalize_text(text: str) -> list[str]:
    """The word stream shingles are built from: frontmatter stripped,
    lowercased, punctuation collapsed to spaces."""
    _meta, body = split_frontmatter(text)
    return _WORD_RE.sub(" ", body.lower()).split()


def shingles(words: list[str], k: int = SHINGLE_WORDS) -> set[str]:
    if len(words) < k:
        return set()
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}


def minhash_signature(shingle_set: set[str]) -> tuple[int, ...] | None:
    """NUM_PERMS-slot MinHash signature, or None for a shingle-less text."""
    if not shingle_set:
        return None
    hashes = [
        int.from_bytes(
            hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest(), "big")
        for s in shingle_set
    ]
    return tuple(
        min((a * h + b) % _MERSENNE for h in hashes) for a, b in _PERMS)


def band_keys(sig: tuple[int, ...]) -> list[tuple[int, tuple[int, ...]]]:
    """(band_index, band_slice) keys; signatures sharing any key are
    candidate pairs — the standard LSH banding trick."""
    return [
        (b, sig[b * LSH_ROWS:(b + 1) * LSH_ROWS]) for b in range(LSH_BANDS)]


def jaccard_estimate(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    return sum(1 for x, y in zip(a, b) if x == y) / len(a)


def unpack_vector(blob: bytes) -> list[float]:
    """Inverse of embeddings.pack_vector: little-endian float32 blob."""
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


def mean_pool(vectors: list[list[float]]) -> list[float]:
    n = len(vectors)
    return [sum(col) / n for col in zip(*vectors)]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def sign_bits(vec: list[float]) -> int:
    """One sign bit per dimension, packed into an int. Hamming distance over
    these approximates angular distance, so the O(n^2) cosine pass can cheaply
    skip pairs that cannot clear DUP_COSINE (verified exactly afterwards)."""
    bits = 0
    for x in vec:
        bits = (bits << 1) | (1 if x > 0 else 0)
    return bits


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()
