import os
import time
from datetime import date as _date

import pytest

from brain.cli import main
from brain.doctor import _check_citations, _check_intel, _citation_urls, run_doctor

from .test_cli import SPACES_YAML, seed_meta


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
    # Aged past the grace window: a FRESH tomb is now indistinguishable from a
    # compile in flight, and reporting those turned a healthy brain red for the
    # length of its own cycle. What still earns an error is one that survived.
    from brain.doctor import TOMB_GRACE_SEC

    seed_meta(master)
    out = _compile(master, tmp_path)
    tomb = out / ".bob.old"
    tomb.mkdir()
    stale = time.time() - (TOMB_GRACE_SEC + 600)
    os.utime(tomb, (stale, stale))
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


def test_doctor_does_not_flag_predication_about_a_named_page(master):
    # Helm had 193 of these and zero real contradictions.
    seed_meta(master)
    (master / "Clients/acme/Soho Grant of Delaware LLC.md").write_text(
        "---\nentity: client\naliases: [Soho Grant]\n---\n# Soho Grant of Delaware LLC\n\n"
        "- Soho Grant of Delaware LLC is a Delaware LLC [from:: 2013-03]\n"
        "- Soho Grant of Delaware LLC is required to keep separate books [from:: 2013-03]\n"
        "- Rob Arifur is the project architect [from:: 2019-05]\n"
        "- Rob Arifur is reachable at r@x.com [from:: 2020-02]\n")
    from brain.doctor import run_doctor
    assert [f for f in run_doctor(master) if f.check == "fact-conflict"] == []


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
    # A possessive marks the attribute slot; a bare "[[Ghost]] status is …"
    # is a predication and is deliberately silent (see the predication guard
    # in _diverges).
    seed_meta(master)
    (master / "Company/A.md").write_text(
        "# A\n\n- [[Ghost]]'s status is active [from:: 2025-06]\n")
    (master / "Company/B.md").write_text(
        "# B\n\n- [[Ghost]]'s status is churned [from:: 2026-02]\n")
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


def test_delegated_promotion_decisions_surface_as_info(tmp_path):
    m = tmp_path / "master"
    m.mkdir()
    seed_meta(m)
    today = _date.today()
    d = m / "_meta/promotions/approved"
    d.mkdir(parents=True)
    (d / "p-ops.md").write_text(
        f"---\npromotion-id: p-ops\nfrom: bob\ntarget-path: Teams/ops/Escalation.md\n"
        f"source: s\ncreated: 2026-08-10\napproved-on: {today.isoformat()}\n"
        f"approved-by: mary\nvia: delegated\n---\nbody\n")
    r = m / "_meta/promotions/rejected"
    r.mkdir(parents=True)
    (r / "p-old.md").write_text(   # stale: outside the window
        "---\npromotion-id: p-old\ntarget-path: Teams/ops/Old.md\n"
        "rejected-on: 2020-01-01\nrejected-by: mary\nvia: delegated\n---\n")
    (d / "p-admin.md").write_text(  # admin-side: no via, no finding
        "---\npromotion-id: p-admin\ntarget-path: Company/X.md\n"
        f"approved-on: {today.isoformat()}\napproved-by: admin\n---\n")
    findings = run_doctor(m)
    msgs = [f.message for f in findings if f.check == "promotions"]
    assert any("Teams/ops/Escalation.md" in x and "approved by mary" in x
               and "delegated" in x for x in msgs)
    assert not any("Old.md" in x for x in msgs)
    assert not any("Company/X.md" in x and "delegated" in x for x in msgs)


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


def _warm_embeddings(master, tmp_path, monkeypatch, rels):
    """Point doctor at a fake-32 embedding cache holding every chunk of `rels`
    — the provider is configured but never called."""
    import hashlib as _hashlib

    from brain.chunker import chunk_markdown, embedding_input
    from brain.embeddings import EmbeddingCache, FakeEmbeddingProvider, pack_vector

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


def test_embedding_near_duplicate_via_warmed_cache(master, tmp_path, monkeypatch):
    seed_meta(master)
    rels = _shuffled_pair(master)
    _warm_embeddings(master, tmp_path, monkeypatch, rels)

    findings = run_doctor(master)
    assert "warn" in _severities(findings, "dup-near")
    hit = next(f for f in findings if f.check == "dup-near" and f.severity == "warn")
    assert "Shuffle A" in hit.message and "Shuffle B" in hit.message


