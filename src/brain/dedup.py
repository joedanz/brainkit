"""Duplicate-detection primitives — pure functions over text and vectors.

Doctor's `_check_duplicates` is the only consumer. Everything here is
deterministic: MinHash permutations derive from fixed blake2b constants, not
`random`, so findings are bit-identical on every machine — the same contract
graphrank keeps ("persists nothing, same result everywhere").

The one piece of state is `SignatureCache`, and it cannot change a result: a
signature is a pure function of a note's text and the parameters below, so a
remembered one is exactly the one that would be computed.
"""

from __future__ import annotations

import hashlib
import math
import re
import sqlite3
import struct
from collections.abc import Iterable
from pathlib import Path

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


def _shingle_hash(shingle: str) -> int:
    return int.from_bytes(
        hashlib.blake2b(shingle.encode("utf-8"), digest_size=8).digest(), "big")


def _signature(shingle_set: set[str]) -> tuple[int, ...] | None:
    if not shingle_set:
        return None
    hashes = [_shingle_hash(s) for s in shingle_set]
    return tuple(
        min((a * h + b) % _MERSENNE for h in hashes) for a, b in _PERMS)


def minhash_signature(shingle_set: set[str]) -> tuple[int, ...] | None:
    """NUM_PERMS-slot MinHash signature, or None for a shingle-less text.
    The work is in _signature, which signature_version() probes directly, so
    that a count of calls to this function counts notes and nothing else."""
    return _signature(shingle_set)


def band_keys(sig: tuple[int, ...]) -> list[tuple[int, tuple[int, ...]]]:
    """(band_index, band_slice) keys; signatures sharing any key are
    candidate pairs — the standard LSH banding trick."""
    return [
        (b, sig[b * LSH_ROWS:(b + 1) * LSH_ROWS]) for b in range(LSH_BANDS)]


def jaccard_estimate(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    return sum(1 for x, y in zip(a, b) if x == y) / len(a)


# ---- signatures remembered between runs ------------------------------------

# Bump when normalize_text, shingles or minhash_signature change what they
# compute in a way signature_version() cannot see (its probe text below
# catches most such changes). Every remembered signature then misses, is
# recomputed, and the old row is pruned.
SIGNATURE_SCHEME = 1

# Run through the whole pipeline by signature_version(): a change anywhere in
# normalization, shingling, shingle hashing or min-hashing changes its
# signature, so the cache invalidates itself.
_PROBE = (
    "---\ntitle: Probe\n---\n# Signature probe\n\nThe quick brown fox's "
    "Q3 plan: jumps over 12 lazy dogs, then naïve café-goers re-read "
    "\"Ünïcode\" notes — twice, at 9:30 (UTC) & again.\n"
)

DEDUP_CACHE_REL = "_meta/cache/dedup.db"

_DDL = (
    "CREATE TABLE IF NOT EXISTS signatures ("
    "sha TEXT NOT NULL, version TEXT NOT NULL, sig BLOB NOT NULL, "
    "PRIMARY KEY (sha, version))"
)


def _damaged(e: sqlite3.Error) -> bool:
    """A file that is not, or is no longer, a readable database — as opposed
    to one that is only busy or locked, which the next run can read fine."""
    code = getattr(e, "sqlite_errorcode", None)
    return code is not None and code & 0xFF in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB)


def signature_version() -> str:
    """Everything a signature depends on besides the note's text: the scheme,
    the shingle width, the word pattern, the permutations themselves (their
    count and seeds), and the signature of a fixed probe text, so a change
    to the code that computes signatures is caught without anyone having to
    remember to bump SIGNATURE_SCHEME."""
    probe = _signature(shingles(normalize_text(_PROBE)))
    material = repr((SIGNATURE_SCHEME, SHINGLE_WORDS, NUM_PERMS, _MERSENNE,
                     _WORD_RE.pattern, _PERMS, probe))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


