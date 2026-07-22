from datetime import date as _date
from pathlib import Path

from brain.doctor import run_doctor, _check_intel
from brain.cli import main

from .test_cli import ORG_YAML, SPACES_YAML, seed_meta


def _compile(master, tmp_path):
    out = tmp_path / "compiled"
    main(["compile", "--master", str(master), "--out", str(out)])
    return out


def _severities(findings, check):
    return [f.severity for f in findings if f.check == check]


def test_clean_master_has_no_errors(master):
    seed_meta(master)
    findings = run_doctor(master)
    assert not [f for f in findings if f.severity == "error"]


def test_broken_org_yaml_is_error_and_stops_dependent_checks(master):
    seed_meta(master)
    (master / "_meta/org.yaml").write_text("people: []\n")  # list, not mapping
    findings = run_doctor(master)
    assert _severities(findings, "meta") == ["error"]
    assert not [f for f in findings if f.check == "subjects"]  # skipped


def test_malformed_yaml_is_error_not_crash(master):
    seed_meta(master)
    (master / "_meta/org.yaml").write_text("people: {unclosed\n")  # invalid YAML
    findings = run_doctor(master)  # must not raise
    assert any(f.check == "meta" and f.severity == "error" for f in findings)


def test_unknown_person_subject_is_error(master):
    seed_meta(master)
    (master / "_meta/spaces.yaml").write_text(
        SPACES_YAML + '  - {path: "Clients/acme", read: ["person:ghost"], write: []}\n'
    )
    findings = run_doctor(master)
    assert "error" in _severities(findings, "subjects")


def test_unused_team_subject_is_warn(master):
    seed_meta(master)
    (master / "_meta/spaces.yaml").write_text(
        SPACES_YAML + '  - {path: "Clients/acme", read: ["team:phantom"], write: []}\n'
    )
    findings = run_doctor(master)
    assert "warn" in _severities(findings, "subjects")


def test_rule_path_matching_nothing_is_warn(master):
    seed_meta(master)
    (master / "_meta/spaces.yaml").write_text(
        SPACES_YAML + "  - {path: Handbook, read: [everyone], write: []}\n"
    )
    findings = run_doctor(master)
    assert "warn" in _severities(findings, "rule-paths")


def test_space_with_no_rule_is_warn(master):
    seed_meta(master)
    (master / "Projects").mkdir()  # not a space; ignored by enumerate_spaces
    (master / "Teams/newteam/Notes.md").parent.mkdir(parents=True)
    (master / "Teams/newteam/Notes.md").write_text("x\n")
    # Teams/* rule covers it -> no warning expected for newteam
    findings = run_doctor(master)
    assert "warn" not in _severities(findings, "space-coverage")
    # now remove the wildcard rule so sales/ops/newteam become unreachable
    (master / "_meta/spaces.yaml").write_text(
        'spaces:\n  - {path: Company, read: [everyone], write: ["role:admin"]}\n'
    )
    findings = run_doctor(master)
    assert "warn" in _severities(findings, "space-coverage")


def test_orphan_loose_file_under_nested_top_is_warn(master):
    seed_meta(master)
    # A file directly under Clients/ (not in a client subfolder) is in no space,
    # so the compiler copies it into nobody's vault — it vanishes silently.
    (master / "Clients/Globex.md").write_text("# Globex\nLoose, in no space.\n")
    findings = run_doctor(master)
    assert "warn" in _severities(findings, "orphan-files")
    # A properly nested client file is fine.
    (master / "Clients/Globex.md").unlink()
    (master / "Clients/Globex/Globex.md").parent.mkdir(parents=True)
    (master / "Clients/Globex/Globex.md").write_text("# Globex\n")
    findings = run_doctor(master)
    assert "warn" not in _severities(findings, "orphan-files")


