"""Permission resolution: which spaces a person can read, which paths they can write."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from brain.schemas import Person, SpaceRule

# Company is itself a space; every other non-reserved top-level directory is a
# nested top whose child directories are each a space. No tree name is special:
# spaces.yaml is the only authority on readability, so a space under a top no
# rule covers has zero readers (fail closed).
RESERVED = ("_meta", ".git")


def _is_top(name: str) -> bool:
    return name not in RESERVED and not name.startswith(".")


def enumerate_spaces(master: Path) -> list[str]:
    spaces: list[str] = []
    for top in sorted(p for p in master.iterdir() if p.is_dir()):
        if not _is_top(top.name):
            continue
        if top.name == "Company":
            spaces.append("Company")
        else:
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
    if not _is_top(parts[0]):
        return None
    if parts[0] == "Company":
        return "Company"
    if len(parts) >= 2:
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


def _self_named_spaces(person: Person, rules: tuple[SpaceRule, ...]) -> list[str]:
    """Wildcard spaces this person's own identity names, whether or not they
    exist on disk yet.

    ``enumerate_spaces`` can only see directories that exist, but some spaces
    exist *because* of a person: ``People/*`` read by ``person:{name}`` means
    ``People/bob`` is bob's the moment bob is in org.yaml, before anything has
    been written there. Without this, a newly added person compiled to a vault
    whose context file listed ``Company/`` as their only space and then routed
    every note they take into ``People/<id>/`` — a directory that same file
    said they did not have.

    Only wildcards whose *subjects* interpolate ``{name}`` expand, and only to
    bindings this person actually carries. That distinction matters: the
    default ``{entities}/*`` rule reads ``role:admin``, so its members are
    named by the world, not by the reader — expanding it would invent a
    ``Clients/<admin-id>`` that nobody meant. Candidates still round-trip
    through ``space_of_path`` so a rule over a reserved top yields nothing,
    matching what the write check would allow.
    """
    bindings = (person.id, *person.teams, *person.roles)
    found: set[str] = set()
    for rule in rules:
        parts = rule.path.split("/")
        if parts[-1] != "*" or len(parts) < 2:
            continue
        for subject in rule.read:
            if "{name}" not in subject:
                continue
            for binding in bindings:
                if not _subject_matches(subject, person, binding):
                    continue
                space = "/".join([*parts[:-1], binding])
                if space_of_path(f"{space}/x.md") == space:
                    found.add(space)
    return sorted(found)


def readable_spaces(master: Path, person: Person, rules: tuple[SpaceRule, ...]) -> list[str]:
    spaces = set(enumerate_spaces(master)) | set(_self_named_spaces(person, rules))
    return sorted(s for s in spaces if can_read(s, person, rules))
