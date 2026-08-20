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

// Where a label sits above its node. The radius is in layout units and should
// scale with the node, but the clearance above it is a screen distance — at the
// zoom a fitted vault opens on, a fixed 11 renders as three pixels and puts
// every name on top of its own node.
function labelY(d) { return d.y + rOf(d) + 11 / (S.transform.k || 1); }

// The drawn extent of the settled nodes, radii included. A node the simulation
// has not placed yet has no coordinates at all, and one that has gone
// non-finite would drag the bounds out and blank the whole view, so both are
// skipped rather than allowed to decide the frame.
function boundsOf(nodes) {
  const b = { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity };
  for (const n of nodes) {
    if (!Number.isFinite(n.x) || !Number.isFinite(n.y)) continue;
    const r = rOf(n);
    b.minX = Math.min(b.minX, n.x - r); b.maxX = Math.max(b.maxX, n.x + r);
    b.minY = Math.min(b.minY, n.y - r); b.maxY = Math.max(b.maxY, n.y + r);
  }
  return b;
}

// The transform that puts those bounds inside the USABLE part of a w x h frame.
// forceCenter only moves the centroid to the middle and says nothing about how
// far the graph spreads: measured on a real 300-note vault, the layout settled
// to 1482x1514 inside an 810x558 frame and 120 of the 300 notes simply sat
// outside the edges.
//
// `inset` is per-side rather than one padding because the controls overlay
// floats above the canvas: centring on the whole frame puts the densest part of
// the graph — and therefore the labelled landmarks — underneath it.
function fitTransform(b, w, h, inset, minK, maxK) {
  const availW = w - inset.left - inset.right;
  const availH = h - inset.top - inset.bottom;
  const spanX = b.maxX - b.minX, spanY = b.maxY - b.minY;
  // An axis with no span does not constrain the scale, which is what Infinity
  // says exactly. Both without a span means there is nothing to fit — one note,
  // or every note still stacked on the same spot on the first tick.
  const kx = spanX > 0 ? availW / spanX : Infinity;
  const ky = spanY > 0 ? availH / spanY : Infinity;
  let k = Math.min(kx, ky);
  if (k === Infinity) k = 1;
  // Negated comparisons, so bounds that are not numbers land on a limit rather
  // than propagating a NaN into the transform and blanking the view.
  if (!(k > minK)) k = minK;
  if (!(k < maxK)) k = maxK;
  let cx = (b.minX + b.maxX) / 2, cy = (b.minY + b.maxY) / 2;
  if (!Number.isFinite(cx)) cx = 0;
  if (!Number.isFinite(cy)) cy = 0;
  return d3.zoomIdentity
    .translate(inset.left + availW / 2 - k * cx, inset.top + availH / 2 - k * cy)
    .scale(k);
}

