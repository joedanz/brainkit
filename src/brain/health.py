"""The one file Fleet reads to learn what is inside a brain.

Written by every cycle, overwritten in place, and never committed: it lives
under `_meta/cache/`, which `brain init` gitignores (templates.py) because the
embedding cache already lives there. That placement is the whole safety
argument — README "Limitations" records that revoking access does not
un-deliver git history, so operational exhaust in a tracked path is permanent
and travels to every clone.

Payload is counts only. Messages and paths are dropped upstream in
`triage.count_findings`; this module's job is to keep the written shape closed
so a future field cannot quietly widen it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from brain.version import __version__

SCHEMA = 1
HEALTH_REL = "_meta/cache/health.json"

# The .gitignore line `brain init` writes (templates.py). Verified, never
# written: master's .gitignore is git-tracked, and appending to it would put an
# unrelated edit into whatever the cycle commits next.
_IGNORE_LINE = "_meta/cache/"


def _cache_is_ignored(master: Path) -> bool:
    try:
        lines = (master / ".gitignore").read_text().splitlines()
    except OSError:
        return False
    return any(line.strip() in (_IGNORE_LINE, "_meta/cache") for line in lines)


def write_health(
    master: Path,
    counts: dict[str, int],
    tamper: dict[str, int],
    *,
    now: str,
    duration_ms: int | None = None,
) -> bool:
    """Write the health snapshot. True if written, False if skipped.

    Fails closed: a master whose .gitignore does not cover `_meta/cache/`
    (one predating the template) gets no file rather than an untracked-but-
    committable one. Fleet reads that absence as "not reporting", which is the
    honest answer.
    """
    if not _cache_is_ignored(master):
        return False

    payload = {
        "schema": SCHEMA,
        "generated_at": now,
        "brainkit_version": __version__,
        "ok": not any(k.startswith("error:") for k in counts),
        "counts": counts,
        "tamper": tamper,
    }
    # How long the cycle that wrote this took, in milliseconds.
    #
    # It rides in the snapshot rather than only in the cycle's stdout because
    # the snapshot is the file Fleet already reads off the box — stdout goes to
    # a log nobody parses. A cycle outgrowing its own cron interval is a slow
    # failure (13m, then 20m, then 30m26s as an index outgrew the box's RAM)
    # and it was visible nowhere until runs began overlapping.
    #
    # Omitted rather than zeroed when unknown: a reader must be able to tell
    # "this cycle did not time itself" from "this cycle took no time".
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms

    target = master / HEALTH_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    # Atomic replace, matching the write posture used for tokens and the
    # compiler's two-phase swap: a reader never sees a torn file, and an
    # interrupted run leaves the previous snapshot intact rather than nothing.
    # Compact and single-line, deliberately: Fleet reads this file through a
    # sweep section that is line-oriented, and an indented object would reach
    # the parser as a lone "{" — unparseable, and indistinguishable from a box
    # that never published one. `jq .` restores readability for a human.
    tmp.write_text(json.dumps(payload, sort_keys=True) + "\n")
    os.replace(tmp, target)
    return True