def test_semantic_findings_match_the_per_pair_cosine(master, tmp_path, monkeypatch):
    """Norms computed once per vector must decide exactly what the old
    per-pair cosine decided, on the semantic fixtures: a pair above the
    threshold, and notes that share words but sit below it."""
    import brain.dedup

    from .test_dedup import _generator_cosine

    seed_meta(master)
    rels = _shuffled_pair(master)
    ws = [f"word{i}" for i in range(40)]
    for name, words in (("Half", ws[:20] + [f"other{i}" for i in range(20)]),
                        ("Apart", [f"far{i}" for i in range(40)])):
        (master / f"Company/{name}.md").write_text(f"# {name}\n\n" + " ".join(words) + "\n")
        rels.append(f"Company/{name}.md")
    _warm_embeddings(master, tmp_path, monkeypatch, rels)

    fast = run_doctor(master)
    monkeypatch.setattr(brain.dedup, "cosine_with_norms",
                        lambda a, b, _na, _nb: _generator_cosine(a, b))
    per_pair = run_doctor(master)
    assert fast == per_pair
    assert [f.paths for f in fast if f.check == "dup-near"] == [
        ("Company/Shuffle A.md", "Company/Shuffle B.md")]


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


# ---- the MinHash signature cache (<master>/_meta/cache/dedup.db) ----------- #


def _templated(master, folder, n, prefix="Report", word="tok"):
    """n notes stamped from one template, one word apart: every pair is a
    near-duplicate, none is identical, and each has its own title stem.
    Templates built from different `word`s share nothing."""
    base = [f"{word}{i}" for i in range(60)]
    rels = []
    for i in range(n):
        words = list(base)
        words[30] = f"variant{i}"
        rel = f"{folder}/{prefix} {i:02d}.md"
        (master / rel).parent.mkdir(parents=True, exist_ok=True)
        (master / rel).write_text(f"# {prefix} {i:02d}\n\n" + " ".join(words) + "\n")
        rels.append(rel)
    return rels


def _ignore_cache(master):
    # What `brain init` writes; without it the cache must never be created.
    (master / ".gitignore").write_text("_meta/cache/\n")


def _count_signatures(monkeypatch):
    """Every signature doctor computes (rather than reads from the cache)."""
    import brain.dedup

    calls = []
    real = brain.dedup.minhash_signature

    def spy(shingle_set):
        calls.append(len(shingle_set))
        return real(shingle_set)

    monkeypatch.setattr(brain.dedup, "minhash_signature", spy)
    return calls


def _writable_run(master):
    """One doctor run the way the cycle does it: triage opens the cache
    writable, doctor reads and fills it, triage saves it."""
    from brain.dedup import SignatureCache

    cache = SignatureCache.open_writable(master)
    assert cache is not None
    try:
        findings = run_doctor(master, dedup_cache=cache)
        cache.save()
    finally:
        cache.close()
    return findings


def test_a_warm_cache_computes_no_signature_for_an_unchanged_note(master, monkeypatch):
    seed_meta(master)
    _ignore_cache(master)
    _templated(master, "Company/Reports", 4)
    first = _writable_run(master)
    calls = _count_signatures(monkeypatch)
    second = _writable_run(master)
    assert calls == []
    assert second == first


def test_a_changed_note_is_the_only_signature_recomputed(master, monkeypatch):
    seed_meta(master)
    _ignore_cache(master)
    rels = _templated(master, "Company/Reports", 4)
    _writable_run(master)
    note = master / rels[0]
    note.write_text(note.read_text().replace("tok10", "edited"))
    calls = _count_signatures(monkeypatch)
    _writable_run(master)
    assert len(calls) == 1


def test_a_signature_parameter_change_invalidates_the_cache(master, monkeypatch):
    import brain.dedup

    seed_meta(master)
    _ignore_cache(master)
    _templated(master, "Company/Reports", 4)
    _writable_run(master)
    calls = _count_signatures(monkeypatch)
    scheme = brain.dedup.SIGNATURE_SCHEME
    monkeypatch.setattr(brain.dedup, "SIGNATURE_SCHEME", scheme + 1)
    _writable_run(master)
    assert len(calls) == 4
    # The old version's rows were pruned by that run, not kept alongside:
    # going back recomputes everything again.
    monkeypatch.setattr(brain.dedup, "SIGNATURE_SCHEME", scheme)
    calls.clear()
    _writable_run(master)
    assert len(calls) == 4


def test_the_cache_forgets_notes_that_are_gone(master, monkeypatch):
    seed_meta(master)
    _ignore_cache(master)
    rels = _templated(master, "Company/Reports", 4)
    _writable_run(master)
    gone = master / rels[0]
    text = gone.read_text()
    gone.unlink()
    _writable_run(master)  # prunes the vanished note's row
    gone.write_text(text)
    calls = _count_signatures(monkeypatch)
    _writable_run(master)
    assert len(calls) == 1


def test_standalone_doctor_never_creates_the_cache(master):
    seed_meta(master)
    _ignore_cache(master)
    _templated(master, "Company/Reports", 3)
    run_doctor(master)
    assert not (master / "_meta/cache").exists()


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root reads through any permission")
def test_an_unreadable_cache_folder_falls_back_to_computing(master):
    seed_meta(master)
    _ignore_cache(master)
    _templated(master, "Company/Reports", 3)
    expected = run_doctor(master)
    _writable_run(master)
    folder = master / "_meta/cache"
    folder.chmod(0)
    try:
        assert run_doctor(master) == expected
    finally:
        folder.chmod(0o755)


