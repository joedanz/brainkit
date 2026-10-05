"""Keep links working when a note is renamed or moved.

Notes link to each other by name (`[[Q3 Pipeline]]`) or by path. Rename a note
and every link that used the old name quietly stops resolving. `plan_relink`
rewrites exactly the links whose resolution would change and leaves every other
byte alone, so every link that resolved before still resolves to the same note.

Resolution here mirrors `indexer._resolve_links`. The tests pin that, so any
drift between the two fails CI. See docs/superpowers/specs/2026-10-05-relink-design.md.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from brain.compiler import HELD_NAME, WIKILINK_RE, _stem, is_generated_person_note
from brain.doctor import _reader_index, _walk_content
from brain.errors import BrainError
from brain.facts import parse_entity
from brain.frontmatter import split_frontmatter
from brain.holds import HoldError, load_hold
from brain.promotions import PromotionError, _commit, list_pending
from brain.resolver import space_of_path
from brain.schemas import SchemaError, load_config, load_org, load_spaces

Maps = tuple[set[str], dict[str, str], dict[str, str]]


class RelinkError(BrainError, ValueError):
    """A relink that cannot be done safely. The message says what to do."""


def build_maps(texts: Mapping[str, str]) -> Maps:
    """(paths, by_stem, by_alias) the way the indexer builds them: first sorted
    path wins a duplicate stem, and aliases come from entity pages."""
    by_stem: dict[str, str] = {}
    by_alias: dict[str, str] = {}
    for rel in sorted(texts):
        by_stem.setdefault(_stem(rel), rel)
        meta, _body = split_frontmatter(texts[rel])
        entity = parse_entity(meta)
        if entity is not None:
            for alias in entity[1]:
                by_alias.setdefault(alias.lower(), rel)
    return set(texts), by_stem, by_alias


def resolve(target: str, paths: set[str], by_stem: dict[str, str],
            by_alias: dict[str, str]) -> str | None:
    """The note a raw (already stripped) wikilink target reaches, or None."""
    if "/" in target:
        for candidate in (target, target + ".md"):
            if candidate in paths:
                return candidate
    return by_stem.get(_stem(target)) or by_alias.get(target.strip().lower())


@dataclass(frozen=True)
class RelinkPlan:
    mode: str  # "move" (old exists) or "heal" (old is already gone)
    edits: dict[str, str]  # post-rename path -> new text; changed notes only
    per_note: dict[str, int]  # links rewritten in each edited note
    skipped: int = 0  # links left alone: some reader of the note cannot see the target

    @property
    def links_rewritten(self) -> int:
        return sum(self.per_note.values())


def _bare(path: str) -> str:
    """A note's file name without the `.md`."""
    return PurePosixPath(path).name[:-3]


def _spell(was: str, maps: Maps, *, exact_path: bool) -> str:
    """Link text that reaches `was` in the tree described by `maps`: its bare
    name if that resolves to it, else its full path (without `.md`)."""
    if not exact_path:
        name = _bare(was)
        if resolve(name, *maps) == was:
            return name
    return was[:-3]


def _rewrite_links(text: str, respell) -> tuple[str, int]:
    """Replace each wikilink's target with `respell(target)` when that is not
    None. Only the target span changes: padding, heading, label and embed
    marker stay byte-for-byte."""
    count = 0

    def rewrite(m):
        nonlocal count
        raw = m.group(1)
        spelled = respell(raw.strip())
        if spelled is None:
            return m.group(0)
        count += 1
        lead = raw[: len(raw) - len(raw.lstrip())]
        trail = raw[len(raw.rstrip()):]
        whole = m.group(0)
        start, end = m.start(1) - m.start(), m.end(1) - m.start()
        return whole[:start] + lead + spelled + trail + whole[end:]

    return WIKILINK_RE.sub(rewrite, text), count


