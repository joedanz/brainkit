import json
import os

import pytest
from aiohttp import web

from brain.mcprouter import RouteTable, RouteTableError, create_router_app, load_routes
from tests.remote_helpers import SIGNER, make_verifier

H = "Cf-Access-Jwt-Assertion"


def _routes(tmp_path, routes, version=1):
    p = tmp_path / "mcp-routes.yaml"
    p.write_text(json.dumps({"version": version, "routes": routes}))  # JSON is valid YAML
    return p


@pytest.fixture
async def upstream(aiohttp_server):
    seen = []

    async def handler(request):
        seen.append({"headers": dict(request.headers), "body": await request.read()})
        return web.json_response({"jsonrpc": "2.0", "id": 1, "result": {}},
                                 headers={"Mcp-Session-Id": "s1"})

    app = web.Application()
    app.router.add_post("/mcp", handler)
    server = await aiohttp_server(app)
    server.seen = seen
    return server


async def _client(aiohttp_client, path):
    return await aiohttp_client(create_router_app(RouteTable(path), verifier=make_verifier()))


async def test_routes_by_verified_email_and_passes_headers(aiohttp_client, upstream, tmp_path):
    c = await _client(aiohttp_client, _routes(tmp_path, {"Bob@Acme.com ": upstream.port}))
    r = await c.post("/mcp", data=b'{"jsonrpc":"2.0","id":1,"method":"ping"}',
                     headers={"cf-access-jwt-assertion": SIGNER.token(email="bob@acme.com"),
                              "Content-Type": "application/json", "Mcp-Method": "ping",
                              "User-Agent": "Claude-User", "Cookie": "secret=1"})
    assert r.status == 200 and r.headers["Mcp-Session-Id"] == "s1"
    fwd = upstream.seen[0]["headers"]
    assert fwd[H] and fwd["Mcp-Method"] == "ping" and fwd["User-Agent"] == "Claude-User"
    assert "Cookie" not in fwd


async def test_unknown_email_is_403_no_route(aiohttp_client, upstream, tmp_path):
    c = await _client(aiohttp_client, _routes(tmp_path, {"bob@acme.com": upstream.port}))
    r = await c.post("/mcp", data=b"{}", headers={H: SIGNER.token(email="eve@acme.com")})
    body = await r.json()
    assert r.status == 403 and body["error"] == "no_route" and "eve@acme.com" in body["message"]
    assert upstream.seen == []


async def test_identity_without_email_is_403_no_identity(aiohttp_client, upstream, tmp_path):
    c = await _client(aiohttp_client, _routes(tmp_path, {"bob@acme.com": upstream.port}))
    r = await c.post("/mcp", data=b"{}", headers={H: SIGNER.token(email=None)})
    assert r.status == 403 and (await r.json())["error"] == "no_identity"


async def test_no_assertion_is_401(aiohttp_client, tmp_path):
    c = await _client(aiohttp_client, _routes(tmp_path, {}))
    r = await c.post("/mcp", data=b"{}")
    assert r.status == 401


async def test_dead_upstream_is_503_with_retry_after(aiohttp_client, unused_tcp_port, tmp_path):
    c = await _client(aiohttp_client, _routes(tmp_path, {"bob@acme.com": unused_tcp_port}))
    r = await c.post("/mcp", data=b"{}", headers={H: SIGNER.token()})
    assert r.status == 503 and r.headers["Retry-After"] == "5"
    assert (await r.json())["error"] == "upstream_unavailable"


async def test_healthz_counts_routes_without_naming_anyone(aiohttp_client, tmp_path):
    c = await _client(aiohttp_client, _routes(tmp_path, {"bob@acme.com": 8901}))
    assert await (await c.get("/healthz")).json() == {"ok": True, "routes": 1}


@pytest.mark.parametrize("routes,version,match", [
    ({"bob@acme.com": 80}, 1, "1024"),
    ({"bob@acme.com": "8901"}, 1, "1024"),
    ({"not-an-email": 8901}, 1, "email"),
    ({"bob@acme.com": 8901, "BOB@acme.com": 8902}, 1, "twice"),
    ({}, 2, "version"),
])
def test_load_routes_rejects_bad_tables(tmp_path, routes, version, match):
    with pytest.raises(RouteTableError, match=match):
        load_routes(_routes(tmp_path, routes, version))


def test_route_table_reloads_and_keeps_last_good_on_a_bad_file(tmp_path):
    path = _routes(tmp_path, {"bob@acme.com": 8901})
    now = [0.0]
    table = RouteTable(path, clock=lambda: now[0])
    path.write_text(json.dumps({"version": 1, "routes": {"bob@acme.com": 8902}}))
    os.utime(path, ns=(10**18, 10**18))
    now[0] = 2.0
    assert table.get("bob@acme.com") == 8902
    path.write_text("version: [")
    os.utime(path, ns=(2 * 10**18, 2 * 10**18))
    now[0] = 4.0
    assert table.get("bob@acme.com") == 8902   # last good kept
