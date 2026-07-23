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
from brain.schemas import Org, Person, SchemaError, load_org, load_spaces


class ShareError(ValueError):
    """Invalid share request, subject, space, or unknown share id."""


ACCESS_LEVELS = ("read", "write")
ACTIONS = ("share", "revoke")

_SLUG = re.compile(r"[A-Za-z0-9._-]+")


def validate_subject(subject: str) -> tuple[str, str]:
    if subject == "everyone":
        return "everyone", ""
    kind, sep, name = subject.partition(":")
    if not sep or kind not in ("person", "team") or not _SLUG.fullmatch(name or ""):
        raise ShareError(
            f"invalid subject {subject!r} — expected person:<id>, team:<name>, "
            "or everyone")
    return kind, name


def may_decide(person: Person | None, share_with: str) -> bool:
    """One authority definition for share decisions, used by both the
    server-side gate (approve/reject) and the in-vault Approvals seam.
    Fail closed: an unknown person or malformed subject decides nothing.
    everyone-shares are admin-only by design."""
    if person is None:
        return False
    if "admin" in person.roles:
        return True
    try:
        kind, name = validate_subject(share_with)
    except ShareError:
        return False
    if kind == "person":
        return person.id == name
    if kind == "team":
        return "lead" in person.roles and name in person.teams
    return False  # "everyone" — admin only


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
    if share_with == "everyone" and action == "share" and access != "read":
        raise ShareError("company-wide shares are read-only — use access: read")
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
    if subject == "everyone":
        return True
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
        try:
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

            if action == "share":
                if subject == "everyone" and access != "read":
                    note = _share_inbox_note(
                        master, person_id, slug,
                        f"Cannot share {space} with everyone as write — "
                        "company-wide shares are read-only.", today)
                    consume("rejected", "company-wide shares are read-only", [note])
                    continue
                if not _subject_known(subject, org):
                    note = _share_inbox_note(
                        master, person_id, slug,
                        f"Cannot share {space}: {subject} is not in the org.", today)
                    consume("rejected", "unknown recipient", [note])
                    continue
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

            # action == "revoke" — auto-applied: removing access is risk-decreasing
            if subject == f"person:{person_id}":
                note = _share_inbox_note(
                    master, person_id, slug,
                    "Cannot revoke your own access — ask an admin.", today)
                consume("rejected", "self-revocation", [note])
                continue

            # revoke authority is structural, not effective: a write-holder whose
            # write is team/role-derived (or who simply isn't the bound owner)
            # must not be able to auto-apply a revoke. Only the space's bound
            # owner — the first literal person: entry on the exact rule's write
            # list, set at space creation and never reordered — may revoke.
            rule_line = _find_rule_line((master / "_meta/spaces.yaml").read_text(), space)
            rule_write = rule_line[2] if rule_line else []
            if f"person:{person_id}" not in rule_write:
                note = _share_inbox_note(
                    master, person_id, slug,
                    "Only the space owner can revoke access — ask an admin.", today)
                consume("rejected", "not the bound owner", [note])
                continue
            bound_owner = next((s for s in rule_write if s.startswith("person:")), None)
            if subject == bound_owner:
                note = _share_inbox_note(
                    master, person_id, slug,
                    "Only an admin can remove the space owner's access.", today)
                consume("rejected", "cannot revoke the space owner", [note])
                continue

            removed = remove_subject_from_rule(
                master / "_meta/spaces.yaml", space, subject)
            if not removed:
                note = _share_inbox_note(
                    master, person_id, slug,
                    f"{subject} is not shared on {space} — nothing to revoke.", today)
                consume("rejected", "not shared", [note])
                continue
            archived = master / "_meta/shares/revoked" / f"{share_id}.md"
            archived.parent.mkdir(parents=True, exist_ok=True)
            _, fm, req_body = req.read_text().split("---\n", 2)
            archived.write_text(f"---\n{fm}revoked-on: {today}\n---\n{req_body}")
            consume("revoked",
                    extra=["_meta/spaces.yaml",
                           archived.relative_to(master).as_posix()],
                    message=f"shares: revoke {subject} from {space}")
            continue
        except Exception:
            continue  # unexpected per-request error: leave file in place, touch nothing
    return results


APPROVALS_REL = "People/{person_id}/Approvals"


