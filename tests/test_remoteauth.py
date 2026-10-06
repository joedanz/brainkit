import base64
import json
import time

import jwt
import pytest

from brain.remoteauth import (
    AuthConfig,
    AuthRejected,
    Identity,
    RemoteAuthError,
    Verifier,
    auth_config,
)
from tests.remote_helpers import AUD, ISSUER, JWKS_URL, SIGNER, Signer, make_verifier


def _h(token):
    return {"Cf-Access-Jwt-Assertion": token}


def test_valid_assertion_yields_normalized_email():
    v = make_verifier()
    assert v.verify(_h(SIGNER.token(email=" Bob@Acme.com "))) == Identity("bob@acme.com")


def test_missing_assertion_is_401():
    with pytest.raises(AuthRejected) as e:
        make_verifier().verify({})
    assert (e.value.status, e.value.code) == (401, "unauthenticated")


@pytest.mark.parametrize("override", [
    {"iss": "https://evil.example"},
    {"aud": ["someone-else"]},
    {"exp": int(time.time()) - 120},
])
def test_wrong_issuer_audience_or_expired_is_401(override):
    with pytest.raises(AuthRejected) as e:
        make_verifier().verify(_h(SIGNER.token(**override)))
    assert e.value.status == 401


def test_expiry_within_leeway_still_passes():
    assert make_verifier().verify(_h(SIGNER.token(exp=int(time.time()) - 30)))


def test_assertion_without_email_is_403_no_identity():
    with pytest.raises(AuthRejected) as e:
        make_verifier().verify(_h(SIGNER.token(email=None)))
    assert (e.value.status, e.value.code) == (403, "no_identity")


def test_hs256_token_is_refused():
    forged = jwt.encode({"iss": ISSUER, "aud": [AUD], "email": "bob@acme.com",
                         "exp": int(time.time()) + 600}, "secret", algorithm="HS256",
                        headers={"kid": "k1"})
    with pytest.raises(AuthRejected) as e:
        make_verifier().verify(_h(forged))
    assert e.value.status == 401


def test_unknown_kid_triggers_one_refetch_then_rate_limits():
    rotated = Signer(kid="k2")
    docs = {"keys": [SIGNER.jwk()]}
    calls = []

    def fetch(url):
        calls.append(url)
        return docs

    now = [1000.0]
    v = make_verifier(fetch=fetch, clock=lambda: now[0])
    assert len(calls) == 1
    docs["keys"] = [SIGNER.jwk(), rotated.jwk()]
    now[0] += 61
    assert v.verify(_h(rotated.token())).email == "bob@acme.com"   # refetched
    assert len(calls) == 2
    stranger = Signer(kid="k3")
    with pytest.raises(AuthRejected):
        v.verify(_h(stranger.token()))   # within 60 s of the last fetch: no refetch
    assert len(calls) == 2


def test_jwks_down_at_runtime_keeps_cached_keys():
    now = [1000.0]
    state = {"down": False, "attempts": 0, "raised": 0}

    def fetch(url):
        state["attempts"] += 1
        if state["down"]:
            state["raised"] += 1
            raise OSError("unreachable")
        return {"keys": [SIGNER.jwk()]}

    v = make_verifier(fetch=fetch, clock=lambda: now[0])
    state["down"] = True
    now[0] += 13 * 3600   # past the 12 h forced refresh
    assert v.verify(_h(SIGNER.token())).email == "bob@acme.com"
    assert state["raised"] == 1   # the forced refresh was attempted and failed
    now[0] += 61          # clear the refetch rate limit so the next fetch really runs
    before = state["attempts"]
    with pytest.raises(AuthRejected) as e:
        v.verify(_h(Signer(kid="kx").token()))
    assert e.value.status == 401
    assert state["attempts"] == before + 1 and state["raised"] == 2


@pytest.mark.parametrize("kid", [[], {}, ["k1"], 5])
def test_non_string_kid_is_401_not_500(kid):
    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    token = (b64({"alg": "RS256", "typ": "JWT", "kid": kid}) + "."
             + b64({"iss": ISSUER, "aud": [AUD], "email": "bob@acme.com"}) + ".")
    with pytest.raises(AuthRejected) as e:
        make_verifier().verify(_h(token))
    assert (e.value.status, e.value.code) == (401, "unauthenticated")


def test_load_refuses_when_keys_unreachable_or_empty():
    cfg = AuthConfig(ISSUER, JWKS_URL, AUD)
    with pytest.raises(RemoteAuthError):
        Verifier(cfg, fetch=lambda url: (_ for _ in ()).throw(OSError("down"))).load()
    with pytest.raises(RemoteAuthError):
        Verifier(cfg, fetch=lambda url: {"keys": []}).load()


def test_auth_config_reads_env_and_names_what_is_missing():
    cfg = auth_config(environ={"BRAIN_MCP_ISSUER": ISSUER + "/",
                               "BRAIN_MCP_JWKS_URL": JWKS_URL,
                               "BRAIN_MCP_AUDIENCE": AUD})
    assert cfg == AuthConfig(ISSUER, JWKS_URL, AUD, "Cf-Access-Jwt-Assertion")
    with pytest.raises(RemoteAuthError, match="BRAIN_MCP_AUDIENCE"):
        auth_config(environ={"BRAIN_MCP_ISSUER": ISSUER, "BRAIN_MCP_JWKS_URL": JWKS_URL})
    with pytest.raises(RemoteAuthError, match="https"):
        auth_config(issuer="http://x", jwks_url=JWKS_URL, audience=AUD, environ={})


def test_flags_override_env():
    cfg = auth_config(audience="flag-aud", environ={"BRAIN_MCP_ISSUER": ISSUER,
                      "BRAIN_MCP_JWKS_URL": JWKS_URL, "BRAIN_MCP_AUDIENCE": "env-aud"})
    assert cfg.audience == "flag-aud"


def test_a_slow_key_fetch_never_blocks_other_requests():
    """While one request is mid-fetch (lock held), others must carry on with
    the cached keys instead of queueing behind a hung endpoint."""
    import threading

    now = [1000.0]
    v = make_verifier(clock=lambda: now[0])
    now[0] += 61                     # a refresh is allowed
    result = {}

    def call():
        try:
            v.verify(_h(Signer(kid="unknown").token()))
        except AuthRejected as e:
            result["status"] = e.status

    with v._lock:                    # another request is "mid-fetch"
        t = threading.Thread(target=call, daemon=True)
        t.start()
        t.join(2)
        assert not t.is_alive(), "verify blocked behind an in-flight key fetch"
    assert result["status"] == 401
