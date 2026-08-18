import hashlib
from pathlib import Path

import pytest

from brain.promotions import (
    PromotionError,
    approve,
    draft_into_space,
    draft_promotion,
    generate_promotion_decider_section,
    list_pending,
    may_approve,
    reject,
    sweep_promotion_approvals,
)
from brain.schemas import Person, SpaceRule
from tests.conftest import RULES


def _hash_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

ORG_YAML = """\
people:
  alice: {name: Alice Nguyen, roles: [admin], teams: [sales]}
  bob:   {name: Bob Rivera, teams: [ops]}
"""


def _seed_org(master: Path) -> None:
    (master / "_meta/org.yaml").write_text(ORG_YAML)


def test_draft_and_list(master: Path):
    p = draft_promotion(
        master,
        person_id="bob",
        target_path="Company/Playbook/Onboarding-Call-SOP.md",
        source="People/bob/Sessions/2026-07-01-call.md",
        body="## Onboarding call SOP\n1. Confirm goals.\n",
        promo_id="p-001",
        created="2026-07-07",
    )
    assert p == master / "_meta/promotions/pending/p-001.md"
    pending = list_pending(master)
    assert len(pending) == 1
    assert pending[0].person_id == "bob"
    assert pending[0].target_path == "Company/Playbook/Onboarding-Call-SOP.md"
    assert "Confirm goals" in pending[0].body


@pytest.mark.parametrize(
    "bad_target",
    ["People/alice/Memory.md", "_meta/org.yaml", "loose-root-note.md"],
)
def test_draft_rejects_bad_targets(master: Path, bad_target: str):
    with pytest.raises(PromotionError):
        draft_promotion(
            master, person_id="bob", target_path=bad_target,
            source="x", body="b", promo_id="p-002", created="2026-07-07",
        )


def test_approve_writes_target_with_provenance(master: Path):
    _seed_org(master)
    draft_promotion(
        master, person_id="bob",
        target_path="Company/Playbook/SOP.md",
        source="People/bob/Sessions/call.md",
        body="Step one.\n", promo_id="p-003", created="2026-07-07",
    )
    target = approve(master, "p-003", approver="alice", date="2026-07-08")
    text = target.read_text()
    assert text.startswith("---\n")
    assert "promoted-by: bob" in text
    assert "approved-by: alice" in text
    assert "source: People/bob/Sessions/call.md" in text
    assert "Step one." in text
    assert not (master / "_meta/promotions/pending/p-003.md").exists()
    assert (master / "_meta/promotions/approved/p-003.md").exists()


def test_reject_records_reason(master: Path):
    draft_promotion(
        master, person_id="bob", target_path="Company/Playbook/SOP2.md",
        source="s", body="b", promo_id="p-004", created="2026-07-07",
    )
    rejected = reject(master, "p-004", reason="too client-specific", date="2026-07-20")
    assert "rejected-reason: too client-specific" in rejected.read_text()
    assert not (master / "Company/Playbook/SOP2.md").exists()
    assert list_pending(master) == []


def test_approve_revalidates_target(master: Path):
    _seed_org(master)
    # A hand-edited/corrupted pending file with an absolute target must not
    # become an arbitrary file write (Path(master) / "/etc/..." discards master).
    (master / "_meta/promotions/pending/p-evil.md").write_text(
        "---\n"
        "promotion-id: p-evil\n"
        "from: bob\n"
        "target-path: /etc/passwd\n"
        "source: s\n"
        "created: 2026-07-07\n"
        "---\n"
        "pwned\n"
    )
    with pytest.raises(PromotionError):
        approve(master, "p-evil", approver="alice", date="2026-07-08")


def test_parse_defaults_to_create_for_legacy_files(master: Path):
    # A pending file written before modes existed has no mode key at all.
    draft_promotion(
        master, person_id="bob", target_path="Company/Playbook/Legacy.md",
        source="s", body="b", promo_id="p-m1", created="2026-07-21",
    )
    pending = master / "_meta/promotions/pending/p-m1.md"
    text = pending.read_text()
    pending.write_text(text.replace("mode: create\n", ""))  # simulate legacy file
    p = list_pending(master)[0]
    assert p.mode == "create"
    assert p.base_hash == ""


def test_draft_writes_mode_and_base_hash(master: Path):
    draft_promotion(
        master, person_id="bob", target_path="Company/Intel/Portugal.md",
        source="s", body="b", promo_id="p-m2", created="2026-07-21",
        mode="patch", base_hash="abc123",
    )
    text = (master / "_meta/promotions/pending/p-m2.md").read_text()
    assert "mode: patch" in text
    assert "base-hash: abc123" in text
    p = list_pending(master)[0]
    assert p.mode == "patch"
    assert p.base_hash == "abc123"


def test_draft_rejects_unknown_mode(master: Path):
    with pytest.raises(PromotionError):
        draft_promotion(
            master, person_id="bob", target_path="Company/Playbook/X.md",
            source="s", body="b", promo_id="p-m3", created="2026-07-21",
            mode="replace",
        )


def test_draft_rejects_multiline_base_hash(master: Path):
    with pytest.raises(PromotionError, match="single line"):
        draft_promotion(
            master, person_id="bob", target_path="Company/Playbook/Z.md",
            source="s", body="b", promo_id="p-m5", created="2026-07-21",
            mode="patch", base_hash="x\ntarget-path: Company/Playbook/Evil.md",
        )


def test_list_pending_skips_unknown_mode(master: Path):
    draft_promotion(
        master, person_id="bob", target_path="Company/Playbook/Y.md",
        source="s", body="b", promo_id="p-m4", created="2026-07-21",
    )
    pending = master / "_meta/promotions/pending/p-m4.md"
    pending.write_text(pending.read_text().replace("mode: create", "mode: rewrite"))
    assert list_pending(master) == []  # malformed file stays on disk, skipped


def test_approve_refuses_existing_target(master: Path):
    """Approving onto an existing file must fail closed, not overwrite it.
    Promotions are additive: the running curated notes (Company/Memory.md,
    Home.md) are maintained by the admin, never replaced by an approval."""
    _seed_org(master)
    before = (master / "Company/Home.md").read_text()
    draft_promotion(
        master, person_id="bob",
        target_path="Company/Home.md",   # already exists in the fixture
        source="People/bob/Sessions/call.md",
        body="clobber\n", promo_id="p-clobber", created="2026-07-20",
    )
    with pytest.raises(PromotionError, match="already exists"):
        approve(master, "p-clobber", approver="alice", date="2026-07-20")
    # the target is untouched and the pending item survives for re-targeting
    assert (master / "Company/Home.md").read_text() == before
    assert (master / "_meta/promotions/pending/p-clobber.md").exists()
    assert not (master / "_meta/promotions/approved/p-clobber.md").exists()


