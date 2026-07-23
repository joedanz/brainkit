from pathlib import Path

import pytest
import yaml

from brain.frontmatter import split_frontmatter
from brain.resolver import space_of_path as _sop
from brain.shares import ShareError, amend_space_rule, remove_subject_from_rule, request_share, validate_space, validate_subject
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
