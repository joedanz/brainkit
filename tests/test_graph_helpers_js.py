"""Pure graph-engine helpers, run under node against the shipped modules.

Same reasoning as test_graph_layout_js.py: the repo has no JS runner, so node
imports the files that ship and the assertions hold against real code, not a
Python restatement of it.
"""
from __future__ import annotations

import json
import re
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


# ---- hull --------------------------------------------------------------------

def _hull(points, pad):
    return run_js(f"""
const {{ hullFor, centroidOf }} = await import({js_import("hull.js")});
const hull = hullFor({json.dumps(points)}, {pad});
const c = centroidOf({json.dumps(points)});
console.log(JSON.stringify({{ hull, c }}));
""")


def _inside(poly, x, y):
    """Point-in-convex-polygon by consistent cross-product sign."""
    signs = set()
    for i, a in enumerate(poly):
        b = poly[(i + 1) % len(poly)]
        cross = (b["x"] - a["x"]) * (y - a["y"]) - (b["y"] - a["y"]) * (x - a["x"])
        if abs(cross) > 1e-9:
            signs.add(cross > 0)
    return len(signs) == 1


def test_hull_pads_and_contains_every_point():
    pts = [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}, {"x": 0, "y": 10}, {"x": 5, "y": 5}]
    res = _hull(pts, 4)
    hull = res["hull"]
    assert len(hull) >= 4
    for p in pts:
        assert _inside(hull, p["x"], p["y"])
    xs = [h["x"] for h in hull]; ys = [h["y"] for h in hull]
    assert min(xs) == pytest.approx(-4) and max(xs) == pytest.approx(14)
    assert min(ys) == pytest.approx(-4) and max(ys) == pytest.approx(14)
    assert res["c"] == {"x": 5, "y": 5, "z": 0}


def test_hull_of_one_or_two_points_still_has_area():
    one = _hull([{"x": 3, "y": 3}], 5)["hull"]
    assert len(one) >= 6
    assert all(abs(((h["x"] - 3) ** 2 + (h["y"] - 3) ** 2) ** 0.5 - 5) < 1e-6 for h in one)
    two = _hull([{"x": 0, "y": 0}, {"x": 20, "y": 0}], 3)["hull"]
    assert _inside(two, 10, 0) and _inside(two, 10, 2.5)


def test_hull_skips_non_finite_points():
    res = _hull([{"x": 0, "y": 0}, {"x": None, "y": 1}], 2)
    assert res["hull"] and all(isinstance(h["x"], (int, float)) for h in res["hull"])
    assert _hull([{"x": None, "y": None}], 2)["hull"] == []


# ---- engine module -----------------------------------------------------------

def test_engine_exports_the_contract_and_loads_d3_itself():
    """Appendix A: ENGINE_VERSION, mountGraph, and d3 fetched by the engine via
    a dynamic import of the vendored UMD file when globalThis.d3 is missing.
    Importing engine.js must touch no DOM at module load, or node could not
    run this."""
    res = run_js(f"""
const E = await import({js_import("engine.js")});
const before = typeof globalThis.d3;
const d3 = await E.ensureD3();
console.log(JSON.stringify({{
  version: E.ENGINE_VERSION,
  mount: typeof E.mountGraph,
  before,
  after: typeof d3.forceSimulation,
  global: globalThis.d3 === d3,
}}));
""")
    assert res == {"version": 1, "mount": "function", "before": "undefined", "after": "function", "global": True}


def test_engine_never_imports_three_statically():
    src = (GRAPH / "engine.js").read_text(encoding="utf-8")
    assert "three.module" not in src and "OrbitControls" not in src
    assert 'import("./view3d.js")' in src  # 3D stays lazy


def test_prefs_default_by_viewport_and_survive_bad_storage():
    res = run_js(f"""
const {{ loadPrefs, prefsKey }} = await import({js_import("engine.js")});
const store = new Map();
globalThis.localStorage = {{ getItem: (k) => store.has(k) ? store.get(k) : null, setItem: (k, v) => store.set(k, v) }};
const phone = loadPrefs("vault", "phone"), desk = loadPrefs("vault", "desktop");
store.set(prefsKey("master"), JSON.stringify({{ labels: "all", mode: "3d", hidden: ["Company"], nodeSize: "big", orphans: 1 }}));
const saved = loadPrefs("master", "desktop");
store.set(prefsKey("junk"), "{{not json");
const junk = loadPrefs("junk", "desktop");
console.log(JSON.stringify({{ phone: phone.labels, desk: desk.labels, mode: desk.mode, key: prefsKey("vault"),
  saved: [saved.labels, saved.mode, saved.hidden, saved.nodeSize, saved.orphans], junk: junk.labels }}));
""")
    assert res == {"phone": "hubs", "desk": "more", "mode": "2d", "key": "brain-graph-engine:vault",
                   "saved": ["all", "3d", ["Company"], 1, False], "junk": "more"}


