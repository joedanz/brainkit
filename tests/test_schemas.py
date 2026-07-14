from pathlib import Path

import pytest

from brain.schemas import Org, Person, SchemaError, SpaceRule, load_org, load_spaces

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
    ("people:\n  alice: {name: Alice, email: Bob@Acme.com}\n"
     "  bob: {name: Bob, email: bob@acme.com}\n", "duplicate email"),
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
    ("spaces:\n  - {path: Company, read: [everyone], write: []}\n"
     "  - {path: Company, read: [everyone], write: []}\n", "duplicate"),
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
