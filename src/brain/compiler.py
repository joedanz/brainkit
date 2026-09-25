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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from brain.errors import HANDLED, BrainError, describe
from brain.resolver import readable_spaces
from brain.schemas import DEFAULT_SHARED, Org, Person, SpaceRule, VaultConfig, load_config

if TYPE_CHECKING:
    from brain.promotions import Promotion

MANIFEST_NAME = ".brain-manifest.json"


def write_manifest(path: Path, manifest: dict) -> None:
    """Serialize a vault manifest the one way every writer must agree on, so
    a rewrite (e.g. clearing the busy key) reproduces the committed bytes
    exactly."""
    path.write_text(json.dumps(manifest, indent=2))


# A person's hold record (People/<id>/.held.json): server-side bookkeeping
# about edits write-back could not apply. Never compiled into any vault, so
# nobody can edit or delete their own record through a sync.
HELD_NAME = ".held.json"

# A person's correction confirmations (People/<id>/.corrections.json): which
# rule text they confirmed. Server-only for the same reason as the hold
# record: an agent that could write it could confirm its own rules.
CONFIRMED_NAME = ".corrections.json"

# Filenames that are server-side bookkeeping and never shipped to a vault.
SERVER_ONLY_NAMES = frozenset({HELD_NAME, CONFIRMED_NAME})

WIKILINK_RE = re.compile(
    r"!?\[\[([^\][|#]+)(#[^\][|]*)?(\|([^\][]+))?\]\]"
)


def _stem(target: str) -> str:
    """The matching key for a note path or a wikilink target: the final path
    component, lowercased, with only a trailing ``.md`` removed.

    Not ``PurePosixPath.stem`` — that treats the last period as an extension,
    so a link to ``Amendment No. 3`` became ``amendment no`` and never matched
    the file ``Amendment No. 3.md`` (whose stem keeps the period). Titles hold
    periods routinely (``v.``, ``No.``, ``Inc.``); only ``.md`` is a suffix.
    """
    name = PurePosixPath(target.strip()).name
    if name.lower().endswith(".md"):
        name = name[:-3]
    return name.lower()


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


