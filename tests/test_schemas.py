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
