import os
from datetime import date as _date
from pathlib import Path

import pytest

from brain.doctor import run_doctor, _check_citations, _check_intel, _citation_urls
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


def test_orphan_check_covers_custom_tops(master):
    seed_meta(master)
    (master / "Vendors").mkdir()
    (master / "Vendors/loose.md").write_text("stray\n")
    findings = run_doctor(master)
    assert any(f.check == "orphan-files" and "Vendors/loose.md" in f.message
               for f in findings)


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


def test_doctor_flags_conflicting_open_facts_on_entity_page(master):
    # The issue's exact case: the author forgot the [until::] on the first
    # line, so both facts are "true now". Host page carries entity
    # frontmatter, so facts with no wikilinks still key on the page itself.
    seed_meta(master)
    (master / "Clients/acme/Acme.md").write_text(
        "---\nentity: client\n---\n# Acme\n\n"
        "- Acme's plan is Enterprise [from:: 2025-03]\n"
        "- Acme's plan is Growth [from:: 2026-01]\n")
    from brain.doctor import run_doctor
    findings = [f for f in run_doctor(master) if f.check == "fact-conflict"]
    assert len(findings) == 1
    f = findings[0]
    assert f.severity == "warn"
    assert "Clients/acme/Acme.md:6" in f.message
    assert "Clients/acme/Acme.md:7" in f.message
    assert "Enterprise" in f.message and "Growth" in f.message
    assert "[until::]" in f.message


def test_doctor_flags_cross_page_dup_via_stem_resolution(master):
    # Double-landed ingest: the same line landed on two pages. [[Acme]]
    # resolves by stem to Clients/acme/Acme.md on both, so the facts group.
    seed_meta(master)
    (master / "Clients/acme/Acme.md").write_text(
        "---\nentity: client\n---\n# Acme\n")
    (master / "Company/Notes.md").write_text(
        "# Notes\n\n- [[Acme]] is on the Enterprise plan [from:: 2025-03]\n")
    (master / "Teams/sales/Call.md").write_text(
        "# Call\n\n- [[Acme]] is on the Enterprise plan [from:: 2026-01]\n")
    from brain.doctor import run_doctor
    findings = [f for f in run_doctor(master) if f.check == "fact-dup"]
    assert len(findings) == 1
    f = findings[0]
    assert f.severity == "warn"
    assert "Company/Notes.md:3" in f.message
    assert "Teams/sales/Call.md:3" in f.message
    assert "write-back" in f.message


def test_doctor_groups_unresolved_targets_by_raw_text(master):
    # Fresh ingests often reference entity pages that don't exist yet — two
    # facts pointing at the same not-yet-created [[Ghost]] still conflict.
    seed_meta(master)
    (master / "Company/A.md").write_text(
        "# A\n\n- [[Ghost]] status is active [from:: 2025-06]\n")
    (master / "Company/B.md").write_text(
        "# B\n\n- [[Ghost]] status is churned [from:: 2026-02]\n")
    from brain.doctor import run_doctor
    findings = [f for f in run_doctor(master) if f.check == "fact-conflict"]
    assert len(findings) == 1


def test_doctor_fact_conflicts_quiet_on_clean_history(master):
    # A properly closed predecessor, an additive pair, and a keyless fact:
    # none of it should fire either check.
    seed_meta(master)
    (master / "Clients/acme/Acme.md").write_text(
        "---\nentity: client\n---\n# Acme\n\n"
        "- Acme's plan is Enterprise [from:: 2025-03] [until:: 2026-01]\n"
        "- Acme's plan is Growth [from:: 2026-01]\n"
        "- Acme hired [[Bob]] [from:: 2026-02]\n"
        "- Acme hired [[Carol]] [from:: 2026-03]\n")
    (master / "Company/Loose.md").write_text(
        "# Loose\n\n- the sky is blue [from:: 2020-01]\n"
        "- the sky is grey [from:: 2021-01]\n")  # no keys: host not an entity
    from brain.doctor import run_doctor
    checks = {f.check for f in run_doctor(master)}
    assert "fact-dup" not in checks and "fact-conflict" not in checks


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


