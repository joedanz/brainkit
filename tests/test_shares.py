import subprocess
from pathlib import Path

import pytest
import yaml

from brain.frontmatter import split_frontmatter
from brain.resolver import space_of_path as _sop
from brain.schemas import Org, Person
from brain.shares import (
    ShareError, ShareOutcome, admin_revoke, amend_space_rule,
    approve_share, generate_decider_section, generate_space_shares_section,
    list_pending_shares, may_decide,
    reject_share, remove_subject_from_rule, request_share, sweep_approvals, sweep_shares,
    validate_space, validate_subject,
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
    "mary", "role:admin", "everyone:x", "person:", "person:has space",
    'person:x"], "read": ["everyone', "team:with\nnewline", "person:a/b", "",
])
def test_validate_subject_rejects_everything_else(bad):
    with pytest.raises(ShareError):
        validate_subject(bad)


def test_validate_subject_everyone():
    assert validate_subject("everyone") == ("everyone", "")
    with pytest.raises(ShareError):
        validate_subject("everyone:x")


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


def test_sweep_revoke_auto_applies_and_archives(tmp_path: Path):
    m = _master(tmp_path)
    amend_space_rule(m / "_meta/spaces.yaml", "Clients/Danziger Family",
                     "person:mary", "write")
    request_share(m, "joe", "Clients/Danziger Family", "person:mary", "read",
                  "2026-07-22", action="revoke")
    _git_init(m)
    out = sweep_shares(m, _ORG, today="2026-07-22")
    assert [o.status for o in out] == ["revoked"]
    r = {r.path: r for r in load_spaces(m / "_meta/spaces.yaml")}["Clients/Danziger Family"]
    assert "person:mary" not in r.read and "person:mary" not in r.write
    archived = list((m / "_meta/shares/revoked").glob("*.md"))
    assert len(archived) == 1 and "revoked-on: 2026-07-22" in archived[0].read_text()
    assert not list((m / "People/joe/ShareRequests").glob("*.md"))


def test_sweep_revoke_guards(tmp_path: Path):
    m = _master(tmp_path)
    # self-revocation refused; absent subject -> "wasn't shared"
    request_share(m, "joe", "Clients/Danziger Family", "person:joe", "read",
                  "2026-07-22", action="revoke")
    request_share(m, "joe", "Clients/Danziger Family", "person:mary", "read",
                  "2026-07-22", action="revoke")
    _git_init(m)
    out = sweep_shares(m, _ORG, today="2026-07-22")
    assert sorted(o.status for o in out) == ["rejected", "rejected"]
    texts = " ".join(f.read_text() for f in (m / "People/joe/Inbox").glob("*.md"))
    assert "your own access" in texts and "not shared" in texts.lower()
    # joe's own grant untouched
    r = {r.path: r for r in load_spaces(m / "_meta/spaces.yaml")}["Clients/Danziger Family"]
    assert "person:joe" in r.read and "person:joe" in r.write


def test_sweep_skips_poison_utf8_request_and_processes_valid_one(tmp_path: Path):
    """An invalid-UTF-8 request file must not abort the whole sweep — the
    valid share request alongside it still gets queued, and the poison file
    is left untouched for inspection."""
    m = _master(tmp_path)
    request_share(m, "joe", "Clients/Danziger Family", "person:mary", "write",
                  "2026-07-22", body="context for approver\n")
    poison = m / "People/joe/ShareRequests/poison.md"
    poison.write_bytes(b"\xff\xfe garbage")
    _git_init(m)
    out = sweep_shares(m, _ORG, today="2026-07-22")
    assert [o.status for o in out] == ["queued"]
    assert len(list_pending_shares(m)) == 1
    assert poison.exists()
    assert poison.read_bytes() == b"\xff\xfe garbage"


