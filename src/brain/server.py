"""The live dashboard: a localhost aiohttp app over the same stats the static
dashboard renders, pushed over a WebSocket as the brain changes.

Design constraints inherited from the rest of brainkit:

* **Read-only.** Every handler observes; none mutates a vault or its index.
  Stats/search/graph run in a worker thread (`asyncio.to_thread`) because the
  collectors do blocking sqlite + subprocess work.
* **Lens-scoped.** The user lens serves exactly one compiled vault — by
  construction only what that person may read. The admin lens serves master
  overview data, and (for the graph/query tabs) a *named* person's compiled
  vault under ``out_root``; the person id is validated against the org roster so
  a crafted ``?person=`` can't escape into an arbitrary directory.
* **Localhost by default.** Binds 127.0.0.1; a `host_guard` middleware rejects
  cross-origin ``Host``/``Origin`` headers (DNS-rebinding defense) whenever the
  bind is loopback, and a strict CSP keeps the page from talking to anything but
  its own origin. There is no auth — exposing a non-loopback host prints a loud
  warning.

No HTML is ever built from data server-side: handlers return JSON and the
browser builds DOM via ``textContent``, so untrusted note titles never become
markup — the same rule the static dashboard follows.
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path

from aiohttp import WSMsgType, web
from yarl import URL

from brain.watch import Lens, fingerprint

_log = logging.getLogger("brain.server")

# Serve fonts with a real type so the global nosniff header can't make a browser
# reject a woff2 delivered as application/octet-stream.
mimetypes.add_type("font/woff2", ".woff2")

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_MAX_GRAPH_CAP = 2000
_DEFAULT_GRAPH_CAP = 300
_SEND_TIMEOUT = 10.0  # seconds a single WS send may take before we drop the socket


def assets_dir() -> Path:
    return Path(__file__).parent / "assets"


# ---- lens-scoped vault resolution -------------------------------------------

def _org_people(master: Path) -> dict[str, str]:
    """person id -> display name, from the master org roster."""
    from brain.schemas import load_org

    org = load_org(master / "_meta" / "org.yaml")
    return {p.id: p.name for p in org.people.values()}


def _target_vault(app: web.Application, request: web.Request) -> Path:
    """The compiled vault a graph/search/note/notes request should read.

    Raises HTTP 400/404 for a mis-scoped or unknown target — never returns a
    path outside the lens.
    """
    lens: Lens = app["lens"]
    person = request.query.get("person")
    if lens.kind == "vault":
        if person:
            raise web.HTTPBadRequest(reason="person is not valid for the user lens")
        return Path(lens.vault)
    # master lens
    if not person:
        raise web.HTTPBadRequest(reason="person is required for the admin lens")
    if lens.out_root is None:
        raise web.HTTPNotFound(reason="no compiled root (rerun with --out)")
    if person not in app["people"]:
        raise web.HTTPNotFound(reason=f"unknown person: {person}")
    vault = Path(lens.out_root) / person
    if not vault.is_dir():
        raise web.HTTPNotFound(reason=f"{person} is not compiled")
    return vault


# ---- stats collection (in a worker thread) ----------------------------------

def _collect_stats(lens: Lens) -> dict:
    from brain.stats import collect_master_stats, collect_vault_stats

    if lens.kind == "vault":
        return asdict(collect_vault_stats(Path(lens.vault), include_graph=False))
    out_root = Path(lens.out_root) if lens.out_root else None
    return asdict(collect_master_stats(Path(lens.master), out_root))


async def _stats_json(app: web.Application) -> dict:
    return await asyncio.to_thread(_collect_stats, app["lens"])


# ---- HTTP handlers -----------------------------------------------------------

async def handle_index(request: web.Request) -> web.Response:
    index = assets_dir() / "index.html"
    if not index.is_file():
        raise web.HTTPNotFound(reason="dashboard assets not installed")
    return web.Response(
        text=index.read_text(encoding="utf-8"),
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


async def handle_meta(request: web.Request) -> web.Response:
    app = request.app
    lens: Lens = app["lens"]
    vector_search = app["provider"] is not None
    if lens.kind == "vault":
        from brain.writeback import ManifestError, _load_manifest

        person = ""
        with suppress(ManifestError, OSError):
            person = _load_manifest(Path(lens.vault)).get("person", "")
        body = {
            "kind": "vault",
            "title": f"{person or Path(lens.vault).name}'s vault",
            "person": person,
            "vector_search": vector_search,
        }
    else:
        people = [{"id": pid, "name": name} for pid, name in sorted(app["people"].items())]
        body = {
            "kind": "master",
            "title": "company brain",
            "people": people,
            "has_compiled": lens.out_root is not None,
            "vector_search": vector_search,
        }
    return web.json_response(body)


async def handle_stats(request: web.Request) -> web.Response:
    try:
        data = await _stats_json(request.app)
    except Exception as e:  # a bad master meta / uncompiled vault is a 500, with reason
        raise web.HTTPInternalServerError(reason=str(e)) from e
    return web.json_response(data)


async def handle_graph(request: web.Request) -> web.Response:
    from brain.stats import _build_graph, ro_connect

    vault = _target_vault(request.app, request)
    try:
        cap = int(request.query.get("cap", _DEFAULT_GRAPH_CAP))
    except ValueError:
        cap = _DEFAULT_GRAPH_CAP
    if cap <= 0:
        cap = _DEFAULT_GRAPH_CAP
    cap = min(cap, _MAX_GRAPH_CAP)

    def _graph() -> dict:
        db = vault / ".brain" / "index.db"
        if not db.is_file():
            return {"nodes": [], "edges": [], "truncated": False}
        conn = ro_connect(db)
        try:
            return asdict(_build_graph(conn, cap))
        finally:
            conn.close()

    return web.json_response(await asyncio.to_thread(_graph))


async def handle_search(request: web.Request) -> web.Response:
    from brain.search import search_index

    vault = _target_vault(request.app, request)
    query = request.query.get("q", "").strip()
    if not query:
        return web.json_response({"query": "", "mode": "", "hits": []})
    try:
        k = int(request.query.get("k", 25))
    except ValueError:
        k = 25
    k = max(1, min(k, 100))
    keyword_only = request.query.get("keyword_only") in ("1", "true", "yes")
    center = request.query.get("center") or None
    provider = request.app["provider"]

    def _search() -> dict:
        report = search_index(vault, query, k=k, provider=provider,
                              keyword_only=keyword_only, center=center)
        return {
            "query": report.query,
            "mode": report.mode,
            "warnings": report.warnings,
            "hits": [asdict(h) for h in report.hits],
        }

    try:
        return web.json_response(await asyncio.to_thread(_search))
    except Exception as e:  # newer on-disk schema, a locked index, etc.
        raise web.HTTPInternalServerError(reason=str(e)) from e


async def handle_facts(request: web.Request) -> web.Response:
    from brain.facts import query_facts

    vault = _target_vault(request.app, request)
    q = request.query
    include_ended = q.get("include_ended") in ("1", "true", "yes")

    def _facts() -> dict:
        # query_facts never raises for a missing/pre-v3 index — it returns the
        # problem as a warning, so those degrade to 200 + warning, never a 500.
        hits, warnings = query_facts(
            vault,
            entity=q.get("entity") or None,
            etype=q.get("type") or None,
            as_of=q.get("as_of") or None,
            include_ended=include_ended,
        )
        return {"hits": [asdict(h) for h in hits], "warnings": warnings}

    try:
        return web.json_response(await asyncio.to_thread(_facts))
    except Exception as e:  # a corrupt index file, etc.
        raise web.HTTPInternalServerError(reason=str(e)) from e


async def handle_notes(request: web.Request) -> web.Response:
    from brain.filters import list_notes

    vault = _target_vault(request.app, request)
    q = request.query
    try:
        limit = int(q.get("limit", 200))
    except ValueError:
        limit = 200
    limit = max(1, min(limit, 1000))

    def _list() -> list[dict]:
        rows = list_notes(
            vault,
            space=q.get("space") or None,
            path_contains=q.get("contains") or None,
            unresolved_only=q.get("unresolved") in ("1", "true", "yes"),
            pending_only=q.get("pending") in ("1", "true", "yes"),
            modified_after=q.get("after") or None,
            limit=limit,
        )
        return [asdict(r) for r in rows]

    return web.json_response({"notes": await asyncio.to_thread(_list)})


def _lens_person(app: web.Application, request: web.Request) -> str:
    """The person id whose Inbox/Actions a request targets: the manifest person
    under the vault lens, or the validated ``?person=`` under the master lens."""
    lens: Lens = app["lens"]
    if lens.kind == "vault":
        from brain.writeback import ManifestError, _load_manifest

        with suppress(ManifestError, OSError):
            return _load_manifest(Path(lens.vault)).get("person", "")
        return ""
    return request.query.get("person", "")


async def handle_note(request: web.Request) -> web.Response:
    from brain.filters import note_links
    from brain.notes import NoteAccessError, read_note

    vault = _target_vault(request.app, request)
    rel_path = request.query.get("path", "")
    try:
        text = await asyncio.to_thread(read_note, vault, rel_path)
    except NoteAccessError as e:
        raise web.HTTPForbidden(reason=str(e)) from e
    except OSError:
        # Deleted between read_note's is_file() check and read_text (e.g. a
        # concurrent brain cycle rewrote the slice). Not a server fault.
        raise web.HTTPNotFound(reason="note not found") from None
    links = await asyncio.to_thread(note_links, vault, rel_path)
    return web.json_response({"path": rel_path, "text": text, "links": asdict(links)})


async def handle_inbox(request: web.Request) -> web.Response:
    from brain.filters import list_inbox

    vault = _target_vault(request.app, request)
    person = _lens_person(request.app, request)

    def _list() -> list[dict]:
        return [asdict(x) for x in list_inbox(vault, person)]

    return web.json_response({"notes": await asyncio.to_thread(_list)})


async def handle_actions(request: web.Request) -> web.Response:
    from brain.filters import list_actions

    vault = _target_vault(request.app, request)
    person = _lens_person(request.app, request)

    def _list() -> list[dict]:
        return [asdict(x) for x in list_actions(vault, person)]

    return web.json_response({"actions": await asyncio.to_thread(_list)})


# ---- write endpoints (POST; guarded by the non-GET Origin+JSON middleware) ---

async def _json_body(request: web.Request) -> dict:
    try:
        data = await request.json()
    except (json.JSONDecodeError, ValueError):
        raise web.HTTPBadRequest(reason="expected a JSON object body") from None
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(reason="expected a JSON object body")
    return data


def _today() -> str:
    from datetime import date

    return date.today().isoformat()


async def handle_capture(request: web.Request) -> web.Response:
    """Capture a note into a person's Inbox.

    Vault lens: write into the slice's own ``People/<pid>/Inbox/`` — the person's
    own writable space — which write-back carries to master on the next cycle
    (the compiled slice has no master/org/rules to ingest directly). Master lens:
    call the hardened ``ingest_note`` primitive for a named person and commit now.
    """
    from brain.ingest import IngestError, build_inbox_note, ingest_note

    lens: Lens = request.app["lens"]
    data = await _json_body(request)
    body = (data.get("body") or "")
    title = (data.get("title") or "").strip()
    source = (data.get("source") or "dashboard").strip() or "dashboard"
    if not body.strip():
        raise web.HTTPBadRequest(reason="empty note — nothing to capture")
    created = _today()

    if lens.kind == "vault":
        person = _lens_person(request.app, request)
        if not person:
            raise web.HTTPBadRequest(reason="this vault has no person manifest")

        def _write() -> str:
            built = build_inbox_note(Path(lens.vault), person, body,
                                     title=title, source=source, sender="", created=created)
            target = Path(lens.vault) / built.rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(built.text)
            return built.rel_path

        try:
            rel = await asyncio.to_thread(_write)
        except IngestError as e:
            raise web.HTTPBadRequest(reason=str(e)) from e
        return web.json_response({"rel_path": rel, "committed": False,
                                  "note": ("captured to your Inbox — it appears "
                                           "after the next sync")})

    # master lens: direct ingest into a named, validated person
    person_id = data.get("person") or request.query.get("person")
    if not person_id or person_id not in request.app["people"]:
        raise web.HTTPNotFound(reason="unknown or missing person")

    def _ingest() -> tuple[str, bool]:
        from brain.schemas import load_org, load_spaces

        master = Path(lens.master)
        org = load_org(master / "_meta" / "org.yaml")
        rules = load_spaces(master / "_meta" / "spaces.yaml")
        res = ingest_note(master, org.people[person_id], rules, body,
                          title=title, source=source, sender="", created=created)
        return res.rel_path, res.committed

    try:
        rel, committed = await asyncio.to_thread(_ingest)
    except IngestError as e:
        raise web.HTTPBadRequest(reason=str(e)) from e
    return web.json_response({"rel_path": rel, "committed": committed})


async def handle_promote(request: web.Request) -> web.Response:
    """Draft a promotion into the person's own ``Promotions/`` space.

    The employee-side half of sharing: writes a draft the admin later approves.
    Vault lens targets the slice (write-back + the next cycle's sweep carry it to
    the master queue); master lens writes into master for a named person."""
    from brain.promotions import PromotionError, draft_into_space

    lens: Lens = request.app["lens"]
    data = await _json_body(request)
    target = (data.get("target_path") or "").strip()
    body = data.get("body") or ""
    source = (data.get("source") or "dashboard").strip() or "dashboard"
    if not target:
        raise web.HTTPBadRequest(reason="a target path is required")

    if lens.kind == "vault":
        root = Path(lens.vault)
        person = _lens_person(request.app, request)
        if not person:
            raise web.HTTPBadRequest(reason="this vault has no person manifest")
    else:
        person = data.get("person") or request.query.get("person")
        if not person or person not in request.app["people"]:
            raise web.HTTPNotFound(reason="unknown or missing person")
        root = Path(lens.master)

    def _draft() -> str:
        return draft_into_space(root, person, target, source, body, _today())

    try:
        rel = await asyncio.to_thread(_draft)
    except PromotionError as e:
        raise web.HTTPBadRequest(reason=str(e)) from e
    return web.json_response({"rel_path": rel,
                              "note": "drafted — an admin approves it from the Promotions tab"})


def _require_master(request: web.Request) -> Path:
    lens: Lens = request.app["lens"]
    if lens.kind != "master":
        raise web.HTTPForbidden(reason="promotions require the admin (master) lens")
    return Path(lens.master)


async def handle_promotion(request: web.Request) -> web.Response:
    """The full body/source/target of one pending promotion, so a reviewer can
    read what they're about to make visible before approving."""
    from brain.promotions import list_pending, patch_diff

    master = _require_master(request)
    promo_id = request.query.get("id", "")

    def _find() -> dict | None:
        for p in list_pending(master):
            if p.id == promo_id:
                d = asdict(p)
                d["diff"] = patch_diff(master, p)
                return d
        return None

    found = await asyncio.to_thread(_find)
    if found is None:
        raise web.HTTPNotFound(reason=f"no pending promotion {promo_id!r}")
    return web.json_response(found)