class CompileError(BrainError):
    """Part of a fleet compile failed.

    compile_all keeps going past a person whose vault fails to build, so one
    oversized protocol or unreadable note cannot stop everyone else's
    refresh; 0.6.x stopped at the first failure. A vault is swapped in only
    once it is fully built, so none is ever half-written: a build failure
    leaves that person's previous vault in place, and a failure in the git
    step after the swap leaves the new build there, uncommitted. `failures`
    names each failed person with the reason; `completed` is everyone who
    was refreshed.
    """

    def __init__(self, failures: list[tuple[str, str]],
                 completed: list[CompileResult], total: int) -> None:
        self.failures = tuple(failures)
        self.completed = tuple(completed)
        self.total = total
        lines = [f"compiling {pid}: {why}" for pid, why in self.failures]
        lines.append(
            f"  {len(self.completed)} of {total} vault(s) refreshed; "
            f"{len(self.failures)} failed (vaults swap atomically, so none is "
            f"half-written)")
        super().__init__("\n".join(lines))


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
            if name in SERVER_ONLY_NAMES:
                continue
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
    config: VaultConfig | None = None,
    org: Org | None = None,
    pending: list[Promotion] | None = None,
) -> CompileResult:
    """Compile one person's vault.

    ``org`` and ``pending`` are the parsed roster and promotion queue when the
    caller already holds them — a fleet compile parses each once and hands the
    same objects to every person, instead of each vault re-reading an
    O(org)-sized org.yaml and re-globbing the whole queue. Both optional: a
    lone compile leaves them out and they are read where they are needed.
    """
    today = today or date.today().isoformat()
    config = config or load_config(master)
    spaces = readable_spaces(master, person, rules, shared=config.shared)
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
            building, master, person, spaces, rules, compiled, today, config,
            org=org, pending=pending,
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
        if config.shared != DEFAULT_SHARED:
            # How vault-side readers (write-back, the index, MCP) learn to
            # parse this vault's paths. Written only when it is not the
            # default, so a default vault's manifest is byte-unchanged.
            manifest["shared"] = config.shared
        write_manifest(building / MANIFEST_NAME, manifest)

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
    config: VaultConfig,
    org: Org | None = None,
    pending: list[Promotion] | None = None,
) -> list[str]:
    """Post-process the built vault: stub cross-boundary links, generate the
    AGENTS.md/CLAUDE.md context files, and generate the read-only
    People/<pid>/Shares.md promotion-status note and
    People/<pid>/Pending-corrections.md note. Returns the list of
    generated rel paths for the manifest (excluded from the write-back baseline).
    """
    from brain.resolver import can_write_path

    included_stems = {
        _stem(rel) for rel in compiled if rel.endswith(".md")
    }
    master_stems = {
        _stem(p.name)
        for p in master.rglob("*.md")
        if ".git" not in p.parts and "_meta" not in p.parts
    }
    for rel in compiled:
        if rel.endswith(".md") and not can_write_path(rel, person, rules,
                                                      shared=config.shared):
            f = building / rel
            # errors="replace", as doctor and corrections read notes: one pasted
            # Windows-1252 byte must not raise UnicodeDecodeError (a ValueError, not
            # HANDLED) out of the compile and stop the fleet. The reader sees U+FFFD
            # where the byte was; links are still rewritten on the decoded text.
            text = f.read_text(encoding="utf-8", errors="replace")
            f.write_text(stub_links(text, included_stems, master_stems))

    from brain.contextgen import generate_context_files, writable_spaces

    # Derived once and shared: both generators need it, and two derivations
    # would be two sources of truth for one permission fact.
    spaces_rw = writable_spaces(spaces, person, rules, shared=config.shared)
    generated = generate_context_files(
        building, person, spaces_rw, config=config, corrections_root=master)

    from brain.vaultmap import MAP_NAME, generate_map

    # Additive: scan_vault does its own read pass over the built tree, after
    # stubbing has run, so it sees exactly what shipped.
    (building / MAP_NAME).write_text(
        generate_map(building, person, spaces_rw, compiled, config))
    generated.append(MAP_NAME)

    from brain.promotions import (
        SHARES_NOTE_REL,
        generate_promotion_decider_section,
        generate_shares_note,
    )
    from brain.shares import generate_decider_section, generate_space_shares_section

    # People/<pid>/Shares.md is assembled from four independent generators, each
    # of which returns None when it has nothing to say. generate_shares_note
    # brings its own header; the other three are sections, so they need one only
    # when they are the reason the file exists at all.
    note = generate_shares_note(master, person.id, today)
    sections = [
        s for s in (
            generate_space_shares_section(master, person.id, today),
            generate_decider_section(master, person.id, today),
            generate_promotion_decider_section(master, person.id, today,
                                               shared=config.shared, rules=rules,
                                               org=org, pending=pending),
        ) if s is not None
    ]
    if sections and note is None:
        note = ("---\ngenerated: true\n---\n# My Shares\n\n"
                "Status of what you have proposed to share. This file is\n"
                "regenerated on every compile — edits here are discarded.\n")
    for section in sections:
        note = note.rstrip("\n") + "\n\n" + section
    # People/<pid>/Shares.md is a reserved generated filename —
    # regenerated from queue truth each compile.
    _write_generated_note(building, SHARES_NOTE_REL.format(person_id=person.id),
                          note, generated)

    from brain.corrections import PENDING_NOTE_REL, load_corrections, render_pending_note

    # People/<pid>/Pending-corrections.md: a reserved generated filename, like
    # Shares.md -- rebuilt from master each compile, absent when nothing waits.
    pending_note = render_pending_note(load_corrections(master, person.id))
    _write_generated_note(building, PENDING_NOTE_REL.format(person_id=person.id),
                          pending_note, generated)
    return generated


def _write_generated_note(building: Path, rel: str, note: str | None,
                          generated: list[str]) -> None:
    """Write a reserved generated note if there is one to write, and record
    it in `generated` -- shared by the Shares.md and Pending-corrections.md
    blocks above, which differ only in what builds `note`."""
    if note is None:
        return
    dest = building / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(note)
    generated.append(rel)


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
    config: VaultConfig | None = None,
    pending: list[Promotion] | None = None,
    *,
    only: str | None = None,
    before_each: Callable[[Person], None] | None = None,
) -> list[CompileResult]:
    """Compile every person's vault (or just `only`), isolating failures.

    `before_each` runs for each person immediately before their compile; a
    handled error it raises counts as that person's failure and skips their
    compile, leaving their vault as it was.
    """
    today = today or date.today().isoformat()
    config = config or load_config(master)
    if pending is None:
        # One parse of the promotion queue for the whole fleet — every person's
        # decider section filters the same list. A caller that already holds it
        # (brain cycle, which also reports its length) passes it in.
        from brain.promotions import list_pending

        pending = list_pending(master)
    results: list[CompileResult] = []
    failures: list[tuple[str, str]] = []
    people = [p for p in org.people.values() if only is None or p.id == only]
    total = len(people)
    for person in people:
        out = out_root / person.id
        try:
            if before_each is not None:
                before_each(person)
            result = compile_vault(master, person, rules, out, today, config=config,
                                   org=org, pending=pending)
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
        except HANDLED as e:
            failures.append((person.id, describe(e)))
            continue
        results.append(result)
    if failures:
        raise CompileError(failures, results, total)
    return results
