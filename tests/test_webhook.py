import base64
import hashlib
import hmac
import json
import subprocess
from pathlib import Path

import pytest

from brain.cli import main
from brain.webhook import (
    WebhookConfigError,
    create_webhook_app,
    load_webhook_config,
)
from tests.test_cli import seed_meta

NOW = 1_700_000_000  # fixed clock injected into every app
SECRET = "test-secret-for-fathom"
TOKEN = "test-token-for-zapier"

CONFIG = """\
sources:
  - id: fathom-alice
    person: alice
    verify: standard-webhooks
    secret_env: TEST_WEBHOOK_FATHOM
    source: fathom
    body_field: transcript
  - id: zapier-intake
    route: sender-email
    verify: token
    secret_env: TEST_WEBHOOK_ZAPIER
    source: zapier
"""


def _write_config(master: Path, text: str = CONFIG) -> None:
    (master / "_meta" / "webhook.yaml").write_text(text)


def _sign(body: bytes, *, secret: str = SECRET, msg_id: str = "msg_1",
          ts: int = NOW) -> dict[str, str]:
    key = (base64.b64decode(secret[len("whsec_"):]) if secret.startswith("whsec_")
           else secret.encode())
    mac = hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()
    return {
        "webhook-id": msg_id,
        "webhook-timestamp": str(ts),
        "webhook-signature": "v1," + base64.b64encode(mac).decode(),
    }


@pytest.fixture
def hook_master(master, monkeypatch) -> Path:
    seed_meta(master)
    _write_config(master)
    monkeypatch.setenv("TEST_WEBHOOK_FATHOM", SECRET)
    monkeypatch.setenv("TEST_WEBHOOK_ZAPIER", TOKEN)
    return master


def _app(master: Path, **kwargs):
    return create_webhook_app(master, clock=lambda: float(NOW), **kwargs)


def _inbox(master: Path, pid: str) -> list[Path]:
    d = master / "People" / pid / "Inbox"
    return sorted(d.glob("*.md")) if d.is_dir() else []


# ---- happy paths ---------------------------------------------------------------

async def test_standard_webhooks_ingests(aiohttp_client, hook_master):
    client = await aiohttp_client(_app(hook_master))
    body = json.dumps({"title": "Client call", "transcript": "We agreed on X."}).encode()
    resp = await client.post("/hook/fathom-alice", data=body, headers=_sign(body))
    assert resp.status == 200
    out = await resp.json()
    assert out["ok"] is True and out["committed"] is True

    notes = _inbox(hook_master, "alice")
    assert len(notes) == 1
    text = notes[0].read_text()
    assert "We agreed on X." in text
    assert "source: fathom" in text
    assert "title: Client call" in text
    log = subprocess.run(["git", "-C", str(hook_master), "log", "-1", "--format=%an %s"],
                         capture_output=True, text=True, check=True).stdout
    assert "Brain Ingest" in log and "source=fathom" in log


async def test_whsec_prefixed_secret(aiohttp_client, hook_master, monkeypatch):
    whsec = "whsec_" + base64.b64encode(b"raw-key-bytes").decode()
    monkeypatch.setenv("TEST_WEBHOOK_FATHOM", whsec)
    client = await aiohttp_client(_app(hook_master))
    body = json.dumps({"transcript": "signed with whsec key"}).encode()
    resp = await client.post("/hook/fathom-alice", data=body,
                             headers=_sign(body, secret=whsec))
    assert resp.status == 200


