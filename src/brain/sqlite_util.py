"""SQLite plumbing shared by the index, the stats readers and the caches.

Low-level on purpose: nothing here imports another brain module, so any of
them can use it without an import cycle.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote


def connect_readonly(path: Path) -> sqlite3.Connection:
    """Open an existing database read-only. Never creates the file, never
    writes to it; a missing file raises sqlite3.OperationalError.

    URI filenames must be percent-encoded (spaces, '?', '#'); '/' and ':'
    stay literal so absolute paths survive."""
    return sqlite3.connect(f"file:{quote(str(path), safe='/:')}?mode=ro", uri=True)


def is_damaged(e: sqlite3.Error) -> bool:
    """A file that is not, or is no longer, a readable database — as opposed
    to one that is only busy or locked, which the next run can read fine."""
    code = getattr(e, "sqlite_errorcode", None)
    return code is not None and code & 0xFF in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB)


def rebuild(path: Path, connect: Callable[[Path], sqlite3.Connection]) -> sqlite3.Connection:
    """Delete a damaged database and any journal beside it — nothing else in
    its folder — and start empty through `connect`."""
    for suffix in ("", "-journal", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    return connect(path)
