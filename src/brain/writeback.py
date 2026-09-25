"""Write-back: validate a person's vault edits server-side and apply to master.

Client trust is never assumed: every changed path is checked against the
person's write permissions here, regardless of what the sync client allowed.
Each change is checked on its own: in-scope changes are applied and
committed (only those paths), out-of-scope ones are held and reported, never
applied. A git or disk failure restores the master paths it touched and is
reported, never raised.

Diffs run against the manifest's hash baseline (what the compiler shipped),
never live master bytes: compiler rewrites such as stubbed links would
otherwise appear as phantom user edits, and a master that moved on since
compile resolves last-write-wins per the spec.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from brain.compiler import HELD_NAME, MANIFEST_NAME, SERVER_ONLY_NAMES
from brain.errors import BrainError, describe
from brain.resolver import can_write_path
from brain.schemas import DEFAULT_SHARED, Person, SpaceRule

# OS clutter a desktop sync drops next to real notes. Matched on the basename
# at any depth and skipped entirely: never applied, held, or reported. Fixed on
# purpose; a configurable list is one more thing that can hide a real file.
JUNK_NAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})


def is_junk(name: str) -> bool:
    return name in JUNK_NAMES or name.startswith("._") or name.endswith(("~", ".swp"))


HELD_REL = f"People/{{person_id}}/{HELD_NAME}"
NOTICE_REL = "People/{person_id}/Inbox/held-edits.md"


@dataclass
class Change:
    path: str
    kind: str  # "add" | "modify" | "delete"
    # The bytes the diff read and hashed. Apply writes exactly these, never a
    # second read of the vault, so what lands is what was checked.
    data: bytes | None = field(default=None, repr=False, compare=False)
    sha: str | None = field(default=None, compare=False)


class Held(NamedTuple):
    kind: str
    path: str
    reason: str


@dataclass
class WritebackResult:
    applied: list[Change] = field(default_factory=list)
    held: list[Held] = field(default_factory=list)
    error: str = ""  # set when a git/IO failure stopped the apply; master restored


class ManifestError(BrainError, ValueError):
    """The compiled vault's manifest is missing, unreadable, or the wrong shape.

    Write-back diffs against the manifest baseline; without a usable one there
    is no trustworthy baseline, so we refuse rather than guess. Callers surface
    this as a handled error (never a raw traceback): the standalone `writeback`
    command exits non-zero with a message, and `brain cycle` skips just that
    person so one corrupt vault can't abort everyone else's refresh.
    """


def _load_manifest(vault: Path) -> dict:
    path = vault / MANIFEST_NAME
    try:
        manifest = json.loads(path.read_text())
    except FileNotFoundError as e:
        raise ManifestError(f"{vault.name}: manifest missing ({e})") from e
    except ValueError as e:  # includes json.JSONDecodeError
        raise ManifestError(f"{vault.name}: manifest is not valid JSON ({e})") from e
    if not isinstance(manifest, dict) or "compiled" not in manifest or "generated" not in manifest:
        raise ManifestError(
            f"{vault.name}: manifest is the wrong shape "
            "(missing 'compiled'/'generated')")
    return manifest


def shared_of(manifest: dict) -> str:
    """The shared top-level space name a compiled vault was built with.

    Absent key means the vault predates the setting (or uses the default),
    which is the same thing: the default name. A non-string is treated as
    absent rather than trusted into a path parse.
    """
    value = manifest.get("shared", DEFAULT_SHARED)
    return value if isinstance(value, str) and value else DEFAULT_SHARED


def vault_shared(vault: Path) -> str:
    """`shared_of` for callers holding only a path. The one seam vault-side
    entry points (server, MCP) use to learn the name once per request and
    pass it down as a string — no leaf module reads the manifest itself.
    An unreadable manifest yields the default: this is a naming lookup, not
    a permission check, and the callers that DO need a trustworthy manifest
    (write-back) still raise through `_load_manifest`.
    """
    try:
        return shared_of(_load_manifest(vault))
    except ManifestError:
        return DEFAULT_SHARED


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_nofollow(f: Path) -> bytes | None:
    """The file's bytes, or None if it became a symlink after the walk saw it."""
    try:
        fd = os.open(f, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as e:
        if e.errno == errno.ELOOP:
            return None
        raise
    with os.fdopen(fd, "rb") as fh:
        return fh.read()


def diff_vault(vault: Path, manifest: dict | None = None) -> list[Change]:
    manifest = _load_manifest(vault) if manifest is None else manifest
    baseline: dict[str, str] = manifest["compiled"]  # rel path -> sha256
    generated = set(manifest["generated"]) | {MANIFEST_NAME}

    changes: list[Change] = []
    present: set[str] = set()
    for f in sorted(vault.rglob("*")):
        # Symlinks never cross the tenant boundary (same invariant as the
        # compiler): a client-planted link would otherwise leak its TARGET
        # bytes into master. Skipping links also means a baseline path that
        # is now a symlink falls through to the delete pass below — the real
        # file is gone.
        if f.is_symlink() or not f.is_file():
            continue
        rel = str(f.relative_to(vault))
        # Top-level dot-entries (.git, .brain index, .obsidian config) are
        # outside every space — resolver.space_of_path returns None for them,
        # so they can never be a legal write. Ignore them here rather than let
        # them surface as out-of-scope changes.
        if rel.split("/", 1)[0].startswith("."):
            continue
        if rel in generated or is_junk(f.name) or f.name in SERVER_ONLY_NAMES:
            continue
        data = _read_nofollow(f)
        if data is None:
            continue
        present.add(rel)
        sha = _sha(data)
        if rel not in baseline:
            changes.append(Change(rel, "add", data, sha))
        elif sha != baseline[rel]:
            changes.append(Change(rel, "modify", data, sha))
    for rel in sorted(set(baseline) - present):
        if is_junk(PurePosixPath(rel).name):
            continue
        changes.append(Change(rel, "delete"))
    return changes


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )


def commit_paths(repo: Path, paths: list[str], *, name: str, email: str,
                 message: str) -> bool:
    """Stage and commit exactly `paths`, as `name`. Nothing else in the index
    or worktree is touched, so an admin's uncommitted edit elsewhere never
    lands under someone else's name. Paths are literal (no glob or pathspec
    magic). Returns False, with no commit, when nothing differs from HEAD."""
    if not paths:
        return False
    lit = "--literal-pathspecs"
    present = [p for p in paths if (repo / p).is_file()]
    gone = [p for p in paths if p not in set(present)]
    if present:
        _git(repo, lit, "add", "--", *present)
    if gone:
        _git(repo, lit, "rm", "-q", "--cached", "--ignore-unmatch", "--", *gone)
    out = _git(repo, lit, "diff", "--cached", "--name-only", "-z", "--", *paths).stdout
    staged = [p for p in out.split("\0") if p]
    if not staged:
        return False
    _git(repo, lit, "-c", f"user.name={name}", "-c", f"user.email={email}",
         "commit", "-q", "-m", message, "--", *staged)
    return True


def _snapshot(master: Path, rels: list[str]) -> dict[str, bytes | None]:
    snap: dict[str, bytes | None] = {}
    for rel in rels:
        p = master / rel
        snap[rel] = p.read_bytes() if p.is_file() and not p.is_symlink() else None
    return snap


def _restore(master: Path, snap: dict[str, bytes | None]) -> None:
    """Best effort: put every touched master path back as it was and unstage
    it. Never raises — it runs while reporting a failure already."""
    for rel, data in snap.items():
        p = master / rel
        try:
            if data is None:
                p.unlink(missing_ok=True)
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
        except OSError:
            pass
    with contextlib.suppress(OSError, subprocess.CalledProcessError):
        _git(master, "--literal-pathspecs", "reset", "-q", "--", *snap)


_UNSEEN = object()


def apply_writeback(
    master: Path, vault: Path, person: Person, rules: tuple[SpaceRule, ...],
    *, already: Mapping[str, str | None] | None = None,
) -> WritebackResult:
    manifest = _load_manifest(vault)
    shared = shared_of(manifest)
    changes = diff_vault(vault, manifest)
    if already:
        # A second pass in the same cycle diffs against the same baseline, so
        # it re-sees every change the first pass applied. Re-applying them
        # would resurrect files the sweeps have since consumed (promotion
        # drafts, share and client requests). Only NEW changes go through.
        changes = [c for c in changes if already.get(c.path, _UNSEEN) != c.sha]
    to_apply: list[Change] = []
    held: list[Held] = []
    for c in changes:
        # Defense in depth: only bytes the diff read and hashed are ever
        # written; a non-delete change without them is dropped.
        if c.kind != "delete" and c.data is None:
            continue
        if can_write_path(c.path, person, rules, shared=shared):
            to_apply.append(c)
        else:
            held.append(Held(c.kind, c.path, f"outside write scope for {person.id}"))
    if not to_apply:
        return WritebackResult(held=held)

    touched = [c.path for c in to_apply]
    # Deleting the held-edits notice is how a person dismisses a hold; the
    # record goes in the same commit.
    record = HELD_REL.format(person_id=person.id)
    notice = NOTICE_REL.format(person_id=person.id)
    dismiss = (any(c.kind == "delete" and c.path == notice for c in to_apply)
               and (master / record).is_file())
    if dismiss:
        touched.append(record)
    snap = _snapshot(master, touched)
    applied: list[Change] = []
    try:
        for c in to_apply:
            target = master / c.path
            if c.kind == "delete":
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(c.data)
            applied.append(c)
        if dismiss:
            (master / record).unlink(missing_ok=True)
        # The change set can net to zero against master (last-write-wins
        # converged, or a delete of a file master no longer has); then
        # commit_paths makes no commit.
        commit_paths(
            master, touched,
            name=person.name, email=f"{person.id}@brain.local",
            message=f"writeback: {person.id} ({len(applied)} change(s))",
        )
    except (OSError, subprocess.CalledProcessError) as e:
        _restore(master, snap)
        return WritebackResult(held=held, error=describe(e))
    return WritebackResult(applied=applied, held=held)
