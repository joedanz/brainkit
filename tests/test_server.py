import sqlite3

import pytest

from aiohttp import WSServerHandshakeError

from brain.cli import main
from brain.compiler import compile_vault
from brain.indexer import build_index
from brain.server import check_and_broadcast, create_app
from brain.watch import Lens
from tests.conftest import ALICE, RULES


def _vault(master, tmp_path):
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=None, cache=None)
    return vault


def _vault_app(vault):
    return create_app(Lens(kind="vault", vault=vault), poll_interval=3600)


def _master_app(master, tmp_path):
    from tests.test_cli import seed_meta

    seed_meta(master)
    out_root = tmp_path / "compiled"
    assert main(["compile", "--master", str(master), "--out", str(out_root)]) == 0
    for pid in ("alice", "bob"):
        build_index(out_root / pid, provider=None, cache=None)
    return create_app(Lens(kind="master", master=master, out_root=out_root),
                     poll_interval=3600), out_root


# ---- vault lens --------------------------------------------------------------

async def test_stats_and_meta_vault(aiohttp_client, master, tmp_path):
    client = await aiohttp_client(_vault_app(_vault(master, tmp_path)))
    meta = await (await client.get("/api/meta")).json()
    assert meta["kind"] == "vault"
    assert meta["person"] == "alice"
    assert meta["vector_search"] is False

    stats = await (await client.get("/api/stats")).json()
    assert stats["kind"] == "vault"
    assert stats["notes_total"] > 0
    assert "graph" not in stats or stats["graph"] is None  # graph is fetched separately


async def test_graph_and_search(aiohttp_client, master, tmp_path):
    client = await aiohttp_client(_vault_app(_vault(master, tmp_path)))
    graph = await (await client.get("/api/graph")).json()
    assert graph["nodes"]

    res = await (await client.get("/api/search", params={"q": "pipeline"})).json()
    assert res["mode"] == "keyword-only"
    assert any("Pipeline" in h["rel_path"] for h in res["hits"])


async def test_notes_filter(aiohttp_client, master, tmp_path):
    client = await aiohttp_client(_vault_app(_vault(master, tmp_path)))
    body = await (await client.get("/api/notes", params={"space": "Company"})).json()
    assert body["notes"]
    assert all(n["space"] == "Company" for n in body["notes"])


async def test_note_read_and_traversal(aiohttp_client, master, tmp_path):
    client = await aiohttp_client(_vault_app(_vault(master, tmp_path)))
    ok = await client.get("/api/note", params={"path": "Company/Home.md"})
    assert ok.status == 200
    assert "Home" in (await ok.json())["text"]

    for bad in ("AGENTS.md", "Company/../../secret.md", "_meta/org.yaml"):
        resp = await client.get("/api/note", params={"path": bad})
        assert resp.status == 403


async def test_vault_lens_rejects_person(aiohttp_client, master, tmp_path):
    client = await aiohttp_client(_vault_app(_vault(master, tmp_path)))
    assert (await client.get("/api/graph", params={"person": "bob"})).status == 400


# ---- master lens -------------------------------------------------------------

async def test_master_stats_and_person_scoping(aiohttp_client, master, tmp_path):
    app, _ = _master_app(master, tmp_path)
    client = await aiohttp_client(app)

    stats = await (await client.get("/api/stats")).json()
    assert stats["kind"] == "master"
    assert stats["people_count"] == 2

    meta = await (await client.get("/api/meta")).json()
    assert {p["id"] for p in meta["people"]} == {"alice", "bob"}

    assert (await client.get("/api/graph")).status == 400  # person required
    assert (await client.get("/api/graph", params={"person": "nobody"})).status == 404
    assert (await client.get("/api/graph", params={"person": "alice"})).status == 200


# ---- websocket + live push ---------------------------------------------------

async def test_ws_initial_and_push_on_change(aiohttp_client, master, tmp_path):
    vault = _vault(master, tmp_path)
    client = await aiohttp_client(_vault_app(vault))
    ws = await client.ws_connect("/ws")
    first = await ws.receive_json()
    assert first["type"] == "stats" and first["reason"] == "initial"

    # a reindex changes the fingerprint; one manual tick should broadcast it
    (master / "People/alice/Memory.md").write_text("changed\n")
    compile_vault(master, ALICE, RULES, vault)
    build_index(vault, provider=None, cache=None)
    assert await check_and_broadcast(client.app) is True

    pushed = await ws.receive_json()
    assert pushed["type"] == "stats" and pushed["reason"] == "index"
    await ws.close()


# ---- security ----------------------------------------------------------------

async def test_host_guard_rejects_foreign_host(aiohttp_client, master, tmp_path):
    client = await aiohttp_client(_vault_app(_vault(master, tmp_path)))
    resp = await client.get("/api/meta", headers={"Host": "evil.com"})
    assert resp.status == 403


async def test_ws_rejects_foreign_origin(aiohttp_client, master, tmp_path):
    client = await aiohttp_client(_vault_app(_vault(master, tmp_path)))
    with pytest.raises(WSServerHandshakeError):
        await client.ws_connect("/ws", headers={"Origin": "http://evil.com"})