def test_standalone_doctor_reads_the_cache_but_never_writes_it(master, monkeypatch):
    seed_meta(master)
    _ignore_cache(master)
    rels = _templated(master, "Company/Reports", 4)
    _writable_run(master)
    db = master / "_meta/cache/dedup.db"
    before = (db.read_bytes(), db.stat().st_mtime_ns)
    note = master / rels[0]
    note.write_text(note.read_text().replace("tok10", "edited"))
    calls = _count_signatures(monkeypatch)
    run_doctor(master)
    assert len(calls) == 1  # the three unchanged notes came from the cache
    assert (db.read_bytes(), db.stat().st_mtime_ns) == before
    assert sorted(p.name for p in db.parent.iterdir()) == ["dedup.db"]


def test_findings_are_identical_with_and_without_the_cache(master):
    seed_meta(master)
    _ignore_cache(master)
    _templated(master, "Company/Reports", 5)
    _templated(master, "People/bob/Notes", 3, prefix="Log")
    for name in ("Copy One", "Copy Two"):
        (master / f"Company/{name}.md").write_text(BODY_A)
    cold = run_doctor(master)  # no cache file yet
    assert _writable_run(master) == cold
    assert run_doctor(master) == cold  # read-only, every signature a hit


def test_an_unusable_cache_file_falls_back_to_computing(master):
    seed_meta(master)
    _ignore_cache(master)
    _templated(master, "Company/Reports", 3)
    expected = run_doctor(master)
    db = master / "_meta/cache/dedup.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"this is not a database" * 64)
    assert run_doctor(master) == expected


# ---- near-duplicates are reported as groups, not pairs -------------------- #


def _near(findings):
    return [f for f in findings if f.check == "dup-near"]


def test_templated_notes_are_one_near_duplicate_group(master):
    seed_meta(master)
    rels = _templated(master, "Company/Reports", 6)
    near = _near(run_doctor(master))
    assert len(near) == 1
    f = near[0]
    assert f.severity == "warn"
    assert f.paths == tuple(sorted(rels))
    assert f.message == (
        "6 notes are near-duplicates of each other in Company/Reports: "
        "Report 00.md, Report 01.md, Report 02.md, and 3 more — merge them, "
        "or if they share a template on purpose, make them distinct")


def test_a_group_of_three_names_all_three(master):
    seed_meta(master)
    _templated(master, "Company/Reports", 3)
    (f,) = _near(run_doctor(master))
    assert f.message.startswith(
        "3 notes are near-duplicates of each other in Company/Reports: "
        "Report 00.md, Report 01.md, and Report 02.md — ")


def test_a_pair_still_reads_like_a_pair(master):
    # Two notes are a group of two: the message is the one pairs always had.
    seed_meta(master)
    a, b = _templated(master, "Company/Reports", 2)
    (f,) = _near(run_doctor(master))
    assert f.paths == (a, b)
    assert f.message == (
        f"{a} and {b} are near-duplicates (text overlap) — fold one into the "
        "other via a mode: patch promotion")


def test_two_unrelated_templates_are_two_groups(master):
    seed_meta(master)
    reports = _templated(master, "Company/Reports", 4)
    calls = _templated(master, "Teams/sales/Calls", 3, prefix="Call", word="call")
    near = _near(run_doctor(master))
    assert sorted(f.paths for f in near) == [tuple(sorted(reports)), tuple(sorted(calls))]


def test_near_duplicates_across_spaces_stay_pairs(master):
    """Groups never cross a space: matches between two spaces are reported
    pair by pair, exactly as before groups existed."""
    seed_meta(master)
    r0, r1 = _templated(master, "Company/Reports", 2)
    m0, m1 = _templated(master, "Clients/acme", 2, prefix="Memo")
    near = _near(run_doctor(master))
    assert all(f.severity == "warn" for f in near)  # everyone reads both
    assert sorted(f.paths for f in near) == sorted([
        (m0, m1), (r0, r1),                      # a group of two per space
        (m0, r0), (m0, r1), (m1, r0), (m1, r1),  # and the cross-space pairs
    ])
    cross = next(f for f in near if f.paths == (m0, r0))
    assert cross.message == (
        f"{m0} and {r0} are near-duplicates (text overlap) — fold one into the "
        "other via a mode: patch promotion")