def test_sweep_revoke_by_team_derived_write_recipient_is_rejected_not_tampering(tmp_path: Path):
    """mary holds write only via team:concierge (not literally on the rule's
    write list). She must not be able to auto-revoke joe, the bound owner."""
    m = _master(tmp_path)
    amend_space_rule(m / "_meta/spaces.yaml", "Clients/Danziger Family",
                     "team:concierge", "write")
    request_share(m, "mary", "Clients/Danziger Family", "person:joe", "read",
                  "2026-07-22", action="revoke")
    _git_init(m)
    before_after_amend = (m / "_meta/spaces.yaml").read_text()
    out = sweep_shares(m, _ORG, today="2026-07-22")
    assert [o.status for o in out] == ["rejected"]
    r = {r.path: r for r in load_spaces(m / "_meta/spaces.yaml")}["Clients/Danziger Family"]
    assert "person:joe" in r.write  # owner's grant survives
    assert (m / "_meta/spaces.yaml").read_text() == before_after_amend  # untouched by the revoke attempt
    inbox = list((m / "People/mary/Inbox").glob("*.md"))
    assert inbox and "owner" in inbox[0].read_text().lower()
    assert not list((m / "People/mary/ShareRequests").glob("*.md"))  # consumed


def test_sweep_revoke_by_direct_write_recipient_against_owner_is_rejected(tmp_path: Path):
    """mary holds write directly (person:mary on the rule) but is still not
    the bound owner — joe (first person: write entry) is. mary must not be
    able to auto-revoke joe."""
    m = _master(tmp_path)
    amend_space_rule(m / "_meta/spaces.yaml", "Clients/Danziger Family",
                     "person:mary", "write")
    request_share(m, "mary", "Clients/Danziger Family", "person:joe", "read",
                  "2026-07-22", action="revoke")
    _git_init(m)
    before = (m / "_meta/spaces.yaml").read_text()
    out = sweep_shares(m, _ORG, today="2026-07-22")
    assert [o.status for o in out] == ["rejected"]
    r = {r.path: r for r in load_spaces(m / "_meta/spaces.yaml")}["Clients/Danziger Family"]
    assert "person:joe" in r.write  # owner's grant survives
    assert (m / "_meta/spaces.yaml").read_text() == before
    inbox = list((m / "People/mary/Inbox").glob("*.md"))
    assert inbox and "admin" in inbox[0].read_text().lower()
    assert not list((m / "People/mary/ShareRequests").glob("*.md"))  # consumed


def test_sweep_revoke_by_owner_of_shared_recipient_still_works(tmp_path: Path):
    """Positive control: the bound owner (joe, first person: write entry)
    revoking a recipient they shared with still auto-applies."""
    m = _master(tmp_path)
    amend_space_rule(m / "_meta/spaces.yaml", "Clients/Danziger Family",
                     "person:mary", "write")
    request_share(m, "joe", "Clients/Danziger Family", "person:mary", "read",
                  "2026-07-22", action="revoke")
    _git_init(m)
    out = sweep_shares(m, _ORG, today="2026-07-22")
    assert [o.status for o in out] == ["revoked"]
    r = {r.path: r for r in load_spaces(m / "_meta/spaces.yaml")}["Clients/Danziger Family"]
    assert "person:mary" not in r.read and "person:mary" not in r.write
    assert "person:joe" in r.write


def test_sweep_revoke_unknown_subject_wording(tmp_path: Path):
    """Verify revoke requests with unknown subjects get 'not shared' wording, not 'Cannot share'."""
    m = _master(tmp_path)
    # person:ghost is not in org, but should be treated as "not shared" for revoke
    request_share(m, "joe", "Clients/Danziger Family", "person:ghost", "read",
                  "2026-07-22", action="revoke")
    _git_init(m)
    out = sweep_shares(m, _ORG, today="2026-07-22")
    assert [o.status for o in out] == ["rejected"]
    texts = " ".join(f.read_text() for f in (m / "People/joe/Inbox").glob("*.md"))
    # Should say "not shared", not "Cannot share"
    assert "not shared" in texts.lower()
    assert "Cannot share" not in texts


_ORG_YAML = """\
people:
  admin: {name: Admin, roles: [admin]}
  joe:   {name: Joe Danziger}
  mary:  {name: Mary Ops, teams: [concierge]}
"""


def _queued(tmp_path: Path) -> Path:
    m = _master(tmp_path)
    (m / "_meta/org.yaml").write_text(_ORG_YAML)
    request_share(m, "joe", "Clients/Danziger Family", "person:mary", "write",
                  "2026-07-22", body="context\n")
    _git_init(m)
    sweep_shares(m, _ORG, today="2026-07-22")
    return m


