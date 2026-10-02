"""Duplicate-detection primitives — pure functions over text and vectors.

Doctor's `_check_duplicates` is the only consumer. Everything here is
deterministic: MinHash permutations derive from fixed blake2b constants, not
`random`, so findings are bit-identical on every machine — the same contract
graphrank keeps ("persists nothing, same result everywhere").

The one piece of state is `SignatureCache`, and it cannot change a result: a
signature is a pure function of a note's text and the parameters below, so a
remembered one is exactly the one that would be computed. The same holds for
the semantic tier's remembered pairs (`semantic_pairs`): they are keyed by
the bytes of each note's chunk vectors, not by its path or text.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import struct
from collections.abc import Iterable
from pathlib import Path

from brain import sqlite_util
from brain.frontmatter import split_frontmatter
from brain.secrets import KINDS, Hit, scanner_version

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
    ("CREATE TABLE IF NOT EXISTS signatures ("
     "sha TEXT NOT NULL, version TEXT NOT NULL, sig BLOB NOT NULL, "
     "PRIMARY KEY (sha, version))"),
    # Doctor's secrets scan, remembered the same way (see brain.secrets):
    # hits as JSON [[kind, line], ...] — never the matched value.
    ("CREATE TABLE IF NOT EXISTS secret_scans ("
     "sha TEXT NOT NULL, version TEXT NOT NULL, hits TEXT NOT NULL, "
     "PRIMARY KEY (sha, version))"),
    # The semantic tier (see semantic_pairs), keyed by vector_key: each
    # note's sign bits (4-byte dimension, then the bits), and the keys its
    # pooled vector is a near-duplicate of, as a JSON list.
    ("CREATE TABLE IF NOT EXISTS vector_bits ("
     "sha TEXT NOT NULL, version TEXT NOT NULL, bits BLOB NOT NULL, "
     "PRIMARY KEY (sha, version))"),
    ("CREATE TABLE IF NOT EXISTS near_pairs ("
     "sha TEXT NOT NULL, version TEXT NOT NULL, partners TEXT NOT NULL, "
     "PRIMARY KEY (sha, version))"),
)


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

    The same file also remembers doctor's secrets scan per note
    (`get_scans`/`put_scan`, table `secret_scans`, keyed by the same sha and
    `brain.secrets.scanner_version()`), with the same read/write/prune rules:
    it is the other per-note cost that is pure in the note's text. And the
    semantic tier's sign bits and near-duplicate partners
    (`get_vector_bits`/`get_near`, see `semantic_pairs`), keyed by
    `vector_key` rather than by text, again under the same rules.

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
        self.scan_version = scanner_version()
        self._scans_computed: dict[str, str] = {}
        self._scans_asked: set[str] | None = None
        self.bits_version = vector_bits_version()
        self._bits_computed: dict[str, bytes] = {}
        self._bits_asked: set[str] | None = None
        self.near_version: str | None = None  # set by get_near: it needs max_ham
        self._near_computed: dict[str, str] = {}
        self._near_asked: set[str] | None = None

    @classmethod
    def open_readonly(cls, master: Path) -> SignatureCache | None:
        path = Path(master) / DEDUP_CACHE_REL
        try:  # is_file() raises on a folder it may not look into
            if not path.is_file():
                return None
            conn = sqlite_util.connect_readonly(path)
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
            if not sqlite_util.is_damaged(e):
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
        size = 8 * len(_PERMS)
        return {
            sha: struct.unpack(f"<{len(_PERMS)}Q", blob)
            for sha, blob in self._select("signatures", "sig", self.version, shas)
            if len(blob) == size}

    def _select(self, table: str, column: str, version: str,
                shas: list[str]) -> list[tuple[str, object]]:
        """(sha, value) rows of `table` at `version` for `shas`, in batches
        under SQLite's variable limit. Never raises: a failed read returns
        the rows read so far, and a damaged file (writer only) returns none
        and is marked for save() to rebuild — every value is then computed
        and put(), so the file can be rebuilt from this run alone."""
        rows: list[tuple[str, object]] = []
        try:
            for i in range(0, len(shas), 500):
                batch = shas[i:i + 500]
                rows += self._conn.execute(
                    f"SELECT sha, {column} FROM {table} WHERE version = ? AND sha IN "
                    f"({','.join('?' * len(batch))})",
                    (version, *batch),
                ).fetchall()
        except sqlite3.Error as e:
            if self.writable and sqlite_util.is_damaged(e):
                if not self._read_damaged:  # one warning per run
                    self.warnings.append(f"{DEDUP_CACHE_REL}: {e} — rebuilt")
                self._read_damaged = True
                return []
        return rows

    def put(self, sha: str, sig: tuple[int, ...]) -> None:
        if self.writable:
            self._computed[sha] = struct.pack(f"<{len(sig)}Q", *sig)

    def get_scans(self, shas: list[str]) -> dict[str, list[Hit]]:
        """The remembered secrets scan for each sha that has one. Never
        raises, like get_many: an older file without the table, or a row
        that does not parse, is only a note to scan again. A hit whose kind
        is not one of the scanner's labels is dropped: the cache is a file
        on disk, and its contents go into digests."""
        if self._scans_asked is None:
            self._scans_asked = set()
        self._scans_asked.update(shas)
        out: dict[str, list[Hit]] = {}
        for sha, raw in self._select("secret_scans", "hits", self.scan_version, shas):
            try:
                out[sha] = [Hit(k, int(n)) for k, n in json.loads(raw) if k in KINDS]
            except (ValueError, TypeError):
                continue
        return out

    def put_scan(self, sha: str, hits: list[Hit]) -> None:
        if self.writable:
            self._scans_computed[sha] = json.dumps([[h.kind, h.line] for h in hits])

    def get_vector_bits(self, keys: list[str]) -> dict[str, tuple[int, int]]:
        """key -> (dimension, sign bits) for each vector key remembered.
        Never raises, like get_many."""
        if self._bits_asked is None:
            self._bits_asked = set()
        self._bits_asked.update(keys)
        out: dict[str, tuple[int, int]] = {}
        for key, blob in self._select("vector_bits", "bits", self.bits_version, keys):
            if not isinstance(blob, bytes) or len(blob) < 4:
                continue
            (dim,) = struct.unpack("<I", blob[:4])
            if len(blob) == 4 + (dim + 7) // 8:
                out[key] = (dim, int.from_bytes(blob[4:], "big"))
        return out

    def put_vector_bits(self, key: str, dim: int, bits: int) -> None:
        if self.writable:
            self._bits_computed[key] = (
                struct.pack("<I", dim) + bits.to_bytes((dim + 7) // 8, "big"))

    def get_near(self, keys: list[str], max_ham: int) -> dict[str, list[str]]:
        """key -> the keys its vector was found near, for each vector key
        remembered at this prefilter margin. Never raises, like get_many."""
        self.near_version = near_version(max_ham)
        if self._near_asked is None:
            self._near_asked = set()
        self._near_asked.update(keys)
        out: dict[str, list[str]] = {}
        for key, raw in self._select("near_pairs", "partners", self.near_version, keys):
            try:
                partners = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if isinstance(partners, list) and all(isinstance(p, str) for p in partners):
                out[key] = partners
        return out

    def put_near(self, key: str, partners: list[str]) -> None:
        if self.writable:
            self._near_computed[key] = json.dumps(partners)

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
                if not sqlite_util.is_damaged(e):
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
            _replace_and_prune(self._conn, "signatures", "sig", self.version,
                               self._computed, self._asked)
            _replace_and_prune(self._conn, "secret_scans", "hits", self.scan_version,
                               self._scans_computed, self._scans_asked)
            _replace_and_prune(self._conn, "vector_bits", "bits", self.bits_version,
                               self._bits_computed, self._bits_asked)
            if self.near_version is not None:
                _replace_and_prune(self._conn, "near_pairs", "partners",
                                   self.near_version, self._near_computed,
                                   self._near_asked)
        self._computed.clear()
        self._scans_computed.clear()
        self._bits_computed.clear()
        self._near_computed.clear()

    def close(self) -> None:
        self._conn.close()


def _replace_and_prune(conn: sqlite3.Connection, table: str, column: str,
                       version: str, computed: dict, asked: set[str] | None) -> None:
    """Write this run's new rows to `table`, then delete every row of another
    version or for a sha this run did not ask for. `asked` None: nothing was
    looked up, so nothing is pruned."""
    conn.executemany(
        f"INSERT OR REPLACE INTO {table} (sha, version, {column}) VALUES (?, ?, ?)",
        [(sha, version, value) for sha, value in computed.items()])
    if asked is not None:
        stale = [
            (sha, v) for sha, v in
            conn.execute(f"SELECT sha, version FROM {table}").fetchall()
            if v != version or sha not in asked]
        conn.executemany(f"DELETE FROM {table} WHERE sha = ? AND version = ?", stale)


def _connect_writable(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    try:
        for ddl in _DDL:
            conn.execute(ddl)
        conn.commit()
    except sqlite3.Error:
        conn.close()
        raise
    return conn


def _rebuild(path: Path) -> sqlite3.Connection:
    return sqlite_util.rebuild(path, _connect_writable)


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


# ---- semantic near-duplicates remembered between runs ----------------------

# Bump when unpack_vector, mean_pool, sign_bits, hamming, norm or
# cosine_with_norms change what they compute: remembered sign bits and pairs
# then miss, are recomputed, and the old rows are pruned. The thresholds are
# part of the version already.
NEAR_SCHEME = 1


def vector_key(blobs: list[bytes]) -> str:
    """The identity of a note's pooled vector: a digest of its chunk
    vectors' bytes, in order. Everything the semantic tier decides about a
    note is a pure function of these bytes, so two runs that see the same
    key see the same vector, whatever the note's path, text or model."""
    h = hashlib.sha256()
    for blob in blobs:
        h.update(struct.pack("<Q", len(blob)))
        h.update(blob)
    return h.hexdigest()