def plan_relink(texts: Mapping[str, str], old: str, new: str, *,
                can_see: Callable[[str, str], bool] | None = None) -> RelinkPlan:
    """The edits that keep every link reaching the same note across a rename.

    `texts` is the tree as it is now. Move mode: `old` exists, `new` does not.
    Heal mode: `old` is already gone and `new` is there (an agent renamed it
    behind our back); the before-tree is then `new` renamed back to `old`.
    Either way each link is resolved in the before and after trees with `old`
    and `new` treated as one note; a link whose destination would change is
    respelled so it still reaches that note, and nothing else is touched.

    Resolution here is master-wide, but a reader resolves inside their own
    vault. `can_see(source, target)` says whether every reader of the source
    note can read the target; a link that fails it is left alone and counted,
    because rewriting it could break the link for those readers and name a
    note they are not cleared to see.
    """
    old_here, new_here = old in texts, new in texts
    if old_here == new_here:
        raise RelinkError(
            f"expected exactly one of {old!r} and {new!r} to exist; "
            + ("both do" if old_here else "neither does"))
    if old_here:
        mode = "move"
        before = dict(texts)
        after = {(new if p == old else p): t for p, t in texts.items()}
    else:
        mode = "heal"
        after = dict(texts)
        before = {(old if p == new else p): t for p, t in texts.items()}
    before_maps, after_maps = build_maps(before), build_maps(after)

    if _stem(old) != _stem(new):
        if mode == "move" and _stem(new) in before_maps[1]:
            raise RelinkError(
                f"the name {_bare(new)!r} already belongs to "
                f"{before_maps[1][_stem(new)]!r} — pick another name")
        owner = after_maps[1].get(_stem(old)) if mode == "heal" else None
        if owner is not None and owner > old:
            # the old note would have won the bare name, so links written since
            # the rename that mean `owner` cannot be told from ones meant for it
            raise RelinkError(
                f"the old name {_bare(old)!r} is in use by "
                f"{owner!r} — links to it are ambiguous, so they cannot be "
                "healed automatically. If a run already moved the note and "
                "rewrote its links, there is nothing left to do")

    def same(path: str | None) -> str | None:
        return new if path == old else path

    skipped = 0

    def respell(path: str, target: str) -> str | None:
        """The new text for one link's target, or None to leave it alone."""
        nonlocal skipped
        was = same(resolve(target, *before_maps))
        if was is None:
            return None  # dangled before: not a casualty of this rename
        by_path = "/" in target and (
            target in before_maps[0] or target + ".md" in before_maps[0])
        keep_path = by_path and was == new
        if resolve(target, *after_maps) == was and not keep_path:
            return None
        spelled = _spell(was, after_maps, exact_path=keep_path)
        if spelled == target:
            return None
        if can_see is not None and not can_see(path, was):
            skipped += 1
            return None
        return spelled + ".md" if keep_path and target.endswith(".md") else spelled

    edits: dict[str, str] = {}
    per_note: dict[str, int] = {}
    for path, text in after.items():
        new_text, changed = _rewrite_links(text, lambda t, p=path: respell(p, t))
        if changed:
            edits[path] = new_text
            per_note[path] = changed
    return RelinkPlan(mode, edits, per_note, skipped)


@dataclass(frozen=True)
class RelinkReport:
    mode: str
    links_rewritten: int
    paths: tuple[str, ...]  # notes rewritten (post-rename paths), sorted
    skipped_symlinks: int
    skipped_links: int  # links left alone: some reader of the note cannot see their target
    committed: bool
    written: bool

    @property
    def notes_touched(self) -> int:
        return len(self.paths)


def _check_request(master: Path, old: str, new: str, shared: str) -> None:
    for rel in (old, new):
        parts = PurePosixPath(rel).parts
        if rel.startswith("/") or ".." in parts:
            raise RelinkError(f"{rel!r}: path must be inside the master (no '..' or leading '/')")
        if not rel.endswith(".md"):
            raise RelinkError(f"{rel!r}: only .md notes can be relinked")
        if parts and (parts[0] == "_meta" or parts[0].startswith(".")):
            raise RelinkError(f"{rel!r}: _meta and hidden folders are not notes")
        space = space_of_path(rel, shared)
        if space is None or len(parts) <= len(space.split("/")):
            raise RelinkError(f"{rel!r} is not a note inside a space")
        if is_generated_person_note(rel):
            raise RelinkError(f"{rel!r} is a generated note that recompiles every cycle")
    if space_of_path(old, shared) != space_of_path(new, shared):
        raise RelinkError(
            "OLD and NEW are in different spaces; moving a note between spaces "
            "changes who can read it — use a promotion for that")


def _blocking_records(master: Path, names: set[str]) -> list[str]:
    found = [f"pending promotion {p.id}" for p in list_pending(master)
             if {p.target_path, p.source} & names]
    for held in sorted(master.glob(f"People/*/{HELD_NAME}")):
        try:
            paths = {h.get("path") for h in load_hold(master, held.parent.name)["paths"]}
        except (HoldError, AttributeError, TypeError):
            continue
        if paths & names:
            found.append(f"held edit under {held.parent.name}")
    return found