TODAY = _date(2026, 7, 21)


def _distilled(master, rel, source, body):
    f = master / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f"---\ndistilled: {source}\n---\n\n{body}")


def test_citations_ignores_pages_without_the_marker(master):
    # The whole point of the marker: original thinking and distilled content
    # are indistinguishable outside Intel, so an unmarked page is never judged.
    (master / "People/bob/Notes").mkdir(parents=True)
    (master / "People/bob/Notes/Thoughts.md").write_text("My own take.\n")
    assert _check_citations(master, today=TODAY) == []


def test_citations_flags_uncited_distilled_page(master):
    _distilled(master, "People/bob/Notes/Ferries.md",
               "https://example.com/ferries", "Ferries run hourly.\n")
    findings = _check_citations(master, today=TODAY)
    assert [(f.severity, f.check) for f in findings] == [("warn", "citations")]
    assert "no dated citations" in findings[0].message
    assert "https://example.com/ferries" in findings[0].message
    assert findings[0].paths == ("People/bob/Notes/Ferries.md",)


def test_citations_accepts_a_dated_citation(master):
    _distilled(master, "Clients/acme/Ferries.md", "https://example.com/f",
               "Ferries run hourly. [source](https://example.com/f), as of 2026-06\n")
    assert _check_citations(master, today=TODAY) == []


def test_citations_flags_stale_distilled_page(master):
    _distilled(master, "Clients/acme/Ferries.md", "Ferry Times 2025",
               "Ferries run hourly. [s](https://example.com/f), captured 2025-06\n")
    findings = _check_citations(master, today=TODAY)
    assert len(findings) == 1
    assert "stale" in findings[0].message and "2025-06" in findings[0].message


def test_citations_empty_marker_is_not_a_distilled_page(master):
    # A key with no value marks nothing; treating it as distilled would turn a
    # typo into a permanent warning nobody can satisfy.
    f = master / "People/bob/Notes/Ferries.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("---\ndistilled:\n---\n\nFerries run hourly.\n")
    assert _check_citations(master, today=TODAY) == []


def test_citations_leaves_intel_to_the_intel_check(master):
    # An Intel page marked distilled: must produce exactly one finding, not two.
    _intel(master, "Destinations/Lisbon.md",
           "---\ndistilled: https://example.com/lisbon\n---\n\nLisbon is nice.\n")
    assert _check_citations(master, today=TODAY) == []
    assert len(_check_intel(master, today=TODAY)) == 1


def test_citation_urls_extracts_markdown_link_targets():
    text = "A [source](https://a.example/x), as of 2026-01 and [b](http://b.example)."
    assert _citation_urls(text) == ["https://a.example/x", "http://b.example"]


def test_citation_urls_ignores_non_http_and_bare_urls():
    # A bare URL in prose is not a citation under the convention, and a
    # relative/file target has nothing to probe.
    text = "See https://bare.example and [pdf](report.pdf) and [m](mailto:a@b.c)."
    assert _citation_urls(text) == []


def test_citation_urls_dedupes_preserving_order():
    text = "[a](https://x.example) and [b](https://y.example) and [c](https://x.example)"
    assert _citation_urls(text) == ["https://x.example", "https://y.example"]


def test_run_doctor_includes_citations_check(master):
    seed_meta(master)
    _distilled(master, "Clients/acme/Ferries.md", "https://example.com/f",
               "Ferries run hourly.\n")
    assert any(f.check == "citations" for f in run_doctor(master))


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


def test_doctor_surfaces_created_clients(tmp_path):
    from brain.doctor import _check_created_clients

    master = tmp_path / "master"
    log = master / "_meta/clients/created.log"
    log.parent.mkdir(parents=True)
    log.write_text("2026-07-22\tjoe\tDanziger Family\t2026-07-22-danziger-family\n")

    findings = _check_created_clients(master)
    assert len(findings) == 1
    f = findings[0]
    assert f.severity == "info" and f.check == "clients"
    assert "Danziger Family" in f.message and "joe" in f.message


