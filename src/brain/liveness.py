"""Do these URLs still resolve? The network half of provenance resilience.

Strictly "given URLs, are they alive?" — finding the URLs is parsing the
citation grammar, which lives with the rest of that grammar in `brain.doctor`.
So this module knows nothing about markdown, spaces, findings, or master, and
`brain.doctor` imports it lazily: the network code never loads on doctor's
default offline path, and the module docstring there promises read-only *and*
offline, a promise that stays greppable.

Classification is deliberately asymmetric. "Dead" means 404, 410, or a domain
that no longer resolves; everything else that fails — 403 bot-blocks, 429
throttles, 5xx hiccups, timeouts, TLS errors — is `UNKNOWN` and stays silent.
Telling someone their only recovery path is gone is a claim worth being sure
about, so under-reporting is the correct failure direction.
"""

from __future__ import annotations

import socket
import urllib.error
import urllib.request
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor

ALIVE, DEAD, UNKNOWN = "alive", "dead", "unknown"

DEFAULT_TIMEOUT = 5.0
DEFAULT_WORKERS = 8

# A polite, honest identity: some hosts 403 an absent or default UA, which
# would otherwise be indistinguishable from a real block.
_USER_AGENT = "brainkit-doctor/1.0 (+link liveness check)"

# HTTP statuses that mean the resource is genuinely gone, as opposed to
# temporarily withheld from us.
_GONE = frozenset({404, 410})


def _request(url: str, method: str) -> urllib.request.Request:
    return urllib.request.Request(
        url, method=method, headers={"User-Agent": _USER_AGENT})


def probe(url: str, *, timeout: float = DEFAULT_TIMEOUT) -> str:
    """ALIVE / DEAD / UNKNOWN for one URL. Never raises.

    HEAD first — a liveness check has no business downloading bodies — but a
    fair number of servers answer HEAD with 405; those get one GET retry
    before we give up on them.
    """
    for method in ("HEAD", "GET"):
        try:
            with urllib.request.urlopen(_request(url, method), timeout=timeout):
                return ALIVE
        except urllib.error.HTTPError as e:
            if e.code in _GONE:
                return DEAD
            if e.code == 405 and method == "HEAD":
                continue  # server refuses HEAD; ask for the body instead
            return UNKNOWN
        except urllib.error.URLError as e:
            # A domain that no longer resolves is the classic shape of link
            # rot (registration lapsed, host retired). Every other transport
            # failure — refused, unreachable, TLS, timeout — says more about
            # this moment than about the resource.
            return DEAD if isinstance(e.reason, socket.gaierror) else UNKNOWN
        except (OSError, ValueError):
            # Timeouts surface as OSError; a malformed URL as ValueError.
            return UNKNOWN
    return UNKNOWN


def probe_all(
    urls: Iterable[str],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    workers: int = DEFAULT_WORKERS,
) -> dict[str, str]:
    """Probe `urls` concurrently, deduplicated. Empty input does no work and
    starts no pool — the common case for a vault whose sources are all fresh."""
    todo = list(dict.fromkeys(urls))
    if not todo:
        return {}
    with ThreadPoolExecutor(max_workers=min(workers, len(todo))) as pool:
        states = pool.map(lambda u: probe(u, timeout=timeout), todo)
        return dict(zip(todo, states))


def wayback(url: str) -> str:
    """The Wayback Machine lookup for a dead source — the recovery hint that
    makes a link-rot finding actionable instead of merely sad."""
    return f"https://web.archive.org/web/*/{url}"