def test_approve_amends_rule_and_archives(tmp_path: Path):
    m = _queued(tmp_path)
    sid = list_pending_shares(m)[0]["id"]
    space = approve_share(m, sid, approver="admin", date="2026-07-23")
    assert space == "Clients/Danziger Family"
    r = {r.path: r for r in load_spaces(m / "_meta/spaces.yaml")}[space]
    assert "person:mary" in r.read and "person:mary" in r.write
    assert not list_pending_shares(m)
    archived = (m / "_meta/shares/approved" / f"{sid}.md").read_text()
    assert "approved-by: admin" in archived and "approved-on: 2026-07-23" in archived


def test_approve_validates_approver_and_id(tmp_path: Path):
    m = _queued(tmp_path)
    sid = list_pending_shares(m)[0]["id"]
    with pytest.raises(ShareError):
        approve_share(m, sid, approver="ghost", date="2026-07-23")
    with pytest.raises(ShareError):
        approve_share(m, "no-such-id", approver="admin", date="2026-07-23")


def test_approve_and_reject_reject_traversal_ids(tmp_path: Path):
    # A path/traversal-shaped id must fail the same not-found way as an
    # unknown one, and never touch anything outside _meta/shares/pending/.
    m = _queued(tmp_path)
    planted = m / "_meta/evil.md"
    planted.write_text("secret\n")

    with pytest.raises(ShareError):
        approve_share(m, "../../evil", approver="admin", date="2026-07-23")
    assert planted.read_text() == "secret\n"

    with pytest.raises(ShareError):
        reject_share(m, "../../evil", reason="n/a", date="2026-07-23", approver="admin")
    assert planted.read_text() == "secret\n"


def test_reject_archives_with_reason(tmp_path: Path):
    m = _queued(tmp_path)  # joe -> mary write share pending; mary consents
    sid = list_pending_shares(m)[0]["id"]
    reject_share(m, sid, reason="not appropriate", date="2026-07-23", approver="mary")
    assert not list_pending_shares(m)
    rejected = (m / "_meta/shares/rejected" / f"{sid}.md").read_text()
    assert "not appropriate" in rejected
    assert "rejected-by: mary" in rejected


def test_admin_revoke_direct(tmp_path: Path):
    m = _master(tmp_path)
    (m / "_meta/org.yaml").write_text(_ORG_YAML)
    amend_space_rule(m / "_meta/spaces.yaml", "Clients/Danziger Family",
                     "person:mary", "read")
    _git_init(m)
    assert admin_revoke(m, "Clients/Danziger Family", "person:mary",
                        date="2026-07-23") is True
    r = {r.path: r for r in load_spaces(m / "_meta/spaces.yaml")}["Clients/Danziger Family"]
    assert "person:mary" not in r.read
    assert list((m / "_meta/shares/revoked").glob("admin-*.md"))
    with pytest.raises(ShareError):
        admin_revoke(m, "Clients/Danziger Family", "role:admin", date="2026-07-23")


def test_space_shares_section_lists_pending_and_decided(tmp_path: Path):
    m = _queued(tmp_path)  # joe -> mary write share pending
    sec = generate_space_shares_section(m, "joe", today="2026-07-22")
    assert sec is not None and "## Space shares" in sec
    assert "Clients/Danziger Family" in sec and "person:mary" in sec
    assert "awaiting approval" in sec
    sid = list_pending_shares(m)[0]["id"]
    approve_share(m, sid, approver="admin", date="2026-07-23")
    sec2 = generate_space_shares_section(m, "joe", today="2026-07-23")
    assert "approved 2026-07-23" in sec2
    assert generate_space_shares_section(m, "mary", today="2026-07-23") is None


def test_space_shares_section_shows_revoked_entries(tmp_path: Path):
    """Verify revoked entries appear in the Shares.md section (regression: revoked archives use owner field)."""
    m = _master(tmp_path)
    (m / "_meta/org.yaml").write_text(_ORG_YAML)
    # Grant mary write access initially
    amend_space_rule(m / "_meta/spaces.yaml", "Clients/Danziger Family",
                     "person:mary", "write")
    # Joe files a revoke request for mary's access
    request_share(m, "joe", "Clients/Danziger Family", "person:mary", "read",
                  "2026-07-22", action="revoke")
    _git_init(m)
    # Revoke auto-applies during sweep
    sweep_shares(m, _ORG, today="2026-07-22")
    # Joe's section should show the revoked entry
    sec = generate_space_shares_section(m, "joe", today="2026-07-22")
    assert sec is not None
    assert "revoked 2026-07-22" in sec
    assert "person:mary" in sec
    assert "Clients/Danziger Family" in sec
    # Mary's section should NOT show joe's revoke (privacy filter)
    sec_mary = generate_space_shares_section(m, "mary", today="2026-07-22")
    assert sec_mary is None


