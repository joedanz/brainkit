"""Space shares: owner-requested grant changes on spaces they own.

Mirrors the existing propose-then-server-acts seams and is deliberately
noun-agnostic — it operates on space paths, never on entity vocabulary. An agent drops a request
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
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

from brain.clients import _validate_owner_id
from brain.frontmatter import split_frontmatter
from brain.promotions import _commit, _slug
from brain.resolver import can_write_path, space_of_path
from brain.schemas import Org, load_spaces


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


def _emit_rule(space: str, read: list[str], write: list[str]) -> str:
    def lst(subjects: list[str]) -> str:
        return "[" + ", ".join(f'"{s}"' for s in subjects) + "]"
    return f'  - {{path: "{space}", read: {lst(read)}, write: {lst(write)}}}'


def _find_rule_line(text: str, space: str) -> tuple[int, list[str], list[str]] | None:
    """Locate the one line holding the exact rule for ``space``. Returns
    (line index, read list, write list) or None. Each candidate line is a
    single YAML flow mapping — safe to parse in isolation."""
    for i, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        try:
            entry = yaml.safe_load(stripped[2:])
        except yaml.YAMLError:
            continue
        if isinstance(entry, dict) and entry.get("path") == space:
            return i, list(entry.get("read") or []), list(entry.get("write") or [])
    return None


def _rewrite_line(spaces_path: Path, idx: int, new_line: str) -> None:
    lines = spaces_path.read_text().splitlines()
    lines[idx] = new_line
    spaces_path.write_text("\n".join(lines) + "\n")


def amend_space_rule(spaces_path: Path, space: str, subject: str, access: str) -> bool:
    """Add ``subject`` to the exact rule's read list (and write, for
    access=="write"). Only ever adds; refuses missing rules and wildcards;
    idempotent. Every other line survives byte-identical."""
    validate_subject(subject)
    validate_space(space)
    if access not in ACCESS_LEVELS:
        raise ShareError(f"unknown access {access!r} — expected read or write")
    found = _find_rule_line(spaces_path.read_text(), space)
    if found is None:
        raise ShareError(f"no exact rule for {space!r} — nothing to amend")
    idx, read, write = found
    changed = False
    if subject not in read:
        read.append(subject)
        changed = True
    if access == "write" and subject not in write:
        write.append(subject)
        changed = True
    if not changed:
        return False
    _rewrite_line(spaces_path, idx, _emit_rule(space, read, write))
    return True


def remove_subject_from_rule(spaces_path: Path, space: str, subject: str) -> bool:
    """Remove ``subject`` from both lists of the exact rule. role:admin is
    structural oversight and can never be removed."""
    if subject == "role:admin":
        raise ShareError("role:admin cannot be revoked — admin oversight is structural")
    validate_subject(subject)
    validate_space(space)
    found = _find_rule_line(spaces_path.read_text(), space)
    if found is None:
        raise ShareError(f"no exact rule for {space!r} — nothing to revoke")
    idx, read, write = found
    if subject not in read and subject not in write:
        return False
    read = [s for s in read if s != subject]
    write = [s for s in write if s != subject]
    _rewrite_line(spaces_path, idx, _emit_rule(space, read, write))
    return True


SHARE_REQUESTS_REL = "People/{person_id}/ShareRequests"


def request_share(
    root: Path,
    person_id: str,
    space: str,
    share_with: str,
    access: str,
    created: str,
    body: str = "",
    action: str = "share",
) -> str:
    """Write a share/revoke request into the person's own space; return its
    vault-relative path. ``root`` may be a compiled slice — write-back carries
    it to master, where sweep_shares routes it. ``body`` is an optional note
    to the approver."""
    validate_space(space)
    validate_subject(share_with)
    if access not in ACCESS_LEVELS:
        raise ShareError(f"unknown access {access!r} — expected read or write")
    if action not in ACTIONS:
        raise ShareError(f"unknown action {action!r} — expected share or revoke")
    for field, value in (("space", space), ("share-with", share_with),
                         ("owner", person_id), ("created", created)):
        if "\n" in value or "\r" in value:
            raise ShareError(f"{field} must be a single line")

    req_rel = SHARE_REQUESTS_REL.format(person_id=person_id)
    ancestor = root
    for part in PurePosixPath(req_rel).parts:
        ancestor = ancestor / part
        if ancestor.is_symlink():
            raise ShareError(f"{req_rel} contains a symlink — refusing to write")

    dir_ = root / req_rel
    base = _slug(f"{action}-{PurePosixPath(space).name}-{share_with}") or "share"
    fname = f"{created}-{base}.md"
    n = 2
    while (dir_ / fname).exists() or (dir_ / fname).is_symlink():
        fname = f"{created}-{base}-{n}.md"
        n += 1
    rel_path = f"{req_rel}/{fname}"
    if space_of_path(rel_path) != f"People/{person_id}":
        raise ShareError(f"refusing to write outside {req_rel}")

    dest = root / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        "---\n"
        f"space: {space}\n"
        f"share-with: {share_with}\n"
        f"access: {access}\n"
        f"action: {action}\n"
        f"owner: {person_id}\n"
        f"created: {created}\n"
        "---\n"
        f"{body}"
    )
    return rel_path


@dataclass(frozen=True)
class ShareOutcome:
    space: str
    owner: str
    subject: str
    action: str
    status: str  # "queued" | "revoked" | "rejected" | "tampering"
    reason: str = ""


def _pending_dir(master: Path) -> Path:
    return master / "_meta/shares/pending"


def _decided_ids(master: Path) -> set[str]:
    ids: set[str] = set()
    for state in ("approved", "rejected", "revoked"):
        d = master / "_meta/shares" / state
        if d.is_dir():
            ids.update(f.stem for f in d.glob("*.md"))
    return ids


def _share_inbox_note(master: Path, person_id: str, slug: str, text: str,
                      today: str) -> str:
    rel = f"People/{person_id}/Inbox/share-{slug}.md"
    dest = master / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(f"---\ncreated: {today}\n---\n{text}\n")
    return rel


def list_pending_shares(master: Path) -> list[dict]:
    d = _pending_dir(master)
    if not d.exists():
        return []
    entries: list[dict] = []
    for f in sorted(d.glob("*.md")):
        try:
            meta, body = split_frontmatter(f.read_text())
        except (KeyError, ValueError):
            continue
        if not meta:
            continue
        entries.append({**meta, "body": body, "id": meta.get("share-id", f.stem)})
    return entries


def _subject_known(subject: str, org: Org) -> bool:
    kind, name = validate_subject(subject)
    if kind == "person":
        return name in org.people
    return any(name in p.teams for p in org.people.values())


def sweep_shares(master: Path, org: Org, today: str) -> list[ShareOutcome]:
    """Route People/*/ShareRequests/*.md: shares to the human-gated pending
    queue, revokes auto-applied (Task 5). The <pid> path segment is the
    authoritative requester; server-side ownership is re-checked against the
    live exact rule per iteration."""
    results: list[ShareOutcome] = []
    decided = _decided_ids(master)
    for req in sorted(master.glob("People/*/ShareRequests/*.md")):
        if req.is_symlink():
            continue
        rel = req.relative_to(master)
        person_id = rel.parts[1]
        try:
            _validate_owner_id(person_id)
        except Exception:
            continue  # malformed pid folder: leave in place, touch nothing
        meta, body = split_frontmatter(req.read_text())
        if not meta:
            continue
        person = org.people.get(person_id)
        name_id = person.name if person else person_id
        space = str(meta.get("space", ""))
        subject = str(meta.get("share-with", ""))
        access = str(meta.get("access", "read"))
        action = str(meta.get("action", "share"))
        slug = _slug(req.stem)
        share_id = f"{person_id}-{slug}"

        def consume(status: str, reason: str = "", extra: list[str] | None = None,
                    message: str = "") -> None:
            req.unlink()
            _commit(master, [rel.as_posix(), *(extra or [])],
                    message or f"shares: {status} {share_id}",
                    name_id, f"{person_id}@brain.local")
            results.append(ShareOutcome(space, person_id, subject, action,
                                        status, reason))

        # syntactic validity — malformed stays in place for inspection
        try:
            validate_space(space)
            validate_subject(subject)
            if access not in ACCESS_LEVELS or action not in ACTIONS:
                raise ShareError("bad access/action")
        except ShareError:
            continue

        if meta.get("owner", person_id) != person_id:
            consume("tampering", "owner mismatch")
            continue

        # ownership: the live exact rule must grant this pid write
        rules = load_spaces(master / "_meta/spaces.yaml")
        exact = next((r for r in rules if r.path == space), None)
        if exact is None or person is None or not can_write_path(
                f"{space}/x.md", person, rules):
            consume("tampering", "not the owner of this space")
            continue

        if share_id in decided:
            req.unlink()
            _commit(master, [rel.as_posix()], f"shares: drop stale {share_id}",
                    name_id, f"{person_id}@brain.local")
            continue

        if not _subject_known(subject, org):
            note = _share_inbox_note(
                master, person_id, slug,
                f"Cannot share {space}: {subject} is not in the org.", today)
            consume("rejected", "unknown recipient", [note])
            continue

        if action == "share":
            if subject in exact.read and (access == "read" or subject in exact.write):
                note = _share_inbox_note(
                    master, person_id, slug,
                    f"{subject} already has {access} access to {space}.", today)
                consume("rejected", "already shared", [note])
                continue
            if (_pending_dir(master) / f"{share_id}.md").exists():
                continue  # already queued: leave request untouched (promotions posture)
            dest = _pending_dir(master) / f"{share_id}.md"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(
                "---\n"
                f"share-id: {share_id}\n"
                f"from: {person_id}\n"
                f"space: {space}\n"
                f"share-with: {subject}\n"
                f"access: {access}\n"
                f"created: {meta.get('created', today)}\n"
                "---\n"
                f"{body}"
            )
            consume("queued", extra=[dest.relative_to(master).as_posix()],
                    message=f"shares: queue {share_id}")
            continue

        # action == "revoke" — implemented in Task 5
        continue
    return results
