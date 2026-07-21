from pathlib import Path

import pytest

from brain.promotions import (
    PromotionError,
    approve,
    draft_into_space,
    draft_promotion,
    list_pending,
    reject,
)

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
    kwargs = dict(target_path="Company/Playbook/SOP.md", source="src",
                  body="b", created="2026-07-07")
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