def test_cross_space_reference_warns_and_same_space_is_silent(master):
    seed_meta(master)
    # The fixture's Company/Home.md links to [[Big Deal Decision]] (Company, same
    # space) and [[Q3 Pipeline]] (Teams/sales). Company is everyone-readable, but
    # bob (ops) cannot read Teams/sales — so the second link leaks the name.
    findings = [f for f in run_doctor(master) if f.check == "cross-refs"]
    home = [f for f in findings if f.message.startswith("Company/Home.md")]
    assert len(home) == 1                     # same-space link is NOT flagged
    assert home[0].severity == "warn"
    assert "Teams/sales" in home[0].message   # the space that leaked
    assert "bob" in home[0].message           # the reader who cannot see it


def _restrict_vandenberg(master):
    """Add a Vandenberg client space readable only by alice."""
    (master / "_meta/spaces.yaml").write_text(
        SPACES_YAML
        + '  - {path: "Clients/Vandenberg", read: ["person:alice"], write: ["person:alice"]}\n')
    (master / "Clients/Vandenberg").mkdir(parents=True, exist_ok=True)
    (master / "Clients/Vandenberg/Vandenberg.md").write_text("# Vandenberg\nprivate.\n")


def test_plain_text_client_name_in_shared_prose_is_warn(master):
    seed_meta(master)
    _restrict_vandenberg(master)
    # Company is everyone-readable; naming the client in prose (no wikilink) leaks
    # the name to bob, who cannot see that client.
    (master / "Company/Memory.md").write_text(
        "We learned a lot from the Vandenberg expedition.\n")
    refs = [f for f in run_doctor(master) if f.check == "plain-ref"]
    mem = [f for f in refs if f.message.startswith("Company/Memory.md")]
    assert mem and mem[0].severity == "warn"
    assert "Vandenberg" in mem[0].message and "bob" in mem[0].message


def test_plain_ref_skips_wikilinks_and_lowercase_names(master):
    seed_meta(master)
    _restrict_vandenberg(master)
    # A wikilink mention is cross-refs' job, not plain-ref; and a lowercase
    # restricted space (Teams/sales) is never scanned (would collide with prose).
    (master / "Company/Memory.md").write_text(
        "See [[Vandenberg]] for context. Our sales pipeline is healthy.\n")
    refs = [f for f in run_doctor(master) if f.check == "plain-ref"]
    assert not any(f.message.startswith("Company/Memory.md") for f in refs)


def test_compiled_checks_clean_and_missing_vault(master, tmp_path):
    seed_meta(master)
    out = _compile(master, tmp_path)
    findings = run_doctor(master, out)
    assert not [f for f in findings if f.severity == "error"]

    import shutil
    shutil.rmtree(out / "bob")
    findings = run_doctor(master, out)
    assert "warn" in _severities(findings, "compiled")  # bob never compiled


import json as jsonlib


def test_cli_doctor_clean_exits_zero(master, tmp_path, capsys):
    seed_meta(master)
    out = _compile(master, tmp_path)
    capsys.readouterr()
    code = main(["doctor", "--master", str(master), "--out", str(out)])
    assert code == 0
    assert "0 error(s)" in capsys.readouterr().out


def test_cli_doctor_error_exits_one_and_json(master, tmp_path, capsys):
    seed_meta(master)
    (master / "Company/evil.md").symlink_to(master / "People/bob/Memory.md")
    code = main(["doctor", "--master", str(master), "--json"])
    assert code == 1
    payload = jsonlib.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert any(f["check"] == "symlinks" for f in payload["findings"])


def test_meta_inside_vault_is_security_error(master, tmp_path):
    seed_meta(master)
    out = _compile(master, tmp_path)
    (out / "alice/_meta").mkdir()
    (out / "alice/_meta/org.yaml").write_text("people: {}\n")
    findings = run_doctor(master, out)
    assert "error" in _severities(findings, "compiled")