def test_doctor_no_findings_without_log(tmp_path):
    from brain.doctor import _check_created_clients
    assert _check_created_clients(tmp_path / "master") == []


def test_doctor_surfaces_pending_shares(tmp_path):
    from brain.doctor import _check_pending_shares

    master = tmp_path / "master"
    d = master / "_meta/shares/pending"
    d.mkdir(parents=True)
    (d / "joe-x.md").write_text(
        "---\nshare-id: joe-x\nfrom: joe\nspace: Clients/Danziger Family\n"
        "share-with: person:mary\naccess: write\ncreated: 2026-07-22\n---\nnote\n")
    findings = _check_pending_shares(master)
    assert len(findings) == 1
    f = findings[0]
    assert f.severity == "info" and f.check == "shares"
    assert "Danziger Family" in f.message and "person:mary" in f.message


def test_doctor_no_share_findings_without_queue(tmp_path):
    from brain.doctor import _check_pending_shares
    assert _check_pending_shares(tmp_path / "master") == []


def test_created_log_message_uses_configured_noun(tmp_path):
    master = tmp_path / "master"
    master.mkdir()
    seed_meta(master)
    (master / "_meta/config.yaml").write_text("entities: Families\nentity: family\n")
    log = master / "_meta/clients/created.log"
    log.parent.mkdir(parents=True)
    log.write_text("2026-07-23\tjoe\tDanziger\tslug\n")
    findings = run_doctor(master)
    assert any("Families/Danziger" in f.message for f in findings)


def test_malformed_config_is_an_error_finding(tmp_path):
    master = tmp_path / "master"
    master.mkdir()
    seed_meta(master)
    (master / "_meta/config.yaml").write_text("entities: [broken\n")
    findings = run_doctor(master)
    assert any(f.severity == "error" and "config.yaml" in f.message
               for f in findings)


def test_delegated_decisions_surface_as_info(tmp_path):
    m = tmp_path / "master"
    m.mkdir()
    seed_meta(m)
    d = m / "_meta/shares/approved"
    d.mkdir(parents=True)
    today = _date.today()
    (d / "joe-x.md").write_text(
        f"---\nshare-id: joe-x\nfrom: joe\nspace: Clients/Acme\n"
        f"share-with: person:mary\naccess: read\ncreated: 2026-07-20\n"
        f"approved-on: {today.isoformat()}\napproved-by: mary\nvia: delegated\n---\n")
    old = m / "_meta/shares/rejected"
    old.mkdir(parents=True)
    (old / "joe-y.md").write_text(   # stale: outside the 30-day window
        "---\nspace: Clients/Old\nshare-with: person:bob\n"
        "rejected-on: 2020-01-01\nrejected-by: bob\nvia: delegated\n---\n")
    admin_side = d / "joe-z.md"      # not delegated: no finding
    admin_side.write_text(
        "---\nspace: Clients/B\nshare-with: person:bob\n"
        "approved-on: 2026-07-23\napproved-by: admin\n---\n")
    findings = run_doctor(m)
    msgs = [f.message for f in findings if f.check == "shares"]
    assert any("approved by mary" in x and "delegated" in x for x in msgs)
    assert not any("Clients/Old" in x for x in msgs)
    assert not any("Clients/B" in x and "delegated" in x for x in msgs)


BODY_A = (
    "# Field Notes\n\n"
    "alpha beta gamma delta epsilon zeta eta theta iota kappa "
    "lamda mu nu xi omicron pi rho sigma tau upsilon phi chi psi omega\n"
)


def test_exact_duplicate_visible_to_common_reader_warns(master):
    seed_meta(master)
    (master / "Company/Kickoff Notes.md").write_text(BODY_A)
    (master / "Company/Kickoff Recap.md").write_text(BODY_A)
    findings = run_doctor(master)
    assert "warn" in _severities(findings, "dup-exact")


