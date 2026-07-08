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


def test_unknown_subject_rejected(tmp_path: Path):
    f = tmp_path / "spaces.yaml"
    f.write_text('spaces:\n  - {path: Company, read: ["group:staff"], write: []}\n')
    with pytest.raises(SchemaError, match="group:staff"):
        load_spaces(f)


def test_duplicate_rule_path_rejected(tmp_path: Path):
    f = tmp_path / "spaces.yaml"
    f.write_text(
        "spaces:\n"
        "  - {path: Company, read: [everyone], write: []}\n"
        "  - {path: Company, read: [everyone], write: []}\n"
    )
    with pytest.raises(SchemaError, match="duplicate"):
        load_spaces(f)


def test_scalar_roles_rejected(tmp_path: Path):
    f = tmp_path / "org.yaml"
    f.write_text("people:\n  alice: {name: Alice Nguyen, roles: admin}\n")
    with pytest.raises(SchemaError, match="'alice'.*roles"):
        load_org(f)


def test_scalar_teams_rejected(tmp_path: Path):
    f = tmp_path / "org.yaml"
    f.write_text("people:\n  bob: {name: Bob Rivera, teams: ops}\n")
    with pytest.raises(SchemaError, match="'bob'.*teams"):
        load_org(f)


def test_non_dict_person_rejected(tmp_path: Path):
    f = tmp_path / "org.yaml"
    f.write_text("people:\n  alice: just a string\n")
    with pytest.raises(SchemaError, match="'alice'"):
        load_org(f)


def test_non_dict_spaces_entry_rejected(tmp_path: Path):
    f = tmp_path / "spaces.yaml"
    f.write_text("spaces:\n  - just a string\n")
    with pytest.raises(SchemaError, match="mapping"):
        load_spaces(f)


def test_scalar_read_rejected(tmp_path: Path):
    f = tmp_path / "spaces.yaml"
    f.write_text("spaces:\n  - {path: Company, read: everyone, write: []}\n")
    with pytest.raises(SchemaError, match="'Company'.*read must be a list"):
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


def test_duplicate_email_rejected(tmp_path: Path):
    f = tmp_path / "org.yaml"
    f.write_text(
        "people:\n"
        "  alice: {name: Alice, email: Bob@Acme.com}\n"
        "  bob: {name: Bob, email: bob@acme.com}\n"
    )
    with pytest.raises(SchemaError, match="duplicate email"):
        load_org(f)


def test_non_string_email_rejected(tmp_path: Path):
    f = tmp_path / "org.yaml"
    f.write_text("people:\n  alice: {name: Alice, email: [x]}\n")
    with pytest.raises(SchemaError, match="'alice'.*email must be a string"):
        load_org(f)


def test_whitespace_email_rejected(tmp_path: Path):
    f = tmp_path / "org.yaml"
    f.write_text('people:\n  alice: {name: Alice, email: "a b@acme.com"}\n')
    with pytest.raises(SchemaError, match="email must not contain whitespace"):
        load_org(f)


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
