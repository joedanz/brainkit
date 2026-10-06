"""Route remote-MCP requests to the right person's brain (`brain mcp-router`).

One per company, behind the edge. It verifies the edge's signed identity,
looks the email up in a route table the operator writes (Fleet), and forwards
to that person's ``brain mcp --http`` on loopback. It never opens a vault and
reads nothing under /srv/brain; the vault process re-verifies the identity, so
a wrong route is refused there rather than served.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path

import aiohttp
import yaml
from aiohttp import web

from brain.errors import BrainError
from brain.mcphttp import LOOPBACK, MAX_BODY, http_error
from brain.remoteauth import AuthConfig, AuthRejected, RemoteAuthError, Verifier, normalize_email

log = logging.getLogger("brain.mcprouter")

ROUTES_VERSION = 1
RELOAD_CHECK_S = 1.0
FORWARD_HEADERS = ("Content-Type", "Accept", "User-Agent", "Mcp-Method", "Mcp-Name",
                   "MCP-Protocol-Version", "Mcp-Session-Id")
RETURN_HEADERS = ("Content-Type", "Mcp-Session-Id", "Retry-After")


class RouteTableError(BrainError, ValueError):
    """A route table the router cannot use."""


def load_routes(path: Path) -> dict[str, int]:
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        raise RouteTableError(f"{path}: {e}") from e
    if not isinstance(doc, dict) or doc.get("version") != ROUTES_VERSION:
        raise RouteTableError(f"{path}: expected `version: {ROUTES_VERSION}`")
    routes = doc.get("routes") or {}
    if not isinstance(routes, dict):
        raise RouteTableError(f"{path}: `routes` must map email: port")
    out: dict[str, int] = {}
    for email, port in routes.items():
        if not isinstance(email, str) or "@" not in email:
            raise RouteTableError(f"{path}: bad email {email!r}")
        if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
            raise RouteTableError(f"{path}: {email}: port must be an integer 1024-65535")
        key = normalize_email(email)
        if key in out:
            raise RouteTableError(f"{path}: {email} is listed twice")
        out[key] = port
    return out


class RouteTable:
    """The route table, reloaded when the file changes (checked at most once a
    second). A bad new file is logged and the last good table kept."""

    def __init__(self, path: Path, *, clock: Callable[[], float] = time.monotonic):
        self.path = Path(path)
        self._clock = clock
        self._routes = load_routes(self.path)
        self._mtime = self.path.stat().st_mtime_ns
        self._checked = clock()

    @property
    def count(self) -> int:
        return len(self._routes)

    def _maybe_reload(self) -> None:
        now = self._clock()
        if now - self._checked < RELOAD_CHECK_S:
            return
        self._checked = now
        try:
            mtime = self.path.stat().st_mtime_ns
        except OSError as e:
            log.error("route table unreadable, keeping the last good one: %s", e)
            return
        if mtime == self._mtime:
            return
        self._mtime = mtime
        try:
            self._routes = load_routes(self.path)
            log.info("route table reloaded: %d route(s)", len(self._routes))
        except RouteTableError as e:
            log.error("route table rejected, keeping the last good one: %s", e)

    def get(self, email: str) -> int | None:
        self._maybe_reload()
        return self._routes.get(normalize_email(email))


async def handle_route(request: web.Request) -> web.StreamResponse:
    app = request.app
    if request.method != "POST":
        return http_error(405, "method_not_allowed", "use POST", headers={"Allow": "POST"})
    if request.headers.get("Content-Encoding"):
        return http_error(415, "unsupported_encoding", "compressed bodies are not accepted")
    verifier: Verifier = app["verifier"]
    try:
        ident = await asyncio.to_thread(verifier.verify, request.headers)
    except AuthRejected as e:
        return http_error(e.status, e.code, e.message)
    port = app["routes"].get(ident.email)
    if port is None:
        log.info("no route for %s", ident.email)
        return http_error(403, "no_route",
                          f"No brain is set up for {ident.email}. Ask your admin.")
    try:
        body = await request.read()
    except web.HTTPRequestEntityTooLarge:
        return http_error(413, "too_large", "request body too large")
    names = (*FORWARD_HEADERS, verifier.config.header)
    headers = {h: request.headers[h] for h in names if h in request.headers}
    try:
        async with app["session"].post(f"http://127.0.0.1:{port}/mcp", data=body,
                                       headers=headers) as up:
            payload = await up.read()
            back = {h: up.headers[h] for h in RETURN_HEADERS if h in up.headers}
            return web.Response(status=up.status, body=payload, headers=back)
    except (TimeoutError, aiohttp.ClientError) as e:
        log.warning("upstream %d for %s unavailable: %s", port, ident.email, type(e).__name__)
        return http_error(503, "upstream_unavailable",
                          "this brain is restarting — try again shortly",
                          headers={"Retry-After": "5"})


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "routes": request.app["routes"].count})


def create_router_app(routes: RouteTable, *, verifier: Verifier,
                      upstream_timeout: float = 30.0) -> web.Application:
    from brain.webhook import _security_headers

    app = web.Application(client_max_size=MAX_BODY, middlewares=[_security_headers])
    app["routes"] = routes
    app["verifier"] = verifier

    async def _open(app):
        app["session"] = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=upstream_timeout), auto_decompress=False)

    async def _close(app):
        await app["session"].close()

    app.on_startup.append(_open)
    app.on_cleanup.append(_close)
    app.router.add_route("*", "/mcp", handle_route)
    app.router.add_get("/healthz", handle_health)
    return app


def run_router(routes_path: Path, *, auth: AuthConfig, port: int = 8900,
               host: str = "127.0.0.1") -> int:
    if host not in LOOPBACK:
        raise RemoteAuthError("brain mcp-router binds loopback only — the edge "
                              "(cloudflared) sits in front")
    # Verifier first: a missing [remote] extra must be the error a person
    # sees, not a complaint about the routes file.
    verifier = Verifier(auth)
    table = RouteTable(Path(routes_path))
    verifier.load()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    web.run_app(create_router_app(table, verifier=verifier), host=host, port=port,
                print=None, access_log=None)
    return 0
