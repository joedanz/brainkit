import subprocess
from pathlib import Path

import pytest
import yaml

from brain.frontmatter import split_frontmatter
from brain.resolver import space_of_path as _sop
from brain.schemas import Org, Person
from brain.shares import (
    ShareError, ShareOutcome, amend_space_rule, list_pending_shares,
    remove_subject_from_rule, request_share, sweep_shares, validate_space,
    validate_subject,
)
from brain.schemas import load_spaces


@pytest.mark.parametrize("subject,expected", [
    ("person:mary", ("person", "mary")),
    ("team:concierge", ("team", "concierge")),
    ("person:j.o-e_2", ("person", "j.o-e_2")),
])
def test_validate_subject_accepts_person_and_team(subject, expected):
    assert validate_subject(subject) == expected


@pytest.mark.parametrize("bad", [
    "mary", "role:admin", "everyone", "person:", "person:has space",
    'person:x"], "read": ["everyone', "team:with\nnewline", "person:a/b", "",
])
def test_validate_subject_rejects_everything_else(bad):
    with pytest.raises(ShareError):
        validate_subject(bad)


@pytest.mark.parametrize("space", ["Clients/Danziger Family", "Teams/sales"])
def test_validate_space_accepts_two_segment_shared_spaces(space):
    validate_space(space)  # no raise


@pytest.mark.parametrize("bad", [
    "People/joe", "Company", "Clients", "Clients/A/B", "Clients/*",
    "../etc", "_meta/shares", "",
])
def test_validate_space_rejects_non_shareable_paths(bad):
    with pytest.raises(ShareError):
        validate_space(bad)


_SPACES = """\
spaces:
  # who reads what — comments must survive surgery
  - {path: Company,     read: [everyone],        write: ["role:admin"]}
  - {path: "People/*",  read: ["person:{name}"], write: ["person:{name}"]}
  - {path: "Clients/*", read: ["role:admin"],    write: ["role:admin"]}
  - {path: "Clients/Danziger Family", read: ["role:admin", "person:joe"], write: ["role:admin", "person:joe"]}
"""


def _write(tmp_path: Path) -> Path:
    sp = tmp_path / "spaces.yaml"
    sp.write_text(_SPACES)
    return sp


def _other_lines(text: str) -> list[str]:
    return [l for l in text.splitlines() if "Danziger" not in l]


def test_amend_read_adds_reader_only(tmp_path: Path):
    sp = _write(tmp_path)
    before_others = _other_lines(sp.read_text())
    assert amend_space_rule(sp, "Clients/Danziger Family", "person:mary", "read") is True
    rules = {r.path: r for r in load_spaces(sp)}
    r = rules["Clients/Danziger Family"]
    assert "person:mary" in r.read and "person:mary" not in r.write
    # every non-rule line byte-identical (comments included)
    assert _other_lines(sp.read_text()) == before_others


def test_amend_write_adds_both_lists_and_team_works(tmp_path: Path):
    sp = _write(tmp_path)
    assert amend_space_rule(sp, "Clients/Danziger Family", "team:concierge", "write") is True
    r = {r.path: r for r in load_spaces(sp)}["Clients/Danziger Family"]
    assert "team:concierge" in r.read and "team:concierge" in r.write


def test_amend_is_idempotent_and_read_then_write_upgrades(tmp_path: Path):
    sp = _write(tmp_path)
    assert amend_space_rule(sp, "Clients/Danziger Family", "person:mary", "read") is True
    assert amend_space_rule(sp, "Clients/Danziger Family", "person:mary", "read") is False
    # upgrade: already a reader, now gets write
    assert amend_space_rule(sp, "Clients/Danziger Family", "person:mary", "write") is True
    r = {r.path: r for r in load_spaces(sp)}["Clients/Danziger Family"]
    assert r.read.count("person:mary") == 1 and "person:mary" in r.write


def test_amend_refuses_missing_rule_wildcard_and_bad_subject(tmp_path: Path):
    sp = _write(tmp_path)
    with pytest.raises(ShareError):
        amend_space_rule(sp, "Clients/Nakamura", "person:mary", "read")  # no exact rule
    with pytest.raises(ShareError):
        amend_space_rule(sp, "Clients/*", "person:mary", "read")
    with pytest.raises(ShareError):
        amend_space_rule(sp, "Clients/Danziger Family", 'person:x"],"read":["everyone', "read")
    assert sp.read_text() == _SPACES  # nothing written on any refusal


def test_remove_strips_both_lists_and_protects_admin(tmp_path: Path):
    sp = _write(tmp_path)
    amend_space_rule(sp, "Clients/Danziger Family", "person:mary", "write")
    assert remove_subject_from_rule(sp, "Clients/Danziger Family", "person:mary") is True
    r = {r.path: r for r in load_spaces(sp)}["Clients/Danziger Family"]
    assert "person:mary" not in r.read and "person:mary" not in r.write
    with pytest.raises(ShareError):
        remove_subject_from_rule(sp, "Clients/Danziger Family", "role:admin")
    assert remove_subject_from_rule(sp, "Clients/Danziger Family", "person:ghost") is False


def test_request_share_writes_artifact_in_owner_space(tmp_path: Path):
    rel = request_share(tmp_path, "joe", "Clients/Danziger Family",
                        "person:mary", "write", "2026-07-22",
                        body="Mary covers the KC trip.\n")
    assert _sop(rel) == "People/joe"
    meta, body = split_frontmatter((tmp_path / rel).read_text())
    assert meta["space"] == "Clients/Danziger Family"
    assert meta["share-with"] == "person:mary"
    assert meta["access"] == "write"
    assert meta["action"] == "share"
    assert meta["owner"] == "joe"
    assert "KC trip" in body


