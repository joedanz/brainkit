"""The secrets scanner: high-confidence credential formats only.

Every fake credential below is assembled from pieces at runtime, so this file
holds no string a scanner (brainkit's own, or a code host's push protection)
would match.
"""

import dataclasses

import pytest

from brain.secrets import KINDS, Hit, scan_text, scanner_version

A = "a" * 36
UPPER16 = "ABCDEFGHIJKLMNOP"
NESTED = "postgres://" + "svc:" + "Tr0ub4dor-x9" + "@db.internal/app"

CAUGHT = {
    "private key": "-----BEGIN " + "RSA PRIVATE" + " KEY-----",
    "private key (openssh)": "-----BEGIN " + "OPENSSH PRIVATE" + " KEY-----",
    "private key (bare)": "-----BEGIN " + "PRIVATE" + " KEY-----",
    "AWS access key id": "AK" + "IA" + UPPER16,
    "AWS access key id (session)": "AS" + "IA" + UPPER16,
    "GitHub token (ghp)": "gh" + "p_" + A,
    "GitHub token (gho)": "gh" + "o_" + A,
    "GitHub token (ghs)": "gh" + "s_" + A,
    "GitHub token (ghu)": "gh" + "u_" + A,
    "GitHub token (ghr)": "gh" + "r_" + A,
    "GitHub token (pat)": "github" + "_pat_" + "B" * 22 + "_" + "c" * 59,
    "Slack token": "xo" + "xb-" + "123456789012-" + "1234567890123-" + "d" * 24,
    "Slack webhook URL": "https://hooks." + "slack.com/services/"
                         + "T0" + "ABCDEFG" + "/B0" + "ABCDEFG" + "/" + "e" * 24,
    "Stripe live key": "sk" + "_live_" + "f" * 24,
    "Stripe live key (restricted)": "rk" + "_live_" + "f" * 30,
    "OpenAI API key": "sk" + "-proj-" + "g" * 48,
    "Anthropic API key": "sk" + "-ant-" + "api03-" + "h" * 80,
    "Google API key": "AI" + "za" + "i" * 35,
    "password in a URL": "postgres://" + "svc_app" + ":" + "Tr0ub4dor-x9" + "@db.internal:5432/app",
    "password in a URL (empty user)": "redis://" + ":" + "S3cr3t-pw9" + "@cache.internal:6379",
    "password in a URL (slash)": "postgres://" + "svc" + ":" + "ab/cd+ef9" + "@db.internal/app",
    "private key (pgp)": "-----BEGIN " + "PGP PRIVATE" + " KEY BLOCK-----",
    "AWS access key id (after underscore)": "AWS_KEY_" + "AK" + "IA" + UPPER16,
    "password in a URL (starts with my)": "postgres://" + "app:" + "myS3cret-9x" + "@db.internal/app",
    # Digits then "/" is only a port when the "user" looks like a host.
    "password in a URL (digits and slash)": "postgres://" + "svc:" + "12/ab+Cd9" + "@db",
    "password in a URL (port-like, plain user)": "https://" + "user:" + "443/Secr3tPw" + "@db",
    # A real credential nested inside a span that was skipped as a placeholder.
    "password in a URL (nested after a port)": "https://proxy.acme.io:8080/r?to=" + NESTED,
    "password in a URL (nested after example)":
        "https://" + "bob:hunter2x" + "@api.example.com/?next=" + NESTED,
    "password in a URL (nested in a placeholder password)": "https://" + "app:${X}" + NESTED,
}

def _expected_kind(label: str) -> str:
    """Each CAUGHT label starts with the kind it must be reported as."""
    [kind] = [k for k in KINDS if label.startswith(k)]
    return kind


@pytest.mark.parametrize("label", sorted(CAUGHT))
def test_each_format_is_caught_with_its_line(label):
    text = f"# Setup\n\nsome prose\nthe value: {CAUGHT[label]} (do not share)\n"
    assert scan_text(text) == [Hit(_expected_kind(label), 4)]