def vector_bits_version() -> str:
    return hashlib.sha256(repr(("bits", NEAR_SCHEME)).encode("utf-8")).hexdigest()[:16]


def near_version(max_ham: int) -> str:
    """Everything a remembered pair depends on besides its two vectors."""
    material = repr(("near", NEAR_SCHEME, DUP_COSINE, DUP_HAMMING_FRAC, max_ham))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def semantic_pairs(
    notes: dict[str, list[bytes]], first: str, cache: SignatureCache | None = None,
) -> set[tuple[str, str]]:
    """Every (a, b), a <= b, of vector keys whose pooled vectors pass the
    sign-bit prefilter and reach DUP_COSINE; (a, a) when a note's vector is
    near itself, which matters when several notes share one key. `notes`
    maps vector_key -> the note's chunk vector blobs; `first` is the key
    whose dimension sets the prefilter margin.

    The answer is the all-pairs one, but only new keys are compared. A key
    with a remembered row was compared against every key present when its
    row was written; the row survives only while every later writing run
    still had that key (save prunes the rest), so of two remembered keys
    the later-written one has an answer for the pair. A key with no row is
    compared against every key present now, once per pair."""
    keys = sorted(notes)
    if not keys:
        return set()
    pooled: dict[str, list[float]] = {}
    norms: dict[str, float] = {}

    def vec(k: str) -> list[float]:
        v = pooled.get(k)
        if v is None:
            v = pooled[k] = mean_pool([unpack_vector(b) for b in notes[k]])
        return v

    def nrm(k: str) -> float:
        n = norms.get(k)
        if n is None:
            n = norms[k] = norm(vec(k))
        return n

    known_bits = cache.get_vector_bits(keys) if cache is not None else {}
    bits: dict[str, int] = {}
    dims: dict[str, int] = {}
    for k in keys:
        hit = known_bits.get(k)
        if hit is None:
            v = vec(k)
            hit = (len(v), sign_bits(v))
            if cache is not None:
                cache.put_vector_bits(k, *hit)
        dims[k], bits[k] = hit
    max_ham = int(dims[first] * DUP_HAMMING_FRAC)

    known = cache.get_near(keys, max_ham) if cache is not None else {}
    pairs: set[tuple[str, str]] = set()
    for k, partners in known.items():
        for p in partners:
            if p in known:  # a partner without a row is compared below
                pairs.add((min(k, p), max(k, p)))
    new = [k for k in keys if k not in known]
    new_set = set(new)
    found: dict[str, list[str]] = {k: [] for k in new}
    for k in new:
        for j in keys:
            if j in new_set and j < k:
                continue  # compared from j's side
            # The prefilter first: nearly every pair fails it.
            if hamming(bits[k], bits[j]) > max_ham:
                continue
            if cosine_with_norms(vec(k), vec(j), nrm(k), nrm(j)) >= DUP_COSINE:
                pairs.add((min(k, j), max(k, j)))
                found[k].append(j)
                if j in new_set and j != k:
                    found[j].append(k)
    if cache is not None:
        for k in new:
            cache.put_near(k, sorted(found[k]))
    return pairs


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
