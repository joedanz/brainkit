"""Write-back: validate a person's vault edits server-side and apply to master.

Client trust is never assumed: every changed path is checked against the
person's write permissions here, regardless of what the sync client allowed.
One out-of-scope change rejects the whole change set.

Diffs run against the manifest's hash baseline (what the compiler shipped),
never live master bytes: compiler rewrites such as stubbed links would
otherwise appear as phantom user edits, and a master that moved on since
compile resolves last-write-wins per the spec.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from brain.compiler import MANIFEST_NAME
from brain.resolver import can_write_path
from brain.schemas import Person, SpaceRule


@dataclass
class Change:
    path: str
    kind: str  # "add" | "modify" | "delete"


@dataclass
class WritebackResult:
    applied: list[Change] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)


class ManifestError(ValueError):
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


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def diff_vault(vault: Path) -> list[Change]:
    manifest = _load_manifest(vault)
    baseline: dict[str, str] = manifest["compiled"]  # rel path -> sha256
    generated = set(manifest["generated"]) | {MANIFEST_NAME}

    changes: list[Change] = []
    present: set[str] = set()
    for f in sorted(vault.rglob("*")):
        if ".git" in f.parts:
            continue
        # Symlinks never cross the tenant boundary (same invariant as the
        # compiler): a client-planted link would otherwise leak its TARGET
        # bytes into master. Skipping links also means a baseline path that
        # is now a symlink falls through to the delete pass below — the real
        # file is gone.
        if f.is_symlink() or not f.is_file():
            continue
        rel = str(f.relative_to(vault))
        if rel in generated:
            continue
        present.add(rel)
        if rel not in baseline:
            changes.append(Change(rel, "add"))
        elif _sha(f.read_bytes()) != baseline[rel]:
            changes.append(Change(rel, "modify"))
    for rel in sorted(set(baseline) - present):
        changes.append(Change(rel, "delete"))
    return changes


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )


def apply_writeback(
    master: Path, vault: Path, person: Person, rules: tuple[SpaceRule, ...]
) -> WritebackResult:
    changes = diff_vault(vault)
    violations = [
        f"{c.kind} {c.path}: outside write scope for {person.id}"
        for c in changes
        if not can_write_path(c.path, person, rules)
    ]
    if violations:
        return WritebackResult(applied=[], violations=violations)
    if not changes:
        return WritebackResult()

    for c in changes:
        target = master / c.path
        if c.kind == "delete":
            target.unlink(missing_ok=True)
        else:
            src = vault / c.path
            if src.is_symlink():
                # Defense in depth: diff_vault never emits symlinks, so this
                # only fires if the vault changed between diff and apply.
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(src.read_bytes())

    _git(master, "add", "-A")
    # The change set can net to zero delta against master's current state
    # (e.g. last-write-wins converged: vault bytes already equal master's,
    # or a delete of a file master no longer has). git commit exits 1 on an
    # empty index, so only commit when something is actually staged.
    if _git(master, "status", "--porcelain").stdout.strip():
        _git(
            master,
            "-c", f"user.name={person.name}",
            "-c", f"user.email={person.id}@brain.local",
            "commit", "-m", f"writeback: {person.id} ({len(changes)} change(s))",
        )
    return WritebackResult(applied=changes)