def test_a_shared_template_does_not_merge_everyones_notes(master):
    """One template in Company/ near-duplicates notes in several people's
    spaces. Each person's own notes are their group; matches with the shared
    note, or across people, stay pairs. (dup-near findings now route to the
    admins only, not to the people whose notes they name.)"""
    from brain.schemas import load_org, load_spaces
    from brain.triage import route_findings

    seed_meta(master)
    (tpl,) = _templated(master, "Company", 1, prefix="Template")
    alices = _templated(master, "People/alice/Notes", 3, prefix="Mine")
    bobs = _templated(master, "People/bob/Notes", 3, prefix="Log")
    near = _near(run_doctor(master))
    groups = [f for f in near if len(f.paths) > 2]
    assert sorted(f.paths for f in groups) == [tuple(alices), tuple(bobs)]
    assert all(f.severity == "warn" for f in groups)
    pairs = [f for f in near if len(f.paths) == 2]
    assert {f.paths for f in pairs if tpl in f.paths} == {
        tuple(sorted((tpl, p))) for p in alices + bobs}
    assert all(f.severity == "warn" for f in pairs if tpl in f.paths)
    assert {f.paths for f in pairs if tpl not in f.paths} == {
        (a, b) for a in alices for b in bobs}
    assert all(f.severity == "info" for f in pairs if tpl not in f.paths)

    org = load_org(master / "_meta/org.yaml")
    rules = load_spaces(master / "_meta/spaces.yaml")
    routed, _ = route_findings(groups, org, rules)
    bob_group = next(f for f in groups if f.paths == tuple(bobs))
    alice_group = next(f for f in groups if f.paths == tuple(alices))
    # dup-near is admin-only: both groups reach alice (the admin), never
    # bob's own digest, even though the group is entirely his own notes.
    assert "bob" not in routed
    assert set(routed["alice"]) == {bob_group, alice_group}


def test_a_group_splits_by_readership(master):
    """Severity is still per pair: bob's two notes (a pair bob reads both
    sides of) are warn; alice's copy, in another space with no common
    reader, is an info-level promotion hint against each, as pairs."""
    seed_meta(master)
    (bob_a, bob_b) = _templated(master, "People/bob/Notes", 2)
    (alice,) = _templated(master, "People/alice/Notes", 1, prefix="Copy")
    near = _near(run_doctor(master))
    warn = [f for f in near if f.severity == "warn"]
    info = [f for f in near if f.severity == "info"]
    assert [f.paths for f in warn] == [(bob_a, bob_b)]
    assert sorted(f.paths for f in info) == [(alice, bob_a), (alice, bob_b)]
    assert info[0].message == (
        f"{info[0].paths[0]} and {info[0].paths[1]} cover similar content in "
        "unshared spaces — promotion candidate")


def test_an_info_group_within_one_space(master):
    """Pairs in a space nobody reads have no common reader: an info group."""
    seed_meta(master)
    rels = _templated(master, "Teams/ghosts", 3)  # no one is on team ghosts
    (f,) = _near(run_doctor(master))
    assert (f.severity, f.paths) == ("info", tuple(rels))
    assert f.message == (
        "3 notes in unshared spaces cover similar content in Teams/ghosts: "
        "Report 00.md, Report 01.md, and Report 02.md — promotion candidate")


def test_a_near_duplicate_group_message_stays_short(master):
    """Three names however big the group: the line does not grow with it."""
    seed_meta(master)
    rels = _templated(master, "Company/Reports/Weekly", 40,
                      prefix="Weekly status report for the operations team")
    (f,) = _near(run_doctor(master))
    assert len(f.paths) == 40
    assert f.message.startswith("40 notes are near-duplicates of each other in "
                                "Company/Reports/Weekly: ")
    assert ", and 37 more — " in f.message
    assert len([r for r in rels if r.rsplit("/", 1)[1] in f.message]) == 3
    for rel in rels[4:]:
        (master / rel).unlink()
    (four,) = _near(run_doctor(master))
    assert len(four.paths) == 4
    assert len(f.message) - len(four.message) == 2  # "40" vs "4", "37" vs "1"


def _with_up(master, rel, parent_stem):
    """Give an existing note an `up:` to parent_stem, keeping its body."""
    p = master / rel
    p.write_text(f"---\nup: [[{parent_stem}]]\n---\n" + p.read_text())


def test_up_child_and_parent_are_not_near_duplicates(master):
    seed_meta(master)
    rels = _templated(master, "Company/Reports", 2)
    _with_up(master, rels[1], "Report 00")
    assert _near(run_doctor(master)) == []


def test_up_exemption_keeps_sibling_pairs(master):
    seed_meta(master)
    rels = _templated(master, "Company/Reports", 3)
    _with_up(master, rels[1], "Report 00")
    _with_up(master, rels[2], "Report 00")
    (f,) = _near(run_doctor(master))
    assert f.paths == (rels[1], rels[2])


def test_up_exemption_applies_across_spaces(master):
    seed_meta(master)
    (_parent,) = _templated(master, "People/bob/Notes", 1, prefix="Aventura")
    (child,) = _templated(master, "Company/Neighborhoods", 1, prefix="Aventura History")
    _with_up(master, child, "Aventura 00")
    assert _near(run_doctor(master)) == []


