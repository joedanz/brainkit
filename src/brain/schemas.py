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
# The shared top may be named anything except the two per-identity trees
# and the operational directory: those tops carry structural meaning
# (their children are spaces), so a shared space named for one of them
# would be a space and a nested top at once.
_RESERVED_SHARED = ("teams", "people", "_meta")
DEFAULT_SHARED = "Company"
_MAX_CONFIG_WORD = 64
_MAX_CHARTER = 400


def _config_word(value: object, key: str) -> str:
    if not isinstance(value, str):
        raise SchemaError(f"config.yaml: {key} must be a string")
    if (not _CONFIG_WORD.fullmatch(value) or value.startswith(".")
            or len(value) > _MAX_CONFIG_WORD):
        raise SchemaError(f"config.yaml: invalid {key} {value!r}")
    return value


def _config_prose(value: object, key: str, limit: int = _MAX_CHARTER) -> str:
    """One line of free prose, safe to paste into generated markdown.

    Unlike a config word this is a sentence a human wrote, so it cannot be
    charset-restricted. Two things are enforced instead. Every whitespace run
    collapses to a single space: a charter is rendered into AGENTS.md as a
    paragraph, and a value carrying its own newlines could open a heading or a
    list there — forging protocol structure in the very file that tells the
    agent what its rules are. And the length is capped, because the rendered
    protocol has a hard ROOT_LIMIT and an unbounded charter would blow it for
    every person in the vault at once, failing the compile rather than the
    edit that caused it.
    """
    if not isinstance(value, str):
        raise SchemaError(f"config.yaml: {key} must be a string")
    text = " ".join(value.split())
    if len(text) > limit:
        raise SchemaError(
            f"config.yaml: {key} is {len(text)} chars, limit is {limit}")
    return text


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

    `charter` is the one non-naming field: a sentence saying what this brain is
    for, so the admission rules in the generated protocol have a subject to
    test relevance against. Empty is the honest default — a vault whose owner
    has not said what it collects gets the domain-agnostic tests alone, never
    an invented purpose.
    """
    entities: str = "Clients"   # TitleCase tree/folder name (plural)
    entity: str = "client"      # lowercase singular: prose + frontmatter key
    shared: str = DEFAULT_SHARED  # TitleCase name of the shared top-level space
    charter: str = ""           # one sentence: what this brain collects

    def __post_init__(self) -> None:
        _config_word(self.entities, "entities")
        _config_word(self.entity, "entity")
        _config_word(self.shared, "shared")
        # Normalize in place, exactly as the charset rules above are enforced
        # here rather than in make_config: the safety of the rendered protocol
        # must not depend on which construction path was taken, and a test or
        # a caller building VaultConfig(charter=...) directly is a path.
        object.__setattr__(self, "charter", _config_prose(self.charter, "charter"))

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


def make_config(entities: str, entity: str | None = None,
                shared: str = DEFAULT_SHARED, charter: str = "") -> VaultConfig:
    entities = _config_word(entities, "entities")
    if entities.lower() in _RESERVED_TOPS:
        raise SchemaError(f"config.yaml: entities {entities!r} is a reserved name")
    shared = _config_word(shared, "shared")
    if shared.lower() in _RESERVED_SHARED:
        raise SchemaError(f"config.yaml: shared {shared!r} is a reserved name")
    if shared.lower() == entities.lower():
        raise SchemaError(
            f"config.yaml: shared {shared!r} collides with entities {entities!r}")
    entity = _config_word(entity if entity is not None else derive_entity(entities),
                          "entity")
    return VaultConfig(entities=entities, entity=entity, shared=shared,
                       charter=charter)


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
    return make_config(data.get("entities", "Clients"), data.get("entity"),
                       data.get("shared", DEFAULT_SHARED),
                       data.get("charter", "") or "")


def master_shared(master: Path, shared: str | None = None) -> str:
    """The shared space name for a master-side operation. `None` means "ask
    the master" — its config.yaml is the authority, so this fallback is
    correct rather than a guess; callers holding a config pass it to skip
    the read. A malformed config raises, because a write path must not act
    on a guessed vocabulary."""
    return shared if shared is not None else load_config(master).shared


def master_shared_or_default(master: Path, shared: str | None = None) -> str:
    """`master_shared` for read-only surfaces that must survive a broken
    config — doctor reports the malformation itself, and a dashboard request
    should render rather than 500. Degrading to the default is safe here
    precisely because these callers never write."""
    if shared is not None:
        return shared
    try:
        return load_config(master).shared
    except SchemaError:
        return DEFAULT_SHARED


@dataclass(frozen=True)
class Person:
    id: str
    name: str
    roles: tuple[str, ...] = ()
    teams: tuple[str, ...] = ()
    email: str = ""  # optional; the auth key for `brain ingest --from`

    @property
    def is_admin(self) -> bool:
        """Structural oversight: admins decide promotions and any share, are
        routed every finding triage can't give an owner, and can never be
        revoked from a space rule. One definition so the role name is not a
        string literal spread across the modules that ask."""
        return "admin" in self.roles


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