@pytest.mark.parametrize("bad_approver", ["", "   ", "mallory"])
def test_approve_rejects_missing_or_unknown_approver(master: Path, bad_approver: str):
    _seed_org(master)
    draft_promotion(
        master, person_id="bob",
        target_path="Company/Playbook/SOP.md",
        source="People/bob/Sessions/call.md",
        body="Step one.\n", promo_id="p-010", created="2026-07-07",
    )
    with pytest.raises(PromotionError):
        approve(master, "p-010", approver=bad_approver, date="2026-07-08")
    # a failed approval must not consume the pending file
    assert (master / "_meta/promotions/pending/p-010.md").exists()
    assert not (master / "Company/Playbook/SOP.md").exists()


def test_approve_requires_an_admin_approver(master: Path):
    """bob is in the org but holds no admin role. Promotion approval publishes
    into a space other people read, so the roster check alone is not enough."""
    _seed_org(master)
    draft_promotion(
        master, person_id="bob",
        target_path="Company/Playbook/SOP.md",
        source="People/bob/Sessions/call.md",
        body="Step one.\n", promo_id="p-011", created="2026-07-07",
    )
    with pytest.raises(PromotionError, match="role:admin"):
        approve(master, "p-011", approver="bob", date="2026-07-08")
    assert (master / "_meta/promotions/pending/p-011.md").exists()
    assert not (master / "Company/Playbook/SOP.md").exists()

    # alice holds role: admin — same promotion, same target, now permitted
    target = approve(master, "p-011", approver="alice", date="2026-07-08")
    assert target.exists()
    assert "approved-by: alice" in target.read_text()


def test_approve_requires_admin_for_every_space_not_just_shared(master: Path):
    """Team spaces are the deliberate future relaxation, not today's rule —
    until team routing exists, a non-admin approves nothing anywhere."""
    _seed_org(master)
    draft_promotion(
        master, person_id="bob",
        target_path="Teams/ops/Escalation.md",
        source="People/bob/Sessions/call.md",
        body="Restart the thing.\n", promo_id="p-012", created="2026-07-07",
    )
    with pytest.raises(PromotionError, match="role:admin"):
        approve(master, "p-012", approver="bob", date="2026-07-08")
    assert not (master / "Teams/ops/Escalation.md").exists()


def test_may_approve_fails_closed_on_unknown_person():
    assert may_approve(None, "Company/Playbook/SOP.md") is False


_LEAD_SALES = Person(id="lead_sales", name="Lead Sales", roles=("lead",), teams=("sales",))
_MEMBER_SALES = Person(id="pat", name="Pat", roles=(), teams=("sales",))
_ADMIN = Person(id="alice", name="Alice", roles=("admin",))


def test_may_approve_lead_on_own_team_space():
    assert may_approve(_LEAD_SALES, "Teams/sales/Playbook.md") is True


def test_may_approve_lead_refused_on_other_team_space():
    assert may_approve(_LEAD_SALES, "Teams/ops/Runbook.md") is False


def test_may_approve_member_without_lead_role_refused():
    assert may_approve(_MEMBER_SALES, "Teams/sales/Playbook.md") is False


@pytest.mark.parametrize("target", [
    "Company/Playbook/SOP.md",       # shared space: admin only
    "Clients/acme/Overview.md",      # entity space: admin only (out of scope)
])
def test_may_approve_lead_refused_outside_teams(target: str):
    assert may_approve(_LEAD_SALES, target) is False


def test_may_approve_admin_everywhere():
    for t in ("Company/x.md", "Teams/sales/x.md", "Teams/ops/x.md", "Clients/acme/x.md"):
        assert may_approve(_ADMIN, t) is True


def test_may_approve_honours_custom_shared_name():
    # A vault whose shared space is called "Wayfarer" — Teams/* still routes.
    assert may_approve(_LEAD_SALES, "Teams/sales/x.md", shared="Wayfarer") is True
    assert may_approve(_LEAD_SALES, "Wayfarer/x.md", shared="Wayfarer") is False


ORG_YAML_LEADS = """\
people:
  alice:      {name: Alice Nguyen, roles: [admin], teams: [sales]}
  bob:        {name: Bob Rivera, teams: [ops]}
  lead_ops:   {name: Lead Ops, roles: [lead], teams: [ops]}
  lead_sales: {name: Lead Sales, roles: [lead], teams: [sales]}
"""


def test_approve_lead_publishes_into_own_team_space(master: Path):
    (master / "_meta/org.yaml").write_text(ORG_YAML_LEADS)
    draft_promotion(
        master, person_id="bob",
        target_path="Teams/ops/Escalation.md",
        source="People/bob/Sessions/call.md",
        body="Restart the thing.\n", promo_id="p-lead", created="2026-08-17",
    )
    with pytest.raises(PromotionError, match="lead of the team"):
        approve(master, "p-lead", approver="lead_sales", date="2026-08-17")
    target = approve(master, "p-lead", approver="lead_ops", date="2026-08-17")
    assert target.exists()
    assert "approved-by: lead_ops" in target.read_text()


def test_list_pending_skips_malformed_files(master: Path):
    draft_promotion(
        master, person_id="bob", target_path="Company/Playbook/Good.md",
        source="s", body="b", promo_id="p-good", created="2026-07-07",
    )
    # Missing required keys — must not break listing of the whole queue.
    (master / "_meta/promotions/pending/p-mangled.md").write_text(
        "---\npromotion-id: p-mangled\n---\nbody\n"
    )
    # No frontmatter at all.
    (master / "_meta/promotions/pending/p-nofm.md").write_text("just text\n")
    pending = list_pending(master)
    assert [p.id for p in pending] == ["p-good"]
    # Skipped files stay on disk for manual inspection.
    assert (master / "_meta/promotions/pending/p-mangled.md").exists()
    assert (master / "_meta/promotions/pending/p-nofm.md").exists()


def test_sweep_skips_symlinked_drafts(master: Path, tmp_path: Path):
    from brain.promotions import sweep

    outside = tmp_path / "outside.md"
    outside.write_text(
        "---\ntarget-path: Company/Playbook/Smuggled.md\n---\nhost content\n"
    )
    d = master / "People/bob/Promotions"
    d.mkdir(parents=True)
    (d / "link.md").symlink_to(outside)
    moved = sweep(master, today="2026-07-07")
    assert moved == []
    assert (d / "link.md").is_symlink()  # left in place, never queued
    assert list_pending(master) == []