async def handle_promotion_action(request: web.Request) -> web.Response:
    from brain.promotions import PromotionError, approve, reject

    master = _require_master(request)
    promo_id = request.match_info["id"]
    action = request.match_info["action"]
    data = await _json_body(request)

    def _do() -> None:
        if action == "approve":
            approver = str(data.get("approver") or "").strip()
            if approver not in request.app["people"]:
                raise web.HTTPBadRequest(reason="approver must be a person in the org")
            approve(master, promo_id, approver, _today())
        elif action == "reject":
            reason = str(data.get("reason") or "").strip()
            if not reason:
                raise web.HTTPBadRequest(reason="a rejection reason is required")
            reject(master, promo_id, reason, _today())
        else:
            raise web.HTTPNotFound(reason=f"unknown action {action!r}")

    try:
        await asyncio.to_thread(_do)
    except PromotionError as e:
        raise web.HTTPNotFound(reason=str(e)) from e
    return web.json_response({"ok": True, "id": promo_id, "action": action})


async def handle_share_action(request: web.Request) -> web.Response:
    from brain.shares import ShareError, approve_share, reject_share

    master = _require_master(request)
    share_id = request.match_info["id"]
    action = request.match_info["action"]
    data = await _json_body(request)

    def _do() -> None:
        if action == "approve":
            approver = str(data.get("approver") or "").strip()
            if approver not in request.app["people"]:
                raise web.HTTPBadRequest(reason="approver must be a person in the org")
            approve_share(master, share_id, approver, _today())
        elif action == "reject":
            reason = str(data.get("reason") or "").strip()
            if not reason:
                raise web.HTTPBadRequest(reason="a rejection reason is required")
            approver = str(data.get("approver") or "").strip()
            if approver not in request.app["people"]:
                raise web.HTTPBadRequest(reason="approver must be a person in the org")
            reject_share(master, share_id, reason, _today(), approver)
        else:
            raise web.HTTPNotFound(reason=f"unknown action {action!r}")

    try:
        await asyncio.to_thread(_do)
    except ShareError as e:
        raise web.HTTPNotFound(reason=str(e)) from e
    return web.json_response({"ok": True, "id": share_id, "action": action})


