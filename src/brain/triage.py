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
