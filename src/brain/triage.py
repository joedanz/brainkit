"""Route doctor findings to responsible people as rolling Inbox digests.

Doctor finds; triage delivers. Warn-level content findings go to the owner of
the space they're about (personal spaces have exactly one owner); findings
about shared spaces, unresolvable paths, or departed people go to the admins,
as do error-level infra findings the admin's agent can only escalate. Each
recipient gets ONE machine-owned digest note (People/<id>/Inbox/doctor-digest.md)
that is fingerprint-skipped when unchanged, rewritten when the finding set
changes, and deleted when nothing remains — re-running is always idempotent
and findings never multiply. Fixes flow back through the existing gates
(write-back validation, human-approved promotions); triage never applies one.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from brain.doctor import Finding, run_doctor
from brain.frontmatter import split_frontmatter
from brain.resolver import can_write_path, space_of_path
from brain.schemas import Org, SchemaError, load_org, load_spaces

DIGEST_NAME = "doctor-digest.md"

# Content findings an agent can act on. Everything else is either infra
# (routed to admins at error severity only) or has its own queue already
# (pending shares, promotions) and is never routed.
TRIAGE_CHECKS = frozenset({
    "unlinked-notes", "orphan-files", "intel",
    "dup-exact", "dup-near", "stem-collision",
    "fact-dup", "fact-conflict",
})


def route_findings(
    findings: list[Finding], org: Org,
) -> tuple[dict[str, list[Finding]], int]:
    """Map findings to recipient person ids. Returns (routed, unrouted_count).

    Content checks route per path: a People/<id> space to its owner, anything
    else (shared space, unresolvable path, id not in org) to the admins.
    Info-level findings are never routed — the disjoint-space dup tier is a
    hint, not work. Error-severity findings from non-content checks are
    escalations for the admins. With no admins configured, findings that
    needed one count as unrouted (surfaced in the report, never a crash).
    """
    admins = sorted(p.id for p in org.people.values() if "admin" in p.roles)
    routed: dict[str, list[Finding]] = {}
    unrouted = 0

    def assign(finding: Finding, recipients: list[str]) -> None:
        nonlocal unrouted
        if not recipients:
            unrouted += 1
            return
        for pid in recipients:
            bucket = routed.setdefault(pid, [])
            if finding not in bucket:
                bucket.append(finding)

    for f in findings:
        if f.check in TRIAGE_CHECKS:
            if f.severity != "warn":
                continue
            recipients: list[str] = []
            need_admins = not f.paths
            for path in f.paths:
                space = space_of_path(path)
                pid = space.split("/", 1)[1] if space and space.startswith("People/") else None
                if pid is not None and pid in org.people:
                    if pid not in recipients:
                        recipients.append(pid)
                else:
                    need_admins = True
            if need_admins:
                recipients += [a for a in admins if a not in recipients]
            assign(f, recipients)
        elif f.severity == "error":
            assign(f, admins)
    return routed, unrouted


@dataclass
class TriageReport:
    routed: int            # distinct findings delivered to >=1 recipient
    digests_written: int   # digest notes created or rewritten this run
    digests_removed: int   # digest notes deleted because nothing remains
    unrouted: int          # findings with no eligible recipient (no admins)
    warnings: list[str] = field(default_factory=list)


def _fingerprint(findings: list[Finding]) -> str:
    key = "\n".join(sorted(f"{f.check}\t{f.message}" for f in findings))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def render_digest(findings: list[Finding], today: str, fingerprint: str) -> str:
    lines = [
        "---",
        "title: Doctor digest",
        "source: doctor",
        f"created: {today}",
        f"fingerprint: {fingerprint}",
        "---",
        "",
        "Integrity findings from `brain doctor`, routed to you. Fix what you",
        "can in writable spaces; fixes to shared pages go as `mode: patch`",
        "promotions; items only a human can decide get a one-line reason in",
        "`Needs-Routing.md`. Do not edit or archive this note — it rewrites",
        "itself as findings change and disappears when everything is fixed.",
    ]
    by_check: dict[str, list[Finding]] = {}
    for f in findings:
        by_check.setdefault(f.check, []).append(f)
    for check in sorted(by_check):
        lines += ["", f"## {check}", ""]
        lines += [f"- {f.message}"
                  for f in sorted(by_check[check], key=lambda f: f.message)]
    return "\n".join(lines) + "\n"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )


def run_triage(master: Path, out_root: Path | None = None, *, today: str) -> TriageReport:
    """Run doctor, route findings, and reconcile every recipient's digest.

    Write posture mirrors ingest: the digest path is constructed, symlinked
    ancestors are refused, and the person's own write grant is re-checked
    (fail closed). Unlike ingest the filename is STABLE — the note is a
    rolling reconciliation target, not an append-only intake — so identical
    finding sets (same fingerprint) skip the write entirely and an emptied
    set deletes the file. One commit covers the whole run.
    """
    findings = run_doctor(master, out_root)
    try:
        org = load_org(master / "_meta/org.yaml")
        rules = load_spaces(master / "_meta/spaces.yaml")
    except (SchemaError, OSError, yaml.YAMLError) as e:
        return TriageReport(0, 0, 0, len(findings),
                            [f"meta unreadable — nothing routed: {e}"])

    routed, unrouted = route_findings(findings, org)
    delivered = len({f for fs in routed.values() for f in fs})
    written = removed = 0
    changed: list[str] = []
    warnings: list[str] = []

    for person in org.people.values():
        rel = f"People/{person.id}/Inbox/{DIGEST_NAME}"
        # Refuse symlinked ancestors before touching anything (ingest posture).
        ancestor = master
        symlinked = False
        for part in Path(rel).parent.parts:
            ancestor = ancestor / part
            if ancestor.is_symlink():
                warnings.append(f"{rel}: ancestor is a symlink — refusing to write")
                symlinked = True
                break
        if symlinked:
            continue
        target = master / rel
        person_findings = routed.get(person.id, [])
        if not person_findings:
            if target.is_file() and not target.is_symlink():
                target.unlink()
                removed += 1
                changed.append(rel)
            continue
        if space_of_path(rel) != f"People/{person.id}":
            warnings.append(f"{rel}: resolves outside People/{person.id} — skipped")
            continue
        if not can_write_path(rel, person, rules):
            warnings.append(
                f"{person.id} has no write grant on their own space — skipped")
            continue
        fp = _fingerprint(person_findings)
        if target.is_file() and not target.is_symlink():
            try:
                meta, _body = split_frontmatter(target.read_text())
            except (KeyError, ValueError, UnicodeDecodeError):
                meta = {}
            if meta and meta.get("fingerprint") == fp:
                continue  # same findings — leave the note (and `created`) alone
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_digest(person_findings, today, fp))
        written += 1
        changed.append(rel)

    if changed:
        try:
            _git(master, "add", "--", *changed)  # stages edits AND deletions
            if _git(master, "status", "--porcelain", "--", *changed).stdout.strip():
                _git(master,
                     "-c", "user.name=Brain Triage",
                     "-c", "user.email=triage@brain.local",
                     "commit", "-m",
                     f"triage: {written} digest(s) written, {removed} removed")
        except subprocess.CalledProcessError as e:
            warnings.append(f"git commit failed: {e.stderr.strip()}")

    return TriageReport(delivered, written, removed, unrouted, warnings)