def test_crashed_compile_tombstone_is_error(master, tmp_path):
    seed_meta(master)
    out = _compile(master, tmp_path)
    (out / ".bob.old").mkdir()
    findings = run_doctor(master, out)
    assert "error" in _severities(findings, "compiled")


def test_drift_is_info_not_error(master, tmp_path):
    seed_meta(master)
    out = _compile(master, tmp_path)
    (out / "bob/People/bob/Memory.md").write_text("edited, not yet written back\n")
    findings = run_doctor(master, out)
    drift = [f for f in findings if f.check == "compiled" and "awaiting writeback" in f.message]
    assert drift and all(f.severity == "info" for f in drift)


def test_malformed_pending_promotion_is_warn(master):
    seed_meta(master)
    (master / "_meta/promotions/pending/broken.md").write_text("no frontmatter\n")
    findings = run_doctor(master)
    assert "warn" in _severities(findings, "promotions")


def test_stuck_draft_without_target_is_warn(master):
    seed_meta(master)
    d = master / "People/bob/Promotions"
    d.mkdir(parents=True)
    (d / "no-target.md").write_text("---\nsource: x\n---\nBody.\n")
    findings = run_doctor(master)
    assert any(
        f.check == "promotions" and f.severity == "warn" and "no-target.md" in f.message
        for f in findings
    )


def test_pending_count_is_info(master):
    seed_meta(master)
    from brain.promotions import draft_promotion
    draft_promotion(master, "bob", "Company/Playbook/SOP.md",
                    "People/bob/x.md", "Body.\n", "p-1", "2026-07-07")
    findings = run_doctor(master)
    assert any(f.check == "promotions" and f.severity == "info" for f in findings)


def test_manifest_missing_compiled_key_is_error_not_crash(master, tmp_path):
    seed_meta(master)
    out = _compile(master, tmp_path)
    from brain.compiler import MANIFEST_NAME
    (out / "bob" / MANIFEST_NAME).write_text("{}")   # valid JSON, wrong shape
    findings = run_doctor(master, out)               # must not raise
    assert any(f.check == "compiled" and f.severity == "error"
               and "bob" in f.message for f in findings)


def test_doctor_warns_on_malformed_facts_and_empty_entity(master):
    seed_meta(master)
    (master / "Company/Bad.md").write_text(
        "---\nentity: \n---\n# Bad\n\n"
        "- broken [from:: 2026-99]\n"
        "- inverted [from:: 2026-05] [until:: 2026-01]\n")
    from brain.doctor import run_doctor
    findings = [f for f in run_doctor(master) if f.check == "facts"]
    msgs = [f.message for f in findings]
    assert all(f.severity == "warn" for f in findings)
    assert any("Company/Bad.md:6" in m and "unparseable" in m for m in msgs)
    assert any("Company/Bad.md:7" in m and "before" in m for m in msgs)
    assert any("empty entity type" in m for m in msgs)


def test_doctor_quiet_on_wellformed_facts(master):
    seed_meta(master)
    (master / "Company/Good.md").write_text(
        "---\nentity: client\n---\n# Good\n\n- fine [from:: 2026-01]\n")
    from brain.doctor import run_doctor
    assert [f for f in run_doctor(master) if f.check == "facts"] == []


def test_space_readable_by_no_one_is_warn(master):
    seed_meta(master)
    findings = run_doctor(master)
    assert "warn" not in _severities(findings, "unreadable-spaces")
    # a folder matching no team id (e.g. a case mismatch like Teams/Sales vs
    # 'sales', or a team no one is on) matches the Teams/* rule but resolves to
    # zero readers — hidden from everyone, silently. (A literal case-mismatch
    # dir can't be created next to Teams/sales on case-insensitive filesystems,
    # so the fixture uses a distinct name; the reader math is identical.)
    (master / "Teams/Design").mkdir()
    (master / "Teams/Design/Playbook.md").write_text("x\n")
    findings = run_doctor(master)
    warns = [f.message for f in findings
             if f.check == "unreadable-spaces" and f.severity == "warn"]
    assert any("Teams/Design" in m for m in warns)


