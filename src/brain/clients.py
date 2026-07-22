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
from pathlib import Path, PurePosixPath

from brain.promotions import _slug
from brain.resolver import space_of_path
from brain.schemas import load_spaces


class ClientError(ValueError):
    """Invalid client name or malformed client request."""


_UNSAFE = re.compile(r'[\\/"\x00-\x1f]')


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
