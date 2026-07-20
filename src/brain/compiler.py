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
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath

from brain.resolver import readable_spaces
from brain.schemas import Org, Person, SpaceRule

MANIFEST_NAME = ".brain-manifest.json"

WIKILINK_RE = re.compile(
    r"!?\[\[([^\][|#]+)(#[^\][|]*)?(\|([^\][]+))?\]\]"
)


def _stem(target: str) -> str:
    return PurePosixPath(target.strip()).stem.lower()


def extract_wikilinks(text: str) -> list[str]:
    """Raw wikilink targets in order of appearance, heading and alias stripped.
    Embeds (``![[...]]``) count as links."""
    return [m.group(1).strip() for m in WIKILINK_RE.finditer(text)]


def stub_links(text: str, included_stems: set[str], master_stems: set[str]) -> str:
    def repl(m: re.Match) -> str:
        target, alias = m.group(1), m.group(4)
        stem = _stem(target)
        if stem in included_stems or stem not in master_stems:
            return m.group(0)
        return (alias or target).strip()

    return WIKILINK_RE.sub(repl, text)


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
    master: Path,
    person: Person,
    rules: tuple[SpaceRule, ...],
    out: Path,
    today: str | None = None,
) -> CompileResult:
    today = today or date.today().isoformat()
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
            for keep in (".git", ".brain"):
                if (old / keep).exists() and not (out / keep).exists():
                    shutil.move(str(old / keep), str(out / keep))
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

        generated = _post_process(
            building, master, person, spaces, rules, compiled, today
        )

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
        # Machine-local state (git history, the .brain search index) lives in
        # the vault but is never compiled output; carry it across the swap.
        for keep in (".git", ".brain"):
            if (old / keep).exists():
                shutil.move(str(old / keep), str(out / keep))
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
    today: str,
) -> list[str]:
    """Hook for link stubbing (Task 4) and context-file generation (Task 5).

    Returns the list of generated rel paths for the manifest.
    """
    from brain.resolver import can_write_path

    included_stems = {
        PurePosixPath(rel).stem.lower() for rel in compiled if rel.endswith(".md")
    }
    master_stems = {
        p.stem.lower()
        for p in master.rglob("*.md")
        if ".git" not in p.parts and "_meta" not in p.parts
    }
    for rel in compiled:
        if rel.endswith(".md") and not can_write_path(rel, person, rules):
            f = building / rel
            f.write_text(stub_links(f.read_text(), included_stems, master_stems))

    from brain.contextgen import generate_context_files

    generated = generate_context_files(building, person, spaces, rules)

    from brain.promotions import SHARES_NOTE_REL, generate_shares_note

    note = generate_shares_note(master, person.id, today)
    if note is not None:
        rel = SHARES_NOTE_REL.format(person_id=person.id)
        dest = building / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(note)
        generated.append(rel)
    return generated


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )


def compile_all(
    master: Path,
    org: Org,
    rules: tuple[SpaceRule, ...],
    out_root: Path,
    today: str | None = None,
) -> list[CompileResult]:
    today = today or date.today().isoformat()
    results = []
    for person in org.people.values():
        out = out_root / person.id
        result = compile_vault(master, person, rules, out, today)
        if not (out / ".git").exists():
            _git(out, "init", "-b", "main")
        _git(out, "add", "-A")
        status = _git(out, "status", "--porcelain").stdout
        if status.strip():
            _git(
                out,
                "-c", "user.name=Brain Compiler",
                "-c", "user.email=compiler@brain.local",
                "commit", "-m", f"compile: refresh vault for {person.id}",
            )
        results.append(result)
    return results
