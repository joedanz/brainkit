import inspect
import json
import subprocess
from pathlib import Path

import pytest

from brain.cli import main
from brain.doctor import Finding
from brain.schemas import Org, Person
from brain.triage import route_findings

from .conftest import RULES
from .test_cli import seed_meta

ALICE = Person(id="alice", name="Alice", roles=("admin",), teams=("sales",))
BOB = Person(id="bob", name="Bob", teams=("ops",))
ORG = Org(people={"alice": ALICE, "bob": BOB})


def test_personal_space_finding_routes_to_owner():
    f = Finding("warn", "unlinked-notes", "People/bob/Notes/Solo.md: no links",
                paths=("People/bob/Notes/Solo.md",))
    routed, unrouted = route_findings([f], ORG, RULES)
    assert routed == {"bob": [f]}
    assert unrouted == 0


def test_corrections_budget_finding_routes_to_its_owner():
    """A correction that never reaches the agent is only fixable by the person
    who wrote it. Left out of TRIAGE_CHECKS the finding is produced and then
    discarded, which is the silent drop this feature exists to prevent."""
    f = Finding("warn", "corrections-budget",
                "People/bob/Corrections/: 2 correction(s) do not fit the protocol budget",
                paths=("People/bob/Corrections/a.md", "People/bob/Corrections/b.md"))
    routed, unrouted = route_findings([f], ORG, RULES)
    assert routed == {"bob": [f]}
    assert unrouted == 0


def test_protocol_findings_reach_the_admins_even_as_warnings():
    """protocol-stale warnings used to reach nobody: triage routed only
    errors outside TRIAGE_CHECKS. Only an admin can act on a protocol, so
    these go to the admins at warn and error alike."""
    size = Finding("warn", "protocol-size",
                   "bob: generated protocol is 41,000 of 50,000 characters (82%)")
    stale = Finding("warn", "protocol-stale", "AGENTS.md differs from the shipped protocol",
                    paths=("AGENTS.md",))
    routed, unrouted = route_findings([size, stale], ORG, RULES)
    assert routed == {"alice": [size, stale]}
    assert unrouted == 0


def test_shared_space_and_unresolvable_route_to_admins():
    shared = Finding("warn", "intel", "Company/Intel/X.md: stale",
                     paths=("Company/Intel/X.md",))
    stray = Finding("warn", "orphan-files", "People/stray.md sits directly under People/",
                    paths=("People/stray.md",))  # People/stray is no org member
    routed, unrouted = route_findings([shared, stray], ORG, RULES)
    assert routed == {"alice": [shared, stray]}
    assert unrouted == 0


def test_two_path_finding_routes_to_both_owners():
    f = Finding("warn", "dup-exact", "People/bob/Notes/Copy.md and Company/Orig.md ...",
                paths=("People/bob/Notes/Copy.md", "Company/Orig.md"))
    routed, _ = route_findings([f], ORG, RULES)
    assert routed == {"bob": [f], "alice": [f]}


def test_error_infra_routes_to_admins_and_info_is_dropped():
    err = Finding("error", "symlinks", "x is a symlink")
    info_dup = Finding("info", "dup-near", "a and b cover similar content",
                       paths=("People/alice/Notes/a.md", "People/bob/Notes/b.md"))
    info_shares = Finding("info", "shares", "pending share")
    warn_infra = Finding("warn", "rule-paths", "rule 'X': missing")
    routed, unrouted = route_findings(
        [err, info_dup, info_shares, warn_infra], ORG, RULES)
    assert routed == {"alice": [err]}  # info + warn-infra never routed
    assert unrouted == 0


def test_dup_near_warn_reaches_only_admins_even_in_a_personal_space():
    warn = Finding("warn", "dup-near", "4 notes are near-duplicates …",
                   paths=("People/bob/Notes/a.md", "People/bob/Notes/b.md"))
    info = Finding("info", "dup-near", "a and b cover similar content",
                   paths=("People/bob/Notes/a.md", "People/alice/Notes/b.md"))
    routed, unrouted = route_findings([warn, info], ORG, RULES)
    assert routed == {"alice": [warn]}
    assert unrouted == 0


def test_no_admins_counts_unrouted():
    org = Org(people={"bob": BOB})
    shared = Finding("warn", "intel", "Company/Intel/X.md: stale",
                     paths=("Company/Intel/X.md",))
    mine = Finding("warn", "unlinked-notes", "People/bob/Notes/Solo.md: no links",
                   paths=("People/bob/Notes/Solo.md",))
    routed, unrouted = route_findings([shared, mine], org, RULES)
    assert routed == {"bob": [mine]}
    assert unrouted == 1


