import subprocess

import pytest
from pathlib import Path

from brain.clients import (
    ClientError,
    ClientProvision,
    normalize_client_name,
    request_client,
    append_client_grant,
    materialize_clients,
)
from brain.frontmatter import split_frontmatter
from brain.resolver import space_of_path, can_read, can_write_path
from brain.schemas import Org, Person, VaultConfig, load_spaces


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


def test_append_grant_rejects_injecting_owner_id(tmp_path: Path):
    sp = tmp_path / "spaces.yaml"
    sp.write_text(_BASE_SPACES)
    with pytest.raises(ClientError):
        append_client_grant(sp, "Injected Client", 'x"], "read": ["everyone')
    # nothing was appended — file is unchanged, so no world-readable rule leaked
    assert sp.read_text() == _BASE_SPACES


def _git_init(master: Path) -> None:
    subprocess.run(["git", "-C", str(master), "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(master), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(master), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-m", "seed"], check=True, capture_output=True)


def _git_master(tmp_path: Path) -> Path:
    """A bare git-initialized master with spaces.yaml and an empty Clients/
    tree — no requests filed yet."""
    master = tmp_path / "master"
    (master / "_meta").mkdir(parents=True)
    (master / "_meta/spaces.yaml").write_text(_BASE_SPACES)
    (master / "Clients").mkdir()
    _git_init(master)
    return master


def _org() -> Org:
    return Org(people={"joe": Person(id="joe", name="Joe Danziger", roles=(), teams=())})


def _master_with_request(tmp_path: Path) -> Path:
    master = tmp_path / "master"
    (master / "_meta").mkdir(parents=True)
    (master / "_meta/spaces.yaml").write_text(_BASE_SPACES)
    (master / "Clients").mkdir()
    request_client(master, "joe", "Danziger Family",
                   "Members: Mikey (football), Roslyn (basketball). [[JCC Maccabi Games 2026]]\n",
                   "2026-07-22", source="People/joe/Inbox/chat.md")
    _git_init(master)
    return master


def test_materialize_creates_space_grant_note_and_log(tmp_path: Path):
    master = _master_with_request(tmp_path)
    org = Org(people={"joe": Person(id="joe", name="Joe Danziger", roles=(), teams=())})

    result = materialize_clients(master, org, today="2026-07-22")

    assert result == [ClientProvision("Danziger Family", "joe", "created")]
    note = master / "Clients/Danziger Family/Danziger Family.md"
    assert note.exists()
    meta, body = split_frontmatter(note.read_text())
    assert meta["entity"] == "client" and meta["owner"] == "joe"
    assert "Mikey" in body
    rules = load_spaces(master / "_meta/spaces.yaml")
    joe = org.people["joe"]
    assert can_write_path("Clients/Danziger Family/x.md", joe, rules)
    log = (master / "_meta/clients/created.log").read_text()
    assert "Danziger Family" in log and "joe" in log
    # request artifact consumed
    assert not list((master / "People/joe/ClientRequests").glob("*.md"))


def test_materialize_merges_when_owner_already_granted(tmp_path: Path):
    master = tmp_path / "master"
    (master / "_meta").mkdir(parents=True)
    (master / "_meta/spaces.yaml").write_text(_BASE_SPACES)
    append_client_grant(master / "_meta/spaces.yaml", "Danziger Family", "joe")
    note = master / "Clients/Danziger Family/Danziger Family.md"
    note.parent.mkdir(parents=True)
    note.write_text("---\nentity: client\n---\n# Danziger Family\nExisting.\n")
    request_client(master, "joe", "Danziger Family", "New detail: moved to KC.\n", "2026-07-22")
    _git_init(master)
    org = Org(people={"joe": Person(id="joe", name="Joe Danziger", roles=(), teams=())})

    result = materialize_clients(master, org, today="2026-07-22")
    assert result == [ClientProvision("Danziger Family", "joe", "merged")]
    text = note.read_text()
    assert "Existing." in text and "New detail: moved to KC." in text
    # no duplicate grant
    assert sum(r.path == "Clients/Danziger Family"
               for r in load_spaces(master / "_meta/spaces.yaml")) == 1


