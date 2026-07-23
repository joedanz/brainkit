"""Space shares: owner-requested grant changes on spaces they own.

Mirrors the clients/promotions seams and is deliberately noun-agnostic — it
operates on space paths, never on entity vocabulary. An agent drops a request
in its own writable People/<pid>/ShareRequests/; sweep_shares (run inside
brain cycle) routes it: `share` requests go to the human-gated
_meta/shares/pending/ queue (adding access is risk-increasing), `revoke`
requests auto-apply (removing access is risk-decreasing) with an audit
archive. Two text-surgery primitives amend the one exact rule line in
spaces.yaml, preserving every other line byte-for-byte.

Fail closed: the authoritative requester is the <pid> in the request path,
re-verified against the live rule server-side; role:admin can never be
removed; subjects pass a strict charset before touching YAML.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from brain.resolver import space_of_path


class ShareError(ValueError):
    """Invalid share request, subject, space, or unknown share id."""


ACCESS_LEVELS = ("read", "write")
ACTIONS = ("share", "revoke")

_SLUG = re.compile(r"[A-Za-z0-9._-]+")


def validate_subject(subject: str) -> tuple[str, str]:
    kind, sep, name = subject.partition(":")
    if not sep or kind not in ("person", "team") or not _SLUG.fullmatch(name or ""):
        raise ShareError(f"invalid subject {subject!r} — expected person:<id> or team:<name>")
    return kind, name


def validate_space(space: str) -> None:
    if "*" in space:
        raise ShareError(f"cannot share a wildcard path {space!r}")
    if len(PurePosixPath(space).parts) != 2:
        raise ShareError(f"{space!r} is not a shareable space (expected <Top>/<Name>)")
    if space_of_path(f"{space}/x.md") != space:
        raise ShareError(f"{space!r} is not inside any space family")
    if space.startswith("People/"):
        raise ShareError("personal spaces cannot be shared")
