from pathlib import Path

from brain.resolver import (
    can_read,
    can_write_path,
    enumerate_spaces,
    readable_spaces,
    space_of_path,
)
from brain.schemas import Person, SpaceRule

RULES = (
    SpaceRule("Company", read=("everyone",), write=("role:admin",)),
    SpaceRule("Teams/*", read=("team:{name}",), write=("team:{name}",)),
    SpaceRule("People/*", read=("person:{name}",), write=("person:{name}",)),
    SpaceRule("Clients/*", read=("everyone",), write=("role:admin",)),
    SpaceRule("Clients/private-co", read=("person:alice",), write=("person:alice",)),
)

ALICE = Person(id="alice", name="Alice", roles=("admin",), teams=("sales",))
BOB = Person(id="bob", name="Bob", roles=(), teams=("ops",))


def make_master(tmp_path: Path) -> Path:
    for d in ("Company", "Teams/sales", "Teams/ops", "People/alice",
              "People/bob", "Clients/acme", "Clients/private-co", "_meta"):
        (tmp_path / d).mkdir(parents=True)
    return tmp_path


def test_enumerate_spaces_skips_meta(tmp_path: Path):
    master = make_master(tmp_path)
    spaces = enumerate_spaces(master)
    assert "Company" in spaces
    assert "Teams/sales" in spaces
    assert not any(s.startswith("_meta") for s in spaces)


def test_space_of_path():
    assert space_of_path("Company/Decisions/d1.md") == "Company"
    assert space_of_path("Teams/sales/notes.md") == "Teams/sales"
    assert space_of_path("People/alice/Memory.md") == "People/alice"
    assert space_of_path("_meta/org.yaml") is None
    assert space_of_path("stray-root-file.md") is None


def test_can_read_wildcard_binding():
    assert can_read("People/alice", ALICE, RULES)
    assert not can_read("People/alice", BOB, RULES)
    assert can_read("Teams/ops", BOB, RULES)
    assert not can_read("Teams/sales", BOB, RULES)


def test_exact_rule_beats_wildcard():
    assert can_read("Clients/private-co", ALICE, RULES)
    assert not can_read("Clients/private-co", BOB, RULES)  # exact rule overrides Clients/*
    assert can_read("Clients/acme", BOB, RULES)


def test_can_write_path():
    assert can_write_path("People/bob/Actions/todo.md", BOB, RULES)
    assert not can_write_path("Company/Memory.md", BOB, RULES)      # not admin
    assert can_write_path("Company/Memory.md", ALICE, RULES)        # role:admin
    assert not can_write_path("_meta/spaces.yaml", ALICE, RULES)    # _meta locked for all
    assert not can_write_path("stray-root-file.md", ALICE, RULES)   # outside any space


def test_traversal_and_absolute_paths_rejected():
    assert not can_write_path("People/bob/../../Company/x.md", BOB, RULES)
    assert not can_write_path("Company/../_meta/org.yaml", ALICE, RULES)
    assert space_of_path("/People/alice/x.md") is None
    assert space_of_path("Teams/ops/../sales/y.md") is None


def test_readable_spaces(tmp_path: Path):
    master = make_master(tmp_path)
    spaces = set(readable_spaces(master, BOB, RULES))
    assert spaces == {"Company", "Teams/ops", "People/bob", "Clients/acme"}