def test_materialize_rejects_name_owned_by_other_without_leak(tmp_path: Path):
    master = tmp_path / "master"
    (master / "_meta").mkdir(parents=True)
    (master / "_meta/spaces.yaml").write_text(_BASE_SPACES)
    append_client_grant(master / "_meta/spaces.yaml", "Smith", "mary")  # mary owns Smith
    (master / "Clients/Smith").mkdir(parents=True)
    (master / "Clients/Smith/Smith.md").write_text("Mary's client.\n")
    request_client(master, "joe", "Smith", "Joe's new Smith.\n", "2026-07-22")
    _git_init(master)
    org = Org(people={
        "joe": Person(id="joe", name="Joe", roles=(), teams=()),
        "mary": Person(id="mary", name="Mary", roles=(), teams=()),
    })

    result = materialize_clients(master, org, today="2026-07-22")
    assert result[0].status == "rejected"
    # no new grant for joe, mary's note untouched, ownership never revealed
    rules = load_spaces(master / "_meta/spaces.yaml")
    assert not can_write_path("Clients/Smith/x.md", org.people["joe"], rules)
    assert "Joe's new Smith." not in (master / "Clients/Smith/Smith.md").read_text()
    inbox = list((master / "People/joe/Inbox").glob("*.md"))
    assert inbox and "already exists" in inbox[0].read_text()
    assert "mary" not in inbox[0].read_text()  # no owner leak
    assert not list((master / "People/joe/ClientRequests").glob("*.md"))  # consumed, no loop


def test_materialize_rejects_owner_mismatch(tmp_path: Path):
    master = tmp_path / "master"
    (master / "_meta").mkdir(parents=True)
    (master / "_meta/spaces.yaml").write_text(_BASE_SPACES)
    (master / "Clients").mkdir()
    rel = request_client(master, "joe", "Danziger", "body\n", "2026-07-22")
    # tamper: claim a different owner than the path's <pid>
    p = master / rel
    p.write_text(p.read_text().replace("owner: joe", "owner: mary"))
    _git_init(master)
    org = Org(people={"joe": Person(id="joe", name="Joe", roles=(), teams=()),
                      "mary": Person(id="mary", name="Mary", roles=(), teams=())})

    result = materialize_clients(master, org, today="2026-07-22")
    assert result[0].status == "rejected" and "owner" in result[0].reason
    assert not (master / "Clients/Danziger").exists()


def test_materialize_skips_poison_utf8_request_and_processes_valid_one(tmp_path: Path):
    """An invalid-UTF-8 request file must not abort the whole sweep — the
    valid request alongside it still gets provisioned, and the poison file
    is left untouched for inspection."""
    master = tmp_path / "master"
    (master / "_meta").mkdir(parents=True)
    (master / "_meta/spaces.yaml").write_text(_BASE_SPACES)
    (master / "Clients").mkdir()
    request_client(master, "joe", "Danziger Family",
                   "Members: Mikey (football).\n", "2026-07-22",
                   source="People/joe/Inbox/chat.md")
    poison = master / "People/joe/ClientRequests/poison.md"
    poison.write_bytes(b"\xff\xfe garbage")
    _git_init(master)
    org = Org(people={"joe": Person(id="joe", name="Joe Danziger", roles=(), teams=())})

    result = materialize_clients(master, org, today="2026-07-22")

    assert result == [ClientProvision("Danziger Family", "joe", "created")]
    assert poison.exists()
    assert poison.read_bytes() == b"\xff\xfe garbage"