@dataclass(frozen=True)
class DecisionOutcome:
    share_id: str
    decider: str
    decision: str
    status: str  # "applied" | "refused" | "tampering"
    reason: str = ""


def sweep_approvals(master: Path, org: Org, today: str) -> list[DecisionOutcome]:
    """Apply delegated decisions from People/*/Approvals/*.md to the pending
    share queue. The <pid> path segment is the authoritative decider (writeback
    already gated the write); the filename stem is the share id. Eligibility is
    re-checked at decision time with may_decide. Company-wide (everyone) shares
    are never decidable here — master-side admins only. Only a forged owner:
    is tampering; every other failure is a routine refusal with an inbox note."""
    results: list[DecisionOutcome] = []
    for note in sorted(master.glob("People/*/Approvals/*.md")):
        if note.is_symlink():
            continue
        rel = note.relative_to(master)
        decider_id = rel.parts[1]
        try:
            _validate_owner_id(decider_id)
        except Exception:
            continue  # malformed pid folder: leave in place, touch nothing
        try:
            share_id = note.stem
            if not _SLUG.fullmatch(share_id):
                continue  # malformed id: leave in place for inspection
            meta, _ = split_frontmatter(note.read_text())
            if not meta:
                continue
            decider = org.people.get(decider_id)
            name_id = decider.name if decider else decider_id
            decision = str(meta.get("decision", ""))
            reason = str(meta.get("reason", ""))

            def consume(status: str, why: str = "",
                        extra: list[str] | None = None) -> None:
                note.unlink()
                _commit(master, [rel.as_posix(), *(extra or [])],
                        f"shares: decision {share_id} {status}",
                        name_id, f"{decider_id}@brain.local")
                results.append(DecisionOutcome(share_id, decider_id, decision,
                                               status, why))

            if meta.get("owner", decider_id) != decider_id:
                consume("tampering", "owner mismatch")
                continue

            try:
                pending = _find_pending_share(master, share_id)
            except ShareError:
                inbox = _share_inbox_note(
                    master, decider_id, _slug(share_id),
                    f"Share {share_id} is already decided or unknown.", today)
                consume("refused", "already decided or unknown", [inbox])
                continue

            pmeta, _ = split_frontmatter(pending.read_text())
            share_with = str(pmeta.get("share-with", ""))
            if share_with == "everyone" or not may_decide(decider, share_with):
                msg = ("Company-wide shares are decided by an admin with "
                       "brain shares approve." if share_with == "everyone" else
                       f"You are not an eligible approver for {share_id}.")
                inbox = _share_inbox_note(master, decider_id, _slug(share_id),
                                          msg, today)
                consume("refused", "not eligible", [inbox])
                continue

            if decision not in ("approve", "reject"):
                inbox = _share_inbox_note(
                    master, decider_id, _slug(share_id),
                    f"Decision for {share_id} must be approve or reject.", today)
                consume("refused", "bad decision", [inbox])
                continue
            if decision == "reject" and not reason.strip():
                inbox = _share_inbox_note(
                    master, decider_id, _slug(share_id),
                    f"Rejecting {share_id} needs a reason: line.", today)
                consume("refused", "missing reason", [inbox])
                continue

            try:
                if decision == "approve":
                    approve_share(master, share_id, approver=decider_id,
                                  date=today, via="delegated")
                else:
                    reject_share(master, share_id, reason=reason, date=today,
                                 approver=decider_id, via="delegated")
            except ShareError as e:
                inbox = _share_inbox_note(
                    master, decider_id, _slug(share_id),
                    f"Could not apply your decision on {share_id}: {e}", today)
                consume("refused", str(e), [inbox])
                continue
            consume("applied", decision)
        except Exception:
            continue  # unexpected per-note error: leave file in place, touch nothing
    return results


def _find_pending_share(master: Path, share_id: str) -> Path:
    # A path/traversal-shaped id (e.g. "../../evil") must fail exactly like an
    # unknown one — the charset check happens before any filesystem lookup,
    # and the error message never reveals which reason applied.
    if not _SLUG.fullmatch(share_id):
        raise ShareError(f"no pending share {share_id!r}")
    p = _pending_dir(master) / f"{share_id}.md"
    if not p.exists():
        raise ShareError(f"no pending share {share_id!r}")
    return p


