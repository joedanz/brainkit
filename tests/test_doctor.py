from pathlib import Path

from brain.doctor import run_doctor

from .test_cli import ORG_YAML, SPACES_YAML, seed_meta


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