def test_materialize_skips_malformed_pid_without_partial_writes(tmp_path: Path):
    master = tmp_path / "master"
    (master / "_meta").mkdir(parents=True)
    (master / "_meta/spaces.yaml").write_text(_BASE_SPACES)
    (master / "Clients").mkdir()
    # a request under a pid folder whose name is not a valid owner id
    bad = master / 'People/joe joe/ClientRequests/2026-07-22-x.md'
    bad.parent.mkdir(parents=True)
    bad.write_text("---\nclient-name: X\nowner: joe\nentity: client\nsource: s\ncreated: 2026-07-22\n---\nbody\n")
    _git_init(master)
    org = Org(people={"joe": Person(id="joe", name="Joe", roles=(), teams=())})
    result = materialize_clients(master, org, today="2026-07-22")
    # skipped cleanly: no crash, no Clients space created, request left in place
    assert all(p.status != "created" for p in result)
    assert not any(master.glob("Clients/*/*.md"))
    assert bad.exists()


def test_materialize_unregistered_pid_collision_rejects_not_crashes(tmp_path: Path):
    # joe owns "Danziger Family"; a request arrives from a valid-charset pid
    # that is NOT in org.people (e.g. removed from org.yaml). It must be
    # refused fail-closed, never crash the batch.
    master = tmp_path / "master"
    (master / "_meta").mkdir(parents=True)
    (master / "_meta/spaces.yaml").write_text(_BASE_SPACES)
    append_client_grant(master / "_meta/spaces.yaml", "Danziger Family", "joe")
    note = master / "Clients/Danziger Family/Danziger Family.md"
    note.parent.mkdir(parents=True)
    note.write_text("Joe's client.\n")
    request_client(master, "ghost", "Danziger Family", "ghost tries to merge\n", "2026-07-22")
    _git_init(master)
    org = Org(people={"joe": Person(id="joe", name="Joe", roles=(), teams=())})  # no 'ghost'

    result = materialize_clients(master, org, today="2026-07-22")  # must not raise

    assert result and result[0].status == "rejected"
    # no grant minted for ghost; joe's rule still the only Danziger rule
    assert sum(r.path == "Clients/Danziger Family"
               for r in load_spaces(master / "_meta/spaces.yaml")) == 1
    # joe's note untouched; ghost got an inbox note that never names joe
    assert note.read_text() == "Joe's client.\n"
    inbox = list((master / "People/ghost/Inbox").glob("*.md"))
    assert inbox and "joe" not in inbox[0].read_text().lower()
    assert not list((master / "People/ghost/ClientRequests").glob("*.md"))  # consumed


FAM = VaultConfig(entities="Families", entity="family")


def test_request_client_custom_noun_writes_derived_folder_and_key(tmp_path):
    rel = request_client(tmp_path, "joe", "Danziger", "Moved to KC.\n",
                         "2026-07-23", config=FAM)
    assert rel.startswith("People/joe/FamilyRequests/")
    text = (tmp_path / rel).read_text()
    assert "family-name: Danziger" in text
    assert "entity: family" in text
    assert "client-name" not in text


def test_materialize_custom_noun_provisions_custom_tree(tmp_path):
    master = _git_master(tmp_path)
    (master / "_meta/config.yaml").write_text("entities: Families\nentity: family\n")
    request_client(master, "joe", "Danziger", "body\n", "2026-07-23", config=FAM)
    results = materialize_clients(master, _org(), today="2026-07-23")
    assert [r.status for r in results] == ["created"]
    assert (master / "Families/Danziger/Danziger.md").is_file()
    rules = load_spaces(master / "_meta/spaces.yaml")
    assert any(r.path == "Families/Danziger" for r in rules)


def test_materialize_accepts_legacy_client_name_key(tmp_path):
    master = _git_master(tmp_path)
    (master / "_meta/config.yaml").write_text("entities: Families\nentity: family\n")
    req = master / "People/joe/FamilyRequests/2026-07-23-x.md"
    req.parent.mkdir(parents=True, exist_ok=True)
    req.write_text("---\nclient-name: Smith\nowner: joe\ncreated: 2026-07-23\n---\nb\n")
    results = materialize_clients(master, _org(), today="2026-07-23")
    assert [r.status for r in results] == ["created"]
    assert (master / "Families/Smith").is_dir()
