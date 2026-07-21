import { el, clear, colorFor, latest } from "../dom.js";
import { api } from "../api.js";
import { loadSettings, saveSettings, mountControls } from "./graph-controls.js";

// Interactive knowledge graph (D3 force layout in SVG). Node positions persist
// across live reloads in `pos`, so when the brain updates the layout stays put
// and only genuinely new notes spring in and pulse. Zoom/pan transform is kept
// in `S.transform` and re-applied on reload so a push never yanks the view.
//
// three.js 3D mode is loaded lazily (graph3d.js) so its ~650KB parses only when
// the user asks for it.

let S = null; // active graph state; module-level so onLive() can reach it

// Node radius: grows with degree (sqrt-scaled so hubs don't dwarf everything)
// and scales with the user's nodeSize setting. Shared by draw(), applyDisplay(),
// and the tick handler's label y-offset so the three never drift apart.
function rOf(d) { return (4 + 2.5 * Math.sqrt(d.degree)) * S.settings.nodeSize; }

export function render(container, ctx) {
  clear(container);
  S = {
    ctx, container,
    cap: 300,
    search: "",
    storeKey: "brain-graph:" + (ctx.meta.kind === "master" ? "master" : "vault"),
    settings: null,           // filled below
    pos: new Map(),           // rel_path -> {x, y}, persisted across reloads
    prev: new Set(),          // rel_paths from the previous load
    transform: d3.zoomIdentity,
    threeD: false,
    loads: latest(),          // guards out-of-order graph fetches
    host: null, panel: null, controls: null, sel: null,
  };
  S.settings = loadSettings(S.storeKey);
  buildChrome();
  load(false);
}

export function onLive() { if (S && !S.threeD) load(true); }

export function dispose() {
  if (S) {
    if (S._three) { S._three.dispose(); S._three = null; }
    if (S.sim) S.sim.stop();
    clearTimeout(S._t3);
  }
  S = null;
}

function buildChrome() {
  const bar = el("div", "graph-toolbar");

  if (S.ctx.meta.kind === "master") {
    const sel = el("select");
    sel.setAttribute("aria-label", "Person");
    S.ctx.meta.people.forEach((p) => {
      const o = el("option", null, p.name || p.id);
      o.value = p.id;
      if (p.id === S.ctx.person) o.selected = true;
      sel.appendChild(o);
    });
    sel.addEventListener("change", () => { S.ctx.person = sel.value; S.pos.clear(); S.prev.clear(); load(false); });
    bar.appendChild(sel);
  }

  const full = el("button", null, "Full graph");
  full.addEventListener("click", () => {
    S.cap = S.cap >= 2000 ? 300 : 2000;
    full.classList.toggle("on", S.cap >= 2000);
    load(false);
  });
  bar.appendChild(full);

  const d3d = el("button", null, "3D view");
  d3d.addEventListener("click", () => toggle3D(d3d));
  bar.appendChild(d3d);

  S.container.appendChild(bar);

  const wrap = el("div", "graph-wrap");
  S.host = el("div", "graph-host");
  // Render surface for svg/canvas/error-banner, kept separate from S.host so
  // the controls overlay (mounted once, below) survives every draw()'s clear().
  S.canvas = el("div", "graph-canvas");
  S.host.appendChild(S.canvas);
  S.panel = el("div");
  S.panel.id = "graph-panel";
  S.panel.appendChild(el("div", "hint", "Click a note to see its connections."));
  wrap.appendChild(S.host);
  wrap.appendChild(S.panel);
  S.container.appendChild(wrap);

  const persist = () => saveSettings(S.storeKey, S.settings);
  S.controls = mountControls(S.host, {
    settings: S.settings,
    spaces: [],
    truncatedNote: null,
    onSearch: (text) => { S.search = text.toLowerCase(); refreshVisibility(); },
    onFilter: () => { persist(); refreshVisibility(); },
    onDisplay: () => { persist(); applyDisplay(); },
    onForces: () => { persist(); applyForces(); },
    onPersist: persist,
  });
}