def test_multipath_finding_escalates_to_admins_when_owner_cannot_read_partner_path():
    """A non-admin owner-recipient of a multi-path finding who cannot read
    one of the finding's other spaces triggers admin escalation, even
    though both paths resolved to legitimate People/<id> owners (so
    need_admins would otherwise stay False)."""
    org = Org(people={
        "alice": ALICE,  # admin
        "carol": Person(id="carol", name="Carol"),
        "dave": Person(id="dave", name="Dave"),
    })
    f = Finding("warn", "fact-dup",
                "People/carol/Notes/Deal.md ↔ People/dave/Notes/Deal.md: dup",
                paths=("People/carol/Notes/Deal.md", "People/dave/Notes/Deal.md"))
    routed, unrouted = route_findings([f], org, RULES)
    assert set(routed) == {"carol", "dave", "alice"}
    assert unrouted == 0


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


def test_a_correction_that_never_rendered_reaches_its_authors_digest(master, tmp_path):
    """End to end: doctor finds it, triage delivers it to bob's Inbox. The
    unit test above proves the routing; this proves the whole path, because
    the finding was reaching nobody while every layer claimed it did."""
    seed_meta(master)
    d = master / "People/bob/Corrections"
    d.mkdir(parents=True, exist_ok=True)
    (d / "broken.md").write_text("---\nfrom: 2026-08-19\n---\nI meant to write a rule.\n")

    run_triage(master, today="2026-07-24")
    digest = _digest(master, "bob").read_text()
    assert "## corrections-budget" in digest
    assert "People/bob/Corrections/" in digest
    # It is bob's alone: the admin digest is not where this belongs.
    assert "corrections-budget" not in _digest(master, "alice").read_text()


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


def test_digest_never_writes_through_symlinked_leaf(master, tmp_path):
    seed_meta(master)
    solo = master / "People/bob/Notes/Solo.md"
    solo.parent.mkdir(parents=True)
    solo.write_text("Completely alone.\n")  # unlinked -> bob

    victim = tmp_path / "victim.md"
    victim.write_text("do not touch\n")
    inbox = master / "People/bob/Inbox"
    inbox.mkdir(parents=True)
    digest_path = inbox / DIGEST_NAME
    digest_path.symlink_to(victim)

    report = run_triage(master, today="2026-07-24")
    assert victim.read_text() == "do not touch\n"
    assert digest_path.is_symlink()  # never replaced by a regular-file write
    assert any("symlink" in w for w in report.warnings)


def _bare_master(tmp_path: Path) -> Path:
    """Minimal seeded master with no content that would route a finding to
    bob — isolates the delete branch (empty findings) from the write branch
    that the other symlink tests exercise."""
    master = tmp_path / "bare-master"
    (master / "_meta").mkdir(parents=True)
    (master / "_meta/org.yaml").write_text(
        "people:\n"
        "  alice: {name: Alice, roles: [admin]}\n"
        "  bob: {name: Bob}\n")
    (master / "_meta/spaces.yaml").write_text(
        "spaces:\n"
        '  - {path: Company,     read: [everyone],        write: ["role:admin"]}\n'
        '  - {path: "People/*",  read: ["person:{name}"], write: ["person:{name}"]}\n')
    (master / "Company").mkdir()
    (master / "Company/Home.md").write_text("# Home\n")
    (master / "People/alice").mkdir(parents=True)
    (master / "People/alice/Memory.md").write_text("# Alice\n")
    (master / "People/bob").mkdir(parents=True)
    subprocess.run(["git", "-C", str(master), "init", "-b", "main"],
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", str(master), "add", "-A"],
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", str(master), "-c", "user.name=t",
                    "-c", "user.email=t@t", "commit", "-m", "seed"],
                   capture_output=True, check=True)
    return master


def test_delete_branch_never_follows_symlinked_digest(tmp_path):
    master = _bare_master(tmp_path)
    victim = tmp_path / "victim.md"
    victim.write_text("do not touch\n")
    inbox = master / "People/bob/Inbox"
    inbox.mkdir(parents=True)
    digest_path = inbox / DIGEST_NAME
    digest_path.symlink_to(victim)

    report = run_triage(master, today="2026-07-24")  # bob has no findings
    assert victim.read_text() == "do not touch\n"
    assert digest_path.is_symlink()  # never unlinked
    assert any("refusing to remove" in w for w in report.warnings)