_ORG_YAML_DECIDER = """\
people:
  admin:    {name: Admin, roles: [admin]}
  joe:      {name: Joe Danziger}
  mary:     {name: Mary Ops}
  lead_ops: {name: Lead Ops, roles: [lead], teams: [ops]}
  carol:    {name: Carol Support}
"""

_ORG_DECIDER = Org(people={
    "admin": Person(id="admin", name="Admin", roles=("admin",)),
    "joe": Person(id="joe", name="Joe Danziger"),
    "mary": Person(id="mary", name="Mary Ops"),
    "lead_ops": Person(id="lead_ops", name="Lead Ops", roles=("lead",), teams=("ops",)),
    "carol": Person(id="carol", name="Carol Support"),
})


def _decider_fixture(tmp_path: Path) -> Path:
    """Two pending shares: joe -> person:mary, joe -> team:ops."""
    m = _master(tmp_path)
    (m / "_meta/org.yaml").write_text(_ORG_YAML_DECIDER)
    request_share(m, "joe", "Clients/Danziger Family", "person:mary", "read",
                  "2026-07-22")
    request_share(m, "joe", "Clients/Danziger Family", "team:ops", "read",
                  "2026-07-22")
    _git_init(m)
    sweep_shares(m, _ORG_DECIDER, today="2026-07-22")
    return m


def test_decider_section_lists_only_eligible_shares(tmp_path: Path):
    master = _decider_fixture(tmp_path)
    sec = generate_decider_section(master, "mary", "2026-07-23")
    assert "Awaiting your decision" in sec
    assert "person:mary" in sec and "team:ops" not in sec
    assert "People/mary/Approvals/" in sec       # the how-to names their path
    assert "explicitly made" in sec              # only-human-decisions rule
    lead = generate_decider_section(master, "lead_ops", "2026-07-23")
    assert "team:ops" in lead and "person:mary" not in lead
    assert generate_decider_section(master, "carol", "2026-07-23") is None
    # admins decide master-side; no queue duplication into their slice
    assert generate_decider_section(master, "admin", "2026-07-23") is None


def test_decider_section_never_renders_requester_body(tmp_path: Path):
    # Untrusted requester free text rendered into another person's vault is
    # an injection surface — generate_decider_section must never echo it.
    m = _master(tmp_path)
    (m / "_meta/org.yaml").write_text(_ORG_YAML_DECIDER)
    request_share(m, "joe", "Clients/Danziger Family", "person:mary", "read",
                  "2026-07-22", body="INJECTION-MARKER-XYZ\n")
    _git_init(m)
    sweep_shares(m, _ORG_DECIDER, today="2026-07-22")

    pending = list_pending_shares(m)
    assert len(pending) == 1
    # Sanity: the marker really is in the pending file's body, so the
    # assertion below can't pass vacuously.
    assert "INJECTION-MARKER-XYZ" in pending[0]["body"]

    sec = generate_decider_section(m, "mary", "2026-07-23")
    assert sec is not None and "INJECTION-MARKER-XYZ" not in sec


def test_may_decide_matrix():
    admin = Person(id="root", name="Root", roles=("admin",))
    bob = Person(id="bob", name="Bob", teams=("ops",))
    lead_ops = Person(id="mary", name="Mary", roles=("lead",), teams=("ops",))
    lead_sales = Person(id="sam", name="Sam", roles=("lead",), teams=("sales",))

    # admin decides anything
    for target in ("person:bob", "team:ops", "everyone"):
        assert may_decide(admin, target)
    # recipient consents to their own person-share; nobody else's
    assert may_decide(bob, "person:bob")
    assert not may_decide(bob, "person:mary")
    # team share: lead of THAT team only; membership without lead is not enough
    assert may_decide(lead_ops, "team:ops")
    assert not may_decide(lead_sales, "team:ops")
    assert not may_decide(bob, "team:ops")
    # a lead is not thereby a recipient-proxy for members
    assert not may_decide(lead_ops, "person:bob")
    # everyone: admin only
    assert not may_decide(lead_ops, "everyone")
    assert not may_decide(bob, "everyone")
    # fail closed
    assert not may_decide(None, "person:bob")
    assert not may_decide(bob, "garbage")
    assert not may_decide(bob, "role:admin")