@pytest.mark.parametrize(
    "bare", ["Company", "Company/", "Teams/sales", "Teams/sales/"],
)
def test_draft_rejects_bare_space_target(master: Path, bare: str):
    with pytest.raises(PromotionError):
        draft_promotion(
            master, person_id="bob", target_path=bare,
            source="s", body="b", promo_id="p-bare", created="2026-07-07",
        )


def test_sweep_moves_agent_drafts_into_queue(master: Path):
    from brain.promotions import sweep

    d = master / "People/bob/Promotions"
    d.mkdir(parents=True)
    (d / "Onboarding SOP.md").write_text(
        "---\n"
        "target-path: Company/Playbook/Onboarding-SOP.md\n"
        "source: People/bob/Sessions/call.md\n"
        "---\n"
        "Step one.\n"
    )
    (d / "broken.md").write_text("no frontmatter, no target\n")
    moved = sweep(master, today="2026-07-07")
    assert len(moved) == 1
    pending = list_pending(master)
    assert pending[0].id == "bob-onboarding-sop"
    assert pending[0].target_path == "Company/Playbook/Onboarding-SOP.md"
    assert not (d / "Onboarding SOP.md").exists()   # swept
    assert (d / "broken.md").exists()               # skipped, left in place


def test_sweep_skips_poison_utf8_request_and_processes_valid_one(master: Path):
    """An invalid-UTF-8 request file must not abort the whole sweep — the
    valid draft alongside it still gets queued, and the poison file is left
    untouched for inspection."""
    from brain.promotions import sweep

    d = master / "People/bob/Promotions"
    d.mkdir(parents=True)
    (d / "Good.md").write_text(
        "---\n"
        "target-path: Company/Playbook/Good-SOP.md\n"
        "source: People/bob/Sessions/call.md\n"
        "---\n"
        "Step one.\n"
    )
    poison = d / "Poison.md"
    poison.write_bytes(b"\xff\xfe garbage")

    moved = sweep(master, today="2026-07-07")

    assert len(moved) == 1
    pending = list_pending(master)
    assert pending[0].target_path == "Company/Playbook/Good-SOP.md"
    assert poison.exists()
    assert poison.read_bytes() == b"\xff\xfe garbage"


def _draft(master: Path, title: str = "CBS Result") -> Path:
    """Write an agent draft into bob's Promotions folder; return its path."""
    d = master / "People/bob/Promotions"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{title}.md"
    f.write_text(
        "---\n"
        "target-path: Company/Playbook/CBS-Result.md\n"
        "source: People/bob/Sessions/call.md\n"
        "---\n"
        "Conflict-based search scales to 40 robots.\n"
    )
    return f


def test_sweep_does_not_resurrect_an_approved_promotion(master: Path):
    """The bug: on the next cycle a person's already-approved draft gets written
    back into their space and re-swept, resurfacing in the queue. Sweep must
    treat an id already in approved/ as done and clear the stale draft."""
    _seed_org(master)
    from brain.promotions import sweep

    draft = _draft(master)
    sweep(master, today="2026-07-07")
    approve(master, "bob-cbs-result", approver="alice", date="2026-07-07")
    assert list_pending(master) == []

    # Simulate the next cycle: the draft reappears in bob's vault (writeback).
    draft = _draft(master)
    moved = sweep(master, today="2026-07-08")

    assert moved == []                       # not re-queued
    assert list_pending(master) == []        # queue stays empty
    assert not draft.exists()                # stale draft cleared, won't recur


def test_sweep_does_not_resurrect_a_rejected_promotion(master: Path):
    from brain.promotions import sweep

    _draft(master)
    sweep(master, today="2026-07-07")
    reject(master, "bob-cbs-result", reason="off-scope", date="2026-07-20")
    assert list_pending(master) == []

    draft = _draft(master)                   # reappears next cycle
    moved = sweep(master, today="2026-07-08")

    assert moved == []                       # a rejected idea does not come back
    assert list_pending(master) == []
    assert not draft.exists()


def test_draft_into_space_stays_in_owner_space(master: Path):
    # Positive control: the employee-side gate writes only inside the caller's
    # own People/<id>/Promotions, preserving the fields it was handed.
    rel = draft_into_space(
        master, "bob", "Company/Playbook/SOP.md", "src-note", "some body", "2026-07-07"
    )
    assert rel.startswith("People/bob/Promotions/")
    dest = master / rel
    assert dest.is_file()
    text = dest.read_text()
    assert "target-path: Company/Playbook/SOP.md" in text
    assert "source: src-note" in text
    assert text.rstrip().endswith("some body")


@pytest.mark.parametrize("overrides", [
    {"target_path": "Company/Playbook/SOP.md\ninjected: true"},
    {"source": "src\ninjected: true"},
], ids=["target-path", "source"])
def test_draft_into_space_rejects_multiline_fields(master: Path, overrides):
    # A newline in a header field would smuggle extra frontmatter into the draft.
    kwargs = {"target_path": "Company/Playbook/SOP.md", "source": "src",
                  "body": "b", "created": "2026-07-07"}
    kwargs.update(overrides)
    with pytest.raises(PromotionError, match="single line"):
        draft_into_space(master, "bob", **kwargs)


def test_draft_into_space_rejects_empty_body(master: Path):
    with pytest.raises(PromotionError, match="empty promotion"):
        draft_into_space(
            master, "bob", "Company/Playbook/SOP.md", "src", "   \n", "2026-07-07"
        )


def test_draft_into_space_refuses_symlinked_ancestor(master: Path, tmp_path: Path):
    # A symlink anywhere in the Promotions path would let a draft land outside
    # the person's own space.
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (master / "People/bob/Promotions").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PromotionError, match="symlink"):
        draft_into_space(
            master, "bob", "Company/Playbook/SOP.md", "src", "body", "2026-07-07"
        )


def test_approve_and_reject_unknown_id_raise(master: Path):
    # Acting on a nonexistent/typo'd id must raise, not silently no-op.
    _seed_org(master)
    with pytest.raises(PromotionError, match="no pending promotion"):
        approve(master, "does-not-exist", approver="alice", date="2026-07-08")
    with pytest.raises(PromotionError, match="no pending promotion"):
        reject(master, "does-not-exist", reason="n/a", date="2026-07-20")


