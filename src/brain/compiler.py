"""Vault compiler: (master, person) -> filtered vault. THE security boundary.

Builds into a temp sibling directory, then swaps in two renames: the previous
vault is renamed aside to `.{out}.old` before the new tree takes its place, so
no failure or crash ever destroys the previous output before the replacement
is in place (fail closed). A crash mid-swap leaves either the old vault intact
at `.old` (still recoverable) or the new vault live with `.git` still under
`.old` — both states are repaired automatically at the start of the next
compile. A person can only ever temporarily see LESS than they are allowed,
never more.

The manifest records the sha256 of every shipped file AFTER post-processing
(link stubbing, context generation). Write-back diffs against this baseline,
so per-person rewrites (stubbed links) never show up as phantom user edits.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from brain.resolver import readable_spaces
from brain.schemas import Person, SpaceRule

MANIFEST_NAME = ".brain-manifest.json"


@dataclass
class CompileResult:
    person_id: str
    files: list[str]  # rel paths of copied source files


def _iter_space_files(master: Path, space: str):
    # Invariant: symlinks never cross the tenant boundary — symlinked files
    # are skipped and symlinked directories are never descended, so a link
    # planted inside a readable space can't materialize an unreadable target.
    root = master / space
    if root.is_symlink():
        return
    rels: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            p = Path(dirpath) / name
            if p.is_symlink():
                continue
            rels.append(str(p.relative_to(master)))
    yield from sorted(rels)


def compile_vault(
    master: Path, person: Person, rules: tuple[SpaceRule, ...], out: Path
) -> CompileResult:
    spaces = readable_spaces(master, person, rules)
    building = out.parent / f".{out.name}.building"
    old = out.parent / f".{out.name}.old"

    # Recover from a previously crashed swap: if `.old` still exists, the last
    # run died mid-swap. If the crash hit before the new tree was promoted,
    # `out` is missing entirely — restore the whole previous vault (content
    # AND .git); this compile then replaces it via the normal two-phase swap.
    # If the new tree landed but lost its git history, move .git back. Either
    # way the tombstone is gone before we build.
    if old.exists():
        if not out.exists():
            old.rename(out)
        else:
            if (old / ".git").exists() and not (out / ".git").exists():
                shutil.move(str(old / ".git"), str(out / ".git"))
            shutil.rmtree(old)

    if building.exists():
        shutil.rmtree(building)
    building.mkdir(parents=True)

    compiled: list[str] = []
    try:
        for space in spaces:
            for rel in _iter_space_files(master, space):
                dest = building / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(master / rel, dest)
                compiled.append(rel)

        generated = _post_process(building, master, person, spaces, rules, compiled)

        # Hash what was actually shipped (post-stubbing); generated files are
        # tracked separately and never counted as user-editable baseline.
        compiled_hashes = {
            rel: hashlib.sha256((building / rel).read_bytes()).hexdigest()
            for rel in compiled
            if rel not in set(generated)
        }
        manifest = {
            "person": person.id,
            "compiled": compiled_hashes,
            "generated": generated,
        }
        (building / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2))

        # Two-phase swap: rename the previous vault aside, promote the new
        # tree, then move the per-person git history into it. The previous
        # output is never deleted before the replacement is in place; any
        # crash window leaves a state the recovery step above repairs.
        if out.exists():
            out.rename(old)
        shutil.move(str(building), str(out))
        if (old / ".git").exists():
            shutil.move(str(old / ".git"), str(out / ".git"))
        if old.exists():
            shutil.rmtree(old)
    finally:
        if building.exists():
            shutil.rmtree(building)

    return CompileResult(person_id=person.id, files=compiled)


def _post_process(
    building: Path,
    master: Path,
    person: Person,
    spaces: list[str],
    rules: tuple[SpaceRule, ...],
    compiled: list[str],
) -> list[str]:
    """Hook for link stubbing (Task 4) and context-file generation (Task 5).

    Returns the list of generated rel paths for the manifest.
    """
    return []
