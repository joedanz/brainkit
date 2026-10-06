"""The whole remote path in one process: edge-signed request -> router ->
one person's vault server -> brain_capture -> spool -> cycle -> master Inbox."""

import json

import pytest

from brain.cli import main
from brain.cycle import run_cycle
from brain.mcphttp import create_vault_app
from brain.mcprouter import RouteTable, create_router_app
from tests.remote_helpers import HEADER as H
from tests.remote_helpers import NOW, SIGNER, make_verifier, rpc
from tests.test_cli import seed_meta


def _rpc(method, mid=1, **params):
    return json.dumps(rpc(method, mid, **params))


@pytest.fixture
async def company(master, tmp_path, aiohttp_server, aiohttp_client):
    seed_meta(master)
    out = tmp_path / "compiled"
    main(["compile", "--master", str(master), "--out", str(out)])
    spool_root = tmp_path / "spool"
    ports = {}
    for pid, email in (("alice", "alice@acme.com"), ("bob", "bob@acme.com")):
        (spool_root / pid).mkdir(parents=True)
        app = create_vault_app(out / pid, person=pid, email=email, verifier=make_verifier(),
                               spool=spool_root / pid, now=lambda: NOW)
        ports[email] = (await aiohttp_server(app)).port
    routes = tmp_path / "mcp-routes.yaml"
    routes.write_text(json.dumps({"version": 1, "routes": ports}))
    router = await aiohttp_client(
        create_router_app(RouteTable(routes), verifier=make_verifier()))
    return {"router": router, "master": master, "out": out, "spool": spool_root,
            "routes": routes, "ports": ports}


async def _call(router, email, body):
    return await router.post("/mcp", data=body, headers={H: SIGNER.token(email=email),
                                                         "Content-Type": "application/json"})


async def test_each_person_reaches_only_their_own_brain(company):
    r = await _call(company["router"], "bob@acme.com", _rpc(
        "tools/call", name="brain_read", arguments={"rel_path": "People/bob/Memory.md"}))
    assert "Bob private memory" in (await r.json())["result"]["content"][0]["text"]

    r = await _call(company["router"], "bob@acme.com", _rpc(
        "tools/call", name="brain_read", arguments={"rel_path": "People/alice/Memory.md"}))
    assert (await r.json())["result"]["isError"] is True   # not in bob's vault


async def test_a_wrong_route_is_refused_by_the_vault_process(company):
    """Route table mistake: alice's email points at bob's process."""
    routes = {"version": 1, "routes": {"alice@acme.com": company["ports"]["bob@acme.com"]}}
    company["routes"].write_text(json.dumps(routes))
    company["router"].server.app["routes"]._checked = -10.0   # skip the 1 s reload throttle
    r = await _call(company["router"], "alice@acme.com", _rpc("tools/list"))
    assert r.status == 403 and (await r.json())["error"] == "wrong_person"


async def test_a_capture_reaches_the_master_inbox_through_the_cycle(company):
    r = await _call(company["router"], "bob@acme.com", _rpc(
        "tools/call", name="brain_capture",
        arguments={"text": "Ana prefers aisle seats", "title": "Ana"}))
    assert (await r.json())["result"]["isError"] is False
    assert len(list((company["spool"] / "bob").glob("*.json"))) == 1

    report = run_cycle(company["master"], company["out"], today="2026-10-06",
                       spool_root=company["spool"])

    assert report.spool_ingested == 1 and report.spool_rejected == 0
    assert list((company["spool"] / "bob").glob("*.json")) == []
    [note] = (company["master"] / "People/bob/Inbox").glob("*ana*.md")
    assert "source: mcp" in note.read_text()
    assert list((company["out"] / "bob/People/bob/Inbox").glob("*ana*.md"))
    assert not list((company["master"] / "People/alice/Inbox").glob("*ana*.md"))
