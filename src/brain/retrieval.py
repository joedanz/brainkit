"""Retrieval telemetry: what the brain returned when it was asked.

Written by every search into ``<vault>/.brain/``, which the compiled vault
gitignores (contextgen.py), write-back skips (writeback.py — top-level dot
entries are outside every space), and the compiler carries across its
two-phase swap (compiler.py). A full reindex unlinks only ``index.db`` and its
``-wal``/``-shm`` siblings, so nothing here is disturbed by the 300-second
vault-sync loop.

Payload is counts only. The ``no index at {db}`` warning interpolates a
filesystem path, so warnings are counted by CATEGORY and their text is never
stored. Fleet re-filters on read; two independent redactions, as with health.
"""

from __future__ import annotations

import fcntl
import gzip
import json
import os
import shutil
from collections.abc import Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from brain.version import __version__

SCHEMA = 1
STATS_NAME = "retrieval-stats.json"
LOCK_NAME = "retrieval-stats.lock"
# Separate from LOCK_NAME on purpose — see _compress_rotated. It serialises
# compression itself, which runs outside the stats lock.
ROTATE_LOCK_NAME = "retrieval-rotate.lock"
SENTINEL_NAME = "retrieval-log.on"
RAW_NAME = "retrieval.jsonl"

# Rotate rather than stop: the useful window is the NEWEST searches, and
# stopping at a cap keeps the oldest. Seven gzipped segments plus the live file
# hold roughly four times what the old 5 MB cap did, in less disk — query logs
# compress about 10x, so compression buys space while the segment count sets
# retention. The set is bounded on purpose; an unbounded archive of everything
# anyone typed at their brain is a different product decision.
ROTATE_AT_BYTES = 2_500_000
RAW_SEGMENTS = 7
ROTATING_NAME = "retrieval.jsonl.rotating"

NO_INDEX = "no-index"
VECTOR_DEGRADED = "vector-degraded"


def _brain_dir(vault: Path) -> Path:
    return Path(vault) / ".brain"