def test_without_up_the_pair_is_still_reported(master):
    seed_meta(master)
    _templated(master, "Company/Reports", 2)
    assert len(_near(run_doctor(master))) == 1


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


def test_family_master_has_no_false_structural_findings(tmp_path):
    from brain.doctor import run_doctor
    from brain.schemas import make_config
    from brain.templates import scaffold_master
    scaffold_master(tmp_path, "Fam", make_config("Clients", "client", "Family"))
    findings = run_doctor(tmp_path)
    bad = [f for f in findings
           if f.check in ("space-coverage", "unreadable-spaces", "orphan-files")
           and "Family" in f.message]
    assert bad == [], [f.message for f in bad]


def test_shared_agreement_mismatch_warns(tmp_path):
    from brain.doctor import run_doctor
    from brain.templates import scaffold_master
    scaffold_master(tmp_path, "Acme")            # default: Company/ on disk
    (tmp_path / "_meta/config.yaml").write_text(
        "entities: Clients\nentity: client\nshared: Family\n")
    msgs = [f.message for f in run_doctor(tmp_path)]
    assert any("spaces.yaml has no exact rule" in m for m in msgs)
    assert any("tree on disk is 'Company'" in m for m in msgs)


def test_shared_agreement_clean_on_default_and_on_real_family(tmp_path):
    from brain.doctor import run_doctor
    from brain.schemas import make_config
    from brain.templates import scaffold_master
    default = tmp_path / "default"
    scaffold_master(default, "Acme")
    family = tmp_path / "family"
    scaffold_master(family, "Fam", make_config("Clients", "client", "Family"))
    for master in (default, family):
        msgs = [f.message for f in run_doctor(master)]
        assert not any("config.yaml names shared" in m for m in msgs), msgs


def test_corrections_over_budget_are_reported_to_their_owner(master):
    seed_meta(master)
    d = master / "People/bob/Corrections"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(80):
        (d / f"r{i:02d}.md").write_text(
            f"---\nrule: Rule {i} " + "x" * 60 + "\nfrom: 2026-08-19\n---\nwhy\n"
        )

    findings = run_doctor(master)
    budget = [f for f in findings if f.check == "corrections-budget"]
    assert budget, "an over-budget correction set must be reported"
    f = budget[0]
    assert f.severity == "warn"
    # Routed by path: this has to reach bob, not the admins.
    assert all(p.startswith("People/bob/Corrections/") for p in f.paths)
    # Counts, not content — the digest should not restate every rule.
    assert "Rule 0 " not in f.message


def test_a_rule_too_long_to_ever_render_is_reported_as_its_own_problem(master):
    """"Remove the ones that no longer apply" is the wrong instruction for a
    single rule that cannot fit an empty budget — pruning around it changes
    nothing. It gets its own message, and it does not evict the rest."""
    seed_meta(master)
    d = master / "People/bob/Corrections"
    d.mkdir(parents=True, exist_ok=True)
    (d / "essay.md").write_text(
        "---\nrule: " + "x" * 4200 + "\nfrom: 2026-08-19\n---\nwhy\n")
    (d / "short.md").write_text(
        "---\nrule: Keep it direct.\nfrom: 2026-01-01\n---\nwhy\n")

    findings = [f for f in run_doctor(master) if f.check == "corrections-budget"]
    assert len(findings) == 1
    f = findings[0]
    assert f.paths == ("People/bob/Corrections/essay.md",)  # short.md still renders
    assert "essay.md" in f.message           # named, so it can be found
    assert "shorten" in f.message
    assert "no longer apply" not in f.message
    assert "x" * 20 not in f.message         # the count, never the rule text


def test_a_misfiled_correction_is_named_rather_than_lost(master):
    """Wrong extension or nested in a subfolder: the loader ignores it, and
    unlinked-notes exempts Corrections/, so without this finding a dropped
    rule reaches nobody — the exact defect this feature exists to prevent."""
    seed_meta(master)
    d = master / "People/bob/Corrections"
    (d / "tone").mkdir(parents=True, exist_ok=True)
    (d / "tone" / "no-filler.md").write_text(
        "---\nrule: Never open with filler.\nfrom: 2026-08-19\n---\nwhy\n")
    (d / "brevity.txt").write_text(
        "---\nrule: Keep it short.\nfrom: 2026-08-19\n---\nwhy\n")

    findings = [f for f in run_doctor(master) if f.check == "corrections-budget"]
    assert len(findings) == 1
    f = findings[0]
    assert sorted(f.paths) == [
        "People/bob/Corrections/brevity.txt",
        "People/bob/Corrections/tone/no-filler.md",
    ]
    assert "brevity.txt" in f.message and "tone/no-filler.md" in f.message
    assert "Never open with filler" not in f.message  # filenames, not rule text