class SignatureCache:
    """MinHash signatures from earlier runs, in SQLite at
    `<master>/_meta/cache/dedup.db`, keyed by (sha256 of the note's text,
    signature_version()).

    Computing signatures was the largest single cost of a doctor run on a big
    brain, and almost every note is unchanged from one cycle to the next.

    Doctor is read-only, so the two ways in differ in what they may do:

    - `open_readonly` (standalone `brain doctor`, the dashboard) reads an
      existing file and never creates or writes one. No file, or one that
      cannot be read, only means computing everything, as before.
    - `open_writable` (triage, which is to say the cycle) also collects what
      this run computed. `save` writes it and deletes every row this run did
      not ask for, so the file tracks the brain rather than its history. It
      refuses a master whose .gitignore does not cover `_meta/cache/`, as the
      health snapshot does: a cache git can see would ride along in the next
      commit.

    A damaged file (SQLITE_CORRUPT, SQLITE_NOTADB) is only ever rebuilt by
    the writer, once, with a line in `warnings`; otherwise every later cycle
    would compute every signature again, forever. The read-only side just
    computes, as it does for any unreadable cache.
    """

    def __init__(self, conn: sqlite3.Connection, *, writable: bool,
                 path: Path | None = None) -> None:
        self._conn = conn
        self._path = path
        self.writable = writable
        self.version = signature_version()
        self.warnings: list[str] = []  # for the writer's caller to report
        self._computed: dict[str, bytes] = {}
        self._asked: set[str] | None = None  # None: no lookup yet, so no pruning
        self._read_damaged = False

    @classmethod
    def open_readonly(cls, master: Path) -> SignatureCache | None:
        from urllib.parse import quote

        path = Path(master) / DEDUP_CACHE_REL
        try:  # is_file() raises on a folder it may not look into
            if not path.is_file():
                return None
            conn = sqlite3.connect(f"file:{quote(str(path), safe='/:')}?mode=ro", uri=True)
        except (OSError, sqlite3.Error):
            return None
        return cls(conn, writable=False)

    @classmethod
    def open_writable(cls, master: Path) -> SignatureCache | None:
        """None when .gitignore does not cover the cache. Raises sqlite3.Error
        or OSError when the file cannot be opened; the caller decides."""
        from brain.health import _cache_is_ignored

        master = Path(master)
        if not _cache_is_ignored(master):
            return None
        path = master / DEDUP_CACHE_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = _connect_writable(path)
        except sqlite3.Error as e:
            if not _damaged(e):
                raise
            cache = cls(_rebuild(path), writable=True, path=path)
            cache.warnings.append(f"{DEDUP_CACHE_REL}: {e} — rebuilt")
            return cache
        return cls(conn, writable=True, path=path)

    def get_many(self, shas: list[str]) -> dict[str, tuple[int, ...]]:
        """The remembered signature for each sha that has one. Never raises:
        a read that fails is a miss, and a miss is only a signature to
        compute."""
        if self._asked is None:
            self._asked = set()
        self._asked.update(shas)
        out: dict[str, tuple[int, ...]] = {}
        size = 8 * len(_PERMS)
        try:
            for i in range(0, len(shas), 500):  # under SQLite's variable limit
                batch = shas[i:i + 500]
                rows = self._conn.execute(
                    "SELECT sha, sig FROM signatures WHERE version = ? AND sha IN "
                    f"({','.join('?' * len(batch))})",
                    (self.version, *batch),
                ).fetchall()
                for sha, blob in rows:
                    if len(blob) == size:
                        out[sha] = struct.unpack(f"<{len(_PERMS)}Q", blob)
        except sqlite3.Error as e:
            if self.writable and _damaged(e):
                # Every signature is then computed and put(), so save() can
                # rebuild the file from this run alone.
                self._read_damaged = True
                self.warnings.append(f"{DEDUP_CACHE_REL}: {e} — rebuilt")
                return {}
        return out

    def put(self, sha: str, sig: tuple[int, ...]) -> None:
        if self.writable:
            self._computed[sha] = struct.pack(f"<{len(sig)}Q", *sig)

    def save(self) -> None:
        """Write this run's new signatures and delete every row it did not ask
        for (other versions included), in one transaction. A run that never
        looked anything up prunes nothing. Raises sqlite3.Error."""
        if not self.writable:
            return
        if not self._read_damaged:
            try:
                self._write()
                return
            except sqlite3.Error as e:
                if not _damaged(e):
                    raise
                if not self.warnings:
                    self.warnings.append(f"{DEDUP_CACHE_REL}: {e} — rebuilt")
        # Damaged: rebuild from _computed. After a failed read that is every
        # signature; after a good read and a failed write it is only the new
        # ones, and the next cycle recomputes the rest.
        self._conn.close()
        self._conn = _rebuild(self._path)
        self._write()

    def _write(self) -> None:
        with self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO signatures (sha, version, sig) VALUES (?, ?, ?)",
                [(sha, self.version, blob) for sha, blob in self._computed.items()])
            if self._asked is not None:
                stale = [
                    (sha, version) for sha, version in
                    self._conn.execute("SELECT sha, version FROM signatures").fetchall()
                    if version != self.version or sha not in self._asked]
                self._conn.executemany(
                    "DELETE FROM signatures WHERE sha = ? AND version = ?", stale)
        self._computed.clear()

    def close(self) -> None:
        self._conn.close()


