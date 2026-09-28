"""The secrets scanner: high-confidence credential formats only.

Every fake credential below is assembled from pieces at runtime, so this file
holds no string a scanner (brainkit's own, or a code host's push protection)
would match.
"""

import dataclasses

import pytest

from brain.secrets import Hit, scan_text, scanner_version

A = "a" * 36
UPPER16 = "ABCDEFGHIJKLMNOP"

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
    "Stripe restricted key": "rk" + "_live_" + "f" * 30,
    "OpenAI API key": "sk" + "-proj-" + "g" * 48,
    "Anthropic API key": "sk" + "-ant-" + "api03-" + "h" * 80,
    "Google API key": "AI" + "za" + "i" * 35,
    "password in a URL": "postgres://" + "svc_app" + ":" + "Tr0ub4dor-x9" + "@db.internal:5432/app",
}

KIND = {
    "private key": "private key",
    "AWS access key id": "AWS access key id",
    "GitHub token": "GitHub token",
    "Slack token": "Slack token",
    "Slack webhook URL": "Slack webhook URL",
    "Stripe": "Stripe live key",
    "OpenAI API key": "OpenAI API key",
    "Anthropic API key": "Anthropic API key",
    "Google API key": "Google API key",
    "password in a URL": "password in a URL",
}


def _expected_kind(label: str) -> str:
    for prefix, kind in KIND.items():
        if label.startswith(prefix):
            return kind
    raise AssertionError(label)


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


@pytest.mark.parametrize("text", ["a." * 100_000, "a-" * 100_000, "https://" + "a:" * 100_000])
def test_pathological_text_scans_in_linear_time(text):
    """An unbounded URL scheme once made "a.a.a…" quadratic: 45 s for 400 KB."""
    import time

    start = time.perf_counter()
    assert scan_text(text) == []
    assert time.perf_counter() - start < 2
