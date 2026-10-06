"""Remote MCP over HTTP for ONE person's compiled vault (`brain mcp --http`).

One process per person, bound to loopback, behind ``brain mcp-router`` and the
edge (Cloudflare Access). Every request must carry the edge's signed identity
and that identity must be this vault's owner — the router already checked, and
this re-check means a routing mistake is refused, never served. The MCP
methods themselves are ``brain.mcp._handle`` unchanged; this module is only
transport, identity and the ``brain_capture`` tool.

The vault directory is replaced on every compile, so nothing here holds a path
handle between requests.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from aiohttp import web

from brain.mcp import _TOOLS, _error, _handle, _result, _text_result
from brain.remoteauth import (
    AuthConfig,
    AuthRejected,
    RemoteAuthError,
    Verifier,
    normalize_email,
)

log = logging.getLogger("brain.mcphttp")

MAX_BODY = 1024 * 1024
RATE_PER_S = 20.0
BURST = 40.0
CAPTURES_PER_HOUR = 60
LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})

CAPTURE_TOOL = {
    "name": "brain_capture",
    "description": "Save a note into your brain's Inbox — something worth keeping from this "
                   "conversation. It appears in your brain after the next sync (usually a few "
                   "minutes); your assistant files it from the Inbox.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "the note, in markdown"},
            "title": {"type": "string", "description": "optional one-line title"},
        },
        "required": ["text"],
    },
}


def http_error(status: int, code: str, message: str, *,
               headers: dict | None = None) -> web.Response:
    return web.json_response({"error": code, "message": message}, status=status,
                             headers=headers)


def check_vault_owner(vault: Path, person: str) -> None:
    """The manifest is a tracked file an agent can push, so it is trusted
    only together with the folder name (compiled vaults live at <out>/<id>) —
    the same rule as server._corrections_person."""
    from brain.writeback import ManifestError, _load_manifest

    try:
        pid = _load_manifest(vault).get("person", "")
    except (ManifestError, OSError) as e:
        raise RemoteAuthError(f"{vault} is not a compiled vault: {e}") from e
    if pid != person:
        raise RemoteAuthError(f"this vault belongs to {pid!r}, not {person!r}")
    if vault.resolve().name != person:
        raise RemoteAuthError(f"the vault folder must be named after its person ({person})")


def check_spool(spool: Path) -> None:
    if spool.is_symlink() or not spool.is_dir():
        raise RemoteAuthError(f"spool {spool} is not a directory")
    if spool.stat().st_uid != os.getuid():
        raise RemoteAuthError(f"spool {spool} is not owned by this user")


def _take(bucket: dict, now: float) -> int:
    """Token bucket; 0 admits, else whole seconds until the next slot."""
    tokens = min(BURST, bucket["tokens"] + (now - bucket["at"]) * RATE_PER_S)
    bucket["at"] = now
    if tokens >= 1.0:
        bucket["tokens"] = tokens - 1.0
        return 0
    bucket["tokens"] = tokens
    return max(1, math.ceil((1.0 - tokens) / RATE_PER_S))


def _capture(app: web.Application, args: dict) -> tuple[str, bool]:
    from brain.spool import SpoolError, write_envelope

    with app["capture_lock"]:
        now = app["clock"]()
        recent: deque = app["captures"]
        while recent and now - recent[0] > 3600:
            recent.popleft()
        if len(recent) >= CAPTURES_PER_HOUR:
            return (f"capture limit reached ({CAPTURES_PER_HOUR} per hour) — try again later",
                    True)
        try:
            write_envelope(app["spool"], app["person"], text=args.get("text"),
                           title=args.get("title") or "", now=app["now"]())
        except SpoolError as e:
            return str(e), True
        recent.append(now)
    saved = ("Saved. It will appear in your Inbox after the next sync "
             "(usually within a few minutes).")
    return saved, False


def _dispatch(app: web.Application, msg) -> dict | None:
    if not isinstance(msg, dict):
        return _error(None, -32600, "invalid request")
    method, mid = msg.get("method"), msg.get("id")
    params = msg.get("params") or {}
    if app["spool"] is not None:
        if method == "tools/list":
            return _result(mid, {"tools": [*_TOOLS, CAPTURE_TOOL]})
        if method == "tools/call" and params.get("name") == "brain_capture":
            args = params.get("arguments") or {}
            if not isinstance(args.get("text"), str):
                return _error(mid, -32602, "brain_capture: missing required argument(s): text")
            text, is_err = _capture(app, args)
            return _text_result(mid, text, is_err)
    from brain.writeback import vault_shared

    vault: Path = app["vault"]
    return _handle(vault, app["provider"], msg, vault_shared(vault))


def _describe(msgs: list) -> str:
    parts = []
    for m in msgs:
        if not isinstance(m, dict):
            parts.append("?")
        elif m.get("method") == "tools/call":
            parts.append(f"tools/call:{(m.get('params') or {}).get('name')}")
        else:
            parts.append(str(m.get("method")))
    return ",".join(parts)


async def handle_mcp(request: web.Request) -> web.StreamResponse:
    app = request.app
    if request.method != "POST":
        return http_error(405, "method_not_allowed", "use POST", headers={"Allow": "POST"})
    if request.headers.get("Content-Encoding"):
        return http_error(415, "unsupported_encoding", "compressed bodies are not accepted")
    try:
        ident = await asyncio.to_thread(app["verifier"].verify, request.headers)
    except AuthRejected as e:
        log.info("refused %s: %s", e.code, e.message)
        return http_error(e.status, e.code, e.message)
    if ident.email != app["email"]:
        log.warning("routing error: %s reached %s's brain", ident.email, app["person"])
        return http_error(403, "wrong_person", "this brain belongs to someone else")
    wait = _take(app["bucket"], app["clock"]())
    if wait:
        return http_error(429, "rate_limited", "too many requests",
                          headers={"Retry-After": str(wait)})
    try:
        raw = await request.read()
    except web.HTTPRequestEntityTooLarge:
        return http_error(413, "too_large", "request body too large")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return web.json_response(_error(None, -32700, "parse error"), status=400)
    batch = isinstance(payload, list)
    msgs = payload if batch else [payload]
    if batch and not msgs:
        return web.json_response(_error(None, -32600, "empty batch"), status=400)
    started = time.perf_counter()
    out = []
    for m in msgs:
        r = await asyncio.to_thread(_dispatch, app, m)
        if r is not None:
            out.append(r)
    log.info("%s %s %dms", ident.email, _describe(msgs),
             (time.perf_counter() - started) * 1000)
    if not out:
        return web.Response(status=202)
    return web.json_response(out if batch else out[0])


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


def create_vault_app(vault: Path, *, person: str, email: str, verifier: Verifier,
                     spool: Path | None = None, provider=None,
                     clock: Callable[[], float] = time.monotonic,
                     now: Callable[[], datetime] = lambda: datetime.now(UTC)
                     ) -> web.Application:
    from brain.webhook import _security_headers

    app = web.Application(client_max_size=MAX_BODY, middlewares=[_security_headers])
    app["vault"] = Path(vault)
    app["person"] = person
    app["email"] = normalize_email(email)
    app["verifier"] = verifier
    app["spool"] = spool
    app["provider"] = provider
    app["clock"] = clock
    app["now"] = now
    app["bucket"] = {"tokens": BURST, "at": clock()}
    app["captures"] = deque()
    app["capture_lock"] = threading.Lock()
    app.router.add_route("*", "/mcp", handle_mcp)
    app.router.add_get("/healthz", handle_health)
    return app


def run_vault_server(vault: Path, *, person: str, email: str, auth: AuthConfig, port: int,
                     host: str = "127.0.0.1", spool: Path | None = None) -> int:
    if host not in LOOPBACK:
        raise RemoteAuthError("brain mcp --http binds loopback only — the edge "
                              "(cloudflared) and brain mcp-router sit in front")
    vault = Path(vault)
    check_vault_owner(vault, person)
    if spool is not None:
        check_spool(spool)
    verifier = Verifier(auth)
    verifier.load()
    from brain.embeddings import provider_from_config

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    app = create_vault_app(vault, person=person, email=email, verifier=verifier,
                           spool=spool, provider=provider_from_config())
    web.run_app(app, host=host, port=port, print=None, access_log=None)
    return 0
