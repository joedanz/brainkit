"""Dashboard tab wiring, checked against the files that ship (node where a
module is DOM-free at load; source scans where it is not)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from tests.conftest import ASSETS

JS = ASSETS / "js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node to run the shipped JS")


def run_js(body: str):
    out = subprocess.run(["node", "--input-type=module", "-e", body],
                         capture_output=True, text=True, check=True, timeout=120)
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_graph_tab_mounts_the_engine_and_the_old_modules_are_gone():
    assert not (JS / "tabs" / "graph3d.js").exists()
    assert not (JS / "tabs" / "graph-controls.js").exists()
    src = (JS / "tabs" / "graph.js").read_text(encoding="utf-8")
    assert 'from "../graph/engine.js"' in src
    assert "d3.forceSimulation" not in src and "three" not in src.lower()
    for f in JS.rglob("*.js"):
        text = f.read_text(encoding="utf-8")
        assert "graph3d.js" not in text and "graph-controls.js" not in text, f.name
    res = run_js(f"""
const g = await import({json.dumps((JS / "tabs" / "graph.js").as_uri())});
console.log(JSON.stringify([typeof g.render, typeof g.onLive, typeof g.dispose]));
""")
    assert res == ["function", "function", "function"]


def test_a_failed_graph_load_tears_the_engine_down_before_clearing_the_host():
    """`clear(S.host)` removes the mounted engine's canvas from the document.
    If `S.engine` survived that, the next successful load would take the
    `update({data})` early return and paint into a detached canvas — the tab
    would show a stale error banner and no graph until it was left and
    re-entered. So the error branch must destroy and null the engine first."""
    src = (JS / "tabs" / "graph.js").read_text(encoding="utf-8")
    catch = re.search(r"\n  \} catch \(e\) \{\n(.*?)\n    return;\n  \}\n", src, re.S)
    assert catch, "the load() error branch is not where this test expects it"
    body = catch.group(1)
    teardown = body.find("S.engine.destroy()")
    assert teardown >= 0, "the error branch must destroy the mounted engine"
    assert "S.engine = null" in body, "the error branch must null the engine"
    assert teardown < body.find("clear(S.host)"), \
        "the engine must be torn down BEFORE the host is cleared out from under it"