def _connect_writable(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    try:
        conn.execute(_DDL)
        conn.commit()
    except sqlite3.Error:
        conn.close()
        raise
    return conn


def _rebuild(path: Path) -> sqlite3.Connection:
    """Delete a damaged cache (and any journal beside it) and start empty."""
    for suffix in ("", "-journal", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    return _connect_writable(path)


def signatures(
    notes: dict[str, tuple[str, list[str]]], cache: SignatureCache | None = None,
) -> dict[str, tuple[int, ...]]:
    """rel -> MinHash signature, for `notes` mapping rel -> (sha256 of the
    text, its normalize_text words). Takes every signature the cache holds
    and computes the rest; notes with the same text share one computation.
    A note too short to shingle has no signature and is left out."""
    shas = sorted({sha for sha, _words in notes.values()})
    known = cache.get_many(shas) if cache is not None else {}
    out: dict[str, tuple[int, ...]] = {}
    for rel, (sha, words) in notes.items():
        sig = known.get(sha)
        if sig is None:
            sig = minhash_signature(shingles(words))
            if sig is None:
                continue
            known[sha] = sig
            if cache is not None:
                cache.put(sha, sig)
        out[rel] = sig
    return out


def unpack_vector(blob: bytes) -> list[float]:
    """Inverse of embeddings.pack_vector: little-endian float32 blob."""
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


def mean_pool(vectors: list[list[float]]) -> list[float]:
    n = len(vectors)
    return [sum(col) / n for col in zip(*vectors)]


def norm(v: list[float]) -> float:
    return math.sqrt(math.sumprod(v, v))


def cosine_with_norms(a: list[float], b: list[float], na: float, nb: float) -> float:
    """Cosine given both norms already computed. The semantic tier compares
    every vector against every other, so it computes each norm once and pays
    only for a dot product per pair."""
    if na == 0.0 or nb == 0.0:
        return 0.0
    return math.sumprod(a, b) / (na * nb)


def cosine(a: list[float], b: list[float]) -> float:
    return cosine_with_norms(a, b, norm(a), norm(b))


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


def clusters(edges: Iterable[tuple[str, str]]) -> list[tuple[str, ...]]:
    """Connected components of the graph `edges` draws: each a sorted tuple
    of members, and the list sorted, whatever order the edges come in. A
    chain a~b~c is one group even where a and c are not close themselves."""
    parent: dict[str, str] = {}

    def root(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # path halving
            x = parent[x]
        return x

    for a, b in edges:
        ra, rb = root(a), root(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    groups: dict[str, list[str]] = {}
    for x in parent:
        groups.setdefault(root(x), []).append(x)
    return sorted(tuple(sorted(g)) for g in groups.values())
