"""Rename the entity tree of a master vault (e.g. Clients -> Vendors).

Run immediately after a cycle so compiled slices are in sync with master.
Edits made in a stale slice under the old tree name are rejected fail-closed
by the next cycle's writeback and reported — never silently lost, never
migrated.

Every step is idempotent, so a partially failed run can be rerun with the
same arguments and completes the remaining steps. Known small window: a
crash after the config write but before the final commit leaves the changes
uncommitted; the rerun exits as a no-op and the next server-side commit
picks the files up.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from brain.errors import BrainError
from brain.schemas import VaultConfig, load_config, make_config


class RenameError(BrainError, ValueError):
    """Invalid or impossible rename request."""


@dataclass(frozen=True)
class RenameReport:
    old: VaultConfig
    new: VaultConfig
    moved_tree: bool
    rules_rewritten: int
    request_dirs_moved: int
    request_files_rewritten: int
    queue_files_rewritten: int
    committed: bool


def _rewrite_file_lines(path: Path, transform) -> int:
    """Apply transform(line) -> line per line; write only on change. Returns
    changed-line count. Non-UTF-8 files are left byte-identical (the sweeps'
    poison discipline)."""
    try:
        text = path.read_text()
    except UnicodeDecodeError:
        return 0
    out, changed = [], 0
    for line in text.splitlines(keepends=True):
        new = transform(line)
        if new != line:
            changed += 1
        out.append(new)
    if changed:
        path.write_text("".join(out))
    return changed


def _prefix_rewriter(keys: tuple[str, ...], old_tree: str, new_tree: str):
    """Rewrite `<key> "<old_tree>/...` and `<key> <old_tree>/...` values."""
    def transform(line: str) -> str:
        for key in keys:
            for pat, rep in ((f'{key} "{old_tree}/', f'{key} "{new_tree}/'),
                             (f"{key} {old_tree}/", f"{key} {new_tree}/")):
                if pat in line:
                    return line.replace(pat, rep, 1)
        return line
    return transform


def rename_entities(master: Path, entities: str,
                    entity: str | None = None) -> RenameReport:
    new = make_config(entities, entity)        # raises SchemaError on bad names
    old = load_config(master)
    if (old.entities, old.entity) == (new.entities, new.entity):
        return RenameReport(old, new, False, 0, 0, 0, 0, False)

    old_dir, new_dir = master / old.entities, master / new.entities
    if not old_dir.is_dir() and not new_dir.is_dir():
        raise RenameError(f"no {old.entities}/ tree in {master}")
    if old_dir.is_dir() and new_dir.exists():
        raise RenameError(f"{new.entities}/ already exists — pick another name")

    moved = False
    if old_dir.is_dir():
        r = subprocess.run(
            ["git", "-C", str(master), "mv", old.entities, new.entities],
            capture_output=True, text=True)
        if r.returncode != 0:                  # untracked tree or no git repo
            old_dir.rename(new_dir)
        moved = True

    rules_rewritten = _rewrite_file_lines(
        master / "_meta/spaces.yaml",
        _prefix_rewriter(("path:",), old.entities, new.entities))
    # word-level comment fix so scaffold comments stay coherent
    _rewrite_file_lines(
        master / "_meta/spaces.yaml",
        lambda ln: ln.replace(f"# {old.entities} are deny-by-default",
                              f"# {new.entities} are deny-by-default")
        if ln.lstrip().startswith("#") else ln)

    key_old, key_new = f"{old.entity}-name:", f"{new.entity}-name:"
    ent_old, ent_new = f"entity: {old.entity}", f"entity: {new.entity}"

    def req_fix(line: str) -> str:
        if line.startswith(key_old):
            return key_new + line[len(key_old):]
        if line.rstrip("\n") == ent_old:
            return ent_new + line[len(ent_old):]
        return line

    dirs_moved = 0
    for d in sorted(master.glob(f"People/*/{old.requests_folder}")):
        if not d.is_dir() or d.is_symlink():
            continue
        target = d.parent / new.requests_folder
        if not target.exists():
            d.rename(target)
        else:                                   # rerun after partial move
            for f in sorted(d.iterdir()):
                f.rename(target / f.name)
            d.rmdir()
        dirs_moved += 1
    files_rewritten = 0
    for f in sorted(master.glob(f"People/*/{new.requests_folder}/*.md")):
        if f.is_symlink():
            continue
        files_rewritten += 1 if _rewrite_file_lines(f, req_fix) else 0

    queue_rewritten = 0
    share_fix = _prefix_rewriter(("space:",), old.entities, new.entities)
    promo_fix = _prefix_rewriter(("target-path:",), old.entities, new.entities)
    for f in sorted(master.glob("_meta/shares/pending/*.md")):
        queue_rewritten += 1 if _rewrite_file_lines(f, share_fix) else 0
    for f in sorted(master.glob("_meta/promotions/pending/*.md")):
        queue_rewritten += 1 if _rewrite_file_lines(f, promo_fix) else 0

    (master / "_meta/config.yaml").write_text(
        f"entities: {new.entities}\nentity: {new.entity}\n")

    committed = False
    if (master / ".git").exists():
        subprocess.run(["git", "-C", str(master), "add", "-A"],
                       capture_output=True, check=True)
        status = subprocess.run(
            ["git", "-C", str(master), "status", "--porcelain"],
            capture_output=True, text=True, check=True).stdout
        if status.strip():
            subprocess.run(
                ["git", "-C", str(master), "-c", "user.name=Brain Server",
                 "-c", "user.email=server@brain.local",
                 "commit", "-m",
                 f"rename-entities: {old.entities} -> {new.entities}"],
                capture_output=True, check=True)
            committed = True

    return RenameReport(old, new, moved, rules_rewritten, dirs_moved,
                        files_rewritten, queue_rewritten, committed)
