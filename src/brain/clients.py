"""Self-service client provisioning.

An agent in a compiled slice cannot write Clients/ or _meta/. It drops a
*request* in its own writable People/<pid>/ClientRequests/ space; the
server-side materialize_clients step (run inside brain cycle) provisions a real
Clients/<Name> space, appends an owner-bound grant to _meta/spaces.yaml, and
logs it. This mirrors the promotions seam (draft_into_space -> sweep) but for
space+grant provisioning rather than content promotion.

Fail closed: a name already owned by someone else is refused without revealing
whose it is; the authoritative owner of a request is the <pid> in its path, not
any frontmatter field.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from brain.frontmatter import split_frontmatter
from brain.promotions import _commit, _slug
from brain.resolver import can_write_path, space_of_path
from brain.schemas import Org, load_spaces


class ClientError(ValueError):
    """Invalid client name or malformed client request."""


_UNSAFE = re.compile(r'[\\/"\x00-\x1f]')
_VALID_OWNER = re.compile(r"[A-Za-z0-9._-]+")


def _validate_owner_id(owner_id: str) -> None:
    if not _VALID_OWNER.fullmatch(owner_id):
        raise ClientError(f"invalid owner id {owner_id!r}")


def normalize_client_name(name: str) -> str:
    if _UNSAFE.search(name):
        raise ClientError(f"client name {name!r} contains an illegal character")
    collapsed = " ".join(name.split())
    if not collapsed:
        raise ClientError("client name is empty")
    if collapsed in (".", "..") or collapsed.startswith("."):
        raise ClientError(f"unsafe client name {name!r}")
    return collapsed


CLIENT_REQUESTS_REL = "People/{person_id}/ClientRequests"


def request_client(
    root: Path,
    person_id: str,
    client_name: str,
    body: str,
    created: str,
    source: str = "",
) -> str:
    """Write a client-creation request into the person's own space; return its
    vault-relative path. `root` may be a compiled slice — write-back carries it
    to master, where materialize_clients provisions the Clients/<Name> space."""
    name = normalize_client_name(client_name)
    for field, value in (("source", source), ("created", created)):
        if "\n" in value or "\r" in value:
            raise ClientError(f"{field} must be a single line")
    if not body.strip():
        raise ClientError("empty client request — nothing to capture")

    req_rel = CLIENT_REQUESTS_REL.format(person_id=person_id)
    ancestor = root
    for part in PurePosixPath(req_rel).parts:
        ancestor = ancestor / part
        if ancestor.is_symlink():
            raise ClientError(f"{req_rel} contains a symlink — refusing to write")

    dir_ = root / req_rel
    base = _slug(name) or "client"
    fname = f"{created}-{base}.md"
    n = 2
    while (dir_ / fname).exists() or (dir_ / fname).is_symlink():
        fname = f"{created}-{base}-{n}.md"
        n += 1
    rel_path = f"{req_rel}/{fname}"
    if space_of_path(rel_path) != f"People/{person_id}":
        raise ClientError(f"refusing to write outside {req_rel}")

    dest = root / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        "---\n"
        f"client-name: {name}\n"
        f"owner: {person_id}\n"
        "entity: client\n"
        f"source: {source}\n"
        f"created: {created}\n"
        "---\n"
        f"{body}"
    )
    return rel_path


def append_client_grant(spaces_path: Path, client_name: str, owner_id: str) -> bool:
    """Append an exact owner-bound Clients/<Name> rule. Text append preserves
    the file's comments (yaml round-tripping would strip them). Idempotent: a
    pre-existing rule for the same path returns False and appends nothing."""
    name = normalize_client_name(client_name)
    _validate_owner_id(owner_id)
    space = f"Clients/{name}"
    if any(r.path == space for r in load_spaces(spaces_path)):
        return False
    subjects = f'["role:admin", "person:{owner_id}"]'
    line = f'  - {{path: "{space}", read: {subjects}, write: {subjects}}}\n'
    text = spaces_path.read_text()
    if not text.endswith("\n"):
        text += "\n"
    spaces_path.write_text(text + line)
    return True


@dataclass(frozen=True)
class ClientProvision:
    name: str
    owner: str
    status: str  # "created" | "merged" | "rejected"
    reason: str = ""


def _created_log(master: Path) -> Path:
    return master / "_meta/clients/created.log"


def _seed_note(body: str, owner: str, source: str, date: str) -> str:
    return (
        "---\n"
        "entity: client\n"
        f"created-by: {owner}\n"
        f"owner: {owner}\n"
        f"source: {source}\n"
        f"date: {date}\n"
        "---\n"
        f"{body}"
    )


def _inbox_note(master: Path, person_id: str, name: str, slug: str, today: str) -> str:
    rel = f"People/{person_id}/Inbox/client-taken-{slug}.md"
    dest = master / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        f"---\ncreated: {today}\n---\n"
        f"A client named **{name}** already exists. Add a distinguishing "
        "detail (first name, company, or city) and try again, or ask an admin "
        "if you believe it should be shared with you.\n"
    )
    return rel


def materialize_clients(master: Path, org: Org, today: str) -> list[ClientProvision]:
    """Provision one Clients/<Name> space per pending request, server-side.

    The authoritative owner is the <pid> segment of the request path (writeback
    already gated that write). Runs inside brain cycle, after writeback and
    before the rules reload + compile so a freshly granted space appears this
    cycle.
    """
    results: list[ClientProvision] = []
    for req in sorted(master.glob("People/*/ClientRequests/*.md")):
        if req.is_symlink():
            continue
        rel = req.relative_to(master)
        person_id = rel.parts[1]
        try:
            _validate_owner_id(person_id)
        except ClientError:
            continue  # malformed pid: leave the request in place, touch nothing
        try:
            meta, body = split_frontmatter(req.read_text())
            if not meta:
                continue
            try:
                name = normalize_client_name(meta.get("client-name", ""))
            except ClientError:
                continue  # malformed request left in place for inspection
            source = meta.get("source", str(rel))
            person = org.people.get(person_id)
            name_id = person.name if person else person_id

            space = f"Clients/{name}"
            note_rel = f"{space}/{name}.md"
            note = master / note_rel
            slug = req.stem

            # (1) owner binding: path <pid> is authoritative; a disagreeing
            # frontmatter owner is tampering.
            if meta.get("owner", person_id) != person_id:
                req.unlink()
                _commit(master, [rel.as_posix()],
                        f"clients: reject {space} (owner mismatch)",
                        name_id, f"{person_id}@brain.local")
                results.append(ClientProvision(name, person_id, "rejected", "owner mismatch"))
                continue

            rules = load_spaces(master / "_meta/spaces.yaml")
            exact = next((r for r in rules if r.path == space), None)
            folder = (master / space).is_dir()

            if exact is not None and person is not None and can_write_path(f"{space}/x.md", person, rules):
                # (2) already this owner's client -> merge, no new grant
                note.parent.mkdir(parents=True, exist_ok=True)
                current = note.read_text() if note.is_file() else ""
                merged = (current.rstrip("\n") + "\n\n---\n\n" + body.strip() + "\n") if current else _seed_note(body, person_id, source, today)
                note.write_text(merged)
                req.unlink()
                _commit(master, [note_rel, rel.as_posix()],
                        f"clients: merge into {space} for {person_id}",
                        name_id, f"{person_id}@brain.local")
                results.append(ClientProvision(name, person_id, "merged"))
                continue

            if exact is not None or folder:
                # (3) name taken by someone else -> refuse, notify, never reveal owner
                inbox_rel = _inbox_note(master, person_id, name, slug, today)
                req.unlink()
                _commit(master, [inbox_rel, rel.as_posix()],
                        f"clients: reject {space} (name taken)",
                        name_id, f"{person_id}@brain.local")
                results.append(ClientProvision(name, person_id, "rejected", "name taken"))
                continue

            # (4) create
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_text(_seed_note(body, person_id, source, today))
            append_client_grant(master / "_meta/spaces.yaml", name, person_id)
            log = _created_log(master)
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a") as fh:
                fh.write(f"{today}\t{person_id}\t{name}\t{slug}\n")
            req.unlink()
            _commit(
                master,
                [note_rel, "_meta/spaces.yaml",
                 _created_log(master).relative_to(master).as_posix(),
                 rel.as_posix()],
                f"clients: create {space} for {person_id}",
                name_id, f"{person_id}@brain.local",
            )
            results.append(ClientProvision(name, person_id, "created"))
        except Exception:
            continue  # unexpected per-request error: leave file in place, touch nothing
    return results
