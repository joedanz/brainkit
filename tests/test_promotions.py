from pathlib import Path

import pytest

from brain.promotions import (
    PromotionError,
    approve,
    draft_promotion,
    list_pending,
    reject,
)


def test_draft_and_list(master: Path):
    p = draft_promotion(
        master,
        person_id="bob",
        target_path="Company/Frameworks/Onboarding-Call-SOP.md",
        source="People/bob/Sessions/2026-07-01-call.md",
        body="## Onboarding call SOP\n1. Confirm goals.\n",
        promo_id="p-001",
        created="2026-07-07",
    )
    assert p == master / "_meta/promotions/pending/p-001.md"
    pending = list_pending(master)
    assert len(pending) == 1
    assert pending[0].person_id == "bob"
    assert pending[0].target_path == "Company/Frameworks/Onboarding-Call-SOP.md"
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
    draft_promotion(
        master, person_id="bob",
        target_path="Company/Frameworks/SOP.md",
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
        master, person_id="bob", target_path="Company/Frameworks/SOP2.md",
        source="s", body="b", promo_id="p-004", created="2026-07-07",
    )
    rejected = reject(master, "p-004", reason="too client-specific")
    assert "rejected-reason: too client-specific" in rejected.read_text()
    assert not (master / "Company/Frameworks/SOP2.md").exists()
    assert list_pending(master) == []


def test_approve_revalidates_target(master: Path):
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


def test_list_pending_skips_malformed_files(master: Path):
    draft_promotion(
        master, person_id="bob", target_path="Company/Frameworks/Good.md",
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
        "---\ntarget-path: Company/Frameworks/Smuggled.md\n---\nhost content\n"
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
        "target-path: Company/Frameworks/Onboarding-SOP.md\n"
        "source: People/bob/Sessions/call.md\n"
        "---\n"
        "Step one.\n"
    )
    (d / "broken.md").write_text("no frontmatter, no target\n")
    moved = sweep(master, today="2026-07-07")
    assert len(moved) == 1
    pending = list_pending(master)
    assert pending[0].id == "bob-onboarding-sop"
    assert pending[0].target_path == "Company/Frameworks/Onboarding-SOP.md"
    assert not (d / "Onboarding SOP.md").exists()   # swept
    assert (d / "broken.md").exists()               # skipped, left in place
