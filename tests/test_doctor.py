from pathlib import Path

from brain.doctor import run_doctor
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
