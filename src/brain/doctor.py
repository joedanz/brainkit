"""Integrity checks for a company brain: surface what otherwise fails silently.

Read-only by design: doctor never mutates master or any compiled vault.
Severity contract: "error" = invariant broken (exit 1), "warn" = probably a
mistake but nothing leaks (fail-closed side), "info" = normal state worth
seeing (e.g. edits awaiting writeback).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from brain.compiler import MANIFEST_NAME
from brain.promotions import PromotionError, _parse, _pending_dir, _validate_target
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
    except (SchemaError, OSError, yaml.YAMLError) as e:
        findings.append(Finding("error", "meta", f"org.yaml: {e}"))
    try:
        rules = load_spaces(master / "_meta/spaces.yaml")
    except (SchemaError, OSError, yaml.YAMLError) as e:
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


def _check_symlinks(master: Path) -> list[Finding]:
    findings: list[Finding] = []
    for p in sorted(master.rglob("*")):
        if ".git" in p.parts:
            continue
        if p.is_symlink():
            findings.append(Finding(
                "error", "symlinks",
                f"{p.relative_to(master)} is a symlink — compiler and writeback "
                "skip links, so this content is dead weight or an escape attempt"))
    return findings


def _check_promotions(master: Path) -> list[Finding]:
    findings: list[Finding] = []
    pending_dir = _pending_dir(master)
    valid_pending = 0
    if pending_dir.is_dir():
        for f in sorted(pending_dir.glob("*.md")):
            try:
                promo = _parse(f)
                _validate_target(promo.target_path)
                valid_pending += 1
            except (KeyError, ValueError, PromotionError) as e:
                findings.append(Finding(
                    "warn", "promotions",
                    f"pending/{f.name}: malformed, will never be approvable ({e})"))
    if valid_pending:
        findings.append(Finding(
            "info", "promotions",
            f"{valid_pending} promotion(s) awaiting approval"))

    # Drafts sweep() will silently skip forever: missing/invalid target-path.
    for f in sorted(master.glob("People/*/Promotions/*.md")):
        if f.is_symlink():
            continue
        text = f.read_text()
        rel = f.relative_to(master)
        if text.count("---\n") < 2:
            findings.append(Finding(
                "warn", "promotions", f"{rel}: draft has no frontmatter, sweep skips it"))
            continue
        _, fm, _ = text.split("---\n", 2)
        meta = dict(
            (line.partition(": ")[0], line.partition(": ")[2])
            for line in fm.strip().splitlines()
        )
        try:
            _validate_target(meta.get("target-path", ""))
        except PromotionError as e:
            findings.append(Finding(
                "warn", "promotions", f"{rel}: sweep will never move it ({e})"))
    return findings


def _check_compiled(master: Path, org, out_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for person in org.people.values():
        vault = out_root / person.id
        for tomb in (out_root / f".{person.id}.old", out_root / f".{person.id}.building"):
            if tomb.exists():
                findings.append(Finding(
                    "error", "compiled",
                    f"{tomb.name}: leftover from a crashed compile — "
                    "next compile will attempt recovery; investigate first"))
        if not vault.is_dir():
            findings.append(Finding(
                "warn", "compiled", f"{person.id}: no compiled vault yet"))
            continue
        if (vault / "_meta").exists():
            findings.append(Finding(
                "error", "compiled",
                f"{person.id}: _meta/ present inside compiled vault — "
                "SECURITY: server-only data leaked to a person"))
        manifest_path = vault / MANIFEST_NAME
        try:
            manifest = json.loads(manifest_path.read_text())
            drifted = 0
            for rel, sha in manifest["compiled"].items():
                f = vault / rel
                if not f.is_file():
                    drifted += 1
                elif hashlib.sha256(f.read_bytes()).hexdigest() != sha:
                    drifted += 1
        except (FileNotFoundError, ValueError, KeyError) as e:
            findings.append(Finding(
                "error", "compiled", f"{person.id}: unreadable manifest ({e})"))
            continue
        if drifted:
            findings.append(Finding(
                "info", "compiled",
                f"{person.id}: {drifted} file(s) awaiting writeback"))
    return findings


def run_doctor(master: Path, out_root: Path | None = None) -> list[Finding]:
    findings, org, rules = _check_meta(master)
    if org is None or rules is None:
        return findings  # dependent checks are meaningless on broken meta
    findings += _check_subjects(org, rules)
    findings += _check_rule_paths(master, rules)
    findings += _check_space_coverage(master, rules)
    findings += _check_symlinks(master)
    findings += _check_promotions(master)
    if out_root is not None:
        findings += _check_compiled(master, org, out_root)
    return findings
