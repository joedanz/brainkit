"""Find credentials pasted into notes — high-confidence formats only.

A note is copied into the vault of every person who can read its space, and
into git history for good. A key that lands in one is leaked as far as that
note travels, so doctor's `secrets` check looks for them.

Only formats with a fixed, distinctive shape are matched: a vendor prefix
plus a fixed-length body, a private key header, a URL that carries a
password. There is no entropy guessing — a random-looking string in prose is
far more often a commit hash or an id than a key, and an alarm that cries
wolf gets ignored.

A `Hit` records what kind of credential was found and on which line, never
the value itself: every place a hit can travel (a finding, a digest, a
cache row) is somewhere the value must not go.
"""

from __future__ import annotations

import bisect
import hashlib
import re
from dataclasses import dataclass

# Bump when scan_text changes what it reports in a way the patterns below do
# not show (scanner_version() already covers every pattern and label).
SECRETS_SCHEME = 1


@dataclass(frozen=True)
class Hit:
    kind: str  # plain-language label, e.g. "GitHub token"
    line: int  # 1-based


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9]{22}_[A-Za-z0-9]{59})\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-\d{6,}-[0-9A-Za-z-]{10,}")),
    ("Slack webhook URL", re.compile(
        r"hooks\.slack\.com/services/T[0-9A-Z]{6,}/B[0-9A-Z]{6,}/[0-9A-Za-z]{16,}")),
    ("Stripe live key", re.compile(r"\b[sr]k_live_[0-9A-Za-z]{24,}")),
    ("OpenAI API key", re.compile(r"\bsk-proj-[A-Za-z0-9_-]{40,}")),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{40,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])")),
)

# scheme://user:password@host. The password stops at whitespace, "@" and
# "/", so a path segment is never read as one.
_URL_CREDS = re.compile(
    r"\b[A-Za-z][A-Za-z0-9+.-]{0,31}://([^\s:/@]{1,256}):([^\s/@]{1,256})@([^\s/?#]{1,256})")
_URL_KIND = "password in a URL"
_NEWLINE = re.compile("\n")

# Documentation writes these where a real value would go.
_PLACEHOLDER_WORDS = frozenset({
    "user", "username", "usr", "login", "name", "pass", "password", "passwd",
    "pwd", "secret", "token", "apikey", "key", "changeme", "example", "xxx",
    "foo", "bar",
})
_PLACEHOLDER_MARKS = ("<", ">", "${", "{{", "%s", "*", "...", "[", "]")


def _is_placeholder(user: str, password: str, host: str) -> bool:
    if any(m in s for s in (user, password) for m in _PLACEHOLDER_MARKS):
        return True
    if password.lower() in _PLACEHOLDER_WORDS or password == user:
        return True
    if set(password.lower()) <= {"x"}:
        return True
    return "example" in host.lower()


def _url_matches(text: str):
    for m in _URL_CREDS.finditer(text):
        if not _is_placeholder(*m.groups()):
            yield m


def scan_text(text: str) -> list[Hit]:
    """Every credential-shaped string in `text`, as one Hit per (kind,
    line), sorted by line. Lines count from 1."""
    found: set[Hit] = set()
    newlines: list[int] | None = None

    def line_of(pos: int) -> int:
        nonlocal newlines
        if newlines is None:
            newlines = [m.start() for m in _NEWLINE.finditer(text)]
        return bisect.bisect_left(newlines, pos) + 1

    for kind, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            found.add(Hit(kind, line_of(m.start())))
    for m in _url_matches(text):
        found.add(Hit(_URL_KIND, line_of(m.start())))
    return sorted(found, key=lambda h: (h.line, h.kind))


def scanner_version() -> str:
    """Everything a scan result depends on besides the text: the scheme, and
    every pattern and label. Cached results from any other version are
    ignored, so a changed pattern rescans every note on its own."""
    material = repr((
        SECRETS_SCHEME,
        [(kind, p.pattern) for kind, p in _PATTERNS],
        _URL_KIND, _URL_CREDS.pattern,
        sorted(_PLACEHOLDER_WORDS), _PLACEHOLDER_MARKS,
    ))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