def approve_share(master: Path, share_id: str, approver: str, date: str,
                   via: str = "") -> str:
    """Amend the rule per the pending request. Everything is re-validated at
    decision time: the pending file sat on disk between sweep and approval."""
    if not approver.strip():
        raise ShareError("an approver is required")
    people = load_org(master / "_meta/org.yaml").people
    if approver not in people:
        raise ShareError(f"unknown approver {approver!r} — not a person in the org")
    pending = _find_pending_share(master, share_id)
    meta, _ = split_frontmatter(pending.read_text())
    space = str(meta.get("space", ""))
    subject = str(meta.get("share-with", ""))
    access = str(meta.get("access", "read"))
    owner = str(meta.get("from", ""))
    validate_space(space)
    validate_subject(subject)
    if access not in ACCESS_LEVELS:
        raise ShareError(f"pending share {share_id!r} has invalid access {access!r}")
    if subject == "everyone" and access != "read":
        raise ShareError("company-wide shares are read-only")
    if not may_decide(people[approver], subject):
        raise ShareError(
            f"{approver!r} may not decide this share — the approver must be "
            "role:admin, the recipient, or a lead of the recipient team")
    org = load_org(master / "_meta/org.yaml")
    rules = load_spaces(master / "_meta/spaces.yaml")
    owner_person = org.people.get(owner)
    if owner_person is None or not can_write_path(f"{space}/x.md", owner_person, rules):
        raise ShareError(f"{owner!r} no longer owns {space!r} — refusing to apply")
    if not _subject_known(subject, org):
        raise ShareError(f"{subject!r} no longer resolves in the org")
    amend_space_rule(master / "_meta/spaces.yaml", space, subject, access)
    archived = master / "_meta/shares/approved" / pending.name
    archived.parent.mkdir(parents=True, exist_ok=True)
    _, fm, body = pending.read_text().split("---\n", 2)
    via_line = f"via: {via}\n" if via else ""
    archived.write_text(
        f"---\n{fm}approved-on: {date}\napproved-by: {approver}\n{via_line}---\n{body}")
    pending.unlink()
    _commit(
        master,
        ["_meta/spaces.yaml",
         archived.relative_to(master).as_posix(),
         pending.relative_to(master).as_posix()],
        f"shares: approve {share_id} -> {subject} on {space}",
        people[approver].name, f"{approver}@brain.local",
    )
    return space


def reject_share(master: Path, share_id: str, reason: str, date: str, approver: str,
                  via: str = "") -> Path:
    if not approver.strip():
        raise ShareError("an approver is required")
    people = load_org(master / "_meta/org.yaml").people
    if approver not in people:
        raise ShareError(f"unknown approver {approver!r} — not a person in the org")
    pending = _find_pending_share(master, share_id)
    meta, _ = split_frontmatter(pending.read_text())
    subject = str(meta.get("share-with", ""))
    if not may_decide(people[approver], subject):
        raise ShareError(
            f"{approver!r} may not decide this share — the approver must be "
            "role:admin, the recipient, or a lead of the recipient team")
    _, fm, body = pending.read_text().split("---\n", 2)
    rejected = master / "_meta/shares/rejected" / pending.name
    rejected.parent.mkdir(parents=True, exist_ok=True)
    via_line = f"via: {via}\n" if via else ""
    rejected.write_text(
        f"---\n{fm}rejected-reason: {reason}\nrejected-on: {date}\n"
        f"rejected-by: {approver}\n{via_line}---\n{body}")
    pending.unlink()
    _commit(
        master,
        [rejected.relative_to(master).as_posix(),
         pending.relative_to(master).as_posix()],
        f"shares: reject {share_id} ({reason})",
        people[approver].name, f"{approver}@brain.local",
    )
    return rejected


def admin_revoke(master: Path, space: str, subject: str, date: str) -> bool:
    """Direct admin revoke — no request file. Doubles as the veto lever for
    the created-clients review list. role:admin remains irremovable."""
    removed = remove_subject_from_rule(master / "_meta/spaces.yaml", space, subject)
    if not removed:
        return False
    audit = master / "_meta/shares/revoked" / f"admin-{date}-{_slug(f'{space}-{subject}')}.md"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(
        f"---\nspace: {space}\nshare-with: {subject}\nrevoked-on: {date}\n"
        "revoked-by: admin\n---\n"
    )
    _commit(master, ["_meta/spaces.yaml", audit.relative_to(master).as_posix()],
            f"shares: admin revoke {subject} from {space}",
            "Brain Shares", "shares@brain.local")
    return True