def test_request_share_revoke_and_validation(tmp_path: Path):
    rel = request_share(tmp_path, "joe", "Clients/Danziger Family",
                        "person:mary", "read", "2026-07-22", action="revoke")
    meta, _ = split_frontmatter((tmp_path / rel).read_text())
    assert meta["action"] == "revoke"
    for kwargs in (
        dict(space="People/joe", share_with="person:mary", access="read"),
        dict(space="Clients/X", share_with="mary", access="read"),
        dict(space="Clients/X", share_with="person:mary", access="admin"),
        dict(space="Clients/X", share_with="person:mary", access="read", action="delete"),
    ):
        with pytest.raises(ShareError):
            request_share(tmp_path, "joe", kwargs["space"], kwargs["share_with"],
                          kwargs["access"], "2026-07-22",
                          action=kwargs.get("action", "share"))


def test_request_share_refuses_symlinked_ancestor(tmp_path: Path):
    (tmp_path / "People").mkdir()
    (tmp_path / "People/joe").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ShareError):
        request_share(tmp_path, "joe", "Clients/X", "person:mary", "read", "2026-07-22")


def test_request_share_rejects_newline_person_id(tmp_path: Path):
    """Verify person_id injection attacks are rejected and no file is written."""
    with pytest.raises(ShareError):
        request_share(tmp_path, "joe\naccess: write", "Clients/X", "person:mary",
                      "read", "2026-07-22")
    # Verify nothing was written
    assert not (tmp_path / "People").exists()


_ORG = Org(people={
    "admin": Person(id="admin", name="Admin", roles=("admin",)),
    "joe": Person(id="joe", name="Joe Danziger"),
    "mary": Person(id="mary", name="Mary Ops", teams=("concierge",)),
})


def _git_init(master: Path) -> None:
    for cmd in (["init", "-b", "main"], ["add", "-A"],
                ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "seed"]):
        subprocess.run(["git", "-C", str(master), *cmd], check=True, capture_output=True)


def _master(tmp_path: Path) -> Path:
    m = tmp_path / "master"
    (m / "_meta").mkdir(parents=True)
    (m / "_meta/spaces.yaml").write_text(_SPACES)
    (m / "Clients/Danziger Family").mkdir(parents=True)
    (m / "Clients/Danziger Family/Danziger Family.md").write_text("client\n")
    return m


def test_sweep_queues_valid_share_request(tmp_path: Path):
    m = _master(tmp_path)
    request_share(m, "joe", "Clients/Danziger Family", "person:mary", "write",
                  "2026-07-22", body="context for approver\n")
    _git_init(m)
    out = sweep_shares(m, _ORG, today="2026-07-22")
    assert [o.status for o in out] == ["queued"]
    pending = list_pending_shares(m)
    assert len(pending) == 1
    p = pending[0]
    assert p["from"] == "joe" and p["space"] == "Clients/Danziger Family"
    assert p["share-with"] == "person:mary" and p["access"] == "write"
    assert "context for approver" in p["body"]
    assert not list((m / "People/joe/ShareRequests").glob("*.md"))  # consumed


def test_sweep_nonowner_request_is_tampering(tmp_path: Path):
    m = _master(tmp_path)
    # mary does NOT own Danziger Family (rule grants joe)
    request_share(m, "mary", "Clients/Danziger Family", "person:mary", "read",
                  "2026-07-22")
    _git_init(m)
    out = sweep_shares(m, _ORG, today="2026-07-22")
    assert [o.status for o in out] == ["tampering"]
    assert not list_pending_shares(m)  # never queued
    assert not list((m / "People/mary/ShareRequests").glob("*.md"))  # consumed


def test_sweep_unknown_recipient_and_already_shared(tmp_path: Path):
    m = _master(tmp_path)
    request_share(m, "joe", "Clients/Danziger Family", "person:ghost", "read",
                  "2026-07-22")
    request_share(m, "joe", "Clients/Danziger Family", "person:joe", "read",
                  "2026-07-22")  # joe already on the rule
    _git_init(m)
    out = sweep_shares(m, _ORG, today="2026-07-22")
    assert sorted(o.status for o in out) == ["rejected", "rejected"]
    assert not list_pending_shares(m)
    inbox = list((m / "People/joe/Inbox").glob("*.md"))
    texts = " ".join(f.read_text() for f in inbox)
    assert "already" in texts.lower()          # already-shared note
    assert "ghost" in texts                    # unknown-recipient note


def test_sweep_decided_ids_never_requeue(tmp_path: Path):
    m = _master(tmp_path)
    rel = request_share(m, "joe", "Clients/Danziger Family", "person:mary",
                        "read", "2026-07-22")
    stem = Path(rel).stem
    decided = m / f"_meta/shares/approved/joe-{_slug_for_test(stem)}.md"
    decided.parent.mkdir(parents=True)
    decided.write_text("---\nshare-id: x\n---\n")
    _git_init(m)
    out = sweep_shares(m, _ORG, today="2026-07-22")
    assert out == []            # stale request dropped, nothing queued
    assert not list_pending_shares(m)
    assert not list((m / "People/joe/ShareRequests").glob("*.md"))


def _slug_for_test(stem: str) -> str:
    from brain.promotions import _slug
    return _slug(stem)
