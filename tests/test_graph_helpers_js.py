"""Pure graph-engine helpers, run under node against the shipped modules.

Same reasoning as test_graph_layout_js.py: the repo has no JS runner, so node
imports the files that ship and the assertions hold against real code, not a
Python restatement of it.
"""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from tests.conftest import ASSETS

GRAPH = ASSETS / "js" / "graph"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node to run the shipped JS")


def run_js(body: str):
    """Evaluate an ES-module body that ends with console.log(JSON.stringify(...))."""
    out = subprocess.run(
        ["node", "--input-type=module", "-e", body],
        capture_output=True, text=True, check=True, timeout=120,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


def js_import(name: str) -> str:
    return json.dumps((GRAPH / name).as_uri())


# ---- palette -----------------------------------------------------------------

def test_color_for_is_stable_and_shared():
    res = run_js(f"""
const {{ colorFor }} = await import({js_import("palette.js")});
const dom = await import({json.dumps((ASSETS / "js" / "dom.js").as_uri())});
console.log(JSON.stringify({{
  same: colorFor("Company") === colorFor("Company"),
  differ: colorFor("Company") !== colorFor("Teams/sales"),
  hex: /^#[0-9a-f]{{6}}$/.test(colorFor("Company")),
  viaDom: dom.colorFor("Company") === colorFor("Company"),
}}));
""")
    assert res == {"same": True, "differ": True, "hex": True, "viaDom": True}


# ---- health ------------------------------------------------------------------

def _flags(nodes, edges):
    return run_js(f"""
const {{ healthFlags }} = await import({js_import("health.js")});
console.log(JSON.stringify(healthFlags({json.dumps({"nodes": nodes, "edges": edges, "truncated": False})})));
""")


def test_health_flags_inbound_orphan_dead_end():
    # 0 -> 1, 0 -> 2, 2 -> 1 ; 3 is alone
    nodes = [{"id": i, "degree": d} for i, d in enumerate([2, 2, 2, 0])]
    edges = [{"source": 0, "target": 1}, {"source": 0, "target": 2}, {"source": 2, "target": 1}]
    res = _flags(nodes, edges)
    assert res["inbound"] == [0, 2, 1, 0]
    assert res["orphan"] == [False, False, False, True]
    # 1 has no outbound link but is linked to: a dead end. 3 is an orphan, not a dead end.
    assert res["deadEnd"] == [False, True, False, False]


def test_health_flags_ignore_self_links_and_out_of_range():
    nodes = [{"id": 0, "degree": 1}, {"id": 1, "degree": 0}]
    edges = [{"source": 0, "target": 0}, {"source": 0, "target": 7}]
    res = _flags(nodes, edges)
    assert res["inbound"] == [0, 0]
    assert res["orphan"] == [False, True]   # degree says 0 has a link (cut by the cap); trust it


# ---- labels ------------------------------------------------------------------

def _labels(expr: str):
    return run_js(f"""
const L = await import({js_import("labels.js")});
console.log(JSON.stringify({expr}));
""")


def test_label_budget_per_stop():
    assert _labels('[L.labelBudget("hubs", 300), L.labelBudget("more", 300), L.labelBudget("all", 300)]') == [12, 25, 300]
    assert _labels('[L.labelBudget("hubs", 5), L.labelBudget("more", 5), L.labelBudget("all", 5)]') == [5, 5, 5]
    assert _labels('L.labelBudget("bogus", 100)') == 12   # unknown stop reads as hubs


def test_busiest_always_named_the_rest_need_room():
    # rank < HUBS: shown at any zoom, even before the gap is measured
    assert _labels("L.labelShown(0, 0.3, 0, 25)") is True
    assert _labels("L.labelShown(11, 0.3, 10, 25)") is True
    # rank >= HUBS: needs k * gap >= 44 screen px
    assert _labels("L.labelShown(12, 1.0, 43.9, 25)") is False
    assert _labels("L.labelShown(12, 1.0, 44, 25)") is True
    assert _labels("L.labelShown(12, 2.0, 22, 25)") is True
    # and never past the budget, whatever the room
    assert _labels("L.labelShown(25, 8, 1000, 25)") is False
    assert _labels("L.labelShown(24, 8, 1000, 25)") is True