async def test_index_shell_is_cdn_free(aiohttp_client, master, tmp_path):
    client = await aiohttp_client(_vault_app(_vault(master, tmp_path)))
    resp = await client.get("/")
    assert resp.status == 200
    html = await resp.text()
    # the live shell loads only from its own origin — no external fetches
    assert "http://" not in html and "https://" not in html
    assert 'src="/assets/' in html


async def test_security_headers_present(aiohttp_client, master, tmp_path):
    client = await aiohttp_client(_vault_app(_vault(master, tmp_path)))
    resp = await client.get("/api/stats")
    assert "default-src 'self'" in resp.headers["Content-Security-Policy"]
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Cache-Control"] == "no-store"


# ---- read-only search (no index side effects) --------------------------------

async def test_search_does_not_create_index(aiohttp_client, master, tmp_path):
    # a compiled-but-unindexed vault: searching must not materialize index.db
    vault = tmp_path / "alice"
    compile_vault(master, ALICE, RULES, vault)
    assert not (vault / ".brain" / "index.db").exists()
    client = await aiohttp_client(_vault_app(vault))
    res = await (await client.get("/api/search", params={"q": "pipeline"})).json()
    assert res["hits"] == []
    assert not (vault / ".brain" / "index.db").exists()  # search stayed read-only


# ---- write guard (CSRF) ------------------------------------------------------

_LOCAL = {"Origin": "http://127.0.0.1"}


async def test_write_requires_local_origin_and_json(aiohttp_client, master, tmp_path):
    client = await aiohttp_client(_vault_app(_vault(master, tmp_path)))
    payload = {"body": "hello"}
    # no Origin at all → refused
    assert (await client.post("/api/capture", json=payload)).status == 403
    # foreign Origin → refused
    assert (await client.post("/api/capture", json=payload,
                              headers={"Origin": "http://evil.com"})).status == 403
    # local Origin but form encoding (skips preflight) → refused
    assert (await client.post("/api/capture", data="body=hello",
                              headers={**_LOCAL, "Content-Type": "application/x-www-form-urlencoded"})).status == 403


# ---- capture -----------------------------------------------------------------

async def test_capture_vault_lens_writes_inbox(aiohttp_client, master, tmp_path):
    vault = _vault(master, tmp_path)
    client = await aiohttp_client(_vault_app(vault))
    resp = await client.post("/api/capture", json={"title": "Idea", "body": "a thought"},
                             headers=_LOCAL)
    assert resp.status == 200
    rel = (await resp.json())["rel_path"]
    assert rel.startswith("People/alice/Inbox/")
    assert (vault / rel).is_file()  # landed in the slice; write-back carries it later

    inbox = await (await client.get("/api/inbox")).json()
    assert rel in [n["rel_path"] for n in inbox["notes"]]

    assert (await client.post("/api/capture", json={"body": "   "}, headers=_LOCAL)).status == 400


# ---- promotions --------------------------------------------------------------

async def test_promotion_review_and_approve(aiohttp_client, master, tmp_path):
    app, _ = _master_app(master, tmp_path)
    client = await aiohttp_client(app)

    # draft (employee side) → sweep into the queue → review body → approve
    drafted = await client.post("/api/promote", json={
        "person": "bob", "target_path": "Company/Shared/Promoted.md",
        "body": "shared body", "source": "manual"}, headers=_LOCAL)
    assert drafted.status == 200
    assert (await client.post("/api/promotions/sweep", json={}, headers=_LOCAL)).status == 200

    stats = await (await client.get("/api/stats")).json()
    ids = [p["id"] for p in stats["promotions_pending"]]
    assert ids, "sweep should have queued the draft"
    pid = ids[0]

    full = await (await client.get("/api/promotion", params={"id": pid})).json()
    assert full["body"].strip() == "shared body"  # reviewer sees content before deciding

    assert (await client.post(f"/api/promotions/{pid}/approve", json={"approver": "alice"},
                              headers=_LOCAL)).status == 200
    assert (master / "Company/Shared/Promoted.md").is_file()


async def test_approve_requires_known_approver(aiohttp_client, master, tmp_path):
    app, _ = _master_app(master, tmp_path)
    client = await aiohttp_client(app)
    await client.post("/api/promote", json={"person": "bob",
        "target_path": "Company/Shared/A.md", "body": "b"}, headers=_LOCAL)
    await client.post("/api/promotions/sweep", json={}, headers=_LOCAL)
    pid = (await (await client.get("/api/stats")).json())["promotions_pending"][0]["id"]

    for bad in ({}, {"approver": ""}, {"approver": "mallory"}):
        resp = await client.post(f"/api/promotions/{pid}/approve", json=bad, headers=_LOCAL)
        assert resp.status == 400, f"payload {bad!r} should be rejected"
    assert not (master / "Company/Shared/A.md").exists()

    assert (await client.post(f"/api/promotions/{pid}/approve",
                              json={"approver": "alice"}, headers=_LOCAL)).status == 200