def test_exact_duplicate_across_private_spaces_is_info(master):
    seed_meta(master)
    (master / "People/alice/Notes").mkdir(parents=True, exist_ok=True)
    (master / "People/bob/Notes").mkdir(parents=True, exist_ok=True)
    (master / "People/alice/Notes/Article.md").write_text(BODY_A)
    (master / "People/bob/Notes/Saved.md").write_text(BODY_A)
    findings = run_doctor(master)
    assert set(_severities(findings, "dup-exact")) == {"info"}
    hit = next(
        f for f in findings
        if f.check == "dup-exact" and "Article" in f.message)
    assert "promotion candidate" in hit.message


def test_personal_skeleton_files_never_flagged(master):
    # The fixture scaffolds every person with the same skeleton
    # (People/<id>/Memory.md etc.) — identical templates must not flag.
    seed_meta(master)
    findings = run_doctor(master)
    assert not [
        f for f in findings
        if f.check.startswith("dup") and "Memory.md" in f.message]


def test_stub_files_below_min_words_not_flagged(master):
    seed_meta(master)
    (master / "Company/Stub One.md").write_text("# Stub\n\nshort note\n")
    (master / "Company/Stub Two.md").write_text("# Stub\n\nshort note\n")
    findings = run_doctor(master)
    assert not _severities(findings, "dup-exact")


def test_stem_collision_with_common_reader_warns(master):
    seed_meta(master)
    (master / "Clients/acme").mkdir(parents=True, exist_ok=True)
    (master / "Company/Acme.md").write_text("# Acme\n\ncompany-side view\n")
    (master / "Clients/acme/Acme.md").write_text("# Acme\n\nclient-side view\n")
    findings = run_doctor(master)
    assert "warn" in _severities(findings, "stem-collision")
    hit = next(
        f for f in findings
        if f.check == "stem-collision" and "[[Acme]]" in f.message)
    assert hit.severity == "warn"


def test_stem_collision_disjoint_readers_is_silent(master):
    seed_meta(master)
    (master / "People/alice/Notes").mkdir(parents=True, exist_ok=True)
    (master / "People/bob/Notes").mkdir(parents=True, exist_ok=True)
    (master / "People/alice/Notes/Acme.md").write_text("# Acme\n\nalice take\n")
    (master / "People/bob/Notes/Acme.md").write_text("# Acme\n\nbob take\n")
    findings = run_doctor(master)
    assert not _severities(findings, "stem-collision")


def test_inbox_and_sessions_exempt_from_dup_checks(master):
    seed_meta(master)
    (master / "People/alice/Inbox").mkdir(parents=True, exist_ok=True)
    (master / "People/alice/Sessions").mkdir(parents=True, exist_ok=True)
    (master / "People/alice/Inbox/Capture.md").write_text(BODY_A)
    (master / "People/alice/Sessions/Old.md").write_text(BODY_A)
    (master / "Company/Kickoff Notes.md").write_text(BODY_A)
    findings = run_doctor(master)
    assert not [
        f for f in findings
        if f.check.startswith("dup") and (
            "Inbox" in f.message or "Sessions" in f.message)]


def test_skeleton_pair_suppresses_identical_personal_scaffolds(master):
    # Byte-identical substantive files at the SAME subpath inside two
    # personal spaces are scaffold structure — suppressed entirely, not
    # even info. The same content at a DIFFERENT subpath is a real
    # cross-private duplicate and keeps its info promotion hint.
    seed_meta(master)
    (master / "People/alice/Notes").mkdir(parents=True, exist_ok=True)
    (master / "People/bob/Notes").mkdir(parents=True, exist_ok=True)
    (master / "People/alice/Notes/Reading List.md").write_text(BODY_A)
    (master / "People/bob/Notes/Reading List.md").write_text(BODY_A)
    findings = run_doctor(master)
    assert not [
        f for f in findings
        if f.check.startswith("dup") and "Reading List" in f.message]
    (master / "People/bob/Notes/Other Name.md").write_text(BODY_A)
    findings = run_doctor(master)
    assert any(
        f.check == "dup-exact" and f.severity == "info"
        and "Other Name" in f.message for f in findings)


