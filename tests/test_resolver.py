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


def test_readable_spaces_includes_self_named_spaces_that_do_not_exist_yet(tmp_path: Path):
    """A person added to org.yaml owns People/<id> before anything is written
    there. Resolving that off disk alone produced a vault whose context file
    listed Company/ as the only space while its routing rules sent every note
    to People/<id>/."""
    (tmp_path / "Company").mkdir(parents=True)
    spaces = readable_spaces(tmp_path, BOB, RULES)

    assert "People/bob" in spaces        # named by bob, not yet on disk
    assert "Teams/ops" in spaces         # same, via team binding
    assert "Clients/acme" not in spaces  # world-named: only disk can say
    # Whatever is listed must actually be writable where the rules say so.
    assert can_write_path("People/bob/Memory.md", BOB, RULES)


def test_self_named_expansion_ignores_rules_not_bound_to_the_reader(tmp_path: Path):
    """The shipped {entities}/* rule reads `role:admin` — its members are named
    by the world, so expanding it by an admin's own bindings would invent a
    Clients/<admin-id> nobody meant."""
    rules = (*RULES[:3], SpaceRule("Clients/*", read=("role:admin",), write=("role:admin",)))
    (tmp_path / "Company").mkdir(parents=True)

    assert "Clients/alice" not in readable_spaces(tmp_path, ALICE, rules)
    assert "Clients/admin" not in readable_spaces(tmp_path, ALICE, rules)
    assert "People/alice" in readable_spaces(tmp_path, ALICE, rules)


def test_self_named_expansion_cannot_reach_a_reserved_top(tmp_path: Path):
    rules = (SpaceRule("_meta/*", read=("person:{name}",), write=("person:{name}",)),)
    (tmp_path / "Company").mkdir(parents=True)
    # can_write_path already refuses _meta for everyone; enumeration must agree.
    assert readable_spaces(tmp_path, BOB, rules) == []
    assert not can_write_path("_meta/bob/x.md", BOB, rules)


def test_any_top_level_dir_is_a_nested_top(tmp_path):
    (tmp_path / "Vendors/Acme").mkdir(parents=True)
    (tmp_path / "Company").mkdir()
    (tmp_path / "_meta").mkdir()
    (tmp_path / ".hidden/x").mkdir(parents=True)
    assert enumerate_spaces(tmp_path) == ["Company", "Vendors/Acme"]


def test_space_of_path_generic_top():
    assert space_of_path("Vendors/Acme/notes.md") == "Vendors/Acme"
    assert space_of_path("Archive/old/x.md") == "Archive/old"
    assert space_of_path("_meta/spaces.yaml") is None
    assert space_of_path(".git/config") is None
    assert space_of_path("loose.md") is None
    assert space_of_path("Company/Home.md") == "Company"


def test_unknown_top_space_is_denied_without_a_rule():
    person = Person(id="joe", name="Joe", roles=("admin",))
    # no rule matches Archive/old -> denied even for an admin
    assert not can_write_path("Archive/old/x.md", person, ())