async function toggle3D(button) {
  clearTimeout(S._t3);
  if (S.threeD) { // turn 3D off, back to the 2D SVG
    S.threeD = false;
    button.classList.remove("on");
    if (S._three) { S._three.dispose(); S._three = null; }
    load(false);
    return;
  }
  S.threeD = true;
  button.classList.add("on");
  const token = S.loads.begin();
  try {
    const mod = await import("./graph3d.js");
    if (!S || !S.loads.current(token)) return; // disposed mid-import
    const params = S.ctx.meta.kind === "master" ? { cap: S.cap, person: S.ctx.person } : { cap: S.cap };
    const g = await api.graph(params);
    if (!S || !S.loads.current(token)) return; // disposed / superseded mid-fetch
    S.graph = g;
    S._threeMod = mod;
    if (S._three) S._three.dispose();
    S._three = mod.mount(S.canvas, filteredGraph(), (node) => selectByPath(node.rel_path, S.graph));
  } catch (e) {
    if (!S || !S.loads.current(token)) return;
    // No WebGL (headless, disabled GPU, locked-down browser) → fall back to 2D
    // with a note rather than a stuck, empty toolbar button.
    S.threeD = false;
    button.classList.remove("on");
    load(false);
    clear(S.panel);
    S.panel.appendChild(el("div", "hint", "3D view unavailable (WebGL): " + (e.message || e)));
  }
}

async function load(preserveView) {
  const token = S.loads.begin();
  let g;
  try {
    const params = S.ctx.meta.kind === "master" ? { cap: S.cap, person: S.ctx.person } : { cap: S.cap };
    g = await api.graph(params);
  } catch (e) {
    if (!S || !S.loads.current(token)) return; // disposed or superseded
    clear(S.canvas);
    S.canvas.appendChild(el("div", "error-banner", "Graph unavailable: " + e.message));
    return;
  }
  if (!S || !S.loads.current(token)) return; // tab switched / newer load won
  draw(g, preserveView);
}