def test_departed_persons_space_is_warn(master):
    seed_meta(master)
    (master / "People/ghost/Notes.md").parent.mkdir(parents=True)
    (master / "People/ghost/Notes.md").write_text("left the company\n")
    findings = run_doctor(master)
    warns = [f.message for f in findings
             if f.check == "unreadable-spaces" and f.severity == "warn"]
    assert any("People/ghost" in m for m in warns)


def test_unreadable_space_check_skips_empty_org(master):
    seed_meta(master)
    (master / "_meta/org.yaml").write_text("people: {}\n")
    (master / "Teams/Design").mkdir()
    (master / "Teams/Design/Playbook.md").write_text("x\n")
    findings = run_doctor(master)  # with no people, every space is unreadable — noise
    assert not [f for f in findings if f.check == "unreadable-spaces"]


def test_doctor_flags_patch_draft_with_missing_target(master):
    seed_meta(master)
    d = master / "People/bob/Promotions/ghost.md"
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_text("---\ntarget-path: Company/Intel/Ghost.md\nmode: patch\n---\nbody\n")
    findings = run_doctor(master)
    assert any(f.check == "promotions" and f.severity == "warn"
               and "missing page" in f.message for f in findings)


def _intel(master, name, text):
    f = master / "Company/Intel" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)


def test_intel_absent_dir_is_silent(master):
    assert _check_intel(master, today=_date(2026, 7, 21)) == []


def test_intel_flags_lingering_addenda_both_dashes(master):
    _intel(master, "Portugal — updates 2026-06.md", "New ferry. [s](https://x), as of 2026-06\n")
    _intel(master, "Spain - updates 2026-05.md", "Visa change. [s](https://x), as of 2026-05\n")
    msgs = [f.message for f in _check_intel(master, today=_date(2026, 7, 21))]
    assert len(msgs) == 2
    assert all("unfolded addendum" in m for m in msgs)


def test_intel_flags_stale_and_uncited_pages(master):
    _intel(master, "Fresh.md", "Claim. [s](https://x), as of 2026-01\n")
    _intel(master, "Stale.md", "Claim. [s](https://x), as of 2025-06\n")
    _intel(master, "Captured.md", "Claim. [s](file.pdf), captured 2026-07\n")
    _intel(master, "Uncited.md", "No dates here at all.\n")
    _intel(master, "Home.md", "Map of pages — no citations by design.\n")
    findings = _check_intel(master, today=_date(2026, 7, 21))
    assert all(f.severity == "warn" and f.check == "intel" for f in findings)
    msgs = "\n".join(f.message for f in findings)
    assert "Stale.md" in msgs and "stale" in msgs
    assert "Uncited.md" in msgs and "no dated citations" in msgs
    assert "Fresh.md" not in msgs
    assert "Captured.md" not in msgs
    assert "Home.md" not in msgs


def test_intel_boundary_is_over_twelve_months(master):
    # Exactly 12 months old is fine; 13 is stale.
    _intel(master, "Edge.md", "Claim. [s](https://x), as of 2025-07\n")
    assert _check_intel(master, today=_date(2026, 7, 21)) == []
    _intel(master, "Over.md", "Claim. [s](https://x), as of 2025-06\n")
    assert len(_check_intel(master, today=_date(2026, 7, 21))) == 1


def test_run_doctor_includes_intel_check(master):
    seed_meta(master)
    _intel(master, "Old — updates 2025-01.md", "x\n")
    assert any(f.check == "intel" for f in run_doctor(master))


def test_doctor_flags_unknown_mode_draft(master):
    seed_meta(master)
    d = master / "People/bob/Promotions/odd.md"
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_text("---\ntarget-path: Company/Intel/X.md\nmode: rewrite\n---\nbody\n")
    findings = run_doctor(master)
    assert any(f.check == "promotions" and f.severity == "warn"
               and "sweep will never move it" in f.message for f in findings)