_DECIDED_WINDOW_DAYS = 30
_DECIDED_CAP = 20


def generate_space_shares_section(master: Path, person_id: str, today: str) -> str | None:
    """Markdown section for the person's Shares.md: their pending share
    requests plus decisions from the last 30 days. None when empty."""
    from datetime import date as _date, timedelta

    base = master / "_meta/shares"

    def _entries(state: str) -> list[dict]:
        d = base / state
        if not d.is_dir():
            return []
        out = []
        for f in sorted(d.glob("*.md")):
            try:
                meta, _ = split_frontmatter(f.read_text())
            except (KeyError, ValueError):
                continue
            if meta and (meta.get("from") or meta.get("owner")) == person_id:
                out.append(meta)
        return out

    cutoff = _date.fromisoformat(today) - timedelta(days=_DECIDED_WINDOW_DAYS)

    def _when(meta: dict, key: str) -> _date | None:
        try:
            return _date.fromisoformat(meta.get(key) or meta.get("created", ""))
        except ValueError:
            return None

    decided: list[tuple[_date, str]] = []
    for state, key, fmt in (
        ("approved", "approved-on",
         "- ✅ `{space}` → {who} ({access}) — approved {d}"),
        ("rejected", "rejected-on",
         "- ❌ `{space}` → {who} ({access}) — rejected {d}: {reason}"),
        ("revoked", "revoked-on",
         "- ↩️ `{space}` → {who} — revoked {d}"),
    ):
        for meta in _entries(state):
            d = _when(meta, key)
            if d is None or d < cutoff:
                continue
            decided.append((d, fmt.format(
                space=meta.get("space", "?"), who=meta.get("share-with", "?"),
                access=meta.get("access", "?"), d=d.isoformat(),
                reason=meta.get("rejected-reason", "no reason recorded"))))
    decided.sort(key=lambda t: t[0], reverse=True)
    decided = decided[:_DECIDED_CAP]

    pending = _entries("pending")
    if not pending and not decided:
        return None

    lines = ["## Space shares", ""]
    if pending:
        lines += [f"- `{m.get('space', '?')}` → {m.get('share-with', '?')} "
                  f"({m.get('access', '?')}) — awaiting approval"
                  for m in pending]
    if decided:
        lines += ["", "### Recently decided", ""] + [line for _, line in decided]
    return "\n".join(lines) + "\n"


def generate_decider_section(master: Path, person_id: str, today: str) -> str | None:
    """Markdown section for Shares.md: pending shares this person may decide
    from their own vault. Admin eligibility is deliberately excluded — admins
    decide master-side and duplicating the whole queue into every admin slice
    would be noise, not signal. None when there is nothing to decide."""
    try:
        org = load_org(master / "_meta/org.yaml")
    except (SchemaError, OSError, yaml.YAMLError):
        # A missing/malformed org.yaml means nothing is decidable, not a
        # crashed compile — same fail-quiet posture as the rest of this
        # module's per-entry handling (sweep_shares, sweep_approvals).
        return None
    person = org.people.get(person_id)
    if person is None:
        return None
    delegated_view = Person(
        id=person.id, name=person.name,
        roles=tuple(r for r in person.roles if r != "admin"),
        teams=person.teams)
    mine = [s for s in list_pending_shares(master)
            if may_decide(delegated_view, str(s.get("share-with", "")))]
    if not mine:
        return None
    lines = [
        "## Awaiting your decision", "",
        "These share requests name you (or a team you lead) as recipient.",
        "Record only a decision your human has explicitly made.", "",
    ]
    for s in mine:
        lines.append(
            f"- `{s['id']}`: `{s.get('space', '?')}` from {s.get('from', '?')} "
            f"→ {s.get('share-with', '?')} ({s.get('access', '?')}), "
            f"requested {s.get('created', '?')}")
    lines += [
        "",
        f"To decide, write `People/{person_id}/Approvals/<share-id>.md`:", "",
        "```", "---", "decision: approve   # or: reject",
        "reason: required when rejecting", f"owner: {person_id}",
        f"created: {today}", "---", "```",
    ]
    return "\n".join(lines) + "\n"
