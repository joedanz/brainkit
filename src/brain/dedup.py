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
