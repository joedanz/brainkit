"""Link liveness: URL extraction, probe classification, and the two contracts
that matter — doctor never touches the network unless asked, and "dead" is
claimed only when the source really is gone."""

import socket
import urllib.error
import urllib.request
from datetime import date as _date

import pytest

from brain import liveness
from brain.doctor import _check_liveness, run_doctor
from brain.liveness import ALIVE, DEAD, UNKNOWN, probe, probe_all

from .test_cli import seed_meta

TODAY = _date(2026, 7, 21)
STALE = "Ferries run hourly. [source](https://example.com/f), as of 2024-01\n"
FRESH = "Ferries run hourly. [source](https://example.com/f), as of 2026-06\n"


# --- probe classification ---------------------------------------------------


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _raiser(exc, *, only_method=None, then=None):
    """A fake urlopen raising `exc` (for `only_method`, when given) and
    otherwise returning `then` — enough to drive the HEAD→GET fallback."""
    def fake(req, timeout=None):
        if only_method is None or req.get_method() == only_method:
            raise exc
        if then is None:
            raise AssertionError(f"unexpected {req.get_method()}")
        return then
    return fake


def _http_error(code):
    return urllib.error.HTTPError("https://x.example", code, "nope", {}, None)


@pytest.mark.parametrize("code,expected", [
    (404, DEAD), (410, DEAD),
    (403, UNKNOWN), (429, UNKNOWN), (500, UNKNOWN), (503, UNKNOWN),
])
def test_probe_classifies_http_status(monkeypatch, code, expected):
    monkeypatch.setattr(urllib.request, "urlopen", _raiser(_http_error(code)))
    assert probe("https://x.example") == expected


def test_probe_reports_alive_on_success(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: _Resp())
    assert probe("https://x.example") == ALIVE


def test_probe_falls_back_to_get_when_head_is_refused(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _raiser(
        _http_error(405), only_method="HEAD", then=_Resp()))
    assert probe("https://x.example") == ALIVE


def test_probe_treats_dns_failure_as_dead(monkeypatch):
    # A domain that no longer resolves is the classic shape of link rot.
    monkeypatch.setattr(urllib.request, "urlopen", _raiser(
        urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided"))))
    assert probe("https://gone.example") == DEAD


@pytest.mark.parametrize("reason", [
    ConnectionRefusedError(61, "Connection refused"),
    TimeoutError("timed out"),
    OSError("certificate verify failed"),
])
def test_probe_treats_transport_failures_as_unknown(monkeypatch, reason):
    # A box having a bad day must never read as "your knowledge is gone".
    monkeypatch.setattr(urllib.request, "urlopen",
                        _raiser(urllib.error.URLError(reason)))
    assert probe("https://x.example") == UNKNOWN


def test_probe_survives_a_malformed_url(monkeypatch):
    assert probe("http://") == UNKNOWN


def test_probe_all_dedupes_and_returns_empty_without_work(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen",
                        _raiser(AssertionError("must not be called")))
    assert probe_all([]) == {}
    calls = []

    def fake(url, *, timeout):
        calls.append(url)
        return ALIVE

    monkeypatch.setattr(liveness, "probe", fake)
    assert probe_all(["https://a.example", "https://a.example"]) == {
        "https://a.example": ALIVE}
    assert calls == ["https://a.example"]


# --- doctor integration -----------------------------------------------------


def _page(master, rel, text):
    f = master / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)


def _stub_probes(monkeypatch, states):
    seen = []

    def fake(url, *, timeout=liveness.DEFAULT_TIMEOUT):
        seen.append(url)
        return states.get(url, ALIVE)

    monkeypatch.setattr(liveness, "probe", fake)
    return seen


def test_liveness_warns_on_stale_and_dead(monkeypatch, master):
    _page(master, "Company/Intel/Destinations/Lisbon.md", STALE)
    _stub_probes(monkeypatch, {"https://example.com/f": DEAD})
    findings = _check_liveness(master, today=TODAY)
    assert [(f.severity, f.check) for f in findings] == [("warn", "link-rot")]
    assert "2024-01" in findings[0].message
    assert "web.archive.org" in findings[0].message
    assert findings[0].paths == ("Company/Intel/Destinations/Lisbon.md",)


def test_liveness_covers_distilled_pages_outside_intel(monkeypatch, master):
    _page(master, "Clients/acme/Ferries.md",
          "---\ndistilled: https://example.com/f\n---\n\n" + STALE)
    _stub_probes(monkeypatch, {"https://example.com/f": DEAD})
    findings = _check_liveness(master, today=TODAY)
    assert [f.paths for f in findings] == [("Clients/acme/Ferries.md",)]


def test_liveness_is_silent_when_the_source_is_merely_unreachable(monkeypatch, master):
    _page(master, "Company/Intel/Destinations/Lisbon.md", STALE)
    _stub_probes(monkeypatch, {"https://example.com/f": UNKNOWN})
    assert _check_liveness(master, today=TODAY) == []


def test_liveness_never_probes_a_fresh_page(monkeypatch, master):
    # Network cost scales with the problem: a healthy vault makes no requests.
    _page(master, "Company/Intel/Destinations/Lisbon.md", FRESH)
    seen = _stub_probes(monkeypatch, {})
    assert _check_liveness(master, today=TODAY) == []
    assert seen == []


def test_liveness_ignores_intel_home_and_addenda(monkeypatch, master):
    _page(master, "Company/Intel/Home.md", STALE)
    _page(master, "Company/Intel/Lisbon — updates 2024-01.md", STALE)
    seen = _stub_probes(monkeypatch, {"https://example.com/f": DEAD})
    assert _check_liveness(master, today=TODAY) == []
    assert seen == []


def test_run_doctor_is_offline_unless_net_is_requested(monkeypatch, master):
    """Doctor's contract is read-only AND offline. Anything that reaches the
    network on the default path breaks CI determinism, so this is pinned the
    way the compiler's leak properties are."""
    seed_meta(master)
    _page(master, "Company/Intel/Destinations/Lisbon.md", STALE)
    monkeypatch.setattr(urllib.request, "urlopen",
                        _raiser(AssertionError("doctor went online")))
    assert not any(f.check == "link-rot" for f in run_doctor(master))


def test_run_doctor_net_includes_link_rot(monkeypatch, master):
    seed_meta(master)
    _page(master, "Company/Intel/Destinations/Lisbon.md", STALE)
    _stub_probes(monkeypatch, {"https://example.com/f": DEAD})
    assert any(f.check == "link-rot" for f in run_doctor(master, net=True))