def test_unlinked_notes_flags_isolated_note(tmp_path):
    # A dedicated, minimal master (not the shared `master` fixture, whose
    # baseline content other suites assert byte-for-byte) so the exact set of
    # connections here is fully controlled.
    m = tmp_path / "master"
    m.mkdir()
    seed_meta(m)
    (m / "Company/Hub.md").parent.mkdir(parents=True, exist_ok=True)
    (m / "Company/Hub.md").write_text("See [[Spoke]].\n")
    (m / "Company/Spoke.md").write_text("plain text\n")               # linked: is a target
    (m / "Company/Island.md").write_text("plain text, no links\n")    # flagged
    (m / "Company/Dated.md").write_text("- fact [from:: 2026-01]\n")  # has facts: not flagged
    (m / "People/p1/Inbox/x.md").parent.mkdir(parents=True, exist_ok=True)
    (m / "People/p1/Inbox/x.md").write_text("plain text\n")           # Inbox: exempt

    findings = run_doctor(m)
    unlinked = [f for f in findings if f.check == "unlinked-notes"]
    assert [f.message.split(":")[0] for f in unlinked] == ["Company/Island.md"]
    assert all(f.severity == "warn" for f in unlinked)


def test_unlinked_notes_does_not_flag_mined_edges(tmp_path):
    """A note connected only through mined structure (folder-index parent,
    date-sequence neighbor, or shared entity type) is still reachable by
    brain_graph and PPR retrieval, so it must not be flagged — only a note
    with no connection of any kind (mined or otherwise) should be."""
    m = tmp_path / "master"
    m.mkdir()
    seed_meta(m)

    # Folder-index parent: Projects.md is the index note for Projects/, so
    # Sub.md gets an `up` edge to it purely from folder structure.
    (m / "Company/Projects").mkdir(parents=True, exist_ok=True)
    (m / "Company/Projects/Projects.md").write_text("Index note.\n")
    (m / "Company/Projects/Sub.md").write_text("No links, no facts.\n")

    # Date-sequence neighbors: same folder, dated filenames, no other links.
    (m / "Company/Logs").mkdir(parents=True, exist_ok=True)
    (m / "Company/Logs/2026-01-01 Standup.md").write_text("Notes.\n")
    (m / "Company/Logs/2026-01-02 Standup.md").write_text("Notes.\n")

    # Shared entity type: both are `entity: client` pages in unrelated
    # folders with no wikilinks between them.
    (m / "Clients/acme").mkdir(parents=True, exist_ok=True)
    (m / "Clients/acme/Acme.md").write_text("---\nentity: client\n---\nAcme.\n")
    (m / "Clients/beta").mkdir(parents=True, exist_ok=True)
    (m / "Clients/beta/Beta.md").write_text("---\nentity: client\n---\nBeta.\n")

    # Truly isolated: no links, no facts, no mined structure of any kind.
    (m / "Company/Island.md").write_text("plain text, no links\n")

    findings = run_doctor(m)
    unlinked = [f for f in findings if f.check == "unlinked-notes"]
    assert [f.message.split(":")[0] for f in unlinked] == ["Company/Island.md"]
    assert all(f.severity == "warn" for f in unlinked)


def test_doctor_flags_symlinked_patch_target(master, tmp_path):
    seed_meta(master)
    outside = tmp_path / "outside.md"
    outside.write_text("x\n")
    link = master / "Company/Intel/Link.md"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside)
    d = master / "People/bob/Promotions/link.md"
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_text("---\ntarget-path: Company/Intel/Link.md\nmode: patch\n---\nbody\n")
    findings = run_doctor(master)
    assert any(f.check == "promotions" and "targets a symlink" in f.message
               for f in findings)