async def handle_promotion_sweep(request: web.Request) -> web.Response:
    from brain.promotions import sweep

    master = _require_master(request)

    def _sweep() -> int:
        return len(sweep(master, _today()))

    moved = await asyncio.to_thread(_sweep)
    return web.json_response({"ok": True, "moved": moved})


# ---- WebSocket + live push ---------------------------------------------------

async def _send_stats(ws: web.WebSocketResponse, app: web.Application, reason: str) -> None:
    try:
        data = await _stats_json(app)
    except Exception as e:
        await ws.send_json({"type": "error", "message": str(e)})
        return
    await ws.send_json({"type": "stats", "reason": reason, "data": data})


async def handle_ws(request: web.Request) -> web.WebSocketResponse:
    if not _origin_ok(request):
        raise web.HTTPForbidden(reason="cross-origin websocket refused")
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    app = request.app
    app["websockets"].add(ws)
    try:
        await _send_stats(ws, app, "initial")
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                with suppress(json.JSONDecodeError):
                    if json.loads(msg.data).get("type") == "refresh":
                        await _send_stats(ws, app, "refresh")
    finally:
        app["websockets"].discard(ws)
    return ws


def _change_reason(old, new) -> str:
    if old is None or old.git_heads != new.git_heads:
        return "git"
    if old.index_mtimes != new.index_mtimes:
        return "index"
    if old.promo_mtime != new.promo_mtime:
        return "promotions"
    return "refresh"


