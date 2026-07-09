"""Cheap change detection for the live dashboard.

The brain has no event bus: updates land through cron `brain cycle` / `brain
index`, which produce git commits and rewrite each vault's ``.brain/index.db``.
So instead of watching the filesystem, the server polls a `fingerprint` — a
snapshot of every git HEAD and index mtime the current lens depends on — a few
times a second. When the fingerprint changes, something wrote; the server
recollects stats and pushes them. `fingerprint` is sync, cheap (one
``git rev-parse`` + a few ``stat`` calls) and never raises: a missing repo or
db becomes ``None`` in the tuple, exactly as `stats._git_log` degrades.

`Lens` lives here — the lowest layer both the watcher and the aiohttp server
build on — so this module stays free of the web dependency and unit-testable on
its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Lens:
    """What the dashboard is looking at. ``vault`` for the user lens; ``master``
    (+ optional compiled ``out_root``) for the admin lens."""
    kind: str  # "vault" | "master"
    vault: Path | None = None
    master: Path | None = None
    out_root: Path | None = None


@dataclass(frozen=True)
class Fingerprint:
    git_heads: tuple[tuple[str, str | None], ...]
    index_mtimes: tuple[tuple[str, int | None], ...]
    promo_mtime: int | None  # master lens: promotions/pending dir mtime


def _git_head(repo: Path) -> str | None:
    """The commit HEAD points at, read straight from ``.git`` — no subprocess.

    The watcher polls this every couple of seconds for the life of the server;
    forking ``git rev-parse`` each tick is the one avoidable syscall-heavy line.
    Resolves a symbolic HEAD via its loose ref or ``packed-refs``, a detached
    HEAD's raw sha, and a ``.git`` *file* (worktree/submodule ``gitdir:``).
    Never raises: anything unexpected degrades to ``None``, like the old path."""
    try:
        git_dir = repo / ".git"
        if git_dir.is_file():
            text = git_dir.read_text().strip()
            if not text.startswith("gitdir:"):
                return None
            git_dir = repo / text[len("gitdir:"):].strip()
        head = (git_dir / "HEAD").read_text().strip()
        if not head.startswith("ref:"):
            return head or None  # detached HEAD: raw sha
        ref = head[4:].strip()
        loose = git_dir / ref
        if loose.is_file():
            return loose.read_text().strip() or None
        packed = git_dir / "packed-refs"
        if packed.is_file():
            for line in packed.read_text().splitlines():
                line = line.strip()
                if not line or line[0] in "#^":
                    continue
                sha, _, name = line.partition(" ")
                if name == ref:
                    return sha or None
        return None
    except OSError:
        return None


def _mtime_ns(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _index_db(vault: Path) -> Path:
    return vault / ".brain" / "index.db"


def fingerprint(lens: Lens) -> Fingerprint:
    git_heads: list[tuple[str, str | None]] = []
    index_mtimes: list[tuple[str, int | None]] = []
    promo_mtime: int | None = None

    if lens.kind == "vault" and lens.vault is not None:
        vault = Path(lens.vault)
        git_heads.append((str(vault), _git_head(vault)))
        db = _index_db(vault)
        index_mtimes.append((str(db), _mtime_ns(db)))
    elif lens.kind == "master" and lens.master is not None:
        master = Path(lens.master)
        git_heads.append((str(master), _git_head(master)))
        promo_mtime = _mtime_ns(master / "_meta" / "promotions" / "pending")
        if lens.out_root is not None:
            out_root = Path(lens.out_root)
            if out_root.is_dir():
                for child in sorted(out_root.iterdir()):
                    if child.is_dir():
                        db = _index_db(child)
                        index_mtimes.append((str(db), _mtime_ns(db)))

    return Fingerprint(
        git_heads=tuple(git_heads),
        index_mtimes=tuple(index_mtimes),
        promo_mtime=promo_mtime,
    )