def test_a_correction_without_a_rule_is_reported(master):
    seed_meta(master)
    d = master / "People/bob/Corrections"
    d.mkdir(parents=True, exist_ok=True)
    (d / "broken.md").write_text("---\nfrom: 2026-08-19\n---\nI meant to write a rule.\n")

    findings = run_doctor(master)
    assert [f for f in findings if f.check == "corrections-budget"
            and "broken.md" in "".join(f.paths)]


def test_a_healthy_correction_set_reports_nothing(master):
    seed_meta(master)
    d = master / "People/bob/Corrections"
    d.mkdir(parents=True, exist_ok=True)
    (d / "voice.md").write_text("---\nrule: Keep it direct.\nfrom: 2026-08-19\n---\nwhy\n")

    findings = run_doctor(master)
    assert not [f for f in findings if f.check == "corrections-budget"]


def test_a_correction_with_an_unusable_date_is_reported(master):
    """It renders — a typo must not cost a rule — but it sorts after every
    dated one, and the person is told so the order is theirs to choose."""
    seed_meta(master)
    d = master / "People/bob/Corrections"
    d.mkdir(parents=True, exist_ok=True)
    (d / "vague.md").write_text(
        "---\nrule: Keep it direct.\nfrom: last tuesday\n---\nwhy\n")

    findings = run_doctor(master)
    dated = [f for f in findings if f.check == "corrections-budget"
             and "from:" in f.message]
    assert len(dated) == 1
    assert dated[0].severity == "warn"
    assert dated[0].paths == ("People/bob/Corrections/vague.md",)
    assert "still render" in dated[0].message


def test_a_non_utf8_correction_does_not_abort_the_doctor_run(master):
    """A bare read_text() here raised UnicodeDecodeError out of run_doctor, so
    one pasted smart quote in one person's Corrections/ ended the whole run
    and every other finding with it."""
    seed_meta(master)
    (master / "People/stray.md").write_text("orphan\n")  # an unrelated finding
    d = master / "People/bob/Corrections"
    d.mkdir(parents=True, exist_ok=True)
    (d / "quote.md").write_bytes(
        b"---\nrule: Never say \x93maybe\x94 to a client.\nfrom: 2026-08-19\n---\nwhy\n")

    findings = run_doctor(master)
    assert not [f for f in findings if f.check == "corrections-budget"]
    assert any(f.check == "orphan-files" for f in findings)  # the run completed


@requires_nonroot
def test_an_unreadable_correction_is_reported_to_its_author(master):
    """The infra check reports the same file to the admins. The author is the
    only one who can fix it, and the only one who believes the rule is live."""
    seed_meta(master)
    d = master / "People/bob/Corrections"
    d.mkdir(parents=True, exist_ok=True)
    locked = d / "locked.md"
    locked.write_text("---\nrule: Keep it direct.\nfrom: 2026-08-19\n---\nwhy\n")
    locked.chmod(0o000)
    try:
        findings = run_doctor(master)
    finally:
        locked.chmod(0o644)

    mine = [f for f in findings if f.check == "corrections-budget"]
    assert len(mine) == 1
    assert mine[0].severity == "warn"
    assert mine[0].paths == ("People/bob/Corrections/locked.md",)
    assert "cannot be read" in mine[0].message


def test_corrections_do_not_trip_the_prose_note_checks(master):
    seed_meta(master)
    # A correction has no wikilinks and no facts by construction, and two
    # people may reasonably both write voice.md. Neither is a defect.
    for pid in ("alice", "bob"):
        d = master / f"People/{pid}/Corrections"
        d.mkdir(parents=True, exist_ok=True)
        (d / "voice.md").write_text(
            f"---\nrule: Keep {pid} mail direct.\nfrom: 2026-08-19\n---\nwhy\n")

    findings = run_doctor(master)
    noisy = [f for f in findings
             if f.check in ("unlinked-notes", "stem-collision", "dup-exact")
             and "Corrections/" in "".join(f.paths)]
    assert not noisy, f"corrections should not trip prose-note checks: {noisy}"


def test_charter_unset_is_reported_at_info(tmp_path):
    """Info, never warn: an unset charter is a deliberate default, and a check
    that fires on every default install is how doctor output stops being
    read. It exists to name the lever, once, where an admin is looking."""
    from brain.doctor import run_doctor
    from brain.schemas import VaultConfig
    from brain.templates import scaffold_master

    scaffold_master(tmp_path, "Acme")
    unset = [f for f in run_doctor(tmp_path) if f.check == "charter-unset"]
    assert len(unset) == 1
    assert unset[0].severity == "info"
    assert "brain init --charter" in unset[0].message

    scaffold_master(tmp_path / "acme2", "Acme",
                    VaultConfig(charter="Bespoke luxury travel."))
    assert [f for f in run_doctor(tmp_path / "acme2")
            if f.check == "charter-unset"] == []