def test_unreadable_digest_warns_instead_of_crashing(master, tmp_path, monkeypatch):
    seed_meta(master)
    solo = master / "People/bob/Notes/Solo.md"
    solo.parent.mkdir(parents=True)
    solo.write_text("Completely alone.\n")  # unlinked -> bob
    run_triage(master, today="2026-07-24")
    bob_d = _digest(master, "bob")
    assert bob_d.exists()

    # Fault-inject only the read triage itself performs when reconciling an
    # existing digest (the fingerprint compare) — not every read of a file
    # named doctor-digest.md, which would also hit doctor's own unrelated
    # content scan (it reads every file under a resolvable space, Inbox
    # included, for graph-connectivity purposes) and mask what this test
    # is isolating.
    original_read_text = Path.read_text

    def flaky_read_text(self, *args, **kwargs):
        if self.name == DIGEST_NAME:
            caller = inspect.currentframe().f_back.f_globals.get("__name__", "")
            if caller == "brain.triage":
                raise PermissionError("simulated unreadable digest")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky_read_text)

    # The fixture vault has baseline unlinked notes routed to admins too, so
    # more than one person may already have a digest at this point — whoever
    # triage reaches first hits the simulated fault. Assert on the general
    # contract (no crash, a warning naming the unreadable digest), not a
    # specific recipient.
    report = run_triage(master, today="2026-07-25")  # must not raise
    assert any(DIGEST_NAME in w for w in report.warnings)


def test_doctor_never_reads_its_own_digest(master, tmp_path):
    """Fixed-point invariant: running triage must never change the next
    doctor run's finding set. Two digest-quoting mechanisms are exercised:
    a fact-dup finding whose advice text contains a literal `[until::]`
    marker (would trip the facts lint's "until without from" check if the
    digest were scanned), and a stem-collision finding whose message
    quotes a `[[wikilink]]` (would mark that stem "connected" and mask a
    genuinely unlinked note if the digest were scanned)."""
    from brain.doctor import run_doctor

    seed_meta(master)

    # (a) duplicate open fact across two files in bob's space -> fact-dup,
    # routed to bob; message text contains "[until::]" verbatim.
    fact1 = master / "People/bob/Notes/Fact1.md"
    fact2 = master / "People/bob/Notes/Fact2.md"
    fact1.parent.mkdir(parents=True, exist_ok=True)
    fact1.write_text("- [[Widget]] costs $10 [from:: 2026-01]\n")
    fact2.write_text("- [[Widget]] costs $10 [from:: 2026-01]\n")

    # (b) two stem-colliding files in Company (a space alice and bob both
    # read) -> stem-collision, routed to admins; message quotes [[Widget]].
    widget_a = master / "Company/Notes/Widget.md"
    widget_b = master / "Company/Archive/Widget.md"
    widget_a.parent.mkdir(parents=True, exist_ok=True)
    widget_b.parent.mkdir(parents=True, exist_ok=True)
    widget_a.write_text("Widget details A. Some content here that differs.\n")
    widget_b.write_text("Widget details B, totally different phrasing altogether.\n")

    before = run_doctor(master)
    assert any(f.check == "fact-dup" for f in before)
    assert any(f.check == "stem-collision" for f in before)

    run_triage(master, today="2026-07-24")

    after = run_doctor(master)
    assert after == before

    again = run_triage(master, today="2026-07-25")
    assert again.digests_written == 0


def _add_carol(master: Path) -> None:
    """Add carol (non-admin) to org.yaml, mirroring test_cycle.py's helper —
    needed here for a non-admin owner pair (bob, carol) neither of whom can
    read the other's personal space."""
    org_yaml = (master / "_meta/org.yaml").read_text()
    org_yaml = org_yaml.replace(
        "bob:   {name: Bob Rivera, teams: [ops], email: bob@acme.com}",
        "bob:   {name: Bob Rivera, teams: [ops], email: bob@acme.com}\n"
        "  carol: {name: Carol, teams: [], email: carol@acme.com}",
    )
    (master / "_meta/org.yaml").write_text(org_yaml)