def test_approve_and_reject_reject_traversal_ids(master: Path):
    # A path/traversal-shaped id must fail the same not-found way as an
    # unknown one, and never touch anything outside _meta/promotions/pending/.
    _seed_org(master)
    planted = master / "_meta/evil.md"
    planted.write_text("secret\n")

    with pytest.raises(PromotionError, match="no pending promotion"):
        approve(master, "../../evil", approver="alice", date="2026-07-08")
    assert planted.read_text() == "secret\n"

    with pytest.raises(PromotionError, match="no pending promotion"):
        reject(master, "../../evil", reason="n/a", date="2026-07-20")
    assert planted.read_text() == "secret\n"


def _draft_p1(master: Path) -> None:
    draft_promotion(
        master, person_id="bob",
        target_path="Company/Playbook/SOP.md",
        source="People/bob/Sessions/call.md",
        body="shareable\n", promo_id="p-001", created="2026-07-01",
    )


def test_approve_stamps_decision_in_archive(master: Path):
    _seed_org(master)
    _draft_p1(master)
    approve(master, "p-001", approver="alice", date="2026-07-20")
    text = (master / "_meta/promotions/approved/p-001.md").read_text()
    assert "approved-on: 2026-07-20" in text
    assert "approved-by: alice" in text
    assert "shareable" in text  # body survives the rewrite


def test_reject_stamps_decision_date(master: Path):
    _draft_p1(master)
    reject(master, "p-001", reason="too raw", date="2026-07-20")
    text = (master / "_meta/promotions/rejected/p-001.md").read_text()
    assert "rejected-on: 2026-07-20" in text
    assert "rejected-reason: too raw" in text


from brain.promotions import generate_shares_note


def _decide_two(master: Path) -> None:
    """One pending (bob), one approved (bob), one rejected (bob), one foreign (alice)."""
    _seed_org(master)
    draft_promotion(master, person_id="bob", target_path="Company/Playbook/A.md",
                    source="s", body="a", promo_id="p-a", created="2026-07-18")
    draft_promotion(master, person_id="bob", target_path="Company/Playbook/B.md",
                    source="s", body="b", promo_id="p-b", created="2026-07-10")
    draft_promotion(master, person_id="bob", target_path="Company/Playbook/C.md",
                    source="s", body="c", promo_id="p-c", created="2026-07-12")
    draft_promotion(master, person_id="alice", target_path="Company/Playbook/D.md",
                    source="s", body="d", promo_id="p-d", created="2026-07-15")
    approve(master, "p-b", approver="alice", date="2026-07-11")
    reject(master, "p-c", reason="too raw", date="2026-07-13")


def test_shares_note_renders_all_states_for_one_person(master: Path):
    _decide_two(master)
    note = generate_shares_note(master, "bob", today="2026-07-20")
    assert note is not None
    assert "## Awaiting approval" in note
    assert "`Company/Playbook/A.md`" in note and "2026-07-18" in note
    assert "## Recently decided" in note
    assert "✅ `Company/Playbook/B.md` — approved 2026-07-11 by alice" in note
    assert "❌ `Company/Playbook/C.md` — rejected 2026-07-13: too raw" in note
    assert "D.md" not in note  # person isolation: alice's item never leaks into bob's note


def test_shares_note_thirty_day_cutoff_and_fallback(master: Path):
    _seed_org(master)
    draft_promotion(master, person_id="bob", target_path="Company/Playbook/Old.md",
                    source="s", body="o", promo_id="p-old", created="2026-05-01")
    approve(master, "p-old", approver="alice", date="2026-06-01")  # 49 days before today
    # legacy archive without stamps: strip them to simulate a pre-upgrade file
    legacy = master / "_meta/promotions/approved/p-old.md"
    legacy.write_text(legacy.read_text()
                      .replace("approved-on: 2026-06-01\n", "")
                      .replace("approved-by: alice\n", ""))
    note = generate_shares_note(master, "bob", today="2026-07-20")
    assert note is None  # fallback `created` 2026-05-01 is outside the window too

    # same legacy file with a created date inside the window -> included via fallback
    legacy.write_text(legacy.read_text().replace("created: 2026-05-01", "created: 2026-07-15"))
    note = generate_shares_note(master, "bob", today="2026-07-20")
    assert note is not None and "approved 2026-07-15" in note


def test_shares_note_none_when_empty_and_skips_malformed(master: Path):
    assert generate_shares_note(master, "bob", today="2026-07-20") is None
    bad = master / "_meta/promotions/pending/garbage.md"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("no frontmatter at all")
    assert generate_shares_note(master, "bob", today="2026-07-20") is None


# --- git audit trail -------------------------------------------------------
# Master vaults are always git repos (brain init creates one); every decision
# on the queue must land in history with a real identity, the same way ingest
# and writeback commits do. Scratch masters without .git (like this suite's
# fixture) skip the commit and still work.

import subprocess


def _git(master: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(master), *args],
                          capture_output=True, text=True, check=True).stdout


def _git_init(master: Path) -> None:
    _git(master, "init", "-b", "main")
    _git(master, "add", "-A")
    _git(master, "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-m", "seed")


def test_approve_commits_under_the_approvers_identity(master: Path):
    _seed_org(master)
    draft_promotion(master, person_id="bob", target_path="Company/Playbook/G.md",
                    source="s", body="g", promo_id="p-1", created="2026-07-07")
    _git_init(master)
    approve(master, "p-1", approver="alice", date="2026-07-08")
    assert _git(master, "status", "--porcelain").strip() == ""
    assert "p-1" in _git(master, "log", "-1", "--format=%s")
    assert _git(master, "log", "-1", "--format=%an").strip() == "Alice Nguyen"


def test_reject_commits_the_queue_move(master: Path):
    _seed_org(master)
    draft_promotion(master, person_id="bob", target_path="Company/Playbook/H.md",
                    source="s", body="h", promo_id="p-2", created="2026-07-07")
    _git_init(master)
    reject(master, "p-2", reason="too raw", date="2026-07-08")
    assert _git(master, "status", "--porcelain").strip() == ""
    assert "p-2" in _git(master, "log", "-1", "--format=%s")


def test_sweep_commits_only_the_queue_paths(master: Path):
    from brain.promotions import sweep

    _seed_org(master)
    _git_init(master)
    d = master / "People/bob/Promotions"
    d.mkdir(parents=True)
    (d / "Share Me.md").write_text(
        "---\ntarget-path: Company/Playbook/S.md\nsource: x\n---\nbody\n")
    (master / "People/bob/Memory.md").write_text("unrelated local edit\n")
    moved = sweep(master, today="2026-07-07")
    assert len(moved) == 1
    porcelain = _git(master, "status", "--porcelain").strip()
    # the queue move is committed; the unrelated edit is exactly what remains
    assert porcelain == "M People/bob/Memory.md"
    assert "sweep" in _git(master, "log", "-1", "--format=%s")