def test_request_share_everyone_must_be_read(tmp_path: Path):
    with pytest.raises(ShareError):
        request_share(tmp_path, "joe", "Clients/Danziger Family", "everyone", "write",
                      "2026-07-23")


def test_everyone_share_flows_to_queue_and_admin_approval(tmp_path: Path):
    m = _master(tmp_path)
    (m / "_meta/org.yaml").write_text(_ORG_YAML)
    request_share(m, "joe", "Clients/Danziger Family", "everyone", "read",
                  "2026-07-23")
    _git_init(m)
    outcomes = sweep_shares(m, _ORG, today="2026-07-23")
    assert [o.status for o in outcomes] == ["queued"]
    pending = list_pending_shares(m)
    assert pending[0]["share-with"] == "everyone"
    # Task 3 (broad non-admin refusal enforcement) has not landed on this
    # branch yet — assert the admin approval succeeds here.
    approve_share(m, pending[0]["id"], approver="admin", date="2026-07-23")
    rules = load_spaces(m / "_meta/spaces.yaml")
    r = next(r for r in rules if r.path == "Clients/Danziger Family")
    assert "everyone" in r.read and "everyone" not in r.write


def test_everyone_write_refused_at_sweep(tmp_path: Path):
    m = _master(tmp_path)
    (m / "_meta/org.yaml").write_text(_ORG_YAML)
    # hand-write the request file to bypass request_share's client-side check
    req = m / "People/joe/ShareRequests/x.md"
    req.parent.mkdir(parents=True, exist_ok=True)
    req.write_text("---\nspace: Clients/Danziger Family\nshare-with: everyone\n"
                   "access: write\naction: share\nowner: joe\n"
                   "created: 2026-07-23\n---\n")
    _git_init(m)
    outcomes = sweep_shares(m, _ORG, today="2026-07-23")
    assert [o.status for o in outcomes] == ["rejected"]
    assert outcomes[0].reason == "company-wide shares are read-only"
    assert not list_pending_shares(m)


def test_owner_can_revoke_everyone(tmp_path: Path):
    m = _master(tmp_path)
    (m / "_meta/org.yaml").write_text(_ORG_YAML)
    amend_space_rule(m / "_meta/spaces.yaml", "Clients/Danziger Family",
                     "everyone", "read")
    request_share(m, "joe", "Clients/Danziger Family", "everyone", "read",
                  "2026-07-23", action="revoke")
    _git_init(m)
    outcomes = sweep_shares(m, _ORG, today="2026-07-23")
    assert [o.status for o in outcomes] == ["revoked"]
    rules = load_spaces(m / "_meta/spaces.yaml")
    r = next(r for r in rules if r.path == "Clients/Danziger Family")
    assert "everyone" not in r.read


def test_approve_share_everyone_write_refused(tmp_path: Path):
    """Defense at decision time: even if a pending file somehow has
    access: write for everyone, approval refuses to apply it."""
    m = _master(tmp_path)
    (m / "_meta/org.yaml").write_text(_ORG_YAML)
    _git_init(m)
    pending = m / "_meta/shares/pending/joe-x.md"
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text(
        "---\nshare-id: joe-x\nfrom: joe\nspace: Clients/Danziger Family\n"
        "share-with: everyone\naccess: write\ncreated: 2026-07-23\n---\n")
    with pytest.raises(ShareError):
        approve_share(m, "joe-x", approver="admin", date="2026-07-23")


# ---- decision authority (Task 3: approve/reject enforce may_decide) -----------

_ORG_YAML_CAROL = """\
people:
  admin: {name: Admin, roles: [admin]}
  joe:   {name: Joe Danziger}
  mary:  {name: Mary Ops, teams: [concierge]}
  carol: {name: Carol Support, teams: [concierge]}
"""

_ORG_CAROL = Org(people={
    "admin": Person(id="admin", name="Admin", roles=("admin",)),
    "joe": Person(id="joe", name="Joe Danziger"),
    "mary": Person(id="mary", name="Mary Ops", teams=("concierge",)),
    "carol": Person(id="carol", name="Carol Support", teams=("concierge",)),
})


