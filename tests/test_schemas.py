from pathlib import Path

import pytest

from brain.schemas import (
    Person,
    SchemaError,
    SpaceRule,
    VaultConfig,
    derive_entity,
    load_config,
    load_org,
    load_spaces,
    make_config,
)

ORG_YAML = """\
people:
  alice: {name: Alice Nguyen, roles: [admin], teams: [sales]}
  bob:   {name: Bob Rivera, teams: [ops]}
"""

SPACES_YAML = """\
spaces:
  - {path: Company,     read: [everyone],        write: ["role:admin"]}
  - {path: "Teams/*",   read: ["team:{name}"],   write: ["team:{name}"]}
  - {path: "People/*",  read: ["person:{name}"], write: ["person:{name}"]}
  - {path: "Clients/*", read: [everyone],        write: ["role:admin"]}
"""


def test_load_org(tmp_path: Path):
    f = tmp_path / "org.yaml"
    f.write_text(ORG_YAML)
    org = load_org(f)
    assert org.people["alice"] == Person(
        id="alice", name="Alice Nguyen", roles=("admin",), teams=("sales",)
    )
    assert org.people["bob"].roles == ()


def test_load_spaces(tmp_path: Path):
    f = tmp_path / "spaces.yaml"
    f.write_text(SPACES_YAML)
    rules = load_spaces(f)
    assert rules[0] == SpaceRule(path="Company", read=("everyone",), write=("role:admin",))
    assert rules[1].read == ("team:{name}",)


@pytest.mark.parametrize("org_yaml,match", [
    ("people:\n  alice: {name: Alice Nguyen, roles: admin}\n", "'alice'.*roles"),
    ("people:\n  bob: {name: Bob Rivera, teams: ops}\n", "'bob'.*teams"),
    ("people:\n  alice: just a string\n", "'alice'"),
    ("people:\n  alice: {name: Alice, email: [x]}\n", "'alice'.*email must be a string"),
    ('people:\n  alice: {name: Alice, email: "a b@acme.com"}\n', "email must not contain whitespace"),
    (("people:\n  alice: {name: Alice, email: Bob@Acme.com}\n"
     "  bob: {name: Bob, email: bob@acme.com}\n"), "duplicate email"),
], ids=["scalar-roles", "scalar-teams", "non-dict-person", "non-string-email",
        "whitespace-email", "duplicate-email"])
def test_load_org_rejects_malformed(tmp_path: Path, org_yaml, match):
    f = tmp_path / "org.yaml"
    f.write_text(org_yaml)
    with pytest.raises(SchemaError, match=match):
        load_org(f)


@pytest.mark.parametrize("spaces_yaml,match", [
    ('spaces:\n  - {path: Company, read: ["group:staff"], write: []}\n', "group:staff"),
    ("spaces:\n  - {path: Company, read: everyone, write: []}\n", "'Company'.*read must be a list"),
    ("spaces:\n  - just a string\n", "mapping"),
    (("spaces:\n  - {path: Company, read: [everyone], write: []}\n"
     "  - {path: Company, read: [everyone], write: []}\n"), "duplicate"),
], ids=["unknown-subject", "scalar-read", "non-dict-entry", "duplicate-path"])
def test_load_spaces_rejects_malformed(tmp_path: Path, spaces_yaml, match):
    f = tmp_path / "spaces.yaml"
    f.write_text(spaces_yaml)
    with pytest.raises(SchemaError, match=match):
        load_spaces(f)


def test_email_loaded_and_defaults_empty(tmp_path: Path):
    f = tmp_path / "org.yaml"
    f.write_text(
        "people:\n"
        "  alice: {name: Alice, email: alice@acme.com}\n"
        "  bob: {name: Bob}\n"
    )
    org = load_org(f)
    assert org.people["alice"].email == "alice@acme.com"
    assert org.people["bob"].email == ""


def test_person_by_email(tmp_path: Path):
    f = tmp_path / "org.yaml"
    f.write_text(
        "people:\n"
        "  alice: {name: Alice, email: alice@acme.com}\n"
        "  bob: {name: Bob}\n"
    )
    org = load_org(f)
    assert org.person_by_email("  ALICE@acme.com ").id == "alice"
    assert org.person_by_email("nobody@evil.com") is None
    assert org.person_by_email("") is None  # never matches bob's empty email
    assert org.person_by_email("   ") is None


# VaultConfig tests
def test_load_config_defaults_when_missing(tmp_path):
    (tmp_path / "_meta").mkdir()
    cfg = load_config(tmp_path)
    assert cfg == VaultConfig()
    assert cfg.entities == "Clients" and cfg.entity == "client"
    assert cfg.requests_folder == "ClientRequests"
    assert cfg.name_key == "client-name"


def test_load_config_reads_custom_pair(tmp_path):
    (tmp_path / "_meta").mkdir()
    (tmp_path / "_meta/config.yaml").write_text("entities: Families\nentity: family\n")
    cfg = load_config(tmp_path)
    assert cfg.entities == "Families" and cfg.entity == "family"
    assert cfg.requests_folder == "FamilyRequests"
    assert cfg.name_key == "family-name"


def test_load_config_missing_entity_derives_from_entities(tmp_path):
    (tmp_path / "_meta").mkdir()
    (tmp_path / "_meta/config.yaml").write_text("entities: Customers\n")
    cfg = load_config(tmp_path)
    assert cfg.entity == "customer"
    assert cfg.requests_folder == "CustomerRequests"


def test_load_config_empty_file_defaults(tmp_path):
    (tmp_path / "_meta").mkdir()
    (tmp_path / "_meta/config.yaml").write_text("")
    assert load_config(tmp_path) == VaultConfig()


@pytest.mark.parametrize("content", [
    "entities: [a, b]\n",              # not a string
    "entities: 'has space'\n",         # fails charset
    "entities: .hidden\n",             # dot-prefixed
    "entities: people\n",              # reserved (case-insensitive)
    "entities: _meta\n",               # reserved
    "entities: \"Bad\\nName\"\n",      # newline
    "- just\n- a\n- list\n",           # not a mapping
    "entities: Ok\nentity: yes\n",     # YAML bool, not a string
])
def test_load_config_invalid_raises(tmp_path, content):
    (tmp_path / "_meta").mkdir()
    (tmp_path / "_meta/config.yaml").write_text(content)
    with pytest.raises(SchemaError):
        load_config(tmp_path)


def test_load_config_unparseable_yaml_raises(tmp_path):
    (tmp_path / "_meta").mkdir()
    (tmp_path / "_meta/config.yaml").write_text("entities: [unclosed\n")
    with pytest.raises(SchemaError):
        load_config(tmp_path)


def test_derive_entity():
    assert derive_entity("Clients") == "client"
    assert derive_entity("Customers") == "customer"
    assert derive_entity("Prey") == "prey"      # no trailing s: unchanged
    assert derive_entity("S") == "s"            # never strips to empty


def test_make_config_derives_and_validates():
    assert make_config("Vendors") == VaultConfig("Vendors", "vendor")
    assert make_config("Families", "family").entity == "family"
    with pytest.raises(SchemaError):
        make_config("People")
    with pytest.raises(SchemaError):
        make_config("Vendors", "bad entity")


def test_vaultconfig_rejects_injection_at_construction():
    with pytest.raises(SchemaError):
        VaultConfig(entity="client\nrole: admin")
    with pytest.raises(SchemaError):
        VaultConfig(entities="Bad Name")
    VaultConfig()                      # defaults stay valid
    VaultConfig("Families", "family")  # normal custom pair stays valid
