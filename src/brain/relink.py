"""Keep links working when a note is renamed or moved.

Notes link to each other by name (`[[Q3 Pipeline]]`) or by path. Rename a note
and every link that used the old name quietly stops resolving. `plan_relink`
rewrites exactly the links whose resolution would change and leaves every other
byte alone, so every link that resolved before still resolves to the same note.

Resolution here mirrors `indexer._resolve_links`. The tests pin that, so any
drift between the two fails CI. See docs/superpowers/specs/2026-10-05-relink-design.md.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

from brain.compiler import WIKILINK_RE, _stem
from brain.errors import BrainError
from brain.facts import parse_entity
from brain.frontmatter import split_frontmatter

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
    links_rewritten: int
    per_note: dict[str, int]  # links rewritten in each edited note


def _spell(was: str, maps: Maps, *, exact_path: bool) -> str:
    """Link text that reaches `was` in the tree described by `maps`: its bare
    name if that resolves to it, else its full path (without `.md`)."""
    if not exact_path:
        name = PurePosixPath(was).name[:-3]
        if resolve(name, *maps) == was:
            return name
    return was[:-3]


def plan_relink(texts: Mapping[str, str], old: str, new: str) -> RelinkPlan:
    """The edits that keep every link reaching the same note across a rename.

    `texts` is the tree as it is now. Move mode: `old` exists, `new` does not.
    Heal mode: `old` is already gone and `new` is there (an agent renamed it
    behind our back); the before-tree is then `new` renamed back to `old`.
    Either way each link is resolved in the before and after trees with `old`
    and `new` treated as one note; a link whose destination would change is
    respelled so it still reaches that note, and nothing else is touched.
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
                f"the name {PurePosixPath(new).name[:-3]!r} already belongs to "
                f"{before_maps[1][_stem(new)]!r} — pick another name")
        if mode == "heal" and _stem(old) in after_maps[1]:
            raise RelinkError(
                f"the old name {PurePosixPath(old).name[:-3]!r} is in use by "
                f"{after_maps[1][_stem(old)]!r} — links to it are ambiguous, "
                "so they cannot be healed automatically")

    def same(path: str | None) -> str | None:
        return new if path == old else path

    edits: dict[str, str] = {}
    per_note: dict[str, int] = {}
    for path, text in after.items():
        pieces: list[str] = []
        cursor = 0
        changed = 0
        for m in WIKILINK_RE.finditer(text):
            raw = m.group(1)
            target = raw.strip()
            was = same(resolve(target, *before_maps))
            if was is None:
                continue  # dangled before: not a casualty of this rename
            now = resolve(target, *after_maps)
            exact = "/" in target and (
                target in before_maps[0] or target + ".md" in before_maps[0])
            if was == now and not (exact and was == new):
                continue
            spelled = _spell(was, after_maps, exact_path=exact and was == new)
            if spelled == target:
                continue
            lead = raw[: len(raw) - len(raw.lstrip())]
            trail = raw[len(raw.rstrip()):]
            if exact and was == new and target.endswith(".md"):
                spelled += ".md"
            pieces.append(text[cursor:m.start(1)])
            pieces.append(lead + spelled + trail)
            cursor = m.end(1)
            changed += 1
        if changed:
            pieces.append(text[cursor:])
            edits[path] = "".join(pieces)
            per_note[path] = changed
    return RelinkPlan(mode, edits, sum(per_note.values()), per_note)