def _queued_with_carol(tmp_path: Path) -> Path:
    """joe -> mary (write) pending share; carol is in the org but is neither
    admin, the recipient, nor a lead of the recipient's team."""
    m = _master(tmp_path)
    (m / "_meta/org.yaml").write_text(_ORG_YAML_CAROL)
    request_share(m, "joe", "Clients/Danziger Family", "person:mary", "write",
                  "2026-07-22", body="context\n")
    _git_init(m)
    sweep_shares(m, _ORG_CAROL, today="2026-07-22")
    return m


def test_approve_requires_authorized_approver(tmp_path: Path):
    m = _queued_with_carol(tmp_path)
    pid = list_pending_shares(m)[0]["id"]
    # carol is in the org but neither admin, recipient, nor lead
    with pytest.raises(ShareError):
        approve_share(m, pid, approver="carol", date="2026-07-23")
    # the recipient herself may approve (consent)
    approve_share(m, pid, approver="mary", date="2026-07-23")


_ORG_YAML_LEADS = """\
people:
  admin:      {name: Admin, roles: [admin]}
  joe:        {name: Joe Danziger}
  lead_ops:   {name: Lead Ops, roles: [lead], teams: [ops]}
  lead_sales: {name: Lead Sales, roles: [lead], teams: [sales]}
"""

_ORG_LEADS = Org(people={
    "admin": Person(id="admin", name="Admin", roles=("admin",)),
    "joe": Person(id="joe", name="Joe Danziger"),
    "lead_ops": Person(id="lead_ops", name="Lead Ops", roles=("lead",), teams=("ops",)),
    "lead_sales": Person(id="lead_sales", name="Lead Sales", roles=("lead",), teams=("sales",)),
})


def test_lead_may_approve_team_share_wrong_lead_may_not(tmp_path: Path):
    m = _master(tmp_path)
    (m / "_meta/org.yaml").write_text(_ORG_YAML_LEADS)
    request_share(m, "joe", "Clients/Danziger Family", "team:ops", "read",
                  "2026-07-22")
    _git_init(m)
    sweep_shares(m, _ORG_LEADS, today="2026-07-22")
    pid = list_pending_shares(m)[0]["id"]
    with pytest.raises(ShareError):
        approve_share(m, pid, approver="lead_sales", date="2026-07-23")
    approve_share(m, pid, approver="lead_ops", date="2026-07-23")


def test_reject_requires_authorized_approver_and_records_it(tmp_path: Path):
    m = _queued_with_carol(tmp_path)
    pid = list_pending_shares(m)[0]["id"]
    with pytest.raises(ShareError):
        reject_share(m, pid, reason="no", date="2026-07-23", approver="carol")
    rejected = reject_share(m, pid, reason="not needed",
                            date="2026-07-23", approver="mary")
    text = rejected.read_text()
    assert "rejected-by: mary" in text


def test_via_delegated_lands_in_archive(tmp_path: Path):
    m = _queued(tmp_path)  # joe -> mary write share pending; mary consents
    pid = list_pending_shares(m)[0]["id"]
    approve_share(m, pid, approver="mary", date="2026-07-23", via="delegated")
    archived = m / "_meta/shares/approved" / f"{pid}.md"
    assert "via: delegated" in archived.read_text()


# ---- sweep_approvals (Task 4: in-vault delegated decisions) --------------------

def _decision_note(master: Path, pid: str, share_id: str, decision: str,
                   reason: str = "", owner: str | None = None,
                   created: str = "2026-07-23") -> None:
    d = master / f"People/{pid}/Approvals"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{share_id}.md").write_text(
        f"---\ndecision: {decision}\nreason: {reason}\n"
        f"owner: {owner or pid}\ncreated: {created}\n---\n")


def test_recipient_decision_applies_share(tmp_path: Path):
    m = _queued(tmp_path)  # joe -> mary (write) pending on Clients/Danziger Family
    pid = list_pending_shares(m)[0]["id"]
    _decision_note(m, "mary", pid, "approve")
    outcomes = sweep_approvals(m, _ORG, today="2026-07-23")
    assert [(o.status, o.decision) for o in outcomes] == [("applied", "approve")]
    rules = load_spaces(m / "_meta/spaces.yaml")
    r = next(r for r in rules if r.path == "Clients/Danziger Family")
    assert "person:mary" in r.read
    archived = (m / "_meta/shares/approved" / f"{pid}.md").read_text()
    assert "approved-by: mary" in archived and "via: delegated" in archived
    assert not (m / "People/mary/Approvals" / f"{pid}.md").exists()


