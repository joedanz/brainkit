"""Integrity checks for a company brain: surface what otherwise fails silently.

Read-only by design: doctor never mutates master or any compiled vault.
Severity contract: "error" = invariant broken (exit 1), "warn" = probably a
mistake but nothing leaks (fail-closed side), "info" = normal state worth
seeing (e.g. edits awaiting writeback).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from brain.resolver import _match_rule, enumerate_spaces
from brain.schemas import Org, SchemaError, SpaceRule, load_org, load_spaces


@dataclass(frozen=True)
class Finding:
    severity: str  # "error" | "warn" | "info"
    check: str
    message: str


def _check_meta(master: Path) -> tuple[list[Finding], Org | None, tuple[SpaceRule, ...] | None]:
    findings: list[Finding] = []
    org = rules = None
    try:
        org = load_org(master / "_meta/org.yaml")
    except (SchemaError, FileNotFoundError, OSError) as e:
        findings.append(Finding("error", "meta", f"org.yaml: {e}"))
    try:
        rules = load_spaces(master / "_meta/spaces.yaml")
    except (SchemaError, FileNotFoundError, OSError) as e:
        findings.append(Finding("error", "meta", f"spaces.yaml: {e}"))
    return findings, org, rules


def _check_subjects(org: Org, rules: tuple[SpaceRule, ...]) -> list[Finding]:
    findings: list[Finding] = []
    teams = {t for p in org.people.values() for t in p.teams}
    roles = {r for p in org.people.values() for r in p.roles}
    for rule in rules:
        for subject in (*rule.read, *rule.write):
            if subject == "everyone" or "{name}" in subject:
                continue
            kind, _, value = subject.partition(":")
            if kind == "person" and value not in org.people:
                findings.append(Finding(
                    "error", "subjects",
                    f"rule {rule.path!r}: person {value!r} not in org.yaml"))
            elif kind == "team" and value not in teams:
                findings.append(Finding(
                    "warn", "subjects",
                    f"rule {rule.path!r}: no one is on team {value!r}"))
            elif kind == "role" and value not in roles:
                findings.append(Finding(
                    "warn", "subjects",
                    f"rule {rule.path!r}: no one holds role {value!r}"))
    return findings


def _check_rule_paths(master: Path, rules: tuple[SpaceRule, ...]) -> list[Finding]:
    findings: list[Finding] = []
    for rule in rules:
        base = rule.path[:-2] if rule.path.endswith("/*") else rule.path
        if not (master / base).is_dir():
            findings.append(Finding(
                "warn", "rule-paths",
                f"rule {rule.path!r}: {base!r} does not exist in master"))
    return findings


def _check_space_coverage(master: Path, rules: tuple[SpaceRule, ...]) -> list[Finding]:
    findings: list[Finding] = []
    for space in enumerate_spaces(master):
        rule, _ = _match_rule(space, rules)
        if rule is None:
            findings.append(Finding(
                "warn", "space-coverage",
                f"space {space!r} matches no rule — unreachable by everyone"))
    return findings


def run_doctor(master: Path, out_root: Path | None = None) -> list[Finding]:
    findings, org, rules = _check_meta(master)
    if org is None or rules is None:
        return findings  # dependent checks are meaningless on broken meta
    findings += _check_subjects(org, rules)
    findings += _check_rule_paths(master, rules)
    findings += _check_space_coverage(master, rules)
    return findings