def _shuffled_pair(master):
    """Two Company notes with the same word bag in different order: shingle
    overlap ~0 (MinHash misses) but bag-of-words embeddings match."""
    ws = [f"word{i}" for i in range(40)]
    (master / "Company/Shuffle A.md").write_text(
        "# Shuffle A\n\n" + " ".join(ws) + "\n")
    (master / "Company/Shuffle B.md").write_text(
        "# Shuffle B\n\n" + " ".join(reversed(ws)) + "\n")
    return ["Company/Shuffle A.md", "Company/Shuffle B.md"]


def test_minhash_near_duplicate_warns(master):
    seed_meta(master)
    ws = [f"tok{i}" for i in range(60)]
    (master / "Company/Draft.md").write_text("# Draft\n\n" + " ".join(ws) + "\n")
    ws[30] = "changed"
    (master / "Company/Final.md").write_text("# Final\n\n" + " ".join(ws) + "\n")
    findings = run_doctor(master)
    assert "warn" in _severities(findings, "dup-near")


def test_no_provider_means_no_embedding_signal(master):
    # conftest's _no_ambient_provider guarantees no provider here: the
    # shuffled pair is invisible to MinHash and must NOT be flagged.
    seed_meta(master)
    _shuffled_pair(master)
    findings = run_doctor(master)
    assert not _severities(findings, "dup-near")


def test_embedding_near_duplicate_via_warmed_cache(master, tmp_path, monkeypatch):
    import hashlib as _hashlib

    from brain.chunker import chunk_markdown, embedding_input
    from brain.embeddings import EmbeddingCache, FakeEmbeddingProvider, pack_vector

    seed_meta(master)
    rels = _shuffled_pair(master)

    cache_path = tmp_path / "emb-cache.db"
    monkeypatch.setenv("BRAIN_EMBED_CACHE", str(cache_path))
    monkeypatch.setenv("BRAIN_EMBED_BASE_URL", "http://unused.invalid")
    monkeypatch.setenv("BRAIN_EMBED_MODEL", "fake-32")

    provider = FakeEmbeddingProvider()  # model == "fake-32", never networked
    cache = EmbeddingCache(cache_path)
    for rel in rels:
        text = (master / rel).read_text()
        inputs = [embedding_input(c) for c in chunk_markdown(rel, text)]
        shas = [_hashlib.sha256(i.encode("utf-8")).hexdigest() for i in inputs]
        vecs = [pack_vector(v) for v in provider.embed(inputs)]
        cache.put_many(list(zip(shas, vecs)), "fake-32")
    cache.close()

    findings = run_doctor(master)
    assert "warn" in _severities(findings, "dup-near")
    hit = [f for f in findings if f.check == "dup-near" and f.severity == "warn"][0]
    assert "Shuffle A" in hit.message and "Shuffle B" in hit.message


def test_warn_dup_findings_never_pair_disjoint_readers(master):
    """The spec invariant, in the spirit of test_leak_property: content
    duplicated across spaces with no common reader must never produce a
    warn — only info (promotion hint) or silence."""
    seed_meta(master)
    (master / "People/alice/Notes").mkdir(parents=True, exist_ok=True)
    (master / "People/bob/Notes").mkdir(parents=True, exist_ok=True)
    private_a = "People/alice/Notes/Research.md"
    private_b = "People/bob/Notes/Research Copy.md"
    (master / private_a).write_text(BODY_A)
    (master / private_b).write_text(BODY_A)
    (master / "Company/Shared One.md").write_text(BODY_A)
    (master / "Company/Shared Two.md").write_text(BODY_A)
    findings = run_doctor(master)
    dup_checks = {"dup-exact", "dup-near", "stem-collision"}
    for f in findings:
        if f.check in dup_checks and f.severity == "warn":
            assert not (private_a in f.message and private_b in f.message), (
                f"warn finding pairs two disjoint-reader files: {f.message}")
    # The layout still produces both classes:
    assert "warn" in _severities(findings, "dup-exact")   # the Company pair
    assert "info" in _severities(findings, "dup-exact")   # a cross-boundary pair
    assert not _severities(findings, "dup-near")