function draw(g, preserveView) {
  if (S.sim) { S.sim.stop(); S.sim = null; } // stop the prior sim before a new one
  clear(S.canvas);
  S.graph = g;
  const W = S.host.clientWidth || 800;
  const H = S.host.clientHeight || 560;

  // Drop persisted positions for notes no longer in the graph so S.pos can't
  // grow without bound across a long-lived, churning session.
  const live = new Set(g.nodes.map((n) => n.rel_path));
  for (const key of S.pos.keys()) if (!live.has(key)) S.pos.delete(key);

  const byId = new Map(g.nodes.map((n) => [n.id, n]));
  const fresh = new Set();
  const nodes = g.nodes.map((n) => {
    const p = S.pos.get(n.rel_path);
    if (!p && S.prev.size) fresh.add(n.rel_path); // new since last load → pulse
    return Object.assign({}, n, p ? { x: p.x, y: p.y } : {});
  });
  const links = g.edges.map((e) => ({ source: e.source, target: e.target }));

  // adjacency from the raw integer pairs (before forceLink rewrites them to refs)
  const adj = new Map();
  g.nodes.forEach((n) => adj.set(n.id, { out: [], in: [] }));
  g.edges.forEach((e) => { adj.get(e.source).out.push(e.target); adj.get(e.target).in.push(e.source); });

  const svg = d3.select(S.canvas).append("svg").attr("viewBox", `0 0 ${W} ${H}`);
  svg.classed("no-glow", nodes.length > 800);
  const gWrap = svg.append("g");

  const link = gWrap.append("g").selectAll("line").data(links).join("line").attr("class", "link")
    .style("stroke-width", S.settings.linkWidth);
  const label = gWrap.append("g").selectAll("text").data(nodes).join("text")
    .attr("class", "graph-label")
    .text((d) => d.title);
  const node = gWrap.append("g").selectAll("circle").data(nodes).join("circle")
    .attr("class", "node")
    .attr("r", rOf)
    .attr("fill", (d) => colorFor(d.space))
    .style("color", (d) => colorFor(d.space))
    .attr("stroke", (d) => (d.entity ? colorFor("entity:" + d.entity) : null))
    .attr("stroke-width", (d) => (d.entity ? 2 : 0))
    .classed("pulse", (d) => fresh.has(d.rel_path))
    .on("click", (ev, d) => select(d, adj, byId))
    .on("mouseenter", (ev, d) => focus(d, adj))
    .on("mouseleave", () => unfocus());
  node.append("title").text((d) => d.title);

  const sim = d3.forceSimulation(nodes)
    .force("link", d3.forceLink(links).id((d) => d.id)
      .distance(60 * S.settings.linkDist).strength(0.4))
    .force("charge", d3.forceManyBody().strength(-90 * S.settings.repel))
    .force("center", d3.forceCenter(W / 2, H / 2).strength(S.settings.centerPull))
    .on("tick", () => {
      link.attr("x1", (d) => d.source.x).attr("y1", (d) => d.source.y)
          .attr("x2", (d) => d.target.x).attr("y2", (d) => d.target.y);
      node.attr("cx", (d) => d.x).attr("cy", (d) => d.y);
      label.attr("x", (d) => d.x)
           .attr("y", (d) => d.y + rOf(d) + 11);
      nodes.forEach((n) => S.pos.set(n.rel_path, { x: n.x, y: n.y }));
    });
  S.sim = sim;

  S._node = node; S._link = link;
  S._label = label;

  const zoom = d3.zoom().scaleExtent([0.1, 8]).on("zoom", (ev) => {
    S.transform = ev.transform;
    gWrap.attr("transform", ev.transform);
    updateLabels();
  });
  svg.call(zoom);
  if (preserveView && S.transform !== d3.zoomIdentity) {
    svg.call(zoom.transform, S.transform);
  }

  node.call(d3.drag()
    .on("start", (ev, d) => { if (!ev.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
    .on("drag", (ev, d) => { d.fx = ev.x; d.fy = ev.y; })
    .on("end", (ev, d) => { if (!ev.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));

  if (fresh.size) sim.alpha(0.5).restart();
  setTimeout(() => node.classed("pulse", false), 1500);

  updateLabels();
  S.prev = new Set(g.nodes.map((n) => n.rel_path));
  const spaces = [...new Set(g.nodes.map((n) => n.space))].sort()
    .map((name) => ({ name, color: colorFor(name) }));
  S.controls.updateSpaces(spaces,
    g.truncated ? "Showing the " + g.nodes.length + " most-connected notes." : null);
  const types = [...new Set(g.nodes.map((n) => n.entity).filter(Boolean))].sort()
    .map((name) => ({ name, color: colorFor("entity:" + name) }));
  S.controls.updateEntities(types);
  refreshVisibility();
  if (S.sel != null) reselect(adj, byId);
}

// A remapped copy of S.graph containing only nodes that pass matches().
// Ids are re-indexed because graph3d indexes positions by node order.
function filteredGraph() {
  const g = S.graph;
  const kept = g.nodes.filter((n) => matches(n));
  const idMap = new Map(kept.map((n, i) => [n.id, i]));
  return {
    nodes: kept.map((n, i) => Object.assign({}, n, { id: i })),
    edges: g.edges
      .filter((e) => idMap.has(e.source) && idMap.has(e.target))
      .map((e) => ({ source: idMap.get(e.source), target: idMap.get(e.target) })),
    truncated: g.truncated,
  };
}

// Debounced remount so typing in search doesn't rebuild the scene per keystroke.
function refresh3D() {
  clearTimeout(S._t3);
  S._t3 = setTimeout(() => {
    if (!S || !S.threeD || !S.graph || !S._threeMod) return;
    if (S._three) S._three.dispose();
    S._three = S._threeMod.mount(S.canvas, filteredGraph(),
      (node) => selectByPath(node.rel_path, S.graph));
  }, 250);
}

function matches(d) {
  if (S.settings.spacesOff.includes(d.space)) return false;
  if (d.entity && S.settings.entitiesOff.includes(d.entity)) return false;
  if (!S.settings.orphans && d.degree === 0) return false;
  if (!S.search) return true;
  return d.title.toLowerCase().includes(S.search) || d.rel_path.toLowerCase().includes(S.search);
}

function refreshVisibility() {
  if (!S._node) return;
  S._node.classed("dim", (d) => !matches(d));
  S._link.style("display", (d) =>
    (matches(d.source) && matches(d.target)) ? null : "none");
  updateLabels();
  if (S.threeD) refresh3D();
}

// Labels are world-space text that fades in past a zoom threshold — hidden on
// the far-out constellation, readable when you fly in (Obsidian's behavior).
function updateLabels() {
  if (!S || !S._label) return;
  const k = S.transform.k;
  const t1 = 1.4 / (S.settings.textFade || 1); // fully visible at this zoom level
  const t0 = 0.55 * t1;              // starts appearing here
  const o = Math.max(0, Math.min(1, (k - t0) / (t1 - t0)));
  S._label
    .style("opacity", o)
    .style("display", (d) => (o < 0.02 || !matches(d)) ? "none" : null);
}

// Display changes restyle in place — no sim reheat, the layout must not jump.
function applyDisplay() {
  if (!S || !S._node) return;
  S._node.attr("r", rOf);
  S._link.style("stroke-width", S.settings.linkWidth);
  S._label.attr("y", (d) => d.y + rOf(d) + 11);
  updateLabels();
}

// Force changes retune the running sim and reheat so the layout re-settles.
function applyForces() {
  if (!S || !S.sim) return;
  S.sim.force("link").distance(60 * S.settings.linkDist);
  S.sim.force("charge").strength(-90 * S.settings.repel);
  S.sim.force("center").strength(S.settings.centerPull);
  S.sim.alpha(0.4).restart();
}

// Hovering a note lights its neighborhood and recedes everything else —
// classes only; filter-driven .dim / display rules are untouched.
function focus(d, adj) {
  if (!S || !S._node) return;
  const hood = new Set([d.id]);
  const a = adj.get(d.id) || { out: [], in: [] };
  a.out.forEach((i) => hood.add(i));
  a.in.forEach((i) => hood.add(i));
  S._node.classed("faded", (n) => !hood.has(n.id));
  S._label.classed("faded", (n) => !hood.has(n.id));
  S._link
    .classed("hot", (l) => l.source.id === d.id || l.target.id === d.id)
    .classed("faded", (l) => !(hood.has(l.source.id) && hood.has(l.target.id)));
}

function unfocus() {
  if (!S || !S._node) return;
  S._node.classed("faded", false);
  S._label.classed("faded", false);
  S._link.classed("hot", false).classed("faded", false);
}

function select(d, adj, byId) {
  S.sel = d.id;
  reselect(adj, byId);
}

function reselect(adj, byId) {
  const d = byId.get(S.sel);
  if (!d) return;
  clear(S.panel);
  S.panel.appendChild(el("h3", null, d.title));
  S.panel.appendChild(el("div", "space-tag",
    d.space + " · " + d.rel_path + (d.entity ? " · " + d.entity : "")));

  const open = el("button", "btn", "Open in Query");
  open.style.margin = "8px 0";
  open.addEventListener("click", () => S.ctx.openNote(d.rel_path));
  S.panel.appendChild(open);

  const a = adj.get(d.id) || { out: [], in: [] };
  const list = (label, ids) => {
    S.panel.appendChild(el("h3", null, label + " (" + ids.length + ")"));
    const ul = el("ul");
    ids.map((i) => byId.get(i).title).sort().forEach((t) => ul.appendChild(el("li", null, t)));
    S.panel.appendChild(ul);
  };
  list("Links to", a.out);
  list("Linked from", a.in);
}

function selectByPath(relPath, g) {
  // Guards on S alone, not S._node: while a session enters 3D before the
  // first 2D load() resolves, that load's token is invalidated by 3D's own
  // S.loads.begin() (see toggle3D), so draw() — the only place that sets
  // S._node — never runs. selectByPath doesn't touch S._node/_link/_label
  // itself (it rebuilds byId/adj fresh from `g` every call), so gating on it
  // only served to silently drop every 3D-first click with no error.
  if (!S) return;
  const n = g.nodes.find((x) => x.rel_path === relPath);
  if (n) {
    const byId = new Map(g.nodes.map((x) => [x.id, x]));
    const adj = new Map();
    g.nodes.forEach((x) => adj.set(x.id, { out: [], in: [] }));
    g.edges.forEach((e) => { adj.get(e.source).out.push(e.target); adj.get(e.target).in.push(e.source); });
    select(n, adj, byId);
  }
}