async def check_and_broadcast(app: web.Application) -> bool:
    """Poll the fingerprint; if it changed, push fresh stats to every socket.

    Returns True when a broadcast happened. Module-level and awaitable so tests
    can drive one tick deterministically instead of racing the poll loop.
    """
    new = await asyncio.to_thread(fingerprint, app["lens"])
    old = app["state"].get("fingerprint")
    if new == old:
        return False
    reason = _change_reason(old, new)
    app["state"]["fingerprint"] = new
    if app["websockets"]:
        try:
            data = await _stats_json(app)
            payload = {"type": "stats", "reason": reason, "data": data}
        except Exception as e:
            _log.warning("stats collection failed during broadcast: %s", e)
            payload = {"type": "error", "message": str(e)}
        await _broadcast(app, payload)
    return True


async def _safe_send(app: web.Application, ws: web.WebSocketResponse, payload: dict) -> None:
    """Send to one socket with a bound timeout; drop a socket that stalls so a
    single slow consumer can't wedge the poll loop for every other client."""
    try:
        await asyncio.wait_for(ws.send_json(payload), timeout=_SEND_TIMEOUT)
    except (TimeoutError, ConnectionError, RuntimeError):
        app["websockets"].discard(ws)
        with suppress(Exception):
            await ws.close(code=1011, message=b"send timeout")


