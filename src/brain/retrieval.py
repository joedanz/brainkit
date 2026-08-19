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
import json
import os
from collections.abc import Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from brain.version import __version__

SCHEMA = 1
STATS_NAME = "retrieval-stats.json"
LOCK_NAME = "retrieval-stats.lock"
SENTINEL_NAME = "retrieval-log.on"
RAW_NAME = "retrieval.jsonl"
RAW_CAP_BYTES = 5 * 1024 * 1024

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


def _append_raw(
    brain_dir: Path,
    *,
    query: str,
    mode: str,
    hit_locations: Sequence[tuple[str, str]],
    now: str,
) -> bool:
    """Append one line. True means the cap stopped it.

    The cap is checked BEFORE appending, so the file never exceeds it. Hitting
    it is reported through `raw_truncated` rather than silently dropping the
    line — the rule the corrections budget already follows.

    Only rel_path and space per hit. A snippet is note content.
    """
    raw = brain_dir / RAW_NAME
    try:
        if raw.is_file() and raw.stat().st_size >= RAW_CAP_BYTES:
            return True
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
        return False
    return False


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
        truncated = bool(data.get("raw_truncated", False))
        if raw_on and query is not None:
            truncated = _append_raw(
                brain_dir, query=query, mode=mode,
                hit_locations=hit_locations, now=now,
            ) or truncated

        payload = {
            "schema": SCHEMA,
            "person": _person(vault),
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