async def test_token_source_routes_by_email(aiohttp_client, hook_master):
    client = await aiohttp_client(_app(hook_master))
    body = json.dumps({"email": "bob@acme.com", "title": "Idea",
                       "body": "Ship the webhook."}).encode()
    resp = await client.post("/hook/zapier-intake", data=body,
                             headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status == 200
    assert len(_inbox(hook_master, "bob")) == 1
    assert not _inbox(hook_master, "alice")


async def test_token_via_custom_header(aiohttp_client, hook_master):
    client = await aiohttp_client(_app(hook_master))
    body = json.dumps({"email": "alice@acme.com", "body": "note"}).encode()
    resp = await client.post("/hook/zapier-intake", data=body,
                             headers={"X-Brain-Token": TOKEN})
    assert resp.status == 200


async def test_title_falls_back_to_first_line(aiohttp_client, hook_master):
    client = await aiohttp_client(_app(hook_master))
    body = json.dumps({"transcript": "# Standup notes\ndetails here"}).encode()
    resp = await client.post("/hook/fathom-alice", data=body, headers=_sign(body))
    assert resp.status == 200
    assert "title: Standup notes" in _inbox(hook_master, "alice")[0].read_text()


async def test_healthz(aiohttp_client, hook_master):
    client = await aiohttp_client(_app(hook_master))
    resp = await client.get("/healthz")
    assert resp.status == 200
    assert (await resp.json()) == {"ok": True}


# ---- fail closed: authentication ------------------------------------------------

async def test_rejects_missing_signature(aiohttp_client, hook_master):
    client = await aiohttp_client(_app(hook_master))
    body = json.dumps({"transcript": "sneaky"}).encode()
    resp = await client.post("/hook/fathom-alice", data=body)
    assert resp.status == 401
    assert not _inbox(hook_master, "alice")


async def test_rejects_bad_signature(aiohttp_client, hook_master):
    client = await aiohttp_client(_app(hook_master))
    body = json.dumps({"transcript": "sneaky"}).encode()
    headers = _sign(body, secret="wrong-secret")
    resp = await client.post("/hook/fathom-alice", data=body, headers=headers)
    assert resp.status == 401
    assert not _inbox(hook_master, "alice")


async def test_rejects_tampered_body(aiohttp_client, hook_master):
    client = await aiohttp_client(_app(hook_master))
    headers = _sign(json.dumps({"transcript": "original"}).encode())
    tampered = json.dumps({"transcript": "tampered"}).encode()
    resp = await client.post("/hook/fathom-alice", data=tampered, headers=headers)
    assert resp.status == 401


async def test_rejects_stale_timestamp(aiohttp_client, hook_master):
    client = await aiohttp_client(_app(hook_master))
    body = json.dumps({"transcript": "replayed"}).encode()
    resp = await client.post("/hook/fathom-alice", data=body,
                             headers=_sign(body, ts=NOW - 3600))
    assert resp.status == 401


async def test_rejects_wrong_token(aiohttp_client, hook_master):
    client = await aiohttp_client(_app(hook_master))
    body = json.dumps({"email": "bob@acme.com", "body": "x"}).encode()
    resp = await client.post("/hook/zapier-intake", data=body,
                             headers={"Authorization": "Bearer nope"})
    assert resp.status == 401


async def test_unknown_source_404(aiohttp_client, hook_master):
    client = await aiohttp_client(_app(hook_master))
    resp = await client.post("/hook/nope", data=b"{}")
    assert resp.status == 404


# ---- fail closed: payload --------------------------------------------------------

async def test_unknown_sender_email_refused(aiohttp_client, hook_master):
    client = await aiohttp_client(_app(hook_master))
    body = json.dumps({"email": "stranger@evil.com", "body": "hi"}).encode()
    resp = await client.post("/hook/zapier-intake", data=body,
                             headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status == 400
    assert not _inbox(hook_master, "alice") and not _inbox(hook_master, "bob")


async def test_missing_body_field(aiohttp_client, hook_master):
    client = await aiohttp_client(_app(hook_master))
    body = json.dumps({"title": "no transcript key"}).encode()
    resp = await client.post("/hook/fathom-alice", data=body, headers=_sign(body))
    assert resp.status == 400


async def test_non_json_body(aiohttp_client, hook_master):
    body = b"plain text, not json"
    client = await aiohttp_client(_app(hook_master))
    resp = await client.post("/hook/fathom-alice", data=body, headers=_sign(body))
    assert resp.status == 400


async def test_frontmatter_injection_refused(aiohttp_client, hook_master):
    client = await aiohttp_client(_app(hook_master))
    body = json.dumps({"title": "ok\ninjected: true",
                       "transcript": "payload"}).encode()
    resp = await client.post("/hook/fathom-alice", data=body, headers=_sign(body))
    assert resp.status == 400
    assert not _inbox(hook_master, "alice")


async def test_rejects_compressed_body(aiohttp_client, hook_master):
    body = json.dumps({"transcript": "x"}).encode()
    headers = {**_sign(body), "Content-Encoding": "gzip"}
    client = await aiohttp_client(_app(hook_master))
    resp = await client.post("/hook/fathom-alice", data=body, headers=headers)
    assert resp.status == 400


async def test_oversize_body_413(aiohttp_client, hook_master):
    client = await aiohttp_client(_app(hook_master, max_body=1024))
    body = json.dumps({"transcript": "x" * 10_000}).encode()
    resp = await client.post("/hook/fathom-alice", data=body, headers=_sign(body))
    assert resp.status == 413


# ---- replay dedup ----------------------------------------------------------------

async def test_duplicate_webhook_id_ingests_once(aiohttp_client, hook_master):
    client = await aiohttp_client(_app(hook_master))
    body = json.dumps({"transcript": "delivered twice"}).encode()
    headers = _sign(body, msg_id="msg_dup")
    assert (await client.post("/hook/fathom-alice", data=body, headers=headers)).status == 200
    second = await client.post("/hook/fathom-alice", data=body, headers=headers)
    assert second.status == 200
    assert (await second.json())["duplicate"] is True
    assert len(_inbox(hook_master, "alice")) == 1


async def test_failed_ingest_stays_retryable(aiohttp_client, hook_master):
    client = await aiohttp_client(_app(hook_master))
    bad = json.dumps({"title": "bad\ntitle", "transcript": "x"}).encode()
    headers = _sign(bad, msg_id="msg_retry")
    assert (await client.post("/hook/fathom-alice", data=bad, headers=headers)).status == 400
    good = json.dumps({"title": "fixed", "transcript": "x"}).encode()
    resp = await client.post("/hook/fathom-alice", data=good,
                             headers=_sign(good, msg_id="msg_retry"))
    assert resp.status == 200  # the failed id was never marked as seen
    assert (await resp.json()).get("duplicate") is None


# ---- rate limiting ---------------------------------------------------------------

LIMITED_CONFIG = CONFIG + """\
  - id: trickle
    person: alice
    verify: token
    secret_env: TEST_WEBHOOK_ZAPIER
    rate_limit: 2
"""


def _note(i: int) -> bytes:
    return json.dumps({"body": f"note {i}"}).encode()


async def test_rate_limit_refuses_excess_with_retry_after(aiohttp_client, hook_master):
    _write_config(hook_master, LIMITED_CONFIG)
    client = await aiohttp_client(_app(hook_master))
    auth = {"Authorization": f"Bearer {TOKEN}"}
    for i in range(2):
        assert (await client.post("/hook/trickle", data=_note(i),
                                  headers=auth)).status == 200
    third = await client.post("/hook/trickle", data=_note(3), headers=auth)
    assert third.status == 429
    assert int(third.headers["Retry-After"]) >= 1
    assert len(_inbox(hook_master, "alice")) == 2  # the refused one never ingested


async def test_rate_limit_refills_with_time(aiohttp_client, hook_master):
    _write_config(hook_master, LIMITED_CONFIG)
    t = [float(NOW)]
    client = await aiohttp_client(
        create_webhook_app(hook_master, clock=lambda: t[0]))
    auth = {"Authorization": f"Bearer {TOKEN}"}
    for i in range(2):
        assert (await client.post("/hook/trickle", data=_note(i),
                                  headers=auth)).status == 200
    assert (await client.post("/hook/trickle", data=_note(3),
                              headers=auth)).status == 429
    t[0] += 30.0  # 2/min refills one token per 30s
    resp = await client.post("/hook/trickle", data=_note(4), headers=auth)
    assert resp.status == 200
    assert len(_inbox(hook_master, "alice")) == 3


async def test_unauthenticated_requests_spend_no_budget(aiohttp_client, hook_master):
    """An attacker without the secret can't starve the legitimate sender."""
    _write_config(hook_master, LIMITED_CONFIG)
    client = await aiohttp_client(_app(hook_master))
    for i in range(10):
        assert (await client.post("/hook/trickle", data=_note(i),
                                  headers={"Authorization": "Bearer nope"})
                ).status == 401
    resp = await client.post("/hook/trickle", data=_note(99),
                             headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status == 200


async def test_duplicate_deliveries_spend_no_budget(aiohttp_client, hook_master):
    """Provider retry storms of an already-ingested id stay cheap acks."""
    _write_config(hook_master, LIMITED_CONFIG + """\
  - id: signed-trickle
    person: alice
    verify: standard-webhooks
    secret_env: TEST_WEBHOOK_FATHOM
    body_field: transcript
    rate_limit: 2
""")
    client = await aiohttp_client(_app(hook_master))
    body = json.dumps({"transcript": "once"}).encode()
    headers = _sign(body, msg_id="msg_storm")
    assert (await client.post("/hook/signed-trickle", data=body,
                              headers=headers)).status == 200
    for _ in range(5):
        resp = await client.post("/hook/signed-trickle", data=body, headers=headers)
        assert resp.status == 200
        assert (await resp.json())["duplicate"] is True
    fresh = json.dumps({"transcript": "second"}).encode()
    resp = await client.post("/hook/signed-trickle", data=fresh,
                             headers=_sign(fresh, msg_id="msg_fresh"))
    assert resp.status == 200  # only 1 of 2 budget slots was spent


async def test_rate_limits_are_per_source(aiohttp_client, hook_master):
    _write_config(hook_master, LIMITED_CONFIG)
    client = await aiohttp_client(_app(hook_master))
    auth = {"Authorization": f"Bearer {TOKEN}"}
    for i in range(3):
        await client.post("/hook/trickle", data=_note(i), headers=auth)
    other = json.dumps({"email": "bob@acme.com", "body": "unaffected"}).encode()
    resp = await client.post("/hook/zapier-intake", data=other, headers=auth)
    assert resp.status == 200  # trickle's exhausted bucket is not zapier-intake's


# ---- config validation (fail closed at startup) -----------------------------------

def test_missing_secret_env_refuses_startup(hook_master, monkeypatch):
    monkeypatch.delenv("TEST_WEBHOOK_FATHOM")
    with pytest.raises(WebhookConfigError, match="TEST_WEBHOOK_FATHOM"):
        create_webhook_app(hook_master)


def test_unknown_person_refuses_startup(hook_master):
    _write_config(hook_master, """\
sources:
  - id: ghost
    person: nobody
    verify: token
    secret_env: TEST_WEBHOOK_ZAPIER
""")
    with pytest.raises(WebhookConfigError, match="nobody"):
        create_webhook_app(hook_master)


@pytest.mark.parametrize("snippet,match", [
    ("sources: []", "non-empty"),
    ("sources:\n  - {id: 'BAD ID', verify: token, secret_env: E, person: alice}",
     "lowercase"),
    ("sources:\n  - {id: a, verify: hope, secret_env: E, person: alice}", "verify"),
    ("sources:\n  - {id: a, verify: token, person: alice}", "secret_env"),
    ("sources:\n  - {id: a, verify: token, secret_env: E}", "exactly one"),
    ("sources:\n  - {id: a, verify: token, secret_env: E, person: alice, route: sender-email}",
     "exactly one"),
    ("sources:\n  - {id: a, verify: token, secret_env: E, route: magic}", "sender-email"),
    ("sources:\n  - {id: a, verify: token, secret_env: E, person: alice}\n"
     "  - {id: a, verify: token, secret_env: E, person: alice}", "duplicate"),
    ("sources:\n  - {id: a, verify: token, secret_env: E, person: alice, rate_limit: 0}",
     "rate_limit"),
    ("sources:\n  - {id: a, verify: token, secret_env: E, person: alice, rate_limit: -5}",
     "rate_limit"),
    ("sources:\n  - {id: a, verify: token, secret_env: E, person: alice, rate_limit: fast}",
     "rate_limit"),
    ("sources:\n  - {id: a, verify: token, secret_env: E, person: alice, rate_limit: true}",
     "rate_limit"),
])
def test_config_rejects(tmp_path, snippet, match):
    cfg = tmp_path / "webhook.yaml"
    cfg.write_text(snippet)
    with pytest.raises(WebhookConfigError, match=match):
        load_webhook_config(cfg)


# ---- init scaffold + status nudge --------------------------------------------------

def test_init_scaffolds_inert_webhook_example(tmp_path):
    import yaml

    assert main(["init", str(tmp_path / "m"), "--company", "Acme"]) == 0
    example = tmp_path / "m" / "_meta" / "webhook.yaml.example"
    assert example.is_file()
    # Fully commented: activating it must be a deliberate copy-and-edit,
    # never a side effect of init.
    assert yaml.safe_load(example.read_text()) is None
    assert not (tmp_path / "m" / "_meta" / "webhook.yaml").exists()


def test_webhook_example_validates_when_uncommented(tmp_path):
    from brain.templates import WEBHOOK_YAML_EXAMPLE

    lines = WEBHOOK_YAML_EXAMPLE.splitlines()
    start = lines.index("# sources:")
    uncommented = "\n".join(
        line[2:] if line.startswith("# ") else "" for line in lines[start:])
    cfg = tmp_path / "webhook.yaml"
    cfg.write_text(uncommented)
    sources = load_webhook_config(cfg)
    assert {s.id for s in sources} == {"fathom-founder", "zapier-intake"}


def test_webhook_cli_ignores_example_file(tmp_path, capsys):
    assert main(["init", str(tmp_path / "m"), "--company", "Acme"]) == 0
    assert main(["webhook", "--master", str(tmp_path / "m")]) == 1
    assert "webhook.yaml" in capsys.readouterr().err


def test_status_nudges_when_not_configured(master, capsys):
    seed_meta(master)
    assert main(["status", "--master", str(master)]) == 0
    assert "webhook intake: not configured" in capsys.readouterr().out


def test_status_counts_sources_when_configured(hook_master, capsys):
    assert main(["status", "--master", str(hook_master)]) == 0
    assert "webhook intake: 2 source(s)" in capsys.readouterr().out


# ---- CLI + doctor -----------------------------------------------------------------

def test_webhook_cli_without_config(master, capsys):
    seed_meta(master)
    assert main(["webhook", "--master", str(master)]) == 1
    assert "webhook.yaml" in capsys.readouterr().err


def test_webhook_cli_bad_config(hook_master, monkeypatch, capsys):
    monkeypatch.delenv("TEST_WEBHOOK_FATHOM")
    assert main(["webhook", "--master", str(hook_master)]) == 1
    assert "TEST_WEBHOOK_FATHOM" in capsys.readouterr().err


def test_doctor_flags_webhook_problems(hook_master, monkeypatch, capsys):
    from brain.doctor import run_doctor

    monkeypatch.delenv("TEST_WEBHOOK_FATHOM")
    findings = run_doctor(hook_master)
    webhook = [f for f in findings if f.check == "webhook"]
    assert any(f.severity == "warn" and "TEST_WEBHOOK_FATHOM" in f.message
               for f in webhook)
    assert any(f.severity == "info" and "2 webhook source(s)" in f.message
               for f in webhook)


def test_doctor_quiet_without_config(master):
    from brain.doctor import run_doctor

    seed_meta(master)
    assert not [f for f in run_doctor(master) if f.check == "webhook"]
