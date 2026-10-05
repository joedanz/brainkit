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

from brain.compiler import _stem
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