def test_promotions_still_work_without_git(master: Path):
    _seed_org(master)  # fixture master has no .git at all
    draft_promotion(master, person_id="bob", target_path="Company/Playbook/N.md",
                    source="s", body="n", promo_id="p-3", created="2026-07-07")
    target = approve(master, "p-3", approver="alice", date="2026-07-08")
    assert target.exists()


def test_approve_append_adds_block_with_attribution(master: Path):
    _seed_org(master)
    page = master / "Company/Intel/Portugal.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("# Portugal\nGolden visa ended. [src](https://x.y), as of 2026-01\n")
    draft_promotion(
        master, person_id="bob", target_path="Company/Intel/Portugal.md",
        source="People/bob/Notes/pt.md", body="New ferry route. [s](https://a.b), as of 2026-07\n",
        promo_id="p-a1", created="2026-07-21", mode="append",
    )
    target = approve(master, "p-a1", approver="alice", date="2026-07-21")
    text = target.read_text()
    assert text.startswith("# Portugal\n")          # existing content intact
    assert "\n\n---\n\n" in text                     # divider
    assert "New ferry route." in text
    assert "*Promoted by Bob Rivera, approved by Alice Nguyen, 2026-07-21" in text
    assert "source: People/bob/Notes/pt.md*" in text


def test_approve_append_requires_existing_target(master: Path):
    _seed_org(master)
    draft_promotion(
        master, person_id="bob", target_path="Company/Intel/Nowhere.md",
        source="s", body="b", promo_id="p-a2", created="2026-07-21", mode="append",
    )
    with pytest.raises(PromotionError, match="does not exist"):
        approve(master, "p-a2", approver="alice", date="2026-07-21")


def test_approve_append_refuses_symlink_target(master: Path, tmp_path: Path):
    _seed_org(master)
    outside = tmp_path / "outside.md"
    outside.write_text("secret\n")
    link = master / "Company/Intel/Link.md"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside)
    draft_promotion(
        master, person_id="bob", target_path="Company/Intel/Link.md",
        source="s", body="b", promo_id="p-a3", created="2026-07-21", mode="append",
    )
    with pytest.raises(PromotionError, match="symlink"):
        approve(master, "p-a3", approver="alice", date="2026-07-21")


def test_approve_patch_replaces_file_verbatim(master: Path):
    _seed_org(master)
    page = master / "Company/Intel/Portugal.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("# Portugal\nOld claim. [s](https://x.y), as of 2025-01\n")
    revised = "# Portugal\nNew claim. [s](https://x.y), as of 2026-07\n"
    draft_promotion(
        master, person_id="bob", target_path="Company/Intel/Portugal.md",
        source="s", body=revised, promo_id="p-p1", created="2026-07-21",
        mode="patch", base_hash=_hash_of(page),
    )
    target = approve(master, "p-p1", approver="alice", date="2026-07-21")
    assert target.read_text() == revised          # verbatim, no injected frontmatter


def test_approve_patch_fails_closed_on_base_drift(master: Path):
    _seed_org(master)
    page = master / "Company/Intel/Spain.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("v1\n")
    draft_promotion(
        master, person_id="bob", target_path="Company/Intel/Spain.md",
        source="s", body="v2\n", promo_id="p-p2", created="2026-07-21",
        mode="patch", base_hash=_hash_of(page),
    )
    page.write_text("v1 edited meanwhile\n")      # drift after queueing
    with pytest.raises(PromotionError, match="changed since"):
        approve(master, "p-p2", approver="alice", date="2026-07-21")
    assert page.read_text() == "v1 edited meanwhile\n"   # untouched
    assert (master / "_meta/promotions/pending/p-p2.md").exists()  # still queued


def test_approve_patch_requires_base_hash_and_target(master: Path):
    _seed_org(master)
    page = master / "Company/Intel/France.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("v1\n")
    draft_promotion(
        master, person_id="bob", target_path="Company/Intel/France.md",
        source="s", body="v2\n", promo_id="p-p3", created="2026-07-21",
        mode="patch",                              # no base_hash
    )
    with pytest.raises(PromotionError, match="base-hash"):
        approve(master, "p-p3", approver="alice", date="2026-07-21")


def test_approve_patch_refuses_symlink_target(master: Path, tmp_path: Path):
    _seed_org(master)
    outside = tmp_path / "outside2.md"
    outside.write_text("secret\n")
    link = master / "Company/Intel/PLink.md"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside)
    draft_promotion(
        master, person_id="bob", target_path="Company/Intel/PLink.md",
        source="s", body="v2\n", promo_id="p-p4", created="2026-07-21",
        mode="patch", base_hash="deadbeef",
    )
    with pytest.raises(PromotionError, match="symlink"):
        approve(master, "p-p4", approver="alice", date="2026-07-21")


def test_sweep_stamps_base_hash_on_patch_drafts(master: Path):
    from brain.promotions import sweep

    page = master / "Company/Intel/Italy.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("v1\n")
    d = master / "People/bob/Promotions/italy-update.md"
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_text("---\ntarget-path: Company/Intel/Italy.md\nmode: patch\n---\nv2\n")
    moved = sweep(master, today="2026-07-21")
    assert len(moved) == 1
    p = list_pending(master)[0]
    assert p.mode == "patch"
    assert p.base_hash == hashlib.sha256(page.read_bytes()).hexdigest()


def test_sweep_leaves_patch_draft_when_target_missing(master: Path):
    from brain.promotions import sweep

    d = master / "People/bob/Promotions/ghost.md"
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_text("---\ntarget-path: Company/Intel/Ghost.md\nmode: patch\n---\nbody\n")
    assert sweep(master, today="2026-07-21") == []
    assert d.exists()                       # left in place, never guessed at


def test_sweep_queues_append_draft_without_target(master: Path):
    from brain.promotions import sweep

    # Appends queue regardless — existence is an approve-time question, so an
    # append can sit behind the create that makes its target.
    d = master / "People/bob/Promotions/later.md"
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_text("---\ntarget-path: Company/Intel/Later.md\nmode: append\n---\nbody\n")
    assert len(sweep(master, today="2026-07-21")) == 1


