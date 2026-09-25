"""SQLite plumbing shared by the index, the stats readers and the caches.

Low-level on purpose: nothing here imports another brain module, so any of
them can use it without an import cycle.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import quote


def connect_readonly(path: Path) -> sqlite3.Connection:
    """Open an existing database read-only. Never creates the file, never
    writes to it; a missing file raises sqlite3.OperationalError.

    URI filenames must be percent-encoded (spaces, '?', '#'); '/' and ':'
    stay literal so absolute paths survive."""
    return sqlite3.connect(f"file:{quote(str(path), safe='/:')}?mode=ro", uri=True)
