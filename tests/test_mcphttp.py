import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from brain import mcphttp
from brain.cli import main
from brain.mcphttp import check_vault_owner, create_vault_app, run_vault_server
from brain.remoteauth import AuthConfig, RemoteAuthError
from tests.remote_helpers import AUD, ISSUER, JWKS_URL, SIGNER, make_verifier
from tests.test_cli import seed_meta

NOW = datetime(2026, 10, 6, 14, 30, tzinfo=UTC)


@pytest.fixture
def vault(master, tmp_path) -> Path:
    seed_meta(master)
    out = tmp_path / "compiled"
    main(["compile", "--master", str(master), "--out", str(out)])
    return out / "bob"


def _hdr(email="bob@acme.com", header="Cf-Access-Jwt-Assertion"):
    return {header: SIGNER.token(email=email), "Content-Type": "application/json"}


def _rpc(method, mid=1, **params):
    msg = {"jsonrpc": "2.0", "method": method}
    if mid is not None:
        msg["id"] = mid
    if params:
        msg["params"] = params
    return msg


@pytest.fixture
def make_client(aiohttp_client, vault):
    async def _make(spool=None, **kw):
        app = create_vault_app(vault, person="bob", email="Bob@Acme.com ", verifier=make_verifier(),
                               spool=spool, now=lambda: NOW, **kw)
        return await aiohttp_client(app)
    return _make


async def test_healthz_needs_no_identity(make_client):
    c = await make_client()
    r = await c.get("/healthz")
    assert r.status == 200 and await r.json() == {"ok": True}


async def test_no_assertion_is_401_json(make_client):
    c = await make_client()
    r = await c.post("/mcp", json=_rpc("initialize"))
    assert r.status == 401 and (await r.json())["error"] == "unauthenticated"


async def test_someone_elses_identity_is_403(make_client):
    c = await make_client()
    r = await c.post("/mcp", data=json.dumps(_rpc("tools/list")), headers=_hdr("alice@acme.com"))
    assert r.status == 403 and (await r.json())["error"] == "wrong_person"


async def test_lowercase_header_name_is_accepted(make_client):
    c = await make_client()
    r = await c.post("/mcp", data=json.dumps(_rpc("initialize")),
                     headers=_hdr(header="cf-access-jwt-assertion"))
    assert r.status == 200


async def test_initialize_and_tools_list(make_client):
    c = await make_client()
    r = await c.post("/mcp", data=json.dumps(_rpc("initialize", protocolVersion="2025-06-18")),
                     headers=_hdr())
    assert (await r.json())["result"]["protocolVersion"] == "2025-06-18"
    r = await c.post("/mcp", data=json.dumps(_rpc("tools/list", 2)), headers=_hdr())
    names = [t["name"] for t in (await r.json())["result"]["tools"]]
    assert "brain_search" in names and "brain_capture" not in names


async def test_notification_only_is_202(make_client):
    c = await make_client()
    r = await c.post("/mcp", data=json.dumps(_rpc("notifications/initialized", mid=None)),
                     headers=_hdr())
    assert r.status == 202


async def test_server_discover_gets_method_not_found_so_clients_fall_back(make_client):
    c = await make_client()
    r = await c.post("/mcp", data=json.dumps(_rpc("server/discover")), headers=_hdr())
    assert (await r.json())["error"]["code"] == -32601


async def test_batch_returns_a_list(make_client):
    c = await make_client()
    r = await c.post("/mcp", data=json.dumps([_rpc("ping", 1), _rpc("tools/list", 2)]),
                     headers=_hdr())
    body = await r.json()
    assert isinstance(body, list) and [m["id"] for m in body] == [1, 2]


@pytest.mark.parametrize("method,status,code", [("GET", 405, "method_not_allowed"),
                                                ("DELETE", 405, "method_not_allowed")])
async def test_non_post_is_405(make_client, method, status, code):
    c = await make_client()
    r = await c.request(method, "/mcp", headers=_hdr())
    assert r.status == status and (await r.json())["error"] == code


async def test_compressed_body_is_415(make_client):
    c = await make_client()
    r = await c.post("/mcp", data=b"x", headers={**_hdr(), "Content-Encoding": "gzip"})
    assert r.status == 415


async def test_oversized_body_is_413(make_client):
    c = await make_client()
    r = await c.post("/mcp", data=b"x" * (mcphttp.MAX_BODY + 1), headers=_hdr())
    assert r.status == 413 and (await r.json())["error"] == "too_large"


async def test_bad_json_is_parse_error(make_client):
    c = await make_client()
    r = await c.post("/mcp", data=b"{nope", headers=_hdr())
    assert r.status == 400 and (await r.json())["error"]["code"] == -32700


