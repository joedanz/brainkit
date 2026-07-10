"""External intake over HTTP: signed webhooks into a person's Inbox.

The dashboard server trusts its loopback origin and carries no auth; this
receiver exists to accept traffic from outside (Composio triggers, Fathom,
Zapier, n8n), so every request must authenticate before a byte is parsed.
Two verify modes cover the real sender landscape:

- ``standard-webhooks`` — HMAC-SHA256 over ``{id}.{timestamp}.{raw body}``
  per the Standard Webhooks spec (Composio, Fathom, and a growing adopter
  list), with a replay-tolerance window on the timestamp.
- ``token`` — a shared secret in a header, for senders that can add headers
  but cannot sign (Zapier, Make).

Sources are declared in ``_meta/webhook.yaml`` — server-only, never compiled
into any vault. Secrets are never stored in the file: each source names an
environment variable (the embeddings.py precedent), and a source whose
variable is unset refuses to start rather than serving unauthenticated.

Everything after authentication is the existing hardened door: the payload is
routed to a person (a fixed id, or org.yaml email lookup that refuses unknown
senders) and handed to ``ingest_note``, which owns path construction, write
authorization, frontmatter sanitizing, and the git commit. This module never
decides where bytes land — only who may deliver them.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml
from aiohttp import web

CONFIG_NAME = "webhook.yaml"  # under _meta/
VERIFY_MODES = ("standard-webhooks", "token")
REPLAY_TOLERANCE = 300.0  # seconds; Standard Webhooks / Composio SDK default
_TOKEN_HEADER = "X-Brain-Token"
_MAX_SEEN = 4096  # replay-cache entries before a lazy prune
_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_ENV_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_FIELD_RE = re.compile(r"[A-Za-z0-9_.-]+")


class WebhookConfigError(ValueError):
    """Invalid webhook.yaml content or an unset secret variable.

    Raised at load/startup so a misconfigured source can never serve; callers
    surface it as a handled error, never a traceback.
    """


class VerifyError(ValueError):
    """A request that failed authentication. Reason strings are safe to
    return to the caller — they never echo secrets or payload content."""


@dataclass(frozen=True)
class WebhookSource:
    id: str            # URL path segment: POST /hook/<id>
    verify: str        # "standard-webhooks" | "token"
    secret_env: str    # env var holding the secret; never the secret itself
    person: str = ""   # fixed routing; "" => route by sender email
    email_field: str = "email"   # payload field for sender-email routing
    source: str = ""   # provenance label on the note (default: id)
    body_field: str = "body"     # payload field for the note body (dotted path ok)
    title_field: str = "title"   # payload field for the title (optional in payload)


def _str_field(entry: dict, key: str, sid: str, *, default: str = "",
               pattern: re.Pattern | None = None) -> str:
    value = entry.get(key, default)
    if not isinstance(value, str):
        raise WebhookConfigError(f"source {sid!r}: {key} must be a string")
    if value and pattern and not pattern.fullmatch(value):
        raise WebhookConfigError(f"source {sid!r}: invalid {key} {value!r}")
    return value


def load_webhook_config(path: Path) -> tuple[WebhookSource, ...]:
    """Parse and validate ``_meta/webhook.yaml``. Fail closed: any invalid
    entry rejects the whole file, so a typo can't silently drop a source's
    verification."""
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise WebhookConfigError(f"{CONFIG_NAME}: {e}")
    entries = data.get("sources")
    if not isinstance(entries, list) or not entries:
        raise WebhookConfigError(f"{CONFIG_NAME} must define a non-empty 'sources' list")

    sources: list[WebhookSource] = []
    seen_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise WebhookConfigError(f"sources entry {entry!r}: must be a mapping")
        sid = entry.get("id")
        if not isinstance(sid, str) or not _ID_RE.fullmatch(sid):
            raise WebhookConfigError(
                f"source id {sid!r}: must be lowercase letters/digits/hyphens")
        if sid in seen_ids:
            raise WebhookConfigError(f"duplicate source id {sid!r}")
        seen_ids.add(sid)

        verify = entry.get("verify")
        if verify not in VERIFY_MODES:
            raise WebhookConfigError(
                f"source {sid!r}: verify must be one of {', '.join(VERIFY_MODES)}")
        secret_env = _str_field(entry, "secret_env", sid, pattern=_ENV_RE)
        if not secret_env:
            raise WebhookConfigError(f"source {sid!r}: secret_env is required")

        person = _str_field(entry, "person", sid)
        route = _str_field(entry, "route", sid)
        if bool(person) == bool(route):
            raise WebhookConfigError(
                f"source {sid!r}: set exactly one of person: or route: sender-email")
        if route and route != "sender-email":
            raise WebhookConfigError(
                f"source {sid!r}: unknown route {route!r} (only sender-email)")

        sources.append(WebhookSource(
            id=sid,
            verify=verify,
            secret_env=secret_env,
            person=person,
            email_field=_str_field(entry, "email_field", sid,
                                   default="email", pattern=_FIELD_RE),
            source=_str_field(entry, "source", sid, default=sid),
            body_field=_str_field(entry, "body_field", sid,
                                  default="body", pattern=_FIELD_RE),
            title_field=_str_field(entry, "title_field", sid,
                                   default="title", pattern=_FIELD_RE),
        ))
    return tuple(sources)


# ---- verification -------------------------------------------------------------

def _secret_key(secret: str) -> bytes:
    """Standard Webhooks secrets are commonly shipped as ``whsec_<base64>``;
    accept that form or a plain string."""
    if secret.startswith("whsec_"):
        try:
            return base64.b64decode(secret[len("whsec_"):], validate=True)
        except (ValueError, TypeError):
            raise VerifyError("malformed whsec_ secret")  # operator error, but fail closed
    return secret.encode()


def verify_standard_webhooks(headers, raw: bytes, secret: str, *, now: float,
                             tolerance: float = REPLAY_TOLERANCE) -> str:
    """Verify Standard Webhooks headers over the exact raw body; return the
    message id for replay dedup. Raises VerifyError on any failure."""
    msg_id = headers.get("webhook-id", "")
    ts_raw = headers.get("webhook-timestamp", "")
    sig_header = headers.get("webhook-signature", "")
    if not (msg_id and ts_raw and sig_header):
        raise VerifyError("missing webhook signature headers")
    try:
        ts = int(ts_raw)
    except ValueError:
        raise VerifyError("malformed webhook-timestamp")
    if abs(now - ts) > tolerance:
        raise VerifyError("webhook-timestamp outside tolerance")

    mac = hmac.new(_secret_key(secret), f"{msg_id}.{ts}.".encode() + raw,
                   hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode()
    for candidate in sig_header.split():
        version, _, sig = candidate.partition(",")
        if version == "v1" and hmac.compare_digest(sig, expected):
            return msg_id
    raise VerifyError("signature mismatch")


def verify_token(headers, secret: str) -> None:
    """Shared-secret check for senders that can't sign: ``Authorization:
    Bearer <secret>`` or an ``X-Brain-Token`` header."""
    auth = headers.get("Authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() == "bearer" and hmac.compare_digest(token.strip(), secret):
        return
    if hmac.compare_digest(headers.get(_TOKEN_HEADER, ""), secret):
        return
    raise VerifyError("missing or wrong token")


# ---- request handling ----------------------------------------------------------

def _field(payload: dict, path: str):
    """Walk a dotted path through nested dicts; None when any hop is absent."""
    cur: object = payload
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _replay_check(app: web.Application, key: tuple[str, str], now: float) -> bool:
    """True if this (source, webhook-id) was already ingested and is still in
    window. The cache is marked only after a successful ingest — a failed one
    must stay retryable."""
    seen: dict[tuple[str, str], float] = app["seen"]
    if len(seen) > _MAX_SEEN:
        for k, exp in list(seen.items()):
            if exp < now:
                del seen[k]
    return seen.get(key, 0.0) >= now


def _resolve_person(app: web.Application, source: WebhookSource, payload: dict):
    from brain.schemas import Org

    org: Org = app["org"]
    if source.person:
        return org.people[source.person]  # existence checked at startup
    email = _field(payload, source.email_field)
    if not isinstance(email, str) or not email.strip():
        raise web.HTTPBadRequest(
            reason=f"payload field {source.email_field!r} missing — cannot route")
    person = org.person_by_email(email)
    if person is None:
        raise web.HTTPBadRequest(reason="no person with that email — refusing to ingest")
    return person


async def handle_hook(request: web.Request) -> web.Response:
    from datetime import date

    from brain.ingest import IngestError, ingest_note

    app = request.app
    source: WebhookSource | None = app["sources"].get(request.match_info["source"])
    if source is None:
        raise web.HTTPNotFound(reason="unknown source")
    if request.headers.get("Content-Encoding"):
        # HMAC must cover the exact bytes; a compressed body also sidesteps the
        # size cap on some aiohttp versions (CVE-2026-54278). Refuse both risks.
        raise web.HTTPBadRequest(reason="compressed request bodies are not accepted")

    raw = await request.read()  # client_max_size enforced here -> 413
    now: float = app["clock"]()
    secret: str = app["secrets"][source.id]
    try:
        if source.verify == "standard-webhooks":
            msg_id = verify_standard_webhooks(request.headers, raw, secret, now=now)
        else:
            verify_token(request.headers, secret)
            msg_id = ""
    except VerifyError as e:
        raise web.HTTPUnauthorized(reason=str(e))

    replay_key = (source.id, msg_id)
    if msg_id and _replay_check(app, replay_key, now):
        return web.json_response({"ok": True, "duplicate": True})

    try:
        payload = json.loads(raw)
    except ValueError:
        raise web.HTTPBadRequest(reason="expected a JSON object body")
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(reason="expected a JSON object body")

    person = _resolve_person(app, source, payload)
    body = _field(payload, source.body_field)
    if not isinstance(body, str) or not body.strip():
        raise web.HTTPBadRequest(
            reason=f"payload field {source.body_field!r} missing or empty")
    title = _field(payload, source.title_field)
    if not isinstance(title, str):
        title = ""
    if not title.strip():
        title = next((ln.strip().lstrip("# ").strip()
                      for ln in body.splitlines() if ln.strip()), "")

    created = date.today().isoformat()
    sender = person.email or person.id

    def _ingest():
        return ingest_note(app["master"], person, app["rules"], body,
                           title=title, source=source.source, sender=sender,
                           created=created)
    try:
        result = await asyncio.to_thread(_ingest)
    except IngestError as e:
        raise web.HTTPBadRequest(reason=str(e))

    if msg_id:
        app["seen"][replay_key] = now + 2 * REPLAY_TOLERANCE
    return web.json_response({"ok": True, "rel_path": result.rel_path,
                              "committed": result.committed})


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


@web.middleware
async def _security_headers(request: web.Request, handler):
    response = await handler(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers["Cache-Control"] = "no-store"
    return response


# ---- app assembly --------------------------------------------------------------

def create_webhook_app(master: Path, *, max_body: int = 5 * 1024 * 1024,
                       clock: Callable[[], float] = time.time) -> web.Application:
    """Build the receiver or raise WebhookConfigError — a source that can't
    authenticate (unset secret env, unknown person) refuses to start rather
    than serving open."""
    import os

    from brain.schemas import load_org, load_spaces

    org = load_org(master / "_meta" / "org.yaml")
    rules = load_spaces(master / "_meta" / "spaces.yaml")
    sources = load_webhook_config(master / "_meta" / CONFIG_NAME)

    secrets: dict[str, str] = {}
    for s in sources:
        if s.person and s.person not in org.people:
            raise WebhookConfigError(
                f"source {s.id!r}: person {s.person!r} not in org.yaml")
        secret = os.environ.get(s.secret_env, "")
        if not secret:
            raise WebhookConfigError(
                f"source {s.id!r}: environment variable {s.secret_env} is not set "
                "— refusing to start unauthenticated")
        secrets[s.id] = secret

    app = web.Application(middlewares=[_security_headers], client_max_size=max_body)
    app["master"] = master
    app["org"] = org
    app["rules"] = rules
    app["sources"] = {s.id: s for s in sources}
    app["secrets"] = secrets
    app["clock"] = clock
    app["seen"] = {}  # (source id, webhook-id) -> replay-window expiry

    app.router.add_post("/hook/{source}", handle_hook)
    app.router.add_get("/healthz", handle_health)
    return app


def run_webhook_server(master: Path, *, host: str = "127.0.0.1",
                       port: int = 8766, max_body: int = 5 * 1024 * 1024) -> int:
    import sys

    app = create_webhook_app(master, max_body=max_body)

    if host not in ("127.0.0.1", "::1", "localhost"):
        print(f"WARNING: binding {host} — requests authenticate, but the traffic "
              "is cleartext HTTP; put TLS (a reverse proxy) in front.",
              file=sys.stderr)

    async def _serve() -> None:
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        actual = port
        for sock in getattr(site._server, "sockets", None) or []:
            actual = sock.getsockname()[1]
            break
        # flush: under nohup/systemd stdout is block-buffered, and the banner is
        # the operator's only confirmation of which sources are being served.
        print(f"webhook receiver on http://{host}:{actual}/hook/<source>  (Ctrl-C to stop)")
        for s in app["sources"].values():
            routing = s.person or f"by sender email ({s.email_field})"
            print(f"  /hook/{s.id}  verify={s.verify}  ->  {routing}  source={s.source}",
                  flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()

    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        pass
    return 0
