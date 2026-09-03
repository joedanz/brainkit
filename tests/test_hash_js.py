"""The dashboard's URL-hash grammar, run from node against the shipped module.

`#note=<encoded rel_path>` is how anything outside the dashboard — an agent
citing its sources, a pasted link — opens one note. The grammar is pure, so
it is asserted against the file that ships rather than a restatement here
(same reasoning as test_graph_layout_js.py).
"""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from tests.conftest import ASSETS

HASH = ASSETS / "js" / "hash.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node to run the shipped JS")


def _eval(expr: str):
    script = f'import {{ parseHash, noteHash, pageHash }} from "{HASH.as_uri()}"; console.log(JSON.stringify({expr}));'
    r = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


@pytest.mark.parametrize("hash_, expected", [
    ("#query", {"tab": "query", "note": None, "page": None}),
    ("query", {"tab": "query", "note": None, "page": None}),
    ("", {"tab": None, "note": None, "page": None}),
    ("#", {"tab": None, "note": None, "page": None}),
    ("#note=Clients%2FAcme.md", {"tab": "query", "note": "Clients/Acme.md", "page": None}),
    ("#note=Clients/Acme.md", {"tab": "query", "note": "Clients/Acme.md", "page": None}),
    ("#note=", {"tab": "query", "note": None, "page": None}),
    ("#note=%E0%A4%A", {"tab": "query", "note": None, "page": None}),   # malformed escape: query tab, nothing opened
    ("#page=Company%2FHome.md", {"tab": "pages", "note": None, "page": "Company/Home.md"}),
    ("#page=", {"tab": "pages", "note": None, "page": None}),
    ("#page=%E0%A4%A", {"tab": "pages", "note": None, "page": None}),
    ("#pages", {"tab": "pages", "note": None, "page": None}),
])
def test_parse(hash_, expected):
    assert _eval(f"parseHash({json.dumps(hash_)})") == expected


def test_note_hash_round_trips_paths_with_spaces_and_unicode():
    path = "Travel/Côte d'Azur & Nice.md"
    assert _eval(f"parseHash(noteHash({json.dumps(path)})).note") == path


def test_note_hash_is_the_documented_grammar():
    assert _eval('noteHash("a/b c.md")') == "#note=a%2Fb%20c.md"


def test_page_hash_round_trips_and_is_the_documented_grammar():
    path = "Travel/Côte d'Azur & Nice.md"
    assert _eval(f"parseHash(pageHash({json.dumps(path)})).page") == path
    assert _eval('pageHash("a/b c.md")') == "#page=a%2Fb%20c.md"
