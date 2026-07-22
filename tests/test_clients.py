import pytest
from pathlib import Path

from brain.clients import ClientError, normalize_client_name, request_client, append_client_grant
from brain.frontmatter import split_frontmatter
from brain.resolver import space_of_path, can_read, can_write_path
from brain.schemas import Person, load_spaces


@pytest.mark.parametrize("raw,expected", [
    ("Danziger Family", "Danziger Family"),
    ("  John   Danziger  ", "John Danziger"),
    ("Smith (Acme)", "Smith (Acme)"),
    ("O'Brien & Sons", "O'Brien & Sons"),
])
def test_normalize_keeps_human_readable_names(raw, expected):
    assert normalize_client_name(raw) == expected


@pytest.mark.parametrize("bad", [
    "", "   ", ".", "..", "a/b", "a\\b", ".hidden", 'has"quote', "line\nbreak",
])
def test_normalize_rejects_unsafe(bad):
    with pytest.raises(ClientError):
        normalize_client_name(bad)


def test_request_client_writes_artifact_in_owner_space(tmp_path: Path):
    rel = request_client(tmp_path, "joe", "Danziger Family",
                         "Members: Mikey (football), Roslyn (basketball).\n",
                         "2026-07-22", source="People/joe/Inbox/chat.md")
    assert space_of_path(rel) == "People/joe"
    meta, body = split_frontmatter((tmp_path / rel).read_text())
    assert meta["client-name"] == "Danziger Family"
    assert meta["owner"] == "joe"
    assert meta["entity"] == "client"
    assert "Mikey" in body


def test_request_client_rejects_empty_body(tmp_path: Path):
    with pytest.raises(ClientError):
        request_client(tmp_path, "joe", "Danziger", "   \n", "2026-07-22")


def test_request_client_refuses_symlinked_ancestor(tmp_path: Path):
    (tmp_path / "People").mkdir()
    (tmp_path / "People/joe").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ClientError):
        request_client(tmp_path, "joe", "Danziger", "body\n", "2026-07-22")


_BASE_SPACES = """\
spaces:
  - {path: Company,     read: [everyone],        write: ["role:admin"]}
  - {path: "People/*",  read: ["person:{name}"], write: ["person:{name}"]}
  - {path: "Clients/*", read: ["role:admin"],    write: ["role:admin"]}
"""


def test_append_grant_is_owner_bound_and_fail_closed(tmp_path: Path):
    sp = tmp_path / "spaces.yaml"
    sp.write_text(_BASE_SPACES)
    assert append_client_grant(sp, "Danziger Family", "joe") is True

    rules = load_spaces(sp)
    joe = Person(id="joe", name="Joe", roles=(), teams=())
    mary = Person(id="mary", name="Mary", roles=(), teams=())
    assert can_write_path("Clients/Danziger Family/Danziger Family.md", joe, rules)
    assert can_read("Clients/Danziger Family", joe, rules)
    # fail-closed: nobody else, not even by the Clients/* wildcard (admin-only)
    assert not can_write_path("Clients/Danziger Family/x.md", mary, rules)
    assert not can_read("Clients/Danziger Family", mary, rules)


def test_append_grant_is_idempotent(tmp_path: Path):
    sp = tmp_path / "spaces.yaml"
    sp.write_text(_BASE_SPACES)
    assert append_client_grant(sp, "Danziger Family", "joe") is True
    assert append_client_grant(sp, "Danziger Family", "joe") is False
    # exactly one rule for the path (load_spaces would raise on a duplicate)
    assert sum(r.path == "Clients/Danziger Family" for r in load_spaces(sp)) == 1
