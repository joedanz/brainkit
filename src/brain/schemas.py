"""Load and validate _meta/org.yaml and _meta/spaces.yaml."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from brain.errors import BrainError


class SchemaError(BrainError, ValueError):
    """Invalid org.yaml or spaces.yaml content."""


SUBJECT_PREFIXES = ("person:", "team:", "role:")

_CONFIG_WORD = re.compile(r"[A-Za-z0-9._-]+")
_RESERVED_TOPS = ("company", "teams", "people", "_meta")


def _config_word(value: object, key: str) -> str:
    if not isinstance(value, str):
        raise SchemaError(f"config.yaml: {key} must be a string")
    if not _CONFIG_WORD.fullmatch(value) or value.startswith("."):
        raise SchemaError(f"config.yaml: invalid {key} {value!r}")
    return value


@dataclass(frozen=True)
class VaultConfig:
    """Vault-level naming: what the restricted third-party tree is called.

    The structural/permission layer never reads this — spaces.yaml is the only
    readability authority. Config feeds naming surfaces only: scaffold,
    guidance text, the request seam, and human-facing messages.

    Charset validity is an intrinsic invariant of the type, enforced here so
    every construction path (not just make_config/load_config) is safe to
    write into frontmatter unescaped. Reserved-name rejection is vault policy,
    not type validity, and stays in make_config.
    """
    entities: str = "Clients"   # TitleCase tree/folder name (plural)
    entity: str = "client"      # lowercase singular: prose + frontmatter key

    def __post_init__(self) -> None:
        _config_word(self.entities, "entities")
        _config_word(self.entity, "entity")

    @property
    def requests_folder(self) -> str:
        return self.entity[:1].upper() + self.entity[1:] + "Requests"

    @property
    def name_key(self) -> str:
        return f"{self.entity}-name"


def derive_entity(entities: str) -> str:
    """Naive singular: lowercase, strip one trailing 's'. Irregular plurals
    (Families -> family) need the explicit entity value."""
    low = entities.lower()
    return low[:-1] if low.endswith("s") and len(low) > 1 else low


def make_config(entities: str, entity: str | None = None) -> VaultConfig:
    entities = _config_word(entities, "entities")
    if entities.lower() in _RESERVED_TOPS:
        raise SchemaError(f"config.yaml: entities {entities!r} is a reserved name")
    entity = _config_word(entity if entity is not None else derive_entity(entities),
                          "entity")
    return VaultConfig(entities=entities, entity=entity)


def load_config(master: Path) -> VaultConfig:
    """Read _meta/config.yaml. Missing file or keys default (entity derives
    from entities); a present-but-invalid file raises SchemaError — a typo'd
    config must fail the cycle loudly, not silently regress every surface to
    the default noun."""
    path = master / "_meta/config.yaml"
    if not path.is_file():
        return VaultConfig()
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise SchemaError(f"config.yaml does not parse: {e}") from e
    if data is None:
        return VaultConfig()
    if not isinstance(data, dict):
        raise SchemaError("config.yaml must be a mapping")
    return make_config(data.get("entities", "Clients"), data.get("entity"))


@dataclass(frozen=True)
class Person:
    id: str
    name: str
    roles: tuple[str, ...] = ()
    teams: tuple[str, ...] = ()
    email: str = ""  # optional; the auth key for `brain ingest --from`


@dataclass(frozen=True)
class Org:
    people: dict[str, Person]

    def person_by_email(self, email: str) -> Person | None:
        """Resolve a person by their org.yaml email, case/whitespace-insensitive.

        An empty needle never matches (people without an email have "").
        """
        needle = email.strip().lower()
        if not needle:
            return None
        for p in self.people.values():
            if p.email and p.email.lower() == needle:
                return p
        return None


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


def _string_list(value: object, owner: str, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise SchemaError(f"{owner}: {field} must be a list")
    return tuple(value)


def _read_yaml(path: Path) -> dict:
    """Read a _meta file, turning "not there" and "does not parse" into the
    same handled error the content checks raise. Both are things an operator
    causes and can fix — pointing `--master` at the wrong directory is the
    single most common one — so neither belongs in a traceback."""
    try:
        text = path.read_text()
    except OSError as e:
        raise SchemaError(f"cannot read {path.name}: {e.strerror} ({path})") from e
    try:
        return yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise SchemaError(f"{path.name} does not parse: {e}") from e


def load_org(path: Path) -> Org:
    data = _read_yaml(path)
    people_raw = data.get("people")
    if not isinstance(people_raw, dict) or not people_raw:
        raise SchemaError("org.yaml must define a non-empty 'people' mapping")
    people: dict[str, Person] = {}
    emails_seen: dict[str, str] = {}  # lowercased email -> pid, for uniqueness
    for pid, attrs in people_raw.items():
        attrs = attrs or {}
        if not isinstance(attrs, dict):
            raise SchemaError(f"person {pid!r}: value must be a mapping")
        email = attrs.get("email", "")
        if not isinstance(email, str):
            raise SchemaError(f"person {pid!r}: email must be a string")
        if email and email != email.strip():
            raise SchemaError(f"person {pid!r}: email must not have surrounding whitespace")
        if email and (len(email.split()) != 1):
            raise SchemaError(f"person {pid!r}: email must not contain whitespace")
        if email:
            # Email is an auth key for intake; a duplicate would let one address
            # resolve to two people. Reject rather than pick one.
            prior = emails_seen.get(email.lower())
            if prior is not None:
                raise SchemaError(
                    f"duplicate email {email!r}: {prior!r} and {pid!r}")
            emails_seen[email.lower()] = pid
        people[pid] = Person(
            id=pid,
            name=attrs.get("name", pid),
            roles=_string_list(attrs.get("roles"), f"person {pid!r}", "roles"),
            teams=_string_list(attrs.get("teams"), f"person {pid!r}", "teams"),
            email=email,
        )
    return Org(people=people)


def load_spaces(path: Path) -> tuple[SpaceRule, ...]:
    data = _read_yaml(path)
    entries = data.get("spaces")
    if not isinstance(entries, list) or not entries:
        raise SchemaError("spaces.yaml must define a non-empty 'spaces' list")
    rules: list[SpaceRule] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise SchemaError(f"spaces entry {entry!r}: must be a mapping")
        rule_path = entry.get("path")
        if not rule_path:
            raise SchemaError("every spaces entry needs a 'path'")
        if rule_path in seen:
            raise SchemaError(f"duplicate rule path {rule_path!r}")
        seen.add(rule_path)
        read = _string_list(entry.get("read"), f"rule {rule_path!r}", "read")
        write = _string_list(entry.get("write"), f"rule {rule_path!r}", "write")
        for subject in (*read, *write):
            _validate_subject(subject, rule_path)
        rules.append(SpaceRule(path=rule_path, read=read, write=write))
    return tuple(rules)