def test_ineligible_decider_is_routine_refusal(tmp_path: Path):
    m = _master(tmp_path)
    (m / "_meta/org.yaml").write_text(_ORG_YAML_LEADS)
    request_share(m, "joe", "Clients/Danziger Family", "team:ops", "read",
                  "2026-07-22")
    _git_init(m)
    sweep_shares(m, _ORG_LEADS, today="2026-07-22")
    pid = list_pending_shares(m)[0]["id"]
    _decision_note(m, "lead_sales", pid, "approve")
    outcomes = sweep_approvals(m, _ORG_LEADS, today="2026-07-23")
    assert [o.status for o in outcomes] == ["refused"]
    assert list_pending_shares(m)  # still pending
    assert list((m / "People/lead_sales/Inbox").glob("share-*.md"))


def test_forged_owner_is_tampering(tmp_path: Path):
    m = _queued_with_carol(tmp_path)  # joe -> mary (write) pending; carol also in org
    pid = list_pending_shares(m)[0]["id"]
    _decision_note(m, "carol", pid, "approve", owner="mary")
    outcomes = sweep_approvals(m, _ORG_CAROL, today="2026-07-23")
    assert [o.status for o in outcomes] == ["tampering"]
    assert list_pending_shares(m)  # untouched


def test_reject_without_reason_refused_with_reason_applies(tmp_path: Path):
    m = _queued(tmp_path)
    pid = list_pending_shares(m)[0]["id"]
    _decision_note(m, "mary", pid, "reject")
    assert [o.status for o in sweep_approvals(m, _ORG, today="2026-07-23")] == ["refused"]
    _decision_note(m, "mary", pid, "reject", reason="not needed")
    assert [o.status for o in sweep_approvals(m, _ORG, "2026-07-23")] == ["applied"]
    assert "rejected-by: mary" in \
        (m / "_meta/shares/rejected" / f"{pid}.md").read_text()


def test_unknown_or_already_decided_id_refused(tmp_path: Path):
    m = _master(tmp_path)
    (m / "_meta/org.yaml").write_text(_ORG_YAML)
    _git_init(m)
    _decision_note(m, "mary", "no-such-share", "approve")
    assert [o.status for o in sweep_approvals(m, _ORG, "2026-07-23")] == ["refused"]


_ORG_YAML_ROOT = """\
people:
  root: {name: Root, roles: [admin]}
  joe:  {name: Joe Danziger}
"""

_ORG_ROOT = Org(people={
    "root": Person(id="root", name="Root", roles=("admin",)),
    "joe": Person(id="joe", name="Joe Danziger"),
})


def test_everyone_share_never_decidable_via_seam(tmp_path: Path):
    m = _master(tmp_path)
    (m / "_meta/org.yaml").write_text(_ORG_YAML_ROOT)
    request_share(m, "joe", "Clients/Danziger Family", "everyone", "read",
                  "2026-07-22")
    _git_init(m)
    sweep_shares(m, _ORG_ROOT, today="2026-07-22")
    pid = list_pending_shares(m)[0]["id"]
    _decision_note(m, "root", pid, "approve")   # even an admin's vault note
    outcomes = sweep_approvals(m, _ORG_ROOT, "2026-07-23")
    assert [o.status for o in outcomes] == ["refused"]
    assert list_pending_shares(m)


def test_poison_and_symlink_notes_left_alone(tmp_path: Path):
    m = _master(tmp_path)
    (m / "_meta/org.yaml").write_text(_ORG_YAML)
    _git_init(m)
    d = m / "People/mary/Approvals"
    d.mkdir(parents=True, exist_ok=True)
    poison = d / "x.md"
    poison.write_bytes(b"\xff\xfe not utf8")
    before = poison.read_bytes()

    target = m / "People/mary/elsewhere.md"
    target.write_text("---\ndecision: approve\nowner: mary\n---\n")
    symlink_note = d / "y.md"
    symlink_note.symlink_to(target)

    sweep_approvals(m, _ORG, today="2026-07-23")
    assert poison.read_bytes() == before
    assert symlink_note.is_symlink() and symlink_note.resolve() == target.resolve()
