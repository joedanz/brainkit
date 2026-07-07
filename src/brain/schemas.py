"""Load and validate _meta/org.yaml and _meta/spaces.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


class SchemaError(ValueError):
    """Invalid org.yaml or spaces.yaml content."""


SUBJECT_PREFIXES = ("person:", "team:", "role:")


@dataclass(frozen=True)
class Person:
    id: str
    name: str
    roles: tuple[str, ...] = ()
    teams: tuple[str, ...] = ()


@dataclass(frozen=True)
class Org:
    people: dict[str, Person]


@dataclass(frozen=True)
class SpaceRule:
    path: str  # "Company", "Teams/*", "People/*", or exact like "Clients/acme"
    read: tuple[str, ...]
    write: tuple[str, ...]


def _validate_subject(subject: str, rule_path: str) -> None:
    if subject == "everyone":
        return
    if not subject.startswith(SUBJECT_PREFIXES):
        raise SchemaError(f"rule {rule_path!r}: unknown subject {subject!r}")
    if "{name}" in subject and "*" not in rule_path:
        raise SchemaError(f"rule {rule_path!r}: {{name}} requires a wildcard path")


def load_org(path: Path) -> Org:
    data = yaml.safe_load(path.read_text()) or {}
    people_raw = data.get("people")
    if not isinstance(people_raw, dict) or not people_raw:
        raise SchemaError("org.yaml must define a non-empty 'people' mapping")
    people: dict[str, Person] = {}
    for pid, attrs in people_raw.items():
        attrs = attrs or {}
        people[pid] = Person(
            id=pid,
            name=attrs.get("name", pid),
            roles=tuple(attrs.get("roles", ())),
            teams=tuple(attrs.get("teams", ())),
        )
    return Org(people=people)


def load_spaces(path: Path) -> tuple[SpaceRule, ...]:
    data = yaml.safe_load(path.read_text()) or {}
    entries = data.get("spaces")
    if not isinstance(entries, list) or not entries:
        raise SchemaError("spaces.yaml must define a non-empty 'spaces' list")
    rules: list[SpaceRule] = []
    seen: set[str] = set()
    for entry in entries:
        rule_path = entry.get("path")
        if not rule_path:
            raise SchemaError("every spaces entry needs a 'path'")
        if rule_path in seen:
            raise SchemaError(f"duplicate rule path {rule_path!r}")
        seen.add(rule_path)
        read = tuple(entry.get("read", ()))
        write = tuple(entry.get("write", ()))
        for subject in (*read, *write):
            _validate_subject(subject, rule_path)
        rules.append(SpaceRule(path=rule_path, read=read, write=write))
    return tuple(rules)