def test_identical_group_of_three_emits_no_dup_near(master):
    # Tier 1 chains adjacent pairs of an identical group; every other
    # intra-group pair must be suppressed, not resurface as dup-near.
    seed_meta(master)
    for name in ("Copy One", "Copy Two", "Copy Three"):
        (master / f"Company/{name}.md").write_text(BODY_A)
    findings = run_doctor(master)
    assert len(_severities(findings, "dup-exact")) == 2
    assert not _severities(findings, "dup-near")


def test_home_landing_pages_exempt_from_stem_collision(master):
    # Home.md is the per-space landing-page convention (the link map;
    # _check_intel already exempts it from the citation rule) — two spaces
    # each owning a Home.md is structure, not ambiguity worth warning about.
    seed_meta(master)
    (master / "Company/Intel").mkdir(parents=True, exist_ok=True)
    (master / "Company/Home.md").write_text("# Home\n\npriority dashboard\n")
    (master / "Company/Intel/Home.md").write_text("# Intel\n\nintel link map\n")
    findings = run_doctor(master)
    assert not [
        f for f in findings
        if f.check == "stem-collision" and "Home" in f.message]


def test_fresh_scaffold_has_no_dup_findings(tmp_path):
    # A brand-new brain must not start life with doctor warnings from its
    # own scaffold (the Company/Home.md vs Company/Intel/Home.md pair).
    from brain.cli import main

    root = tmp_path / "fresh"
    assert main(["init", str(root), "--company", "TestCo"]) == 0
    findings = run_doctor(root)
    dup = [f for f in findings
           if f.check in ("dup-exact", "dup-near", "stem-collision")]
    assert dup == []


def test_findings_carry_structured_paths(master):
    seed_meta(master)
    (master / "People/stray.md").write_text("orphan\n")
    intel = master / "Company/Intel/Destinations/Lisbon.md"
    intel.parent.mkdir(parents=True)
    intel.write_text("Lisbon is nice.\n")  # no dated citation -> intel warn
    body = ("word " * 25).strip() + "\n"  # >= DUP_MIN_WORDS (20)
    (master / "Company/CopyA.md").write_text(body)
    (master / "Company/CopyB.md").write_text(body)

    findings = run_doctor(master)

    orphan = next(f for f in findings if f.check == "orphan-files")
    assert orphan.paths == ("People/stray.md",)
    it = next(f for f in findings if f.check == "intel" and "Lisbon" in f.message)
    assert it.paths == ("Company/Intel/Destinations/Lisbon.md",)
    dup = next(f for f in findings if f.check == "dup-exact")
    assert sorted(dup.paths) == ["Company/CopyA.md", "Company/CopyB.md"]
    # non-routed checks keep the default
    assert all(f.paths == () for f in findings if f.check == "meta")


requires_nonroot = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root bypasses file permissions, so an unreadable file can't be staged")


@requires_nonroot
def test_unreadable_file_is_reported_and_never_crashes(master):
    """Doctor's whole job is to surface what fails silently — a file it cannot
    read must become a finding, not a traceback. The compiler dies on the same
    file (shutil.copy2), so this is an error: fix it before anyone syncs."""
    seed_meta(master)
    (master / "People/stray.md").write_text("orphan\n")  # an unrelated finding
    locked = master / "People/bob/Notes/Locked.md"
    locked.parent.mkdir(parents=True, exist_ok=True)
    locked.write_text("secret\n")
    locked.chmod(0o000)
    try:
        findings = run_doctor(master)
    finally:
        locked.chmod(0o644)

    unreadable = [f for f in findings if f.check == "unreadable-files"]
    assert len(unreadable) == 1
    assert unreadable[0].severity == "error"
    assert unreadable[0].paths == ("People/bob/Notes/Locked.md",)
    assert "compile" in unreadable[0].message
    # the run completed: checks after the unreadable file still reported
    assert any(f.check == "orphan-files" for f in findings)