@contextmanager
def _locked(brain_dir: Path):
    """Exclusive access for a read-modify-write.

    The lock is its own file, never the stats file: the write below finishes
    with os.replace, which swaps in a NEW inode — a second process holding a
    lock on the old inode would not be excluded at all.
    """
    brain_dir.mkdir(parents=True, exist_ok=True)
    with open(brain_dir / LOCK_NAME, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _load(stats: Path) -> dict:
    """Previous counters, or empty. A corrupt file restarts them rather than
    failing the search that found it."""
    try:
        data = json.loads(stats.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _person(vault: Path) -> str | None:
    try:
        data = json.loads((vault / ".brain-manifest.json").read_text())
    except (OSError, ValueError):
        return None
    pid = data.get("person") if isinstance(data, dict) else None
    return pid if isinstance(pid, str) and pid else None


def _warn_keys(mode: str, warnings: Sequence[str]) -> list[str]:
    """Warning categories, never messages.

    `search_index` emits exactly two warnings. The no-index early return emits
    one AND sets mode="", so `mode == ""` identifies that case completely —
    and it is already counted in by_mode, so counting it here too would
    double-count one search. Every other warning comes from
    `store.vector_status`.
    """
    if mode == "":
        return []
    return [VECTOR_DEGRADED] if warnings else []


def _mode_key(mode: str) -> str:
    return NO_INDEX if mode == "" else mode


def _sentinel_since(sentinel: Path) -> str | None:
    """When raw logging was switched on, from the sentinel's mtime.

    Not derived from the injected `now`: this is a filesystem fact, and it is
    what lets fleet escalate a switch that has been left on.
    """
    try:
        ts = sentinel.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def segment_path(brain_dir: Path, n: int) -> Path:
    """Gzipped segment n. 1 is the most recent; RAW_SEGMENTS is the oldest."""
    return brain_dir / f"{RAW_NAME}.{n}.gz"


def _has_wrapped(brain_dir: Path) -> bool:
    """True once the segment set is full — the next rotation discards the
    oldest. This is what `raw_truncated` now reports."""
    return segment_path(brain_dir, RAW_SEGMENTS).is_file()


def _compress_rotated(brain_dir: Path, rotated: Path) -> None:
    """Compress `rotated`, then shift the segments and install it in slot 1.

    Runs OUTSIDE the stats lock. Gzipping 2.5 MB takes ~100 ms and `record()`
    is on the path every search in the product takes; holding the stats lock
    through that would stall every concurrent search. Safe with respect to
    THAT lock because `rotated` was renamed out of the way and no appender
    can reach it.

    But nothing about being outside the stats lock serialises compression
    against a SECOND process compressing the very same file. `_append_raw`'s
    orphan-recovery branch (meant for a crash between rename and compress)
    fires just as readily on an ordinary in-progress rotation: while process A
    is here compressing, a concurrent `record()` in process B sees `rotated`
    still on disk, treats it as an orphan, and hands the identical path back
    as its own `pending` — so a second `_compress_rotated` call starts on the
    same source file. Without serialising, both would shift the segment set
    (two shifts for one rotation, silently evicting a real segment) and both
    would write the same temp path (interleaved bytes, a corrupt segment).
    The exclusive, non-blocking `ROTATE_LOCK_NAME` lock below closes that:
    whichever process loses the race returns immediately rather than
    duplicate the work — the winner is already doing it, so nothing is lost.
    A pid-suffixed temp filename is belt-and-braces on top of the lock.

    The gzip write happens into a TEMP file, in the same directory as the
    segments, before anything in the segment set moves. `gzip.open(path,
    "wb")` creates its destination eagerly, so writing straight into slot 1
    and failing partway (disk full, etc.) would still leave a well-formed
    ~12-byte gzip header there — a corrupt-but-present segment that looks
    like a real one to the next rotation, which would promote it into slot 2,
    then 3, and so on, walking every real segment out of the set over
    RAW_SEGMENTS retries while `_has_wrapped()` kept reporting a full, healthy
    set throughout. Compressing to a temp file first means the only fallible
    step happens before any existing segment is touched: on failure nothing
    in the segment set has moved, the orphan `rotated` is untouched, and the
    next search retries from unchanged state.
    """
    with open(brain_dir / ROTATE_LOCK_NAME, "a+") as lock_fh:
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            # Another process is already compressing this exact file. The
            # work is being done, not lost — nothing to do here.
            return
        tmp = brain_dir / f"{RAW_NAME}.{os.getpid()}.gz.tmp"
        try:
            with open(rotated, "rb") as fin, gzip.open(tmp, "wb") as fout:
                shutil.copyfileobj(fin, fout)
            segment_path(brain_dir, RAW_SEGMENTS).unlink(missing_ok=True)
            for n in range(RAW_SEGMENTS - 1, 0, -1):
                src = segment_path(brain_dir, n)
                if src.is_file():
                    src.rename(segment_path(brain_dir, n + 1))
            os.replace(tmp, segment_path(brain_dir, 1))
            rotated.unlink(missing_ok=True)
        except OSError:
            # The fallible step (the gzip write) ran first, so nothing in the
            # segment set has moved yet. Drop the temp file so it can't
            # accumulate or be mistaken for a segment, and leave `rotated` in
            # place; the next rotation recovers it.
            tmp.unlink(missing_ok=True)
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)


def _append_raw(
    brain_dir: Path,
    *,
    query: str,
    mode: str,
    hit_locations: Sequence[tuple[str, str]],
    now: str,
) -> Path | None:
    """Append one line, rotating first when the live log is full.

    Returns a file awaiting compression, or None. Rotation here is a RENAME
    only — O(1), microseconds — because this runs inside the exclusive lock.
    `record()` compresses after releasing it.

    Only rel_path and space per hit. A snippet is note content.
    """
    raw = brain_dir / RAW_NAME
    rotating = brain_dir / ROTATING_NAME
    pending: Path | None = None
    try:
        if rotating.is_file():
            # An orphan from a crash between the rename and the compression. It
            # holds real searches, so hand it back to be compressed and skip
            # this round's rotation rather than clobbering it; the live log
            # rotates next time.
            pending = rotating
        elif raw.is_file() and raw.stat().st_size >= ROTATE_AT_BYTES:
            os.replace(raw, rotating)
            pending = rotating
        line = json.dumps(
            {
                "at": now,
                "query": query,
                "mode": mode,
                "hits": [{"rel_path": r, "space": s} for r, s in hit_locations],
            },
            sort_keys=True,
        )
        with open(raw, "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    return pending


def record(
    vault: Path,
    *,
    mode: str,
    hits: int,
    warnings: Sequence[str],
    now: str,
    query: str | None = None,
    hit_locations: Sequence[tuple[str, str]] = (),
) -> None:
    """Count one search. `now` is injected so tests are not clock-dependent."""
    vault = Path(vault)
    brain_dir = _brain_dir(vault)
    pending_rotation: Path | None = None
    with _locked(brain_dir):
        stats_path = brain_dir / STATS_NAME
        data = _load(stats_path)

        by_mode = dict(data.get("by_mode") or {})
        key = _mode_key(mode)
        by_mode[key] = int(by_mode.get(key, 0) or 0) + 1

        warn = dict(data.get("warn") or {})
        for wk in _warn_keys(mode, warnings):
            warn[wk] = int(warn.get(wk, 0) or 0) + 1

        sentinel = brain_dir / SENTINEL_NAME
        raw_on = sentinel.is_file()
        if raw_on and query is not None:
            pending_rotation = _append_raw(
                brain_dir, query=query, mode=mode,
                hit_locations=hit_locations, now=now,
            )
        # Reports that the segment set is full, so the next rotation discards
        # the oldest — NOT that capture stopped, which is what the old cap did.
        # Gated on raw_on: segments left behind by a PAST capture must not
        # pin this true forever once the sentinel is removed — "a badge that
        # can never clear stops being information" (the deleted cap-era
        # comment's own argument, reintroduced here by a different route if
        # this were unconditional).
        truncated = raw_on and _has_wrapped(brain_dir)

        payload = {
            "schema": SCHEMA,
            # Read the manifest only when we have not already recorded who this
            # vault belongs to. It carries a sha256 per compiled file, so it
            # grows with the vault — and parsing all of it on every search,
            # inside the lock, to re-derive a string that cannot change (the
            # vault directory IS named by person id) is pure waste.
            "person": data.get("person") or _person(vault),
            "brainkit_version": __version__,
            "updated_at": now,
            "searches": int(data.get("searches", 0) or 0) + 1,
            "zero_hit": int(data.get("zero_hit", 0) or 0) + (1 if hits == 0 else 0),
            "by_mode": by_mode,
            "warn": warn,
            "raw_log": raw_on,
            "raw_log_since": _sentinel_since(sentinel) if raw_on else None,
            "raw_truncated": truncated,
        }

        tmp = stats_path.with_suffix(".json.tmp")
        # Compact and single-line, deliberately: fleet reads this through a
        # line-oriented sweep section, and an indented object would reach the
        # parser as a lone "{".
        tmp.write_text(json.dumps(payload, sort_keys=True) + "\n")
        os.replace(tmp, stats_path)

    # Outside the lock, deliberately: see _compress_rotated.
    if pending_rotation is not None:
        _compress_rotated(brain_dir, pending_rotation)