def test_engine_styles_adopt_one_sheet_per_document_and_never_touch_style_attributes():
    """Appendix A: a constructable stylesheet adopted once (CSSOM, outside CSP
    style-src), so brainkit's default-src 'self' stays as it is. And no
    `style=` attribute anywhere in the engine — inline style ATTRIBUTES are
    what that CSP blocks — so every dynamic value is a class or a custom
    property set through el.style.setProperty."""
    res = run_js(f"""
const {{ ENGINE_CSS, adoptStyles }} = await import({js_import("styles.js")});
// a stand-in document with the two members adoptStyles uses
class CSSStyleSheet {{ replaceSync(t) {{ this.text = t; }} }}
globalThis.CSSStyleSheet = CSSStyleSheet;
globalThis.document = {{ adoptedStyleSheets: [] }};
adoptStyles(); adoptStyles();
console.log(JSON.stringify({{ kind: typeof ENGINE_CSS, hasChip: ENGINE_CSS.includes(".ge-chip"),
  hasPhone: ENGINE_CSS.includes(".ge-phone"), adopted: document.adoptedStyleSheets.length,
  same: document.adoptedStyleSheets[0].text === ENGINE_CSS }}));
""")
    assert res == {"kind": "string", "hasChip": True, "hasPhone": True, "adopted": 1, "same": True}
    for name in ("engine.js", "view2d.js", "view3d.js", "styles.js"):
        f = GRAPH / name
        if not f.exists():
            continue
        src = f.read_text(encoding="utf-8")
        assert 'setAttribute("style"' not in src and "<style" not in src, name
        assert not re.search(r"\.style\.(?!setProperty|removeProperty)\w+\s*=", src), name


def test_engine_mounts_2d_in_one_place_and_guards_the_3d_fallback():
    """A failed 3D falls back to 2D, and that fallback can fail too (d3
    unreachable). Uncaught, its rejection would escape switchMode: no note, no
    view, a live toolbar over a blank surface. So the 2D mount exists once and
    the fallback call is caught, and nothing runs on a view that never came."""
    src = (GRAPH / "engine.js").read_text(encoding="utf-8")
    assert src.count('import("./view2d.js")') == 1        # one mount path, not two that can drift
    assert re.search(r"await mount2d\(token\)\.catch\(", src)   # the fallback cannot reject outwards
    assert "if (!E.view || E.dead || token !== switching) return;" in src


def test_view3d_is_lazy_and_reuses_the_moved_layout():
    """three.js is ~650KB: it must be reachable ONLY through engine.js's
    dynamic import, or every 2D visitor pays for it. And the 3D integrator
    lives in layout3d.js alone — a copy here would drift from the one the
    layout test measures."""
    src = (GRAPH / "view3d.js").read_text(encoding="utf-8")
    assert 'from "../../vendor/three.module.min.js"' in src
    assert 'from "../../vendor/OrbitControls.js"' in src
    assert 'from "./layout3d.js"' in src
    assert "50 * alpha" not in src   # the integrator lives in layout3d.js only
    for f in GRAPH.glob("*.js"):
        if f.name in ("view3d.js", "engine.js"):
            continue
        # an IMPORT of view3d, not a mention of it: layout3d.js names the file
        # in prose to say who its one consumer is.
        assert not re.search(r"""(?:import|from)\s*\(?\s*["'][^"']*view3d""",
                             f.read_text(encoding="utf-8")), f"{f.name} must not import view3d.js"


def test_view3d_rebuilds_cleanly_and_a_click_does_not_relayout():
    """Three live-only defects a source contract can still pin:

    labels are added to the scene outside `built`, so a rebuild that only drops
    the array leaves stale text drawing (depthTest is off) and leaks a canvas
    texture per sprite; an empty graph never allocates instanceColor, and the
    paint runs after switchMode's try/catch, so the TypeError would escape as
    an unhandled rejection; and a rebuild on select would re-run the 160
    iteration O(n^2) layout on every click, so the engine asks the view to
    repaint focus in place when it can."""
    src = (GRAPH / "view3d.js").read_text(encoding="utf-8")
    body = re.search(r"function clearBuilt\(\) \{(.+?)\n  \}", src, re.S).group(1)
    # labels leave the scene AND free their texture on every rebuild
    assert "clearLabels()" in body
    labels = re.search(r"function clearLabels\(\) \{(.+?)\n  \}", src, re.S).group(1)
    assert "scene.remove(" in labels and "l.dispose()" in labels
    # the empty-graph guard stands before any instanceColor write
    guard = src.index("if (n === 0)")
    assert guard < src.index("mesh.instanceColor.needsUpdate = true")
    # a focus-only repaint that does not call build()
    focus = re.search(r"\n    focus\(\) \{(.+?)\},\n", src, re.S).group(1)
    assert "paintFocus()" in focus and "build()" not in focus

    eng = (GRAPH / "engine.js").read_text(encoding="utf-8")
    assert "E.view.focus ? E.view.focus() : E.view.refresh()" in eng
    sel = re.search(r"function select\(i\) \{(.+?)\n  \}", eng, re.S).group(1)
    assert "repaintFocus()" in sel and "E.view.refresh()" not in sel