// What the controls overlay actually covers right now, measured rather than
// assumed: collapsing it gives the space straight back on the next fit.
function fitInset(host) {
  const pad = 24;
  const box = host.querySelector(".graph-controls");
  if (!box) return { top: pad, right: pad, bottom: pad, left: pad };
  const h = host.getBoundingClientRect(), c = box.getBoundingClientRect();
  // Only the width it takes off the left edge; it never spans the full height,
  // and reserving its height too would waste most of the canvas.
  return { top: pad, right: pad, bottom: pad, left: Math.max(pad, c.right - h.left + 12) };
}

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
    autoFit: true,            // until a real gesture takes the view
    fitNow: null,             // set by draw(); the Fit button calls it
    gap: 0,                   // median on-screen spacing, measured once settled
    threeD: false,
    loads: latest(),          // guards out-of-order graph fetches
    factLoads: latest(),      // guards out-of-order node-panel facts fetches
    facts: null,              // {relPath, hits} — reused across live redraws
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
    sel.addEventListener("change", () => { S.ctx.person = sel.value; S.pos.clear(); S.prev.clear(); S.facts = null; load(false); });
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

  // Auto-fit hands the view back to whoever is looking the moment they scroll
  // or drag, so this is how they get it back.
  const fit = el("button", null, "Fit");
  fit.setAttribute("aria-label", "Fit the whole graph");
  fit.addEventListener("click", () => {
    S.autoFit = true;
    if (S.fitNow) S.fitNow(true);
  });
  bar.appendChild(fit);

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
  // Busiest first, so the label rule can always name the landmarks whatever the
  // zoom. Written onto the node rather than derived at draw time because the
  // selection is rebuilt on every redraw and the order must not shift with it.
  [...nodes].sort((a, b) => (b.degree || 0) - (a.degree || 0))
            .forEach((n, i) => { n.rank = i; });
  const labelGroup = gWrap.append("g");
  S._labelGroup = labelGroup;
  const label = labelGroup.selectAll("text").data(nodes).join("text")
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
      label.attr("x", (d) => d.x).attr("y", labelY);
      nodes.forEach((n) => S.pos.set(n.rel_path, { x: n.x, y: n.y }));
      // Re-fitting each tick makes the graph look like it holds still while it
      // organises itself, instead of growing past the edges of its own frame.
      if (S.autoFit) S.fitNow(false);
    })
    // The moment the layout stops moving, the on-screen spacing is worth
    // measuring — it is what decides how many names there is room for.
    .on("end", () => {
      S.gap = medianGap(nodes);
      updateLabels();
    });
  S.sim = sim;

  S._node = node; S._link = link;
  S._label = label;

  const zoom = d3.zoom().scaleExtent([0.1, 8]).on("zoom", (ev) => {
    S.transform = ev.transform;
    gWrap.attr("transform", ev.transform);
    // d3 leaves sourceEvent null for a transform the page applied to itself and
    // carries the wheel or drag event for one a person made. That is the only
    // thing separating "the layout moved" from "they went looking" — without
    // it the auto-fit would yank the view back on the very next tick.
    if (ev.sourceEvent) S.autoFit = false;
    updateLabels();
  });
  svg.call(zoom);

  S.fitNow = (animate) => {
    const t = fitTransform(boundsOf(nodes), W, H, fitInset(S.host), 0.1, 8);
    zoom.transform(animate ? svg.transition().duration(220) : svg, t);
  };

  // Collapsing or opening the overlay changes how much canvas there is, and by
  // then the simulation has usually stopped ticking, so nothing else would
  // re-fit. Only while the view still belongs to the page.
  if (window.ResizeObserver) {
    const box = S.host.querySelector(".graph-controls");
    if (box) {
      S._fitWatch?.disconnect();
      S._fitWatch = new ResizeObserver(() => { if (S.autoFit && S.fitNow) S.fitNow(true); });
      S._fitWatch.observe(box);
    }
  }

  // A view carried over from the previous draw is where the person left it, so
  // it counts as their choice and the fit must not overrule it.
  if (preserveView && S.transform !== d3.zoomIdentity) {
    S.autoFit = false;
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

// The typical distance from a note to its nearest neighbour, in layout units.
// Multiplied by the zoom it gives the spacing ON SCREEN, which is what actually
// decides whether names collide. A median rather than a mean, so one note
// parked far off on its own cannot speak for the rest.
function medianGap(nodes) {
  const placed = nodes.filter((n) => Number.isFinite(n.x) && Number.isFinite(n.y));
  const n = placed.length;
  if (n < 2) return 0;
  const step = Math.ceil(n / 400);   // past a few hundred, sample: it is a median anyway
  const gaps = [];
  for (let i = 0; i < n; i += step) {
    let best = Infinity;
    for (let j = 0; j < n; j++) {
      if (i === j) continue;
      const dx = placed[i].x - placed[j].x, dy = placed[i].y - placed[j].y;
      best = Math.min(best, dx * dx + dy * dy);
    }
    if (best < Infinity) gaps.push(Math.sqrt(best));
  }
  if (!gaps.length) return 0;
  gaps.sort((a, b) => a - b);
  return gaps[Math.floor(gaps.length / 2)];
}

// Labels are world-space text that fades in as notes spread apart on screen —
// hidden on the far-out constellation, readable when you fly in (Obsidian's
// behavior). The busiest handful are always named: they are the landmarks a
// person steers by, and they are worth an overlap.
//
// The threshold is on-screen SPACING, not zoom alone. It used to be a fixed
// zoom (1.4 / textFade), which was wrong at both ends — and became actively
// wrong once the view fits itself to the frame, because fitting a 300-note
// vault lands at about k=0.5, below the old fade-in floor, so every name would
// have disappeared the moment the graph became visible.
const LABEL_ROOM_PX = 44;   // spacing between node centres, chosen by eye; not a text width
const LANDMARKS = 12;
function updateLabels() {
  if (!S || !S._label) return;
  const k = S.transform.k;
  // Counter-scaled through an inherited custom property rather than per label:
  // this runs on every tick of the settle, and 300 inline styles a tick to say
  // one number is work the browser does not need to do. The .graph-label rule
  // reads it, so the CSS keeps owning how a label looks.
  if (S._labelGroup) S._labelGroup.style("--graph-label-size", (10 / k) + "px");
  // textFade keeps its meaning: turning it up brings the names in sooner.
  const need = LABEL_ROOM_PX / (S.settings.textFade || 1);
  const room = S.gap > 0 ? (k * S.gap) / need : 0;
  const o = Math.max(0, Math.min(1, (room - 0.55) / 0.45));
  S._label
    .style("opacity", (d) => (d.rank < LANDMARKS ? Math.max(o, 0.75) : o))
    .style("display", (d) => {
      if (!matches(d)) return "none";
      return (d.rank < LANDMARKS || o >= 0.02) ? null : "none";
    });
}

// Display changes restyle in place — no sim reheat, the layout must not jump.
function applyDisplay() {
  if (!S || !S._node) return;
  S._node.attr("r", rOf);
  S._link.style("stroke-width", S.settings.linkWidth);
  S._label.attr("y", labelY);
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
  if (d.entity && d.aliases && d.aliases.length) {
    S.panel.appendChild(el("div", "space-tag", "aka " + d.aliases.join(", ")));
  }

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
  // A live push rebuilds this panel; reuse the cached facts for the same node
  // instead of refetching /api/facts on every push. A real click (different
  // rel_path) or a person switch (cache cleared) falls through to a fetch.
  if (d.entity) {
    if (S.facts && S.facts.relPath === d.rel_path) renderFacts(S.facts.hits);
    else loadFacts(d);
  }
}

// For an entity node, fetch its current facts and cache them by rel_path. The
// token guard drops replies that arrive after another selection (or a
// dispose); on any failure nothing is appended — the panel stays useful.
async function loadFacts(d) {
  const token = S.factLoads.begin();
  try {
    const params = { entity: d.rel_path };
    if (S.ctx.meta.kind === "master") params.person = S.ctx.person;
    const body = await api.facts(params);
    if (!S || !S.factLoads.current(token)) return;
    S.facts = { relPath: d.rel_path, hits: body.hits };
    renderFacts(body.hits);
  } catch { /* no facts block on error — never disrupt the panel */ }
}

// Append the facts block from already-fetched hits. Synchronous so a cached
// reselect renders in the same frame as the rest of the panel.
function renderFacts(hits) {
  if (!hits.length) return;
  const host = el("div");
  host.appendChild(el("h3", null, "Facts (" + hits.length + ")"));
  const ul = el("ul");
  hits.forEach((h) => {
    ul.appendChild(el("li", null,
      h.statement + "  (" + h.from_date + " → " + (h.until_date || "") + ")"));
  });
  host.appendChild(ul);
  S.panel.appendChild(host);
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
