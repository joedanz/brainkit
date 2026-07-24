import subprocess
from pathlib import Path

from brain.doctor import Finding
from brain.schemas import Org, Person
from brain.triage import route_findings

from .test_cli import seed_meta  # noqa: F401  (used by later tasks' tests)

ALICE = Person(id="alice", name="Alice", roles=("admin",), teams=("sales",))
BOB = Person(id="bob", name="Bob", teams=("ops",))
ORG = Org(people={"alice": ALICE, "bob": BOB})


def test_personal_space_finding_routes_to_owner():
    f = Finding("warn", "unlinked-notes", "People/bob/Notes/Solo.md: no links",
                paths=("People/bob/Notes/Solo.md",))
    routed, unrouted = route_findings([f], ORG)
    assert routed == {"bob": [f]}
    assert unrouted == 0


def test_shared_space_and_unresolvable_route_to_admins():
    shared = Finding("warn", "intel", "Company/Intel/X.md: stale",
                     paths=("Company/Intel/X.md",))
    stray = Finding("warn", "orphan-files", "People/stray.md sits directly under People/",
                    paths=("People/stray.md",))  # People/stray is no org member
    routed, unrouted = route_findings([shared, stray], ORG)
    assert routed == {"alice": [shared, stray]}
    assert unrouted == 0


def test_two_path_finding_routes_to_both_owners():
    f = Finding("warn", "dup-exact", "People/bob/Notes/Copy.md and Company/Orig.md ...",
                paths=("People/bob/Notes/Copy.md", "Company/Orig.md"))
    routed, _ = route_findings([f], ORG)
    assert routed == {"bob": [f], "alice": [f]}


def test_error_infra_routes_to_admins_and_info_is_dropped():
    err = Finding("error", "symlinks", "x is a symlink")
    info_dup = Finding("info", "dup-near", "a and b cover similar content",
                       paths=("People/alice/Notes/a.md", "People/bob/Notes/b.md"))
    info_shares = Finding("info", "shares", "pending share")
    warn_infra = Finding("warn", "rule-paths", "rule 'X': missing")
    routed, unrouted = route_findings([err, info_dup, info_shares, warn_infra], ORG)
    assert routed == {"alice": [err]}  # info + warn-infra never routed
    assert unrouted == 0


def test_no_admins_counts_unrouted():
    org = Org(people={"bob": BOB})
    shared = Finding("warn", "intel", "Company/Intel/X.md: stale",
                     paths=("Company/Intel/X.md",))
    mine = Finding("warn", "unlinked-notes", "People/bob/Notes/Solo.md: no links",
                   paths=("People/bob/Notes/Solo.md",))
    routed, unrouted = route_findings([shared, mine], org)
    assert routed == {"bob": [mine]}
    assert unrouted == 1


from brain.triage import DIGEST_NAME, run_triage


def _commits(master: Path) -> int:
    r = subprocess.run(["git", "-C", str(master), "rev-list", "--count", "HEAD"],
                       capture_output=True, text=True, check=True)
    return int(r.stdout.strip())


def _digest(master: Path, pid: str) -> Path:
    return master / f"People/{pid}/Inbox/{DIGEST_NAME}"


def test_run_triage_writes_routes_and_is_idempotent(master, tmp_path):
    seed_meta(master)
    (master / "People/stray.md").write_text("orphan\n")  # -> admins (alice)
    solo = master / "People/bob/Notes/Solo.md"
    solo.parent.mkdir(parents=True)
    solo.write_text("Completely alone.\n")  # unlinked -> bob

    report = run_triage(master, today="2026-07-24")
    assert report.routed >= 2 and report.unrouted == 0
    assert report.digests_written >= 2 and not report.warnings

    alice_d, bob_d = _digest(master, "alice"), _digest(master, "bob")
    assert "People/stray.md" in alice_d.read_text()
    assert "People/bob/Notes/Solo.md" in bob_d.read_text()
    assert "fingerprint:" in bob_d.read_text() and "source: doctor" in bob_d.read_text()

    # unchanged second run: no writes, no commits
    before = _commits(master)
    again = run_triage(master, today="2026-07-25")
    assert again.digests_written == 0 and again.digests_removed == 0
    assert _commits(master) == before


def test_fixed_finding_disappears_and_empty_digest_is_deleted(master, tmp_path):
    seed_meta(master)
    solo = master / "People/bob/Notes/Solo.md"
    solo.parent.mkdir(parents=True)
    solo.write_text("Completely alone.\n")
    run_triage(master, today="2026-07-24")
    assert "Solo.md" in _digest(master, "bob").read_text()

    solo.unlink()  # the fix
    r2 = run_triage(master, today="2026-07-24")
    bob_d = _digest(master, "bob")
    # bob's baseline notes may still be unlinked -> rewritten without Solo,
    # or nothing remains -> deleted. Either way Solo.md is gone.
    if bob_d.exists():
        assert "Solo.md" not in bob_d.read_text()
    else:
        assert r2.digests_removed >= 1


def test_digest_never_follows_symlinked_inbox(master, tmp_path):
    seed_meta(master)
    solo = master / "People/bob/Notes/Solo.md"
    solo.parent.mkdir(parents=True)
    solo.write_text("Completely alone.\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (master / "People/bob/Inbox").symlink_to(outside)

    report = run_triage(master, today="2026-07-24")
    assert not (outside / DIGEST_NAME).exists()
    assert any("symlink" in w for w in report.warnings)
