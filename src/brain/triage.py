"""Route doctor findings to responsible people as rolling Inbox digests.

Doctor finds; triage delivers. Warn-level content findings go to the owner of
the space they're about (personal spaces have exactly one owner); findings
about shared spaces, unresolvable paths, or departed people go to the admins,
as do error-level infra findings the admin's agent can only escalate. A
multi-path finding routes to each path's owner independently; if any
non-admin owner-recipient cannot read one of the finding's *other* spaces,
that recipient's digest line is redacted (path/statement withheld) and the
finding is additionally routed to the admins, who always receive the full
message (their oversight role gets the same detail the master dashboard
shows). Each recipient gets ONE machine-owned digest note
(People/<id>/Inbox/doctor-digest.md) that is fingerprint-skipped when
unchanged, rewritten when the finding set changes, and deleted when nothing
remains — re-running is always idempotent and findings never multiply.
Fixes flow back through the existing gates (write-back validation,
human-approved promotions); triage never applies one.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from brain.doctor import DIGEST_NAME, Finding, run_doctor
from brain.frontmatter import split_frontmatter
from brain.resolver import can_read, can_write_path, space_of_path
from brain.schemas import Org, Person, SchemaError, SpaceRule, load_org, load_spaces

# DIGEST_NAME is canonically owned by brain.doctor — doctor's own-digest
# exclusion needs it too (see doctor._is_own_digest: doctor must never read
# the notes triage writes, or the digest's quoted fact markers and
# wikilinks would corrupt doctor's own findings). Importing it here keeps
# it available as brain.triage.DIGEST_NAME for existing callers/tests.

# Content findings an agent can act on. Everything else is either infra
# (routed to admins at error severity only) or has its own queue already
# (pending shares, promotions) and is never routed.
TRIAGE_CHECKS = frozenset({
    "unlinked-notes", "orphan-files", "intel",
    "dup-exact", "dup-near", "stem-collision",
    "fact-dup", "fact-conflict",
})


def route_findings(
    findings: list[Finding], org: Org, rules: tuple[SpaceRule, ...],
) -> tuple[dict[str, list[Finding]], int]:
    """Map findings to recipient person ids. Returns (routed, unrouted_count).

    Content checks route per path: a People/<id> space to its owner, anything
    else (shared space, unresolvable path, id not in org) to the admins. A
    multi-path finding also escalates to the admins if any non-admin
    owner-recipient lacks `can_read` on the space of one of the finding's
    OTHER paths — that recipient's digest line gets redacted (see
    `_display`), and someone with fuller view (the admins) still sees the
    whole picture. Info-level findings are never routed — the disjoint-space
    dup tier is a hint, not work. Error-severity findings from non-content
    checks are escalations for the admins. With no admins configured,
    findings that needed one count as unrouted (surfaced in the report,
    never a crash).
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
            if len(f.paths) > 1 and not need_admins:
                for pid in recipients:
                    if pid in admins:
                        continue
                    person = org.people[pid]
                    if any(
                        (sp := space_of_path(path)) is None
                        or not can_read(sp, person, rules)
                        for path in f.paths
                    ):
                        need_admins = True
                        break
            if need_admins:
                recipients += [a for a in admins if a not in recipients]
            assign(f, recipients)
        elif f.severity == "error":
            assign(f, admins)
    return routed, unrouted


def _display(f: Finding, person: Person, rules: tuple[SpaceRule, ...], *, is_admin: bool) -> str:
    """The line a given recipient sees for this finding. Admins are the
    oversight role and get `f.message` verbatim — the same detail the master
    dashboard already shows them. Non-admins never see a path or fact
    statement from a space they cannot read: if every path's space is
    readable, the message passes through unchanged; otherwise the line is
    rebuilt from ONLY the readable path(s), pointing to the admins' digest
    for the rest. An unresolvable space counts as unreadable (fail closed)."""
    if is_admin:
        return f.message

    def readable(path: str) -> bool:
        space = space_of_path(path)
        return space is not None and can_read(space, person, rules)

    if f.paths and all(readable(p) for p in f.paths):
        return f.message
    own_paths = [p for p in f.paths if readable(p)]
    own = ", ".join(own_paths) if own_paths else "a note of yours"
    return (f"{own}: {f.check} involving a note in a space you cannot "
            "read — the admins' digest has the detail")


@dataclass
class TriageReport:
    routed: int            # distinct findings delivered to >=1 recipient
    digests_written: int   # digest notes created or rewritten this run
    digests_removed: int   # digest notes deleted because nothing remains
    unrouted: int          # findings with no eligible recipient (no admins)
    warnings: list[str] = field(default_factory=list)


def _fingerprint(lines: list[tuple[str, str]]) -> str:
    """`lines` is this recipient's (check, display_line) pairs — the text
    they'll actually see, post-redaction — not the raw finding messages, so
    a fingerprint match means their digest genuinely has nothing new to say."""
    key = "\n".join(sorted(f"{check}\t{msg}" for check, msg in lines))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def render_digest(lines: list[tuple[str, str]], today: str, fingerprint: str) -> str:
    """`lines` is this recipient's (check, display_line) pairs (see
    `_fingerprint`) — already redacted where needed, so rendering never
    re-derives readability."""
    out = [
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
    by_check: dict[str, list[str]] = {}
    for check, msg in lines:
        by_check.setdefault(check, []).append(msg)
    for check in sorted(by_check):
        out += ["", f"## {check}", ""]
        out += [f"- {msg}" for msg in sorted(by_check[check])]
    return "\n".join(out) + "\n"


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

    routed, unrouted = route_findings(findings, org, rules)
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
        # Both branches below touch the same path — protect delete and write
        # with the same posture check, not just write.
        if space_of_path(rel) != f"People/{person.id}":
            warnings.append(f"{rel}: resolves outside People/{person.id} — skipped")
            continue
        if not can_write_path(rel, person, rules):
            warnings.append(
                f"{person.id} has no write grant on their own space — skipped")
            continue
        person_findings = routed.get(person.id, [])
        if not person_findings:
            if target.is_symlink():
                # Mirrors the write branch's refusal below — a symlinked
                # leaf is never touched, deletion included.
                warnings.append(f"{rel}: digest is a symlink — refusing to remove")
            elif target.is_file():
                try:
                    target.unlink()
                except OSError as e:
                    warnings.append(f"{rel}: {e}")
                    continue
                removed += 1
                changed.append(rel)
            continue
        is_admin = "admin" in person.roles
        lines = [(f.check, _display(f, person, rules, is_admin=is_admin))
                 for f in person_findings]
        fp = _fingerprint(lines)
        if target.is_file() and not target.is_symlink():
            try:
                meta, _body = split_frontmatter(target.read_text())
            except (KeyError, ValueError, UnicodeDecodeError):
                # Malformed existing digest — not our concern, treat as no
                # match and fall through to self-heal via rewrite below.
                meta = {}
            except OSError as e:
                # Can't even read it (permissions, I/O error) — don't guess
                # whether a rewrite would fare any better; warn and move on.
                warnings.append(f"{rel}: {e}")
                continue
            if meta and meta.get("fingerprint") == fp:
                continue  # same findings — leave the note (and `created`) alone
        if target.is_symlink():
            warnings.append(f"{rel}: digest is a symlink — refusing to write")
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(render_digest(lines, today, fp))
        except OSError as e:
            warnings.append(f"{rel}: {e}")
            continue
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
