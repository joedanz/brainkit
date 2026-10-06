"""Signing keys and tokens for remote-MCP tests: a local stand-in for the
edge (Cloudflare Access) that signs identity assertions."""

from __future__ import annotations

import json
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

ISSUER = "https://team.example.cloudflareaccess.com"
AUD = "aud-tag-1"
JWKS_URL = ISSUER + "/cdn-cgi/access/certs"


class Signer:
    def __init__(self, kid: str = "k1"):
        self.kid = kid
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def jwk(self) -> dict:
        d = json.loads(RSAAlgorithm.to_jwk(self.key.public_key()))
        d["kid"] = self.kid
        return d

    def token(self, email: str | None = "bob@acme.com", **overrides) -> str:
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": [AUD], "email": email, "iat": now,
                  "nbf": now, "exp": now + 600, "type": "app"}
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": self.kid})


SIGNER = Signer()


def make_verifier(*signers: Signer, fetch=None, clock=None):
    from brain.remoteauth import AuthConfig, Verifier

    doc = {"keys": [s.jwk() for s in (signers or (SIGNER,))]}
    kwargs = {"clock": clock} if clock is not None else {}
    v = Verifier(AuthConfig(ISSUER, JWKS_URL, AUD), fetch=fetch or (lambda url: doc), **kwargs)
    v.load()
    return v