def test_a_live_compile_is_not_reported_as_a_crash(master, tmp_path):
    """A compile in flight looks exactly like a crashed one on disk.

    A cycle creates and removes one `.<id>.building` per person as it works —
    each lives 20-60 seconds — so doctor running on a */5 box sees them most of
    the time. Reporting those sets ok:false in the health snapshot, and Fleet
    renders a healthy brain red for the duration of its own compile.
    """
    seed_meta(master)
    out = _compile(master, tmp_path)
    (out / ".bob.building").mkdir(parents=True, exist_ok=True)

    findings = run_doctor(master, out)

    assert not [f for f in findings if "crashed compile" in f.message]


def test_a_tomb_that_survived_compiles_is_still_an_error(master, tmp_path):
    """The error is worth keeping — it just has to mean something.

    A tomb untouched for several cron intervals says the recovery its own
    message promises is not happening, which is exactly when a human should
    look.
    """
    from brain.doctor import TOMB_GRACE_SEC

    seed_meta(master)
    out = _compile(master, tmp_path)
    tomb = out / ".bob.building"
    tomb.mkdir(parents=True, exist_ok=True)
    stale = time.time() - (TOMB_GRACE_SEC + 600)
    os.utime(tomb, (stale, stale))

    findings = run_doctor(master, out)

    crashed = [f for f in findings if "crashed compile" in f.message]
    assert len(crashed) == 1 and crashed[0].severity == "error"


def test_taxonomy_map_names_a_folder_that_does_not_exist(tmp_path):
    """The defect that let two taxonomies coexist for a week: Memory.md's
    'Where knowledge lives' — the one map every agent reads first — named
    folders nothing had created, and doctor reported zero errors. Placeholders
    (`<Name>`, `*`) are skipped; paths resolve at the root or inside the
    shared space; a page path may omit its .md."""
    from brain.doctor import run_doctor
    from brain.templates import scaffold_master

    scaffold_master(tmp_path, "Acme")
    mem = tmp_path / "Company/Memory.md"
    mem.write_text(mem.read_text() + (
        "- `Company/Partners/` — partners and lenders\n"
        "- `Company/Intel/` — outside knowledge, mapped in `Intel/Home.md`\n"
        "- `Company/Tenants` — who occupies what\n"
        "- `Clients/<Name>/` — one space per client\n"
    ))
    hits = [f for f in run_doctor(tmp_path) if f.check == "taxonomy-map"]
    assert len(hits) == 1 and hits[0].severity == "warn"
    assert hits[0].paths == ("Company/Memory.md",)
    assert "`Company/Partners/`" in hits[0].message
    assert "`Company/Tenants`" in hits[0].message
    assert "Intel" not in hits[0].message      # exists (root and shared-relative)
    assert "Clients" not in hits[0].message    # placeholder, skipped


def test_taxonomy_checks_are_silent_on_a_fresh_scaffold(tmp_path):
    from brain.doctor import run_doctor
    from brain.templates import scaffold_master

    scaffold_master(tmp_path, "Acme")
    assert not [f for f in run_doctor(tmp_path)
                if f.check in ("taxonomy-map", "entity-vocabulary")]


def test_entity_vocabulary_flags_a_type_the_vault_never_declared(tmp_path):
    """An ingest wrote `entity: provider` pages into a vault whose map declared
    property/partner/tenant. Nothing noticed. The config's own entity word is
    always declared; anything else must be named in 'Where knowledge lives'
    (singular or plural) or the pages are using a vocabulary nobody agreed."""
    from brain.doctor import run_doctor
    from brain.templates import scaffold_master

    scaffold_master(tmp_path, "Acme")
    page = tmp_path / "Company/Playbook/Venable.md"
    page.write_text("---\nentity: provider\n---\n# Venable\n")
    own = tmp_path / "Clients/Acme Corp/Acme Corp.md"
    own.parent.mkdir(parents=True)
    own.write_text('---\nentity: "client"\n---\n# Acme\n')
    hits = [f for f in run_doctor(tmp_path) if f.check == "entity-vocabulary"]
    assert len(hits) == 1 and hits[0].severity == "warn"
    assert "`provider`" in hits[0].message and "1 page" in hits[0].message
    assert hits[0].paths == ("Company/Playbook/Venable.md",)
    # naming it in the map — plural is fine — is the declaration
    mem = tmp_path / "Company/Memory.md"
    mem.write_text(mem.read_text() + "- `Company/Playbook/` — our providers\n")
    assert not [f for f in run_doctor(tmp_path) if f.check == "entity-vocabulary"]
    # the one irregular plural: a map that names `People/` declares `person`
    mem.write_text(mem.read_text() + "- `People/` — everyone's own notes\n")
    (tmp_path / "Company/Playbook/Bob.md").write_text("---\nentity: person\n---\n# Bob\n")
    assert not [f for f in run_doctor(tmp_path) if f.check == "entity-vocabulary"]