def test_patch_diff_refuses_tampered_target(master: Path, tmp_path: Path):
    from brain.promotions import Promotion, patch_diff
    outside = tmp_path / "secret.md"
    outside.write_text("secret\n")
    promo = Promotion(
        id="x", person_id="bob", target_path="../secret.md",
        source="s", created="2026-07-21", body="b",
        mode="patch", base_hash="h",
    )
    assert patch_diff(master, promo) is None


def test_sweep_leaves_patch_draft_when_target_is_symlink(master: Path, tmp_path: Path):
    from brain.promotions import sweep
    outside = tmp_path / "outside3.md"
    outside.write_text("x\n")
    link = master / "Company/Intel/SLink.md"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside)
    d = master / "People/bob/Promotions/slink.md"
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_text("---\ntarget-path: Company/Intel/SLink.md\nmode: patch\n---\nv2\n")
    assert sweep(master, today="2026-07-21") == []
    assert d.exists()


def test_validate_target_custom_shared():
    from brain.promotions import PromotionError, _validate_target
    _validate_target("Family/Playbook/Chores.md", "Family")   # ok: file in shared space
    # under a custom name, "Company" is just another nested top, so a file in
    # one of its child spaces is a legal target...
    _validate_target("Company/Playbook/X.md", "Family")
    with pytest.raises(PromotionError):
        _validate_target("Family", "Family")                  # bare space
    with pytest.raises(PromotionError):
        _validate_target("Company/X.md", "Family")            # arity: nested top needs 3+ parts


@pytest.mark.parametrize(
    "bare", ["Family", "Family/", "Teams/sales", "Teams/sales/"],
)
def test_validate_target_rejects_bare_space_custom_shared(bare: str):
    from brain.promotions import _validate_target
    with pytest.raises(PromotionError):
        _validate_target(bare, "Family")


def test_approve_via_stamps_archive(master: Path):
    # A delegated approval is a lead deciding on their own team's space —
    # approve() refuses via=delegated for a shared-space target outright.
    (master / "_meta/org.yaml").write_text(ORG_YAML_LEADS)
    draft_promotion(master, person_id="bob", target_path="Teams/ops/Via.md",
                    source="s", body="b\n", promo_id="p-via", created="2026-08-17")
    approve(master, "p-via", approver="lead_ops", date="2026-08-17", via="delegated")
    archived = (master / "_meta/promotions/approved/p-via.md").read_text()
    assert "via: delegated" in archived
    assert "approved-by: lead_ops" in archived


def test_approve_without_via_has_no_via_line(master: Path):
    _seed_org(master)
    draft_promotion(master, person_id="bob", target_path="Company/Playbook/NoVia.md",
                    source="s", body="b\n", promo_id="p-novia", created="2026-08-17")
    approve(master, "p-novia", approver="alice", date="2026-08-17")
    assert "via:" not in (master / "_meta/promotions/approved/p-novia.md").read_text()


def test_reject_records_approver_and_via_when_given(master: Path):
    _seed_org(master)
    draft_promotion(master, person_id="bob", target_path="Company/Playbook/Rej.md",
                    source="s", body="b\n", promo_id="p-rej", created="2026-08-17")
    rejected = reject(master, "p-rej", reason="not ready", date="2026-08-17",
                      approver="alice", via="delegated")
    text = rejected.read_text()
    assert "rejected-by: alice" in text
    assert "via: delegated" in text
    assert "rejected-reason: not ready" in text


def test_reject_without_approver_keeps_old_shape(master: Path):
    _seed_org(master)
    draft_promotion(master, person_id="bob", target_path="Company/Playbook/Rej2.md",
                    source="s", body="b\n", promo_id="p-rej2", created="2026-08-17")
    rejected = reject(master, "p-rej2", reason="no", date="2026-08-17")
    text = rejected.read_text()
    assert "rejected-by:" not in text
    assert "via:" not in text


# --- in-vault decision seam -------------------------------------------------
# NOTE: this module already defines `import subprocess` and a `_git_init`
# helper (identical shape: init -b main, add -A, commit as t/t@t) further up
# for the git-audit-trail suite — reused here rather than redefined, to avoid
# a ruff F811 (redefinition of unused name) that a literal duplicate would
# trigger.


def _decision(master: Path, pid: str, promo_id: str, decision: str,
              reason: str = "", owner: str | None = None,
              created: str = "2026-08-17") -> Path:
    d = master / f"People/{pid}/PromotionApprovals"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{promo_id}.md"
    p.write_text(f"---\ndecision: {decision}\nreason: {reason}\n"
                 f"owner: {owner or pid}\ncreated: {created}\n---\n")
    return p


def _lead_master(master: Path) -> None:
    """Org with leads; a Teams/ops draft from bob queued as p-ops, and a
    Company draft from bob queued as p-co."""
    (master / "_meta/org.yaml").write_text(ORG_YAML_LEADS)
    draft_promotion(master, person_id="bob", target_path="Teams/ops/Escalation.md",
                    source="s", body="Restart it.\n", promo_id="p-ops",
                    created="2026-08-17")
    draft_promotion(master, person_id="bob", target_path="Company/Playbook/SOP.md",
                    source="s", body="Step one.\n", promo_id="p-co",
                    created="2026-08-17")
    _git_init(master)


def _org(master: Path):
    from brain.schemas import load_org
    return load_org(master / "_meta/org.yaml")


def test_seam_lead_approve_applies(master: Path):
    _lead_master(master)
    note = _decision(master, "lead_ops", "p-ops", "approve")
    out = sweep_promotion_approvals(master, _org(master), "2026-08-17")
    assert [(o.promo_id, o.status) for o in out] == [("p-ops", "applied")]
    assert (master / "Teams/ops/Escalation.md").exists()
    assert not note.exists()  # consumed
    archived = (master / "_meta/promotions/approved/p-ops.md").read_text()
    assert "approved-by: lead_ops" in archived and "via: delegated" in archived
    log = subprocess.run(["git", "-C", str(master), "log", "-1", "--format=%an <%ae>"],
                         capture_output=True, text=True).stdout
    assert "Lead Ops <lead_ops@brain.local>" in log


def test_seam_lead_reject_with_reason_applies(master: Path):
    _lead_master(master)
    _decision(master, "lead_ops", "p-ops", "reject", reason="not ready")
    out = sweep_promotion_approvals(master, _org(master), "2026-08-17")
    assert out[0].status == "applied" and out[0].decision == "reject"
    text = (master / "_meta/promotions/rejected/p-ops.md").read_text()
    assert "rejected-by: lead_ops" in text and "via: delegated" in text
    assert not (master / "Teams/ops/Escalation.md").exists()