def test_redaction_hides_unreadable_path_and_escalates_to_admins(master, tmp_path):
    seed_meta(master)
    _add_carol(master)

    bob_note = master / "People/bob/Notes/Deal.md"
    carol_note = master / "People/carol/Notes/Deal.md"
    bob_note.parent.mkdir(parents=True, exist_ok=True)
    carol_note.parent.mkdir(parents=True, exist_ok=True)
    bob_note.write_text("- [[Widget]] costs $10 [from:: 2026-01]\n")
    carol_note.write_text("- [[Widget]] costs $10 [from:: 2026-01]\n")

    run_triage(master, today="2026-07-24")

    bob_digest = _digest(master, "bob").read_text()
    alice_digest = _digest(master, "alice").read_text()

    # (a) bob (non-admin) never sees carol's path or the shared statement
    # text — only a pointer to the admins' digest.
    assert "People/carol" not in bob_digest
    assert "$10" not in bob_digest
    assert "cannot read" in bob_digest

    # (b) admins (alice) receive the full, unredacted message.
    assert "People/bob/Notes/Deal.md" in alice_digest
    assert "People/carol/Notes/Deal.md" in alice_digest
    assert "$10" in alice_digest

    # (c) redaction routed the finding to the admins in the first place.
    assert _digest(master, "alice").exists()


