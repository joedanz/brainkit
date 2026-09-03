// The shared graph engine. brainkit ships it at /assets/js/graph/engine.js;
// the portal imports the same file through its /brain/assets/* proxy. The
// contract is the spec's Appendix A — names there are exact.
//
// The engine owns everything inside the host: the canvas, the legend chips,
// the toolbar (search, 2D/3D, Fit, Full graph, settings), the tooltip, focus
// and hover, and the persistence of view preferences. It does not own the
// node card: on selection it calls onSelect(node, neighbours) and each
// product renders its own. onOpen fires on a double-click/double-tap.
//
// Nothing at module load touches the DOM, so node can import this file and
// check the contract (tests/test_graph_helpers_js.py).
import { el } from "../dom.js";
import { healthFlags } from "./health.js";
import { STOPS } from "./labels.js";
import { colorFor } from "./palette.js";
import { adoptStyles } from "./styles.js";

export const ENGINE_VERSION = 1;

// ---- preferences (localStorage, keyed per lens) -------------------------------
const PREF_DEFAULTS = {
  labels: null,          // null → decided by viewport at load time
  hidden: [],            // space names toggled off in the legend
  mode: "2d",
  nodeSize: 1, linkWidth: 1, centerPull: 1, repel: 1, linkDist: 1,
  orphans: false,        // highlight degree-0 pages with a dashed ring
  deadEnds: false,       // highlight pages that link nowhere
};
const NUMERIC = ["nodeSize", "linkWidth", "centerPull", "repel", "linkDist"];

export function prefsKey(lens) { return "brain-graph-engine:" + lens; }

export function loadPrefs(lens, viewport) {
  const p = Object.assign({}, PREF_DEFAULTS);
  try {
    const raw = localStorage.getItem(prefsKey(lens));
    if (raw) Object.assign(p, JSON.parse(raw));
  } catch { /* private mode / quota / bad JSON → defaults */ }
  if (!Array.isArray(p.hidden)) p.hidden = [];
  p.hidden = p.hidden.filter((s) => typeof s === "string");
  if (!STOPS.includes(p.labels)) p.labels = viewport === "phone" ? "hubs" : "more";
  if (p.mode !== "3d") p.mode = "2d";
  for (const k of NUMERIC) if (!Number.isFinite(p[k])) p[k] = PREF_DEFAULTS[k];
  p.orphans = p.orphans === true;
  p.deadEnds = p.deadEnds === true;
  return p;
}

function savePrefs(lens, p) {
  try { localStorage.setItem(prefsKey(lens), JSON.stringify(p)); } catch { /* best effort */ }
}

// ---- d3 ------------------------------------------------------------------------
// d3 is a classic UMD global in brainkit's shell. Imported as a module, the
// same UMD wrapper finds no `exports`/`define` and assigns globalThis.d3, so
// one dynamic import is all a host without the script tag (the portal) needs.
export async function ensureD3() {
  if (!globalThis.d3) {
    const mod = await import("../../vendor/d3.v7.min.js");
    // A host that evaluates the file as CommonJS instead (node, running the
    // contract test) hands d3 back through the namespace rather than the
    // global, so publish it under the same global either way.
    if (!globalThis.d3) globalThis.d3 = mod.default || mod;
  }
  if (!globalThis.d3 || typeof globalThis.d3.forceSimulation !== "function") {
    throw new Error("d3 failed to load");
  }
  return globalThis.d3;
}

// ---- mount ---------------------------------------------------------------------
const SLIDERS = [
  { k: "nodeSize",   label: "Node size",     min: 0.4, max: 2.5, step: 0.05, forces: false },
  { k: "linkWidth",  label: "Link width",    min: 0.3, max: 3,   step: 0.05, forces: false },
  { k: "centerPull", label: "Center pull",   min: 0.1, max: 1,   step: 0.05, forces: true },
  { k: "repel",      label: "Repel force",   min: 0.2, max: 3,   step: 0.05, forces: true },
  { k: "linkDist",   label: "Link distance", min: 0.4, max: 3,   step: 0.05, forces: true },
];