def test_seam_reject_without_reason_refused(master: Path):
    _lead_master(master)
    _decision(master, "lead_ops", "p-ops", "reject")
    out = sweep_promotion_approvals(master, _org(master), "2026-08-17")
    assert out[0].status == "refused" and out[0].reason == "missing reason"
    assert (master / "_meta/promotions/pending/p-ops.md").exists()  # untouched
    inbox = list((master / "People/lead_ops/Inbox").glob("promotion-*.md"))
    assert inbox and "reason" in inbox[0].read_text()


def test_seam_wrong_lead_refused_not_eligible(master: Path):
    _lead_master(master)
    _decision(master, "lead_sales", "p-ops", "approve")
    out = sweep_promotion_approvals(master, _org(master), "2026-08-17")
    assert out[0].status == "refused" and out[0].reason == "not eligible"
    assert not (master / "Teams/ops/Escalation.md").exists()


def test_seam_shared_space_target_refused_even_for_admin(master: Path):
    """The in-vault carve-out: Company/ targets are decided at the dashboard.
    alice is admin — may_approve would say yes — and is still refused here."""
    _lead_master(master)
    _decision(master, "alice", "p-co", "approve")
    out = sweep_promotion_approvals(master, _org(master), "2026-08-17")
    assert out[0].status == "refused" and out[0].reason == "shared space"
    assert not (master / "Company/Playbook/SOP.md").exists()
    inbox = list((master / "People/alice/Inbox").glob("promotion-*.md"))
    assert inbox and "admin" in inbox[0].read_text().lower()


def test_approve_delegated_refuses_shared_space_target(master: Path):
    """The carve-out is re-asserted where the write happens, not only in the
    sweep. Between the sweep's parse and approve()'s parse the pending file can
    change under us (the dashboard server writes the same master), so a
    target-path that flips Teams/ -> Company/ must still be refused for a
    delegated decision — even an admin's, whose may_approve says yes."""
    _lead_master(master)
    with pytest.raises(PromotionError, match="dashboard"):
        approve(master, "p-co", approver="alice", date="2026-08-17",
                via="delegated")
    assert not (master / "Company/Playbook/SOP.md").exists()
    assert (master / "_meta/promotions/pending/p-co.md").exists()
    # the dashboard path (no via) is untouched by the carve-out
    approve(master, "p-co", approver="alice", date="2026-08-17")
    assert (master / "Company/Playbook/SOP.md").exists()


def test_seam_owner_mismatch_is_tampering(master: Path):
    _lead_master(master)
    _decision(master, "lead_ops", "p-ops", "approve", owner="alice")
    out = sweep_promotion_approvals(master, _org(master), "2026-08-17")
    assert out[0].status == "tampering"
    assert not (master / "Teams/ops/Escalation.md").exists()
    assert not (master / "People/lead_ops/PromotionApprovals/p-ops.md").exists()


def test_seam_unknown_id_refused(master: Path):
    _lead_master(master)
    _decision(master, "lead_ops", "p-nope", "approve")
    out = sweep_promotion_approvals(master, _org(master), "2026-08-17")
    assert out[0].status == "refused" and out[0].reason == "already decided or unknown"


def test_seam_bad_decision_word_refused(master: Path):
    _lead_master(master)
    _decision(master, "lead_ops", "p-ops", "maybe")
    out = sweep_promotion_approvals(master, _org(master), "2026-08-17")
    assert out[0].status == "refused" and out[0].reason == "bad decision"


def test_seam_malformed_note_left_in_place(master: Path):
    _lead_master(master)
    d = master / "People/lead_ops/PromotionApprovals"
    d.mkdir(parents=True)
    bad = d / "p-ops.md"
    bad.write_text("no frontmatter here\n")
    out = sweep_promotion_approvals(master, _org(master), "2026-08-17")
    assert out == []
    assert bad.exists()


def test_seam_symlink_ignored(master: Path):
    _lead_master(master)
    outside = master.parent / "outside.md"
    outside.write_text("---\ndecision: approve\nowner: lead_ops\n---\n")
    d = master / "People/lead_ops/PromotionApprovals"
    d.mkdir(parents=True)
    (d / "p-ops.md").symlink_to(outside)
    out = sweep_promotion_approvals(master, _org(master), "2026-08-17")
    assert out == []
    assert not (master / "Teams/ops/Escalation.md").exists()


def test_seam_promotion_error_surfaces_as_refusal(master: Path):
    """approve() itself can fail closed (create over an existing file). The
    seam turns that into a refusal + inbox note, never a crash."""
    _lead_master(master)
    (master / "Teams/ops/Escalation.md").write_text("already here\n")
    _decision(master, "lead_ops", "p-ops", "approve")
    out = sweep_promotion_approvals(master, _org(master), "2026-08-17")
    assert out[0].status == "refused" and "already exists" in out[0].reason
    assert (master / "Teams/ops/Escalation.md").read_text() == "already here\n"


# --- rendering review material into a lead's Shares.md ---------------------
# _meta/ never compiles into a vault, so a lead cannot read what they would
# be approving unless the compiler (which has master access) renders it into
# their own generated Shares.md. Eligibility here mirrors
# shares.generate_decider_section: computed on the delegated view (admin
# stripped), so admins see nothing here and keep the dashboard.


def test_decider_section_none_for_admin(master: Path):
    """delegated_view strips admin: alice sees nothing in-vault and uses the
    dashboard. Nothing here even though may_approve(alice, ...) is True."""
    _lead_master(master)
    assert generate_promotion_decider_section(master, "alice", "2026-08-17",
                                              rules=RULES) is None


def test_decider_section_lead_sees_own_team_only(master: Path):
    _lead_master(master)
    text = generate_promotion_decider_section(master, "lead_ops", "2026-08-17",
                                              rules=RULES)
    assert text is not None
    assert "## Promotions awaiting your decision" in text
    assert "p-ops" in text and "Teams/ops/Escalation.md" in text
    assert "Restart it." in text            # the body is rendered
    assert "p-co" not in text               # Company/ is never in-vault
    assert "PromotionApprovals/<promo-id>.md" in text
    # the other lead sees nothing — no ops draft is theirs to decide
    assert generate_promotion_decider_section(master, "lead_sales", "2026-08-17",
                                                  rules=RULES) is None


