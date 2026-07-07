"""Permission resolution: which spaces a person can read, which paths they can write."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from brain.schemas import Person, SpaceRule

# Top-level dirs whose children are each a space; Company is itself a space.
NESTED_TOPS = ("Teams", "People", "Clients")
RESERVED = ("_meta", ".git")


def enumerate_spaces(master: Path) -> list[str]:
    spaces: list[str] = []
    for top in sorted(p for p in master.iterdir() if p.is_dir()):
        if top.name in RESERVED or top.name.startswith("."):
            continue
        if top.name == "Company":
            spaces.append("Company")
        elif top.name in NESTED_TOPS:
            spaces.extend(
                f"{top.name}/{child.name}"
                for child in sorted(top.iterdir())
                if child.is_dir()
            )
    return spaces


def space_of_path(rel_path: str) -> str | None:
    path = PurePosixPath(rel_path)
    parts = path.parts
    if not parts or path.is_absolute() or ".." in parts:
        return None
    if parts[0] in RESERVED or parts[0].startswith("."):
        return None
    if parts[0] == "Company":
        return "Company"
    if parts[0] in NESTED_TOPS and len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return None


def _match_rule(space: str, rules: tuple[SpaceRule, ...]) -> tuple[SpaceRule | None, str | None]:
    """Return (rule, wildcard_binding). Exact match wins over wildcard."""
    parts = space.split("/")
    wildcard_hit: tuple[SpaceRule, str] | None = None
    for rule in rules:
        if rule.path == space:
            return rule, None
        rparts = rule.path.split("/")
        if len(rparts) == len(parts) and rparts[-1] == "*" and rparts[:-1] == parts[:-1]:
            wildcard_hit = (rule, parts[-1])
    if wildcard_hit:
        return wildcard_hit
    return None, None


def _subject_matches(subject: str, person: Person, binding: str | None) -> bool:
    if binding is not None:
        subject = subject.replace("{name}", binding)
    if subject == "everyone":
        return True
    kind, _, value = subject.partition(":")
    if kind == "person":
        return person.id == value
    if kind == "team":
        return value in person.teams
    if kind == "role":
        return value in person.roles
    return False


def _allowed(space: str, person: Person, rules: tuple[SpaceRule, ...], mode: str) -> bool:
    rule, binding = _match_rule(space, rules)
    if rule is None:
        return False
    subjects = rule.read if mode == "read" else rule.write
    return any(_subject_matches(s, person, binding) for s in subjects)


def can_read(space: str, person: Person, rules: tuple[SpaceRule, ...]) -> bool:
    return _allowed(space, person, rules, "read")


def can_write_path(rel_path: str, person: Person, rules: tuple[SpaceRule, ...]) -> bool:
    space = space_of_path(rel_path)
    if space is None:
        return False
    return _allowed(space, person, rules, "write")


def readable_spaces(master: Path, person: Person, rules: tuple[SpaceRule, ...]) -> list[str]:
    return [s for s in enumerate_spaces(master) if can_read(s, person, rules)]