async def _broadcast(app: web.Application, payload: dict) -> None:
    await asyncio.gather(
        *(_safe_send(app, ws, payload) for ws in set(app["websockets"])),
        return_exceptions=True,
    )


async def _watcher_ctx(app: web.Application):
    app["state"]["fingerprint"] = await asyncio.to_thread(fingerprint, app["lens"])
    poll = app["poll_interval"]

    async def _loop():
        while True:
            await asyncio.sleep(poll)
            with suppress(Exception):
                await check_and_broadcast(app)

    task = asyncio.create_task(_loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        for ws in set(app["websockets"]):
            with suppress(Exception):
                await ws.close(code=1001, message=b"server shutting down")


# ---- security middleware -----------------------------------------------------

def _hostname(value: str) -> str:
    try:
        return (URL(f"//{value}").host or "").lower()
    except (ValueError, UnicodeError):
        return ""


def _origin_ok(request: web.Request) -> bool:
    if not request.app["loopback"]:
        return True
    origin = request.headers.get("Origin")
    if origin is None:
        return True  # non-browser client (curl, tests); Host guard still applies
    try:
        host = (URL(origin).host or "").lower()
    except (ValueError, UnicodeError):
        return False
    return host in _LOCAL_HOSTS


def _write_request_ok(request: web.Request) -> bool:
    """CSRF gate for state-changing methods.

    A page the employee visits can fire a cross-origin ``fetch`` at this
    loopback server; CORS hides the *response* but the *write* still happens.
    So for any non-GET we demand (a) a present, local ``Origin`` — unlike the WS
    check we do NOT allow a missing one, since browsers always send it on POST —
    and (b) a JSON content type, which forces a preflight that our missing
    ``Access-Control-Allow-Origin`` will fail, blocking the classic
    simple-request CSRF that ``text/plain``/form encodings would slip through.
    """
    if not request.app["loopback"]:
        return True
    origin = request.headers.get("Origin")
    if not origin:
        return False
    try:
        host = (URL(origin).host or "").lower()
    except (ValueError, UnicodeError):
        return False
    if host not in _LOCAL_HOSTS:
        return False
    ctype = (request.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    return ctype == "application/json"


@web.middleware
async def host_guard(request: web.Request, handler):
    app = request.app
    if app["loopback"]:
        if _hostname(request.host) not in _LOCAL_HOSTS:
            raise web.HTTPForbidden(reason="cross-origin host refused")
        if request.method not in ("GET", "HEAD", "OPTIONS") and not _write_request_ok(request):
            raise web.HTTPForbidden(reason="cross-origin or non-JSON write refused")
    response = await handler(request)
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
    )
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    # no-store everywhere: stats must be fresh, and a cached JS module surviving
    # a brainkit upgrade would run stale UI against a new API. Localhost refetch
    # of ~1MB of assets is immaterial.
    response.headers["Cache-Control"] = "no-store"
    return response


# ---- app assembly ------------------------------------------------------------

def create_app(lens: Lens, *, poll_interval: float = 2.0,
               loopback: bool = True) -> web.Application:
    from brain.embeddings import provider_from_config

    app = web.Application(middlewares=[host_guard])
    app["lens"] = lens
    app["poll_interval"] = poll_interval
    app["loopback"] = loopback
    app["websockets"] = set()
    app["state"] = {"fingerprint": None}  # mutated in place; app keys are frozen post-startup
    app["provider"] = provider_from_config()  # resolved once; None => keyword-only
    app["people"] = _org_people(Path(lens.master)) if lens.kind == "master" else {}

    app.router.add_get("/", handle_index)
    app.router.add_get("/api/meta", handle_meta)
    app.router.add_get("/api/stats", handle_stats)
    app.router.add_get("/api/graph", handle_graph)
    app.router.add_get("/api/search", handle_search)
    app.router.add_get("/api/facts", handle_facts)
    app.router.add_get("/api/notes", handle_notes)
    app.router.add_get("/api/note", handle_note)
    app.router.add_get("/api/inbox", handle_inbox)
    app.router.add_get("/api/actions", handle_actions)
    app.router.add_post("/api/capture", handle_capture)
    app.router.add_post("/api/promote", handle_promote)
    app.router.add_get("/api/promotion", handle_promotion)
    app.router.add_post("/api/promotions/sweep", handle_promotion_sweep)
    app.router.add_post("/api/promotions/{id}/{action}", handle_promotion_action)
    app.router.add_post("/api/shares/{id}/{action}", handle_share_action)
    app.router.add_get("/ws", handle_ws)
    if assets_dir().is_dir():
        app.router.add_static("/assets/", assets_dir(), follow_symlinks=False)

    app.cleanup_ctx.append(_watcher_ctx)
    return app


def run_server(lens: Lens, *, host: str = "127.0.0.1", port: int = 8765,
               open_browser: bool = True) -> int:
    import sys
    import webbrowser

    loopback = host in _LOOPBACK
    if not loopback:
        print(f"WARNING: binding {host} exposes this vault with NO authentication — "
              "anyone who can reach this port can read it.", file=sys.stderr)

    app = create_app(lens, loopback=loopback)

    async def _serve() -> None:
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        actual = port
        for sock in getattr(site._server, "sockets", None) or []:
            actual = sock.getsockname()[1]
            break
        shown_host = "127.0.0.1" if host == "0.0.0.0" else host
        url = f"http://{shown_host}:{actual}/"
        print(f"serving {url}  (Ctrl-C to stop)")
        if open_browser:
            with suppress(Exception):
                webbrowser.open(url)
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()

    with suppress(KeyboardInterrupt):
        asyncio.run(_serve())
    return 0