def test_decider_section_patch_renders_diff(master: Path):
    (master / "_meta/org.yaml").write_text(ORG_YAML_LEADS)
    (master / "Teams/ops/Runbook.md").write_text("Ops runbook.\nline two\n")
    draft_promotion(master, person_id="bob", target_path="Teams/ops/Runbook.md",
                    source="s", body="Ops runbook.\nline two changed\n",
                    promo_id="p-patch", created="2026-08-17", mode="patch")
    text = generate_promotion_decider_section(master, "lead_ops", "2026-08-17",
                                              rules=RULES)
    assert "```diff" in text
    assert "-line two" in text and "+line two changed" in text


def test_decider_section_truncates_with_notice(master: Path):
    from brain.promotions import _REVIEW_CAP
    (master / "_meta/org.yaml").write_text(ORG_YAML_LEADS)
    big = "x" * (_REVIEW_CAP + 500) + "\n"
    draft_promotion(master, person_id="bob", target_path="Teams/ops/Big.md",
                    source="s", body=big, promo_id="p-big", created="2026-08-17")
    text = generate_promotion_decider_section(master, "lead_ops", "2026-08-17",
                                              rules=RULES)
    assert "truncated" in text.lower()
    assert "brain promotions show p-big" in text
    # rendered content is bounded — the note doesn't balloon
    assert len(text) < _REVIEW_CAP + 1500


def test_decider_section_no_notice_below_cap(master: Path):
    _lead_master(master)
    text = generate_promotion_decider_section(master, "lead_ops", "2026-08-17",
                                              rules=RULES)
    assert "truncated" not in text.lower()


def test_decider_section_body_with_backtick_fence_does_not_leak(master: Path):
    """An untrusted body containing its own ``` fence must not terminate the
    section's fence early and bleed into the decision recipe below — that
    recipe is structured instructions an agent acts on."""
    (master / "_meta/org.yaml").write_text(ORG_YAML_LEADS)
    body = "Before.\n```\nnested code\n```\nAfter.\n"
    draft_promotion(master, person_id="bob", target_path="Teams/ops/Fenced.md",
                    source="s", body=body, promo_id="p-fence", created="2026-08-17")
    text = generate_promotion_decider_section(master, "lead_ops", "2026-08-17",
                                              rules=RULES)
    assert text is not None
    # a 4-backtick fence safely contains the body's own 3-backtick fence
    assert "````\nBefore.\n```\nnested code\n```\nAfter.\n````" in text
    # structure past the item survives intact — one decision recipe, closed
    assert text.count("decision: approve   # or: reject") == 1
    assert "To decide, write" in text
    assert text.rstrip("\n").endswith("not here.")


def test_decider_section_body_with_longer_fence_escalates(master: Path):
    """A body containing a 4-backtick run needs a 5-backtick outer fence —
    the fence length must track the content, not a fixed guess."""
    (master / "_meta/org.yaml").write_text(ORG_YAML_LEADS)
    body = "Outer.\n````\ninner fenced block\n````\nDone.\n"
    draft_promotion(master, person_id="bob", target_path="Teams/ops/Fenced2.md",
                    source="s", body=body, promo_id="p-fence2", created="2026-08-17")
    text = generate_promotion_decider_section(master, "lead_ops", "2026-08-17",
                                              rules=RULES)
    assert text is not None
    assert "`````\nOuter.\n````\ninner fenced block\n````\nDone.\n`````" in text
    assert text.count("decision: approve   # or: reject") == 1


# Approving is path-shaped authority; reading is rule-shaped. The two are not
# the same fact, and the section must satisfy both before it renders anything.


def test_decider_section_needs_rules_to_render_anything(master: Path):
    """Fail closed: with no read authority to check against, show nothing —
    never everything."""
    _lead_master(master)
    assert generate_promotion_decider_section(master, "lead_ops", "2026-08-17") is None


def test_decider_section_hidden_when_lead_cannot_read_target_space(master: Path):
    """An exact `Teams/ops` rule shadows the `Teams/*` wildcard (exact beats
    wildcard), so lead_ops leads ops but cannot read it. may_approve still
    says yes — the read check is what must stop the render, body and all."""
    (master / "_meta/org.yaml").write_text(ORG_YAML_LEADS)
    (master / "Teams/ops/Runbook.md").write_text("current ops bytes\nline two\n")
    draft_promotion(master, person_id="bob", target_path="Teams/ops/Runbook.md",
                    source="s", body="current ops bytes\nline two changed\n",
                    promo_id="p-patch", created="2026-08-17", mode="patch")
    assert may_approve(
        Person(id="lead_ops", name="Lead Ops", roles=("lead",), teams=("ops",)),
        "Teams/ops/Runbook.md")
    shadowed = (SpaceRule("Teams/ops", read=("person:bob",), write=("person:bob",)),
                *RULES)
    assert generate_promotion_decider_section(master, "lead_ops", "2026-08-17",
                                              rules=shadowed) is None
    # the only thing that changed is read access: under the plain wildcard the
    # same promotion does render
    text = generate_promotion_decider_section(master, "lead_ops", "2026-08-17",
                                              rules=RULES)
    assert text is not None and "line two changed" in text


def test_decider_section_states_the_audience(master: Path):
    """The dashboard card warns who will be able to read this once approved;
    the in-vault reviewer decides from the same fact."""
    _lead_master(master)
    text = generate_promotion_decider_section(master, "lead_ops", "2026-08-17",
                                              rules=RULES)
    assert "readable by 2 people: bob, lead_ops" in text
    assert "`Teams/ops`" in text
    # ...and it tracks the rules, not the path shape
    everyone = (SpaceRule("Teams/ops", read=("everyone",), write=("team:{name}",)),
                *RULES)
    text = generate_promotion_decider_section(master, "lead_ops", "2026-08-17",
                                              rules=everyone)
    assert "readable by everyone in the org (4 people)" in text


def test_decider_section_audience_collapses_to_a_count_when_large(master: Path):
    """A big audience is stated as a number, not dozens of ids."""
    people = "\n".join(
        f"  p{n}: {{name: Person {n}, teams: [ops]}}" for n in range(12))
    (master / "_meta/org.yaml").write_text(
        f"{ORG_YAML_LEADS}{people}\n")
    draft_promotion(master, person_id="bob", target_path="Teams/ops/Escalation.md",
                    source="s", body="Restart it.\n", promo_id="p-ops",
                    created="2026-08-17")
    text = generate_promotion_decider_section(master, "lead_ops", "2026-08-17",
                                              rules=RULES)
    assert "readable by 14 people." in text
    assert "p11" not in text