export function mountGraph(host, options) {
  const viewport = options.viewport === "phone" ? "phone" : "desktop";
  const lens = options.lens || "vault";
  const prefs = loadPrefs(lens, viewport);
  const E = {
    host, viewport, lens, prefs,
    data: null, tokens: options.tokens, flags: null, rank: [],
    hidden: new Set(prefs.hidden),
    pos: new Map(),              // rel_path → {x, y}; 2D positions survive live reloads
    selected: null, hood: null, hover: null,
    view: null, mode: "2d", dead: false,
    font: "",
    onSelect: options.onSelect || (() => {}),
    onOpen: options.onOpen || (() => {}),
    loadFull: typeof options.loadFull === "function" ? options.loadFull : null,
    visible(i) { return !E.hidden.has(E.data.nodes[i].space); },
    insets() {
      // Only what actually overlays the canvas: the toolbar (top-right) and,
      // on desktop, the legend (bottom-left). On a phone the legend is a row
      // above the surface, not over it.
      const top = (toolbar.offsetHeight || 0) + 8;
      const bottom = viewport === "desktop" ? (legend.offsetHeight || 0) + 8 : 0;
      return { top, right: 0, bottom, left: 0 };
    },
    setHover(i, cx, cy) {
      E.hover = i;
      if (i == null) { tip.hidden = true; return; }
      const r = host.getBoundingClientRect();
      tip.textContent = E.data.nodes[i].rel_path;
      // Custom properties through CSSOM, never a style= attribute (CSP).
      tip.style.setProperty("--ge-tip-x", Math.max(0, cx - r.left + 12) + "px");
      tip.style.setProperty("--ge-tip-y", Math.max(0, cy - r.top + 12) + "px");
      tip.hidden = false;
    },
    select(i) { select(i); },
    open(i) { if (E.data && E.data.nodes[i]) E.onOpen(E.data.nodes[i]); },
  };

  // --- chrome -------------------------------------------------------------------
  adoptStyles();   // once per document; a remount finds the sheet already adopted
  host.classList.add("ge", "ge-" + viewport);
  E.font = getComputedStyle(host).fontFamily || "system-ui, sans-serif";

  const note = el("div", "ge-note");
  const surface = el("div", "ge-surface");
  E.surface = surface;
  const tip = el("div", "ge-tip"); tip.hidden = true;
  const toolbar = el("div", "ge-toolbar");
  const legend = el("div", "ge-legend");
  legend.setAttribute("aria-label", "Spaces");
  const settings = el("div", "ge-settings"); settings.hidden = true;

  const search = el("input");
  search.type = "search"; search.placeholder = "find a page…";
  search.setAttribute("aria-label", "Find a page in the graph");
  // Search focuses the first match, so any page is reachable without hover.
  search.addEventListener("input", () => {
    const q = search.value.trim().toLowerCase();
    if (!q) { select(null); return; }
    const i = E.data.nodes.findIndex((n, idx) => E.visible(idx) &&
      (n.title.toLowerCase().includes(q) || n.rel_path.toLowerCase().includes(q)));
    select(i >= 0 ? i : null);
  });
  search.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && E.selected != null) E.open(E.selected);
    if (ev.key === "Escape") { search.value = ""; select(null); }
  });
  toolbar.appendChild(search);

  const modeBtn = el("button", "ge-btn", "3D");
  modeBtn.type = "button";
  modeBtn.addEventListener("click", () => switchMode(E.mode === "3d" ? "2d" : "3d", true));
  toolbar.appendChild(modeBtn);

  const fitBtn = el("button", "ge-btn ge-desktop-only", "Fit");
  fitBtn.type = "button";
  fitBtn.setAttribute("aria-label", "Fit the whole graph");
  fitBtn.addEventListener("click", () => E.view && E.view.fit(true));
  toolbar.appendChild(fitBtn);

  // Full graph: only when the host can fetch more (loadFull) and the payload
  // says the cap cut it. The engine applies the result like any update.
  const fullBtn = el("button", "ge-btn ge-desktop-only", "Full graph");
  fullBtn.type = "button";
  fullBtn.hidden = true;
  fullBtn.addEventListener("click", async () => {
    if (!E.loadFull) return;
    fullBtn.disabled = true;
    try {
      const data = await E.loadFull();
      if (E.dead) return;
      setData(data);
      fullBtn.classList.add("on");
    } catch (e) {
      note.textContent = "Full graph unavailable: " + (e && e.message ? e.message : e);
    } finally { fullBtn.disabled = false; }
  });
  toolbar.appendChild(fullBtn);

  const gearBtn = el("button", "ge-btn", "Settings");
  gearBtn.type = "button";
  gearBtn.setAttribute("aria-expanded", "false");
  gearBtn.addEventListener("click", () => {
    settings.hidden = !settings.hidden;
    gearBtn.setAttribute("aria-expanded", settings.hidden ? "false" : "true");
  });
  toolbar.appendChild(gearBtn);

  buildSettings();
  host.appendChild(note);
  host.appendChild(toolbar);
  host.appendChild(legend);
  host.appendChild(settings);
  host.appendChild(surface);
  host.appendChild(tip);

  function buildSettings() {
    // Labels: hubs / more / all
    const row = el("div", "ge-row");
    row.appendChild(el("span", null, "Labels"));
    const seg = el("div", "ge-seg");
    seg.setAttribute("role", "group"); seg.setAttribute("aria-label", "Labels");
    for (const stop of STOPS) {
      const b = el("button", stop === E.prefs.labels ? "on" : "", stop);
      b.type = "button";
      b.addEventListener("click", () => {
        E.prefs.labels = stop;
        seg.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
        persist(); E.view && E.view.refresh();
      });
      seg.appendChild(b);
    }
    row.appendChild(seg);
    settings.appendChild(row);
    // Dead ends (less common than orphans; lives here to keep the chip row short)
    const dead = el("label", "ge-check");
    const cb = el("input"); cb.type = "checkbox"; cb.checked = E.prefs.deadEnds;
    cb.addEventListener("change", () => { E.prefs.deadEnds = cb.checked; persist(); E.view && E.view.refresh(); });
    dead.appendChild(cb); dead.appendChild(el("span", null, "Mark dead ends (pages that link nowhere)"));
    settings.appendChild(dead);
    // Sliders
    for (const def of SLIDERS) {
      const r = el("div", "ge-row");
      r.appendChild(el("span", null, def.label));
      const input = el("input"); input.type = "range";
      input.min = def.min; input.max = def.max; input.step = def.step; input.value = E.prefs[def.k];
      input.setAttribute("aria-label", def.label);
      input.addEventListener("input", () => {
        E.prefs[def.k] = Number(input.value); persist();
        if (!E.view) return;
        if (def.forces) E.view.applyForces(); else E.view.refresh();
      });
      r.appendChild(input);
      settings.appendChild(r);
    }
  }

  function persist() { E.prefs.hidden = [...E.hidden]; E.prefs.mode = E.mode; savePrefs(lens, E.prefs); }

  function applyTokens() {
    const t = E.tokens;
    host.style.setProperty("--ge-bg", t.bg);
    host.style.setProperty("--ge-fg", t.fg);
    host.style.setProperty("--ge-muted", t.muted);
    host.style.setProperty("--ge-line", t.line);
    host.style.setProperty("--ge-panel", `color-mix(in srgb, ${t.bg} 86%, transparent)`);
    host.dataset.theme = t.theme === "dark" ? "dark" : "light";
  }

  // --- data ---------------------------------------------------------------------
  function setData(data) {
    E.data = data;
    E.flags = healthFlags(data);
    // Busiest first: rank 0 is the most linked-to page. Written once per data
    // load so the label rule and the 3D sprites never disagree on who is a hub.
    const order = data.nodes.map((n, i) => i)
      .sort((a, b) => (E.flags.inbound[b] - E.flags.inbound[a]) ||
                      ((data.nodes[b].degree || 0) - (data.nodes[a].degree || 0)) || (a - b));
    E.rank = new Array(data.nodes.length);
    order.forEach((i, r) => { E.rank[i] = r; });
    // Drop persisted positions for notes no longer in the graph so E.pos
    // can't grow without bound across a long-lived, churning session.
    const live = new Set(data.nodes.map((n) => n.rel_path));
    for (const key of [...E.pos.keys()]) if (!live.has(key)) E.pos.delete(key);
    // A renamed space must not stay invisibly off.
    const spaces = new Set(data.nodes.map((n) => n.space));
    for (const s of [...E.hidden]) if (!spaces.has(s)) E.hidden.delete(s);
    // Keep the selection across a live reload when the note still exists.
    const keep = E.selected != null && E.prevPath ? data.nodes.findIndex((n) => n.rel_path === E.prevPath) : -1;
    E.selected = keep >= 0 ? keep : null;
    E.hood = null;
    buildLegend();
    fullBtn.hidden = !(E.loadFull && data.truncated);
    note.textContent = data.truncated ? "Showing the " + data.nodes.length + " most-connected pages." : "";
    if (E.view) E.view.setData();
    if (E.selected != null) select(E.selected);
  }

  function buildLegend() {
    legend.textContent = "";
    const counts = new Map();
    for (const n of E.data.nodes) counts.set(n.space, (counts.get(n.space) || 0) + 1);
    for (const space of [...counts.keys()].sort()) {
      const chip = el("button", "ge-chip" + (E.hidden.has(space) ? " off" : ""));
      chip.type = "button";
      chip.setAttribute("aria-pressed", E.hidden.has(space) ? "false" : "true");
      const dot = el("span", "dot"); dot.style.setProperty("--ge-dot", colorFor(space));
      chip.appendChild(dot);
      chip.appendChild(el("span", null, space));
      chip.appendChild(el("span", "n", String(counts.get(space))));
      chip.addEventListener("click", () => {
        if (E.hidden.has(space)) E.hidden.delete(space); else E.hidden.add(space);
        chip.classList.toggle("off", E.hidden.has(space));
        chip.setAttribute("aria-pressed", E.hidden.has(space) ? "false" : "true");
        persist();
        if (E.selected != null && !E.visible(E.selected)) select(null);
        else if (E.view) E.view.refresh();
      });
      legend.appendChild(chip);
    }
    const orphans = E.flags.orphan.filter(Boolean).length;
    const oc = el("button", "ge-chip orphans" + (E.prefs.orphans ? " on" : ""));
    oc.type = "button";
    oc.setAttribute("aria-pressed", E.prefs.orphans ? "true" : "false");
    oc.appendChild(el("span", null, "orphans"));
    oc.appendChild(el("span", "n", String(orphans)));
    oc.addEventListener("click", () => {
      E.prefs.orphans = !E.prefs.orphans;
      oc.classList.toggle("on", E.prefs.orphans);
      oc.setAttribute("aria-pressed", E.prefs.orphans ? "true" : "false");
      persist(); E.view && E.view.refresh();
    });
    legend.appendChild(oc);
  }

  // --- focus mode -----------------------------------------------------------------
  function neighboursOf(i) {
    const ids = new Set();
    for (const e of E.data.edges) {
      if (e.source === i && e.target !== i) ids.add(e.target);
      if (e.target === i && e.source !== i) ids.add(e.source);
    }
    return [...ids];
  }
  function select(i) {
    if (i == null || !E.data || !E.data.nodes[i]) {
      E.selected = null; E.hood = null; E.prevPath = null;
      if (E.view) E.view.refresh();
      E.onSelect(null, []);
      return;
    }
    const nbrs = neighboursOf(i);
    E.selected = i;
    E.prevPath = E.data.nodes[i].rel_path;
    E.hood = new Set([i, ...nbrs]);
    if (E.view) E.view.refresh();
    E.onSelect(E.data.nodes[i], nbrs.map((j) => E.data.nodes[j]));
  }

  // --- views ------------------------------------------------------------------------
  let switching = 0;
  function errText(e) { return e && e.message ? e.message : String(e); }
  // Mounting 2D is both the 2D path and the fallback for a failed 3D, so it
  // lives in one place: two copies could drift, and the copy in the catch is
  // the one nobody exercises until the day WebGL is gone.
  async function mount2d(token) {
    const d3 = await ensureD3();
    const mod = await import("./view2d.js");
    if (E.dead || token !== switching) return;
    E.view = mod.createView2d(E, d3);
  }
  async function switchMode(mode, remember) {
    const token = ++switching;
    if (E.view) { E.view.destroy(); E.view = null; }
    surface.textContent = "";
    note.textContent = E.data && E.data.truncated ? "Showing the " + E.data.nodes.length + " most-connected pages." : "";
    try {
      if (mode === "3d") {
        const mod = await import("./view3d.js");
        if (E.dead || token !== switching) return;
        E.view = mod.createView3d(E);
      } else {
        await mount2d(token);
      }
      E.mode = mode;
    } catch (e) {
      if (E.dead || token !== switching) return;
      if (mode !== "3d") { note.textContent = "Graph unavailable: " + errText(e); return; }
      // No WebGL (headless, disabled GPU, locked-down browser) → 2D, with a
      // one-line note rather than a stuck, empty toolbar button.
      note.textContent = "3D view unavailable (WebGL): " + errText(e);
      E.mode = "2d";
      // The fallback can fail too (d3 unreachable). Caught here: an escaping
      // rejection would leave a live toolbar over a blank surface saying
      // nothing about why.
      await mount2d(token).catch((e2) => { note.textContent = "Graph unavailable: " + errText(e2); });
    }
    // A mount that failed or was overtaken has no view to drive.
    if (!E.view || E.dead || token !== switching) return;
    modeBtn.textContent = E.mode === "3d" ? "2D" : "3D";
    modeBtn.classList.toggle("on", E.mode === "3d");
    if (remember) persist();
    E.view.setData();
    if (E.selected != null) E.view.refresh();
  }

  // --- boot --------------------------------------------------------------------------
  applyTokens();
  setData(options.data);
  // mode: an explicit option, else the choice remembered for this lens, else 2D.
  const initial = options.mode === "3d" || options.mode === "2d" ? options.mode : prefs.mode;
  switchMode(initial, false);

  return {
    setMode(mode) { return switchMode(mode === "3d" ? "3d" : "2d", true); },
    fit() { if (E.view) E.view.fit(true); },
    focus(relPath) {
      if (relPath == null) { select(null); return; }
      const i = E.data.nodes.findIndex((n) => n.rel_path === relPath);
      select(i >= 0 ? i : null);
    },
    update({ data, tokens } = {}) {
      if (tokens) { E.tokens = tokens; applyTokens(); if (E.view) E.view.setTokens(); }
      if (data) setData(data);
    },
    destroy() {
      E.dead = true;
      if (E.view) { E.view.destroy(); E.view = null; }
      host.classList.remove("ge", "ge-desktop", "ge-phone");
      delete host.dataset.theme;
      for (const p of ["--ge-bg", "--ge-fg", "--ge-muted", "--ge-line", "--ge-panel"]) host.style.removeProperty(p);
      host.textContent = "";   // the adopted stylesheet stays; it is inert without a .ge host
    },
  };
}