def _read(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return ""  # still a link target, but nothing to rewrite in it


def _check_on_disk(master: Path, old: str, new: str) -> None:
    """Refuse a path that goes through a symlink: the rename would land outside
    the master, and a symlinked note would be replaced by a regular file."""
    for rel in (old, new):
        here = master
        for part in PurePosixPath(rel).parts[:-1]:
            here = here / part
            if here.is_symlink():
                raise RelinkError(
                    f"{rel!r} passes through a symlink ({part!r}); "
                    "move or edit it by hand")
    if (master / old).is_symlink():
        raise RelinkError(
            f"{old!r} is a symlink; moving it would turn it into a regular "
            "file — move it by hand")


def _visibility(master: Path, shared: str) -> Callable[[str, str], bool]:
    """`can_see(source, target)`: can every reader of the source note's space
    read the target's space? A link failing it is never rewritten."""
    try:
        org = load_org(master / "_meta/org.yaml")
        rules = load_spaces(master / "_meta/spaces.yaml")
    except (SchemaError, OSError) as e:
        raise RelinkError(
            f"cannot tell who can read what ({e}); relink needs a valid "
            "_meta/org.yaml and _meta/spaces.yaml") from e
    readers_of = _reader_index(org, rules)

    def can_see(source: str, target: str) -> bool:
        a, b = space_of_path(source, shared), space_of_path(target, shared)
        return a is not None and b is not None and readers_of(a) <= readers_of(b)

    return can_see


def _atomic_write(dest: Path, data: bytes) -> None:
    tmp = dest.with_name(dest.name + ".relink-tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


def _undo(master: Path, originals: dict[str, bytes], old: str, new: str,
          moved: bool, made_dir: bool) -> list[str]:
    """Put the master back as it was; returns the paths that could not be."""
    failed: list[str] = []
    if moved:
        try:
            os.replace(master / new, master / old)
        except OSError:
            failed.append(new)
    for rel, original in originals.items():
        try:
            _atomic_write(master / rel, original)
        except OSError:
            failed.append(rel)
    if made_dir:
        with contextlib.suppress(OSError):
            (master / new).parent.rmdir()
    return failed


def _apply(master: Path, mode: str, targets: dict[str, str], old: str, new: str) -> None:
    """Rewrite the notes, then rename the file last, so a failure part-way is
    undone completely. The rename is a plain `os.replace`, not `git mv`: a
    staged deletion would drop out of the scoped commit."""
    def on_disk(rel: str) -> str:
        # the moved note's own edits are written before it moves
        return old if mode == "move" and rel == new else rel

    originals: dict[str, bytes] = {}
    moved = made_dir = False
    try:
        for rel, text in targets.items():
            dest = master / on_disk(rel)
            original = dest.read_bytes()
            _atomic_write(dest, text.encode("utf-8"))
            originals[on_disk(rel)] = original
        if mode == "move":
            made_dir = not (master / new).parent.exists()
            (master / new).parent.mkdir(parents=True, exist_ok=True)
            os.replace(master / old, master / new)
            moved = True
    except OSError as e:
        failed = _undo(master, originals, old, new, moved, made_dir)
        if failed:
            raise RelinkError(
                f"could not apply the relink ({e}) and could not undo it for "
                f"{', '.join(failed)}; run `git status` in the master and "
                "`git checkout` those paths") from e
        raise RelinkError(f"could not apply the relink ({e}); nothing was changed") from e


def relink_master(master: Path, old: str, new: str, *, write: bool = False) -> RelinkReport:
    """Validate, plan and (with write=True) apply a relink on a master.

    Without `write` nothing on disk or in git changes. With it: rewrite each
    affected note atomically, move the note if it still exists, and commit
    exactly the touched paths in one commit. Both paths are always committed,
    so a run finished as a heal still records the move.
    """
    master = Path(master)
    shared = load_config(master).shared
    _check_request(master, old, new, shared)
    _check_on_disk(master, old, new)
    blockers = _blocking_records(master, {old, new})
    if blockers:
        raise RelinkError(
            "resolve these first, they name the note by path: " + ", ".join(blockers))

    texts = {rel: _read(master / rel) for rel in _walk_content(master, shared)}
    plan = plan_relink(texts, old, new, can_see=_visibility(master, shared))
    del texts

    # Never write through a symlink or into a generated note.
    editable = {p: t for p, t in plan.edits.items() if not is_generated_person_note(p)}
    symlinked = {p for p in editable if (master / p).is_symlink()}
    targets = {p: t for p, t in editable.items() if p not in symlinked}
    touched = tuple(sorted(targets))
    report = RelinkReport(
        plan.mode, sum(plan.per_note[p] for p in targets), touched, len(symlinked),
        plan.skipped, committed=False, written=False)
    if not write:
        return report

    _apply(master, plan.mode, targets, old, new)
    try:
        committed = _commit(master, sorted({*touched, old, new}),
                            f"relink: {old} -> {new}", "Brain Server",
                            "server@brain.local")
    except PromotionError as e:
        raise RelinkError(
            f"the relink is on disk but could not be committed: {e}. Review it "
            "with `git status` in the master and commit it by hand") from e
    return replace(report, committed=committed, written=True)
