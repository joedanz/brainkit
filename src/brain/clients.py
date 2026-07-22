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