async def test_reject_requires_reason(aiohttp_client, master, tmp_path):
    app, _ = _master_app(master, tmp_path)
    client = await aiohttp_client(app)
    await client.post("/api/promote", json={"person": "bob",
        "target_path": "Company/Shared/R.md", "body": "b"}, headers=_LOCAL)
    await client.post("/api/promotions/sweep", json={}, headers=_LOCAL)
    pid = (await (await client.get("/api/stats")).json())["promotions_pending"][0]["id"]
    assert (await client.post(f"/api/promotions/{pid}/reject", json={}, headers=_LOCAL)).status == 400


async def test_vault_lens_cannot_approve(aiohttp_client, master, tmp_path):
    client = await aiohttp_client(_vault_app(_vault(master, tmp_path)))
    assert (await client.post("/api/promotions/x/approve", json={}, headers=_LOCAL)).status == 403


# ---- note backlinks, inbox, actions ------------------------------------------

async def test_note_payload_has_backlinks(aiohttp_client, master, tmp_path):
    client = await aiohttp_client(_vault_app(_vault(master, tmp_path)))
    body = await (await client.get("/api/note", params={"path": "Company/Home.md"})).json()
    assert "links" in body
    assert set(body["links"]) == {"inbound", "outbound", "unresolved_out"}


async def test_inbox_and_actions_endpoints(aiohttp_client, master, tmp_path):
    client = await aiohttp_client(_vault_app(_vault(master, tmp_path)))
    assert "notes" in await (await client.get("/api/inbox")).json()
    assert "actions" in await (await client.get("/api/actions")).json()


# ---- input clamps ------------------------------------------------------------

async def test_input_clamps(aiohttp_client, master, tmp_path):
    client = await aiohttp_client(_vault_app(_vault(master, tmp_path)))
    # a negative k must not truncate to a single result via a broken break
    res = await (await client.get("/api/search", params={"q": "pipeline", "k": "-5"})).json()
    assert res["mode"] == "keyword-only"  # still ran; clamp kept k >= 1
    # cap=0 falls back to the default, not the max
    graph = await (await client.get("/api/graph", params={"cap": "0"})).json()
    assert "nodes" in graph


# ---- facts endpoint ----------------------------------------------------------

ACME = (
    "---\nentity: client\naliases: [Acme Corp, ACME]\n---\n# Acme\n\n"
    "- Sarah Kim is our main contact [from:: 2026-01] [source:: [[Q3 Pipeline]]]\n"
    "- Dana Ortiz was our main contact [from:: 2024-06] [until:: 2026-01]\n"
)


def _facts_vault(master, tmp_path):
    (master / "Company/Intel").mkdir(parents=True, exist_ok=True)
    (master / "Company/Intel/Acme.md").write_text(ACME)
    return _vault(master, tmp_path)


async def test_facts_endpoint_filters(aiohttp_client, master, tmp_path):
    client = await aiohttp_client(_vault_app(_facts_vault(master, tmp_path)))

    body = await (await client.get("/api/facts")).json()
    assert [h["statement"] for h in body["hits"]] == ["Sarah Kim is our main contact"]

    body = await (await client.get("/api/facts", params={"as_of": "2025-06"})).json()
    assert [h["statement"] for h in body["hits"]] == ["Dana Ortiz was our main contact"]

    body = await (await client.get("/api/facts", params={"include_ended": "1"})).json()
    assert len(body["hits"]) == 2

    body = await (await client.get("/api/facts", params={"type": "person"})).json()
    assert body["hits"] == []

    body = await (await client.get("/api/facts", params={"entity": "acme corp"})).json()
    assert body["hits"]

    body = await (await client.get("/api/facts", params={"entity": "Unknown Co"})).json()
    assert body["hits"] == [] and any("no entity" in w for w in body["warnings"])


async def test_facts_lens_scoping(aiohttp_client, master, tmp_path):
    client = await aiohttp_client(_vault_app(_facts_vault(master, tmp_path)))
    assert (await client.get("/api/facts", params={"person": "bob"})).status == 400

    app, _ = _master_app(master, tmp_path)
    mclient = await aiohttp_client(app)
    assert (await mclient.get("/api/facts")).status == 400          # person required
    assert (await mclient.get("/api/facts", params={"person": "nobody"})).status == 404
    assert (await mclient.get("/api/facts", params={"person": "alice"})).status == 200


async def test_facts_pre_v3_index_is_a_warning_not_500(aiohttp_client, master, tmp_path):
    vault = _facts_vault(master, tmp_path)
    conn = sqlite3.connect(vault / ".brain" / "index.db")
    for t in ("fact_entities", "facts", "entities"):
        conn.execute(f"DROP TABLE {t}")
    conn.commit()
    conn.close()

    client = await aiohttp_client(_vault_app(vault))
    resp = await client.get("/api/facts")
    assert resp.status == 200
    body = await resp.json()
    assert body["hits"] == []
    assert any("index" in w for w in body["warnings"])
