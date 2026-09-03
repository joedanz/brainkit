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


def test_graph_tab_render_disposes_a_previous_singleton_before_building():
    """app.js's showTab() only calls dispose() when the tab id changes, so
    re-clicking the already-active Graph tab calls render() again with the
    old S still live. A reassignment that drops S without tearing it down
    orphans the mounted engine — in 3D a live WebGLRenderer whose rAF loop
    never stops — and repeated re-clicks exhaust the browser's WebGL context
    cap. render() must be idempotent: dispose any previous S first."""
    src = (JS / "tabs" / "graph.js").read_text(encoding="utf-8")
    body = re.search(r"export function render\(container, ctx\) \{\n(.*?)\n\}\n", src, re.S).group(1)
    teardown = body.find("dispose()")
    reassign = body.find("S = {")
    assert teardown >= 0, "render() must tear down a previous S (reuse dispose()) before reassigning it"
    assert reassign >= 0
    assert teardown < reassign, "the previous singleton must be disposed BEFORE render() reassigns S"


def test_note_view_resolver_matches_path_stem_or_trailing_segment():
    """buildResolver is pure, so the wikilink → rel_path rule is asserted here
    against the shipped module (both tabs now share it)."""
    res = run_js(f"""
const {{ buildResolver, noteView, renderLinks }} = await import({json.dumps((JS / "note-view.js").as_uri())});
const r = buildResolver([
  {{ rel_path: "Company/Decisions/Big Deal Decision.md", title: "Big Deal Decision" }},
  {{ rel_path: "Teams/sales/Q3 Pipeline.md", title: "Q3 Pipeline" }},
]);
console.log(JSON.stringify({{
  byPath: r("Teams/sales/Q3 Pipeline.md"),
  byStem: r(" Big Deal Decision "),
  trailing: r("Decisions/Big Deal Decision.md"),
  miss: r("Nowhere"),
  kinds: [typeof noteView, typeof renderLinks],
}}));
""")
    assert res == {"byPath": "Teams/sales/Q3 Pipeline.md",
                   "byStem": "Company/Decisions/Big Deal Decision.md",
                   "trailing": "Company/Decisions/Big Deal Decision.md",
                   "miss": None, "kinds": ["function", "function"]}


def test_query_tab_uses_the_shared_note_view():
    src = (JS / "tabs" / "query.js").read_text(encoding="utf-8")
    assert 'from "../note-view.js"' in src
    assert "function buildResolver" not in src and "function renderLinks" not in src


_TREE = {
    "name": "", "path": "", "count": 3, "pages": [],
    "dirs": [
        {"name": "Company", "path": "Company", "count": 3,
         "pages": [{"rel_path": "Company/Home.md", "title": "Home", "mtime": "2026-09-01"}],
         "dirs": [{"name": "Decisions", "path": "Company/Decisions", "count": 2, "dirs": [],
                   "pages": [{"rel_path": "Company/Decisions/b.md", "title": "b", "mtime": ""},
                             {"rel_path": "Company/Decisions/A.md", "title": "A", "mtime": ""}]}]},
    ],
}


def _model(expr: str):
    return run_js(f"""
const M = await import({json.dumps((JS / "tree-model.js").as_uri())});
const root = {json.dumps(_TREE)};
console.log(JSON.stringify({expr}));
""")


def test_tree_model_ancestors_and_crumbs():
    assert _model('M.ancestors("Company/Decisions/A.md")') == ["Company", "Company/Decisions"]
    assert _model('M.ancestors("Top.md")') == []
    assert _model('M.crumbsFor("Company/Decisions")') == [
        {"name": "", "path": ""}, {"name": "Company", "path": "Company"},
        {"name": "Decisions", "path": "Company/Decisions"}]
    assert _model('M.crumbsFor("")') == [{"name": "", "path": ""}]


def test_tree_model_find_dir_and_locale_order():
    assert _model('M.findDir(root, "").path') == ""
    assert _model('M.findDir(root, "Company/Decisions").count') == 2
    assert _model('M.findDir(root, "Company/Nope")') is None
    assert _model('M.sortedChildren(M.findDir(root, "Company/Decisions")).pages.map(p => p.title)') == ["A", "b"]
    assert _model('M.sortedChildren(root).dirs.map(d => d.name)') == ["Company"]


def test_pages_tab_is_second_in_both_lenses_and_routes_page_hashes():
    src = (JS / "app.js").read_text(encoding="utf-8")
    assert 'import * as pages from "./tabs/pages.js";' in src
    assert 'id: "pages", label: "Pages"' in src
    # Overview then Pages, in the master return AND the vault return
    assert len(re.findall(r"overviewTab,\s*pagesTab", src)) == 2
    assert "ctx.openPage(page)" in src and "pendingPage" in src


def test_pages_tab_module_exports_and_uses_the_shared_pieces():
    src = (JS / "tabs" / "pages.js").read_text(encoding="utf-8")
    for needle in ('from "../note-view.js"', 'from "../tree-model.js"', "pageHash(", "history.replaceState", "(max-width: 899px)"):
        assert needle in src, needle
    res = run_js(f"""
const p = await import({json.dumps((JS / "tabs" / "pages.js").as_uri())});
console.log(JSON.stringify([typeof p.render, typeof p.onLive, typeof p.dispose]));
""")
    assert res == ["function", "function", "function"]


def test_pages_live_push_on_phone_does_not_clobber_an_open_note():
    """A live push on phone (onLive -> load()) must not rebuild an already-open
    note's reader: paintPhone() does clear(S.reader) then remounts noteView,
    which would drop the raw/rendered toggle and in-note scroll and re-fetch
    /api/note for nothing. The phone branch must guard the paintPhone() call on
    S.current, mirroring the desktop branch's
    `if (S.current && !S.reader.firstChild) openPage(...)` guard — only the
    tree data (S.root) refreshes silently while a note is open; the listing
    repaints next time the user goes back to it."""
    src = (JS / "tabs" / "pages.js").read_text(encoding="utf-8")
    m = re.search(r"if \(S\.phone\) \{\n(.*?)\n  \} else \{\n", src, re.S)
    assert m, "the phone branch of load() is not where this test expects it"
    body = m.group(1)
    assert "paintPhone();" in body
    assert re.search(r"if\s*\([^)]*S\.current[^)]*\)\s*paintPhone\(\);", body), \
        "a live push on phone must guard paintPhone() on S.current so an open note keeps its DOM"
