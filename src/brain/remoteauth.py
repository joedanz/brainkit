"""Verify the signed identity an edge adds to each remote-MCP request.

brainkit is not an OAuth server. The edge (Cloudflare Access with Managed
OAuth) runs sign-in and stamps every request it lets through with a short-lived
RS256 JWT naming the person — ``Cf-Access-Jwt-Assertion``. This module checks
that JWT against the edge's published keys and returns the email in it. It
issues nothing and stores nothing.

Needs the optional extra: ``pip install 'brainkit[remote]'``.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from brain.errors import BrainError
from brain.version import __version__

DEFAULT_HEADER = "Cf-Access-Jwt-Assertion"
LEEWAY_S = 60
REFETCH_MIN_INTERVAL_S = 60.0
REFRESH_EVERY_S = 12 * 3600.0
_ENV = {"issuer": "BRAIN_MCP_ISSUER", "jwks_url": "BRAIN_MCP_JWKS_URL",
        "audience": "BRAIN_MCP_AUDIENCE", "header": "BRAIN_MCP_HEADER"}


class RemoteAuthError(BrainError, ValueError):
    """Misconfiguration found at startup — the server refuses to start."""


class AuthRejected(BrainError):
    """A request the server must refuse: ``status`` is the HTTP status,
    ``code`` the stable error code in the JSON body."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass(frozen=True)
class AuthConfig:
    issuer: str
    jwks_url: str
    audience: str
    header: str = DEFAULT_HEADER


@dataclass(frozen=True)
class Identity:
    email: str


def normalize_email(value: str) -> str:
    """The comparison Org.person_by_email uses: case- and whitespace-insensitive."""
    return value.strip().lower()


def auth_config(*, issuer: str | None = None, jwks_url: str | None = None,
                audience: str | None = None, header: str | None = None,
                environ: Mapping[str, str] | None = None) -> AuthConfig:
    """Flags win; each unset flag falls back to its BRAIN_MCP_* variable."""
    env = os.environ if environ is None else environ
    vals = {"issuer": issuer, "jwks_url": jwks_url, "audience": audience, "header": header}
    for key, var in _ENV.items():
        if not vals[key]:
            vals[key] = env.get(var, "")
    missing = [f"--auth-{k.replace('_', '-')} / {_ENV[k]}"
               for k in ("issuer", "jwks_url", "audience") if not vals[k]]
    if missing:
        raise RemoteAuthError("remote MCP needs its identity settings; missing: "
                              + ", ".join(missing))
    for key in ("issuer", "jwks_url"):
        if not vals[key].startswith("https://"):
            raise RemoteAuthError(f"{_ENV[key]} must be an https:// URL, got {vals[key]!r}")
    return AuthConfig(vals["issuer"].rstrip("/"), vals["jwks_url"], vals["audience"],
                      vals["header"] or DEFAULT_HEADER)


def _require_jwt():
    try:
        import jwt
        from jwt.algorithms import RSAAlgorithm  # absent without the [crypto] extra
    except ImportError as e:
        raise RemoteAuthError(
            "remote MCP needs the optional extra — install 'brainkit[remote]'") from e
    return jwt, RSAAlgorithm


def _fetch_json(url: str) -> dict:
    # A named User-Agent: some zones refuse library defaults (Cloudflare 1010).
    req = urllib.request.Request(url, headers={"User-Agent": f"brainkit/{__version__}",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


class Verifier:
    def __init__(self, config: AuthConfig, *, fetch: Callable[[str], dict] = _fetch_json,
                 clock: Callable[[], float] = time.monotonic):
        self._jwt, self._rsa = _require_jwt()
        self.config = config
        self._fetch = fetch
        self._clock = clock
        self._keys: dict[str, object] = {}
        self._loaded_at = -math.inf
        self._attempted_at = -math.inf
        self._lock = threading.Lock()

    def load(self) -> None:
        """Startup: fetch the signing keys, or refuse to start."""
        try:
            keys = self._parse(self._fetch(self.config.jwks_url))
        except Exception as e:
            raise RemoteAuthError(
                f"cannot load signing keys from {self.config.jwks_url}: {e}") from e
        if not keys:
            raise RemoteAuthError(f"no RSA signing keys at {self.config.jwks_url}")
        self._keys = keys
        self._loaded_at = self._attempted_at = self._clock()

    def _parse(self, doc) -> dict[str, object]:
        keys: dict[str, object] = {}
        for k in (doc or {}).get("keys", []):
            if isinstance(k, dict) and k.get("kty") == "RSA" and k.get("kid"):
                keys[k["kid"]] = self._rsa.from_jwk(json.dumps(k))
        return keys

    def _refresh(self) -> None:
        """Best effort, at most once per REFETCH_MIN_INTERVAL_S. A failed or
        empty fetch keeps the keys already held."""
        with self._lock:
            now = self._clock()
            if now - self._attempted_at < REFETCH_MIN_INTERVAL_S:
                return
            self._attempted_at = now
            try:
                keys = self._parse(self._fetch(self.config.jwks_url))
            except Exception:
                return
            if keys:
                self._keys, self._loaded_at = keys, now

    def verify(self, headers: Mapping[str, str]) -> Identity:
        jwt = self._jwt
        token = headers.get(self.config.header, "")
        if not token:
            raise AuthRejected(401, "unauthenticated", "missing identity assertion")
        if self._clock() - self._loaded_at > REFRESH_EVERY_S:
            self._refresh()
        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.PyJWTError:
            raise AuthRejected(401, "unauthenticated", "malformed identity assertion") from None
        if not isinstance(kid, str):
            raise AuthRejected(401, "unauthenticated", "malformed identity assertion")
        key = self._keys.get(kid)
        if key is None:
            self._refresh()
            key = self._keys.get(kid)
        if key is None:
            raise AuthRejected(401, "unauthenticated", "assertion signed by an unknown key")
        try:
            claims = jwt.decode(token, key, algorithms=["RS256"],
                                audience=self.config.audience, issuer=self.config.issuer,
                                leeway=LEEWAY_S, options={"require": ["exp", "iss", "aud"]})
        except jwt.PyJWTError as e:
            raise AuthRejected(401, "unauthenticated",
                               f"invalid identity assertion ({type(e).__name__})") from None
        email = claims.get("email")
        if not isinstance(email, str) or not email.strip():
            raise AuthRejected(403, "no_identity", "the assertion names no person")
        return Identity(normalize_email(email))