def _size_findings(master, pid):
    from brain.doctor import run_doctor

    return [f for f in run_doctor(master)
            if f.check == "protocol-size" and f.message.startswith(f"{pid}:")]


def _seeded_bob_protocol_size(master):
    """seed_meta's org, rules and config, plus the size of bob's protocol at
    the real ROOT_LIMIT: the baseline both size tests scale the limit from."""
    from brain.contextgen import render_person_protocol, writable_spaces
    from brain.resolver import readable_spaces
    from brain.schemas import load_config, load_org, load_spaces
    from tests.test_cli import seed_meta

    seed_meta(master)
    org = load_org(master / "_meta/org.yaml")
    rules = load_spaces(master / "_meta/spaces.yaml")
    config = load_config(master)
    bob = org.people["bob"]
    size = len(render_person_protocol(
        master, bob, writable_spaces(readable_spaces(master, bob, rules), bob, rules),
        config))
    return org, rules, config, size


def test_protocol_size_warns_then_errors_as_a_protocol_nears_the_limit(master, monkeypatch):
    import brain.contextgen as cg

    *_, size = _seeded_bob_protocol_size(master)

    assert _size_findings(master, "bob") == []                       # 26%: silent
    monkeypatch.setattr(cg, "ROOT_LIMIT", int(size / 0.85))
    [f] = _size_findings(master, "bob")
    assert f.severity == "warn" and "(85%)" in f.message
    monkeypatch.setattr(cg, "ROOT_LIMIT", int(size / 0.97))
    [f] = _size_findings(master, "bob")
    assert f.severity == "error" and "(97%)" in f.message
    monkeypatch.setattr(cg, "ROOT_LIMIT", size - 1)
    [f] = _size_findings(master, "bob")
    assert f.severity == "error" and "compile fails until it shrinks" in f.message


def test_protocol_size_percent_always_matches_its_severity(master, monkeypatch):
    """The percent doctor prints must never disagree with the severity it
    chose: 94.6% is a warning, and it must never print as "(95%)". Sweep
    ROOT_LIMIT across both the 80% and 95% boundaries, one character to
    either side, and check that the printed percent and the severity are
    always derived from the same number."""
    import brain.contextgen as cg
    import brain.doctor as doctor_mod

    org, rules, config, size = _seeded_bob_protocol_size(master)

    for pct_target in (79, 80, 94, 95):
        for d in (-1, 0, 1):
            limit = size * 100 // pct_target + d
            monkeypatch.setattr(cg, "ROOT_LIMIT", limit)
            pct = size * 100 // limit
            findings = [
                f for f in doctor_mod._check_protocol_size(master, org, rules, config)
                if f.check == "protocol-size" and f.message.startswith("bob:")
            ]
            if pct < 80:
                assert findings == [], (pct_target, d, pct)
                continue
            [f] = findings
            assert f"({pct}%)" in f.message, (pct_target, d, pct, f.message)
            if pct >= 95:
                assert f.severity == "error", (pct_target, d, pct)
            else:
                assert f.severity == "warn", (pct_target, d, pct)


def test_cached_vectors_are_read_in_one_batch(master, tmp_path, monkeypatch):
    """Every note's chunk shas go to the cache in one get_many, not one per
    note; the findings are exactly what the per-note reads produced."""
    from brain.embeddings import EmbeddingCache

    seed_meta(master)
    rels = _shuffled_pair(master)
    (master / "Company/Other.md").write_text(
        "# Other\n\n" + " ".join(f"far{i}" for i in range(40)) + "\n")
    rels.append("Company/Other.md")
    _warm_embeddings(master, tmp_path, monkeypatch, rels)

    real = EmbeddingCache.get_many
    calls: list[int] = []

    def spy(self, shas, model):
        calls.append(len(shas))
        return real(self, shas, model)

    monkeypatch.setattr(EmbeddingCache, "get_many", spy)
    findings = run_doctor(master)
    assert len(calls) == 1
    assert [f.paths for f in findings if f.check == "dup-near"] == [
        ("Company/Shuffle A.md", "Company/Shuffle B.md")]


def test_doctor_never_touches_a_damaged_embedding_cache(master, tmp_path, monkeypatch):
    """Doctor is read-only: a damaged cache only means no semantic signal;
    rebuilding it is the cycle's job."""
    seed_meta(master)
    rels = _shuffled_pair(master)
    _warm_embeddings(master, tmp_path, monkeypatch, rels)
    db = tmp_path / "emb-cache.db"
    db.write_bytes(b"this is not a database" * 64)
    before = db.read_bytes()
    assert not _severities(run_doctor(master), "dup-near")
    assert db.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir() if "emb-cache" in p.name) == [
        "emb-cache.db"]