def test_cli_triage_json(master, capsys):
    seed_meta(master)
    (master / "People/stray.md").write_text("orphan\n")
    assert main(["triage", "--master", str(master), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["routed"] >= 1 and payload["unrouted"] == 0
    assert _digest(master, "alice").exists()


# ---- triage is the one writer of the signature cache ---------------------- #


def test_triage_keeps_the_signature_cache_warm(master, monkeypatch):
    from .test_doctor import _count_signatures, _ignore_cache, _templated

    seed_meta(master)
    _ignore_cache(master)
    _templated(master, "People/bob/Notes", 4)
    first = run_triage(master, today="2026-07-24")
    assert (master / "_meta/cache/dedup.db").is_file()
    assert not first.warnings
    calls = _count_signatures(monkeypatch)
    again = run_triage(master, today="2026-07-25")
    assert calls == []
    assert again.finding_counts == first.finding_counts


def test_triage_never_creates_a_cache_git_would_see(master):
    # seed_meta writes no .gitignore: an older master, predating the template.
    from .test_doctor import _templated

    seed_meta(master)
    _templated(master, "People/bob/Notes", 4)
    report = run_triage(master, today="2026-07-24")
    assert not (master / "_meta/cache").exists()
    assert not report.warnings


def test_an_unusable_cache_warns_and_triage_still_routes(master):
    from .test_doctor import _ignore_cache, _templated

    seed_meta(master)
    _ignore_cache(master)
    _templated(master, "People/bob/Notes", 4)
    db = master / "_meta/cache/dedup.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"this is not a database" * 64)
    report = run_triage(master, today="2026-07-24")
    assert any("dedup.db" in w and "rebuilt" in w for w in report.warnings)
    assert report.routed >= 1
    assert "dup-near" in _digest(master, "alice").read_text()
    bob = _digest(master, "bob")
    assert not bob.exists() or "dup-near" not in bob.read_text()



def _damage(db: Path, where: str) -> None:
    """Overwrite part of a SQLite file with garbage: the 100-byte header
    ("not a database"), or every page after the first, leaving the header
    and schema intact ("database disk image is malformed")."""
    data = bytearray(db.read_bytes())
    start, end = (0, 100) if where == "header" else (4096, len(data))
    assert end > start, "fixture must span more than one page"
    data[start:end] = b"\xa5" * (end - start)
    db.write_bytes(bytes(data))


@pytest.mark.parametrize("where", ["header", "interior"])
def test_a_damaged_cache_rebuilds_itself(master, monkeypatch, where):
    """A damaged dedup.db must not cost a full recompute on every cycle
    forever: the writer rebuilds it once, says so, and the next cycle is
    warm again."""
    from .test_doctor import _count_signatures, _ignore_cache, _templated

    seed_meta(master)
    _ignore_cache(master)
    _templated(master, "People/bob/Notes", 12)
    first = run_triage(master, today="2026-07-24")
    _damage(master / "_meta/cache/dedup.db", where)

    rebuilt = run_triage(master, today="2026-07-25")
    assert any("dedup.db" in w and "rebuilt" in w for w in rebuilt.warnings)
    assert rebuilt.finding_counts == first.finding_counts

    calls = _count_signatures(monkeypatch)
    again = run_triage(master, today="2026-07-26")
    assert calls == []
    assert not again.warnings
    assert sorted(p.name for p in (master / "_meta/cache").iterdir()) == ["dedup.db"]


def test_standalone_doctor_leaves_a_damaged_cache_alone(master):
    from brain.doctor import run_doctor

    from .test_doctor import _ignore_cache, _templated

    seed_meta(master)
    _ignore_cache(master)
    _templated(master, "People/bob/Notes", 12)
    expected = run_doctor(master)
    run_triage(master, today="2026-07-24")
    db = master / "_meta/cache/dedup.db"
    _damage(db, "interior")
    before = db.read_bytes()
    assert run_doctor(master) == expected
    assert db.read_bytes() == before
    assert sorted(p.name for p in db.parent.iterdir()) == ["dedup.db"]


# ---- near-duplicate groups through triage --------------------------------- #


def test_a_group_in_one_persons_space_still_routes_to_the_admins(master):
    """dup-near is admin-only (ADMIN_CHECKS): even a group entirely inside
    bob's own space never reaches bob — merging near-duplicates needs a
    person's judgement, so it goes to alice, the admin."""
    from brain.doctor import run_doctor

    from .test_doctor import _templated

    seed_meta(master)
    _templated(master, "People/bob/Notes", 5)
    near = [f for f in run_doctor(master) if f.check == "dup-near"]
    assert len(near) == 1 and len(near[0].paths) == 5
    routed, unrouted = route_findings(near, ORG, RULES)
    assert routed == {"alice": near}
    assert unrouted == 0


def test_a_large_group_is_one_short_digest_line(master):
    from .test_doctor import _templated

    seed_meta(master)
    _templated(master, "People/bob/Notes", 30)
    run_triage(master, today="2026-07-24")
    digest = _digest(master, "alice").read_text()  # dup-near is admin-only
    section = digest.split("## dup-near\n\n", 1)[1].split("\n\n## ", 1)[0]
    lines = [ln for ln in section.splitlines() if ln.startswith("- ")]
    assert len(lines) == 1
    assert lines[0].startswith("- 30 notes are near-duplicates of each other in People/bob/Notes: ")
    assert len(lines[0]) < 300
    bob = _digest(master, "bob")
    assert not bob.exists() or "dup-near" not in bob.read_text()


def test_a_redacted_group_line_names_at_most_three_paths():
    """A non-admin who cannot read one member gets the redacted line, built
    from the paths they can read — capped, or a group of hundreds would be
    hundreds of paths on one line."""
    from brain.triage import _display

    mine = [f"People/bob/Notes/Report {i:02d}.md" for i in range(10)]
    f = Finding("warn", "dup-near", "11 notes are near-duplicates of each other: ...",
                paths=tuple(sorted([*mine, "People/carol/Notes/Report 00.md"])))
    line = _display(f, BOB, RULES, is_admin=False)
    assert line == (
        "People/bob/Notes/Report 00.md, People/bob/Notes/Report 01.md, "
        "People/bob/Notes/Report 02.md, and 7 more: dup-near involving a note "
        "in a space you cannot read — the admins' digest has the detail")
    assert "carol" not in line
    # Three or fewer readable paths are all named, as before.
    two = Finding("warn", "dup-near", "...", paths=(mine[0], mine[1], "People/carol/x.md"))
    assert _display(two, BOB, RULES, is_admin=False).startswith(
        f"{mine[0]}, {mine[1]}: dup-near involving")


def test_a_shared_note_does_not_bridge_two_private_spaces(master):
    """dup-near is admin-only: bob's and carol's private copies of the same
    shared note reach alice, never bob or carol, and each pair stays intact
    in her digest — the shared note near-dups bob's copy and carol's copy
    as two separate lines, never merged into one group that would name the
    other's path."""
    from .test_doctor import _templated

    seed_meta(master)
    _add_carol(master)
    (shared,) = _templated(master, "Company/Reports", 1)
    (bobs,) = _templated(master, "People/bob/Notes", 1, prefix="Copy")
    (carols,) = _templated(master, "People/carol/Notes", 1, prefix="Draft")
    run_triage(master, today="2026-07-24")

    alice_digest = _digest(master, "alice").read_text()
    assert f"{shared} and {bobs} are near-duplicates (text overlap)" in alice_digest
    assert f"{shared} and {carols} are near-duplicates (text overlap)" in alice_digest
    bob_digest = _digest(master, "bob")
    assert not bob_digest.exists() or "dup-near" not in bob_digest.read_text()
    carol_digest = _digest(master, "carol")
    assert not carol_digest.exists() or "dup-near" not in carol_digest.read_text()


def test_protocol_blocked_reaches_the_admins_and_info_reaches_nobody():
    from brain.triage import ADMIN_CHECKS
    assert "protocol-blocked" in ADMIN_CHECKS
    err = Finding("error", "protocol-blocked",
                  "bob: AGENTS.md and CLAUDE.md would be dropped whole by Hermes")
    info = Finding("info", "protocol-blocked",
                   "bob: `Clients/X/` is counted in its folder's summary line")
    routed, unrouted = route_findings([err, info], ORG, RULES)
    assert routed == {"alice": [err]}
    assert unrouted == 0