@requires_nonroot
def test_unreadable_file_is_excluded_from_content_checks(master):
    """One file, one finding: the content scans skip what they cannot read
    instead of each reporting it (or guessing at empty content)."""
    seed_meta(master)
    locked = master / "People/bob/Notes/Locked.md"
    locked.parent.mkdir(parents=True, exist_ok=True)
    locked.write_text("secret\n")
    locked.chmod(0o000)
    try:
        findings = run_doctor(master)
    finally:
        locked.chmod(0o644)

    other = [f for f in findings
             if f.check != "unreadable-files" and "Locked.md" in f.message]
    assert other == []


@requires_nonroot
def test_unreadable_intel_page_and_promotion_draft_do_not_crash(master):
    """The Intel and promotion scans walk their own file sets rather than
    _content_files, so they need the same posture."""
    seed_meta(master)
    intel = master / "Company/Intel/Destinations/Lisbon.md"
    intel.parent.mkdir(parents=True, exist_ok=True)
    intel.write_text("Lisbon, as of 2026-07.\n")
    draft = master / "People/bob/Promotions/share.md"
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text("---\ntarget-path: Company/Playbook/SOP.md\n---\nbody\n")
    intel.chmod(0o000)
    draft.chmod(0o000)
    try:
        findings = run_doctor(master)
    finally:
        intel.chmod(0o644)
        draft.chmod(0o644)

    unreadable = {f.paths[0] for f in findings if f.check == "unreadable-files"}
    assert unreadable == {"Company/Intel/Destinations/Lisbon.md",
                          "People/bob/Promotions/share.md"}
    assert not [f for f in findings
                if f.check in ("intel", "promotions") and "Lisbon" in f.message]


def test_dangling_symlink_is_reported_only_as_a_symlink(master):
    """A broken link is unreadable too, but it already has its own check —
    findings stay one-per-problem."""
    seed_meta(master)
    (master / "People/bob/Notes").mkdir(parents=True, exist_ok=True)
    (master / "People/bob/Notes/Ghost.md").symlink_to(master / "nowhere.md")

    findings = run_doctor(master)

    assert [f.check for f in findings if "Ghost.md" in f.message] == ["symlinks"]


@requires_nonroot
def test_unreadable_compiled_file_counts_as_drift(master, tmp_path):
    """_check_compiled hashes every compiled file against the manifest; one it
    cannot read is one it cannot vouch for."""
    seed_meta(master)
    out = _compile(master, tmp_path)
    compiled = out / "bob/People/bob/Memory.md"
    compiled.chmod(0o000)
    try:
        findings = run_doctor(master, out)
    finally:
        compiled.chmod(0o644)

    drift = [f for f in findings if f.check == "compiled" and "bob" in f.message
             and "awaiting writeback" in f.message]
    assert len(drift) == 1


@requires_nonroot
def test_unreadable_clients_log_is_reported(tmp_path):
    """The self-service client log lives under _meta/, so the content-file
    check never sees it — it reports its own read failure."""
    from brain.doctor import _check_created_clients

    master = tmp_path / "master"
    log = master / "_meta/clients/created.log"
    log.parent.mkdir(parents=True)
    log.write_text("2026-07-22\tjoe\tDanziger Family\t2026-07-22-danziger-family\n")
    log.chmod(0o000)
    try:
        findings = _check_created_clients(master)
    finally:
        log.chmod(0o644)

    assert len(findings) == 1
    assert findings[0].severity == "error" and findings[0].check == "clients"
    assert "Danziger Family" not in findings[0].message
