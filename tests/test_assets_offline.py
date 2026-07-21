"""First-party dashboard assets must be self-contained.

The live dashboard promises offline, origin-only operation (see the CSP test in
test_server.py). This guard extends that promise from the HTML shell to every
first-party JS/CSS asset: no external URL may appear. Vendor files (d3, three)
are excluded — they carry homepage URLs in comments — and XML namespace
identifiers are identifiers, not fetches.
"""
from __future__ import annotations

import re
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "src" / "brain" / "assets"
_ALLOWED_PREFIXES = ("http://www.w3.org/",)  # XML/SVG namespaces, never fetched
_URL = re.compile(r"https?://[^\s\"')]+")


def test_first_party_assets_are_offline():
    offenders: list[str] = []
    for f in sorted(ASSETS.rglob("*")):
        if f.suffix not in {".js", ".css", ".html"} or "vendor" in f.parts:
            continue
        for m in _URL.finditer(f.read_text(encoding="utf-8")):
            if not m.group(0).startswith(_ALLOWED_PREFIXES):
                offenders.append(f"{f.relative_to(ASSETS)}: {m.group(0)}")
    assert not offenders, f"external references in first-party assets: {offenders}"