NEAR_MISSES = {
    "short github token": "gh" + "p_short",
    "AKIA plus 15": "AK" + "IA" + UPPER16[:15],
    "AKIA plus 17": "AK" + "IA" + UPPER16 + "Q",
    "stripe test key": "sk" + "_test_" + "f" * 30,
    "public key block": "-----BEGIN " + "PUBLIC" + " KEY-----",
    "the word password": "the password is in 1Password, ask Bob",
    "placeholder user:password": "postgres://" + "user:password" + "@localhost/db",
    "placeholder angle brackets": "https://" + "<user>:<pass>" + "@host.example/x",
    "placeholder env var": "redis://" + "app:${REDIS_PASSWORD}" + "@cache:6379",
    "placeholder example host": "https://" + "bob:hunter2x" + "@api.example.com/v1",
    "plain url": "https://github.com/acme/repo",
    "email address": "mailto:alice@acme.com",
    "short google key": "AI" + "za" + "i" * 20,
    "sk without proj": "sk" + "-" + "g" * 10,
    "pgp public key block": "-----BEGIN " + "PGP PUBLIC" + " KEY BLOCK-----",
    "AWS after a letter": "x" + "AK" + "IA" + UPPER16,
    "AWS before a lowercase letter": "AK" + "IA" + UPPER16 + "q",
    "your_password": "postgres://" + "app:" + "your_password" + "@db.internal/app",
    "YOUR_PASSWORD": "postgres://" + "app:" + "YOUR_PASSWORD" + "@db.internal/app",
    "yourpassword": "postgres://" + "app:" + "yourpassword" + "@db.internal/app",
    "my db password": "postgres://" + "app:" + "my_db_password" + "@db.internal/app",
    "changeme": "postgres://" + "app:" + "changeme" + "@db.internal/app",
    "xxx run": "postgres://" + "app:" + "xxxxxx" + "@db.internal/app",
    "star run": "postgres://" + "app:" + "*****" + "@db.internal/app",
    "localhost host": "postgres://" + "app:" + "Tr0ub4dor-x9" + "@localhost:5432/app",
    "loopback host": "postgres://" + "app:" + "Tr0ub4dor-x9" + "@127.0.0.1:5432/app",
    "ipv6 loopback host": "postgres://" + "app:" + "Tr0ub4dor-x9" + "@[::1]:5432/app",
    "example host": "https://" + "app:" + "Tr0ub4dor-x9" + "@example.org/v1",
    "ssh git user": "ssh://" + "git@github.com" + "/acme/repo.git",
    "user without password": "postgres://" + "user@db.internal" + "/app",
    "port then @ in path": "https://registry.npmjs.org:443/" + "@scope/pkg",
}


@pytest.mark.parametrize("label", sorted(NEAR_MISSES))
def test_near_misses_are_not_caught(label):
    assert scan_text(f"line one\n{NEAR_MISSES[label]}\n") == []


def test_hits_never_carry_the_value():
    fields = {f.name for f in dataclasses.fields(Hit)}
    assert fields == {"kind", "line"}
    token = CAUGHT["GitHub token (ghp)"]
    assert token not in repr(scan_text(token))


def test_one_hit_per_kind_per_line_sorted_by_line():
    gh = CAUGHT["GitHub token (ghp)"]
    aws = CAUGHT["AWS access key id"]
    text = f"{aws}\n\n{gh} and {gh}\n{aws}\n"
    assert scan_text(text) == [
        Hit("AWS access key id", 1),
        Hit("GitHub token", 3),
        Hit("AWS access key id", 4),
    ]


def test_clean_text_has_no_hits():
    assert scan_text("# Notes\n\nNothing to see here.\n") == []
    assert scan_text("") == []


def test_scanner_version_is_stable_and_short():
    assert scanner_version() == scanner_version()
    assert len(scanner_version()) == 16


@pytest.mark.parametrize("text", [
    "a." * 100_000, "a-" * 100_000, "https://" + "a:" * 100_000,
    # the credentialed-URL pattern: empty user, "/" in the password, no "@"
    "x://" + ":" * 200_000,
    "x://:" + "/" * 200_000,
    "x://u:" + "a/" * 100_000,
    "a://:b" * 50_000,
    "x://" + "u:p/" * 60_000,
    "x://:" + "p" * 300 + "@" + "h" * 200_000,
    # many nested schemes, each match skipped and scanning resumed inside it
    "https://h.io:80/" * 40_000 + "@x",
    "x://u:p@example.com/" * 40_000,
    "a://h.io:1/" * 40_000 + "@x " + "b://localhost:9/" * 40_000 + "@y",
], ids=lambda t: f"{t[:12]!r}x{len(t)}")
def test_pathological_text_scans_in_linear_time(text):
    """An unbounded URL scheme once made "a.a.a…" quadratic: 45 s for 400 KB.

    Measured against plain text of the same length on the same machine, not
    a fixed wall-clock limit, so a busy test box can't fail it while a
    quadratic pattern (hundreds of times slower) still does."""
    import time

    def timed(s: str) -> float:
        start = time.perf_counter()
        scan_text(s)
        return time.perf_counter() - start

    assert scan_text(text) == []
    baseline = timed("b" * len(text))
    assert timed(text) < 50 * baseline + 1


def test_every_reported_kind_is_a_known_label():
    text = "\n".join(CAUGHT.values())
    assert {h.kind for h in scan_text(text)} == KINDS