async def test_rate_limit_is_429_with_retry_after(make_client):
    c = await make_client(clock=lambda: 5.0)
    c.server.app["bucket"]["tokens"] = 0.0
    r = await c.post("/mcp", data=json.dumps(_rpc("ping")), headers=_hdr())
    assert r.status == 429 and int(r.headers["Retry-After"]) >= 1


async def test_tool_call_reads_the_vault(make_client):
    c = await make_client()
    r = await c.post("/mcp", data=json.dumps(_rpc("tools/call", name="brain_read",
                     arguments={"rel_path": "People/bob/Memory.md"})), headers=_hdr())
    assert "Bob private memory" in (await r.json())["result"]["content"][0]["text"]


async def test_vault_swapped_between_requests_still_serves(make_client, vault, master):
    c = await make_client()
    main(["compile", "--master", str(master), "--out", str(vault.parent)])  # renames the dir
    r = await c.post("/mcp", data=json.dumps(_rpc("tools/call", name="brain_read",
                     arguments={"rel_path": "People/bob/Memory.md"})), headers=_hdr())
    assert "Bob private memory" in (await r.json())["result"]["content"][0]["text"]


async def test_capture_is_listed_and_queues_one_envelope(make_client, tmp_path):
    spool = tmp_path / "spool"
    spool.mkdir()
    c = await make_client(spool=spool)
    r = await c.post("/mcp", data=json.dumps(_rpc("tools/list")), headers=_hdr())
    assert "brain_capture" in [t["name"] for t in (await r.json())["result"]["tools"]]
    r = await c.post("/mcp", data=json.dumps(_rpc("tools/call", name="brain_capture",
                     arguments={"text": "Ana prefers aisle seats", "title": "Ana"})), headers=_hdr())
    result = (await r.json())["result"]
    assert result["isError"] is False and "Saved" in result["content"][0]["text"]
    [env] = [json.loads(p.read_text()) for p in spool.glob("*.json")]
    assert env["person"] == "bob" and env["body"] == "Ana prefers aisle seats"


async def test_bad_capture_is_a_tool_error_and_writes_nothing(make_client, tmp_path):
    spool = tmp_path / "spool"
    spool.mkdir()
    c = await make_client(spool=spool)
    r = await c.post("/mcp", data=json.dumps(_rpc("tools/call", name="brain_capture",
                     arguments={"text": "x", "title": "a\nb"})), headers=_hdr())
    assert (await r.json())["result"]["isError"] is True
    assert list(spool.glob("*.json")) == []


async def test_capture_hourly_limit(make_client, tmp_path, monkeypatch):
    monkeypatch.setattr(mcphttp, "CAPTURES_PER_HOUR", 1)
    spool = tmp_path / "spool"
    spool.mkdir()
    c = await make_client(spool=spool, clock=lambda: 100.0)
    call = json.dumps(_rpc("tools/call", name="brain_capture", arguments={"text": "x"}))
    assert (await (await c.post("/mcp", data=call, headers=_hdr())).json())["result"]["isError"] is False
    second = await (await c.post("/mcp", data=call, headers=_hdr())).json()
    assert second["result"]["isError"] is True and "limit" in second["result"]["content"][0]["text"]


def test_vault_owner_must_match_manifest_and_folder(vault, tmp_path):
    check_vault_owner(vault, "bob")
    with pytest.raises(RemoteAuthError, match="belongs to"):
        check_vault_owner(vault, "alice")
    other = tmp_path / "alice"
    os.rename(vault, other)
    with pytest.raises(RemoteAuthError, match="named after"):
        check_vault_owner(other, "bob")


def test_run_refuses_a_public_bind(vault):
    cfg = AuthConfig(ISSUER, JWKS_URL, AUD)
    with pytest.raises(RemoteAuthError, match="loopback"):
        run_vault_server(vault, person="bob", email="bob@acme.com", auth=cfg, port=8901,
                         host="0.0.0.0")


def test_run_names_the_missing_extra_before_vault_problems(monkeypatch, tmp_path):
    """A box without brainkit[remote] should be told to install it, not sent
    chasing a vault complaint it would hit next anyway."""
    from brain import remoteauth

    def no_jwt():
        raise RemoteAuthError("remote MCP needs the optional extra — install 'brainkit[remote]'")

    monkeypatch.setattr(remoteauth, "_require_jwt", no_jwt)
    cfg = AuthConfig(ISSUER, JWKS_URL, AUD)
    with pytest.raises(RemoteAuthError, match=r"brainkit\[remote\]"):
        run_vault_server(tmp_path, person="bob", email="bob@acme.com", auth=cfg, port=8901)
