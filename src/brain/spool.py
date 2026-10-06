"""Notes captured over remote MCP, waiting for the cycle to file them.

The per-person MCP process must not write into the compiled vault: a raw write
dirties its git tree (the person's agent pushes bounce until the next cycle),
and a capture landing between the cycle's final write-back and the compile's
directory swap would be lost. So it drops a small JSON envelope here instead,
and ``brain cycle --spool-root`` files each one into master through
``ingest_note`` — the same path, write check and single-file commit the webhook
uses. Only the cycle ever touches master.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime
from pathlib import Path

from brain.errors import BrainError

ENVELOPE_VERSION = 1
MAX_TEXT = 20_000
MAX_TITLE = 120
MAX_PENDING = 200
REJECTED_DIR = ".rejected"
_TMP_DIR = ".tmp"


class SpoolError(BrainError, ValueError):
    """A capture the spool refuses; the message is shown to the person."""


def pending_count(spool: Path) -> int:
    return sum(1 for p in spool.glob("*.json") if p.is_file())


def write_envelope(spool: Path, person: str, *, text: str, title: str,
                   now: datetime) -> str:
    """Queue one capture atomically; return its file name. ``now`` is UTC."""
    if not isinstance(text, str) or not text.strip():
        raise SpoolError("nothing to capture — the text is empty")
    if len(text) > MAX_TEXT:
        raise SpoolError(f"the text is {len(text)} characters; the limit is {MAX_TEXT}")
    title = (title or "").strip()
    if "\n" in title or "\r" in title:
        raise SpoolError("the title must be a single line")
    if len(title) > MAX_TITLE:
        raise SpoolError(f"the title is longer than {MAX_TITLE} characters")
    if pending_count(spool) >= MAX_PENDING:
        raise SpoolError("capture queue full — try again after the next sync")

    name = f"{now:%Y%m%dT%H%M%S}Z-{secrets.token_hex(4)}.json"
    envelope = {"version": ENVELOPE_VERSION, "person": person, "title": title,
                "body": text, "source": "mcp", "created": now.date().isoformat()}
    tmp_dir = spool / _TMP_DIR
    tmp_dir.mkdir(mode=0o700, exist_ok=True)
    tmp = tmp_dir / name
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(envelope, f)
    os.replace(tmp, spool / name)   # a reader never sees half a file
    return name
