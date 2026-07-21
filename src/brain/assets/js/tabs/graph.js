import { el, clear, colorFor, latest, clickable } from "../dom.js";
import { api } from "../api.js";

// Interactive knowledge graph (D3 force layout in SVG). Node positions persist
// across live reloads in `pos`, so when the brain updates the layout stays put
// and only genuinely new notes spring in and pulse. Zoom/pan transform is kept
// in `S.transform` and re-applied on reload so a push never yanks the view.
//
// three.js 3D mode is loaded lazily (graph3d.js) so its ~650KB parses only when
// the user asks for it.

let S = null; // active graph state; module-level so onLive() can reach it

export function render(container, ctx) {
  clear(container);
  S = {
    ctx, container,
    cap: 300,
    search: "",
    hidden: new Set(),        // spaces toggled off in the legend
    pos: new Map(),           // rel_path -> {x, y}, persisted across reloads
    prev: new Set(),          // rel_paths from the previous load
    transform: d3.zoomIdentity,
    threeD: false,
    loads: latest(),          // guards out-of-order graph fetches
    host: null, panel: null, legend: null, sel: null,
  };
  buildChrome();
  load(false);
}

export function onLive() { if (S && !S.threeD) load(true); }

export function dispose() {
  if (S) {
    if (S._three) { S._three.dispose(); S._three = null; }
    if (S.sim) S.sim.stop();
  }
  S = null;
}

function buildChrome() {
  const bar = el("div", "graph-toolbar");

  const search = el("input");
  search.type = "search";
  search.placeholder = "highlight notes…";
  search.setAttribute("aria-label", "Highlight notes in the graph");
  search.addEventListener("input", () => { S.search = search.value.toLowerCase(); refreshVisibility(); });
  bar.appendChild(search);

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
  S.panel = el("div");
  S.panel.id = "graph-panel";
  S.panel.appendChild(el("div", "hint", "Click a note to see its connections."));
  wrap.appendChild(S.host);
  wrap.appendChild(S.panel);
  S.container.appendChild(wrap);

  S.legend = el("div", "legend");
  S.container.appendChild(S.legend);
}

async function toggle3D(button) {
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
    S._three = mod.mount(S.host, g, (node) => selectByPath(node.rel_path, g));
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
    clear(S.host);
    S.host.appendChild(el("div", "error-banner", "Graph unavailable: " + e.message));
    return;
  }
  if (!S || !S.loads.current(token)) return; // tab switched / newer load won
  draw(g, preserveView);
}

function draw(g, preserveView) {
  if (S.sim) { S.sim.stop(); S.sim = null; } // stop the prior sim before a new one
  clear(S.host);
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

  const svg = d3.select(S.host).append("svg").attr("viewBox", `0 0 ${W} ${H}`);
  svg.classed("no-glow", nodes.length > 800);
  const gWrap = svg.append("g");

  const link = gWrap.append("g").selectAll("line").data(links).join("line").attr("class", "link");
  const node = gWrap.append("g").selectAll("circle").data(nodes).join("circle")
    .attr("class", "node")
    .attr("r", (d) => 4 + 2.5 * Math.sqrt(d.degree))
    .attr("fill", (d) => colorFor(d.space))
    .style("color", (d) => colorFor(d.space))
    .classed("pulse", (d) => fresh.has(d.rel_path))
    .on("click", (ev, d) => select(d, adj, byId));
  node.append("title").text((d) => d.title);

  const sim = d3.forceSimulation(nodes)
    .force("link", d3.forceLink(links).id((d) => d.id).distance(60).strength(0.4))
    .force("charge", d3.forceManyBody().strength(-90))
    .force("center", d3.forceCenter(W / 2, H / 2))
    .on("tick", () => {
      link.attr("x1", (d) => d.source.x).attr("y1", (d) => d.source.y)
          .attr("x2", (d) => d.target.x).attr("y2", (d) => d.target.y);
      node.attr("cx", (d) => d.x).attr("cy", (d) => d.y);
      nodes.forEach((n) => S.pos.set(n.rel_path, { x: n.x, y: n.y }));
    });
  S.sim = sim;

  const zoom = d3.zoom().scaleExtent([0.1, 8]).on("zoom", (ev) => {
    S.transform = ev.transform;
    gWrap.attr("transform", ev.transform);
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

  S._node = node; S._link = link;
  S.prev = new Set(g.nodes.map((n) => n.rel_path));
  buildLegend(g);
  refreshVisibility();
  if (S.sel != null) reselect(adj, byId);
}

function buildLegend(g) {
  clear(S.legend);
  const spaces = [...new Set(g.nodes.map((n) => n.space))].sort();
  spaces.forEach((space) => {
    const chip = el("span", "chip" + (S.hidden.has(space) ? " off" : ""));
    const dot = el("span", "dot"); dot.style.background = colorFor(space);
    chip.appendChild(dot);
    chip.appendChild(document.createTextNode(space));
    chip.setAttribute("aria-pressed", S.hidden.has(space) ? "true" : "false");
    clickable(chip, () => {
      if (S.hidden.has(space)) S.hidden.delete(space); else S.hidden.add(space);
      chip.classList.toggle("off");
      chip.setAttribute("aria-pressed", S.hidden.has(space) ? "true" : "false");
      refreshVisibility();
    });
    S.legend.appendChild(chip);
  });
  if (g.truncated) {
    S.legend.appendChild(el("span", "meta",
      "· showing the " + g.nodes.length + " most-connected notes"));
  }
}

function matches(d) {
  if (S.hidden.has(d.space)) return false;
  if (!S.search) return true;
  return d.title.toLowerCase().includes(S.search) || d.rel_path.toLowerCase().includes(S.search);
}

function refreshVisibility() {
  if (!S._node) return;
  S._node.classed("dim", (d) => !matches(d));
  S._link.style("display", (d) =>
    (matches(d.source) && matches(d.target)) ? null : "none");
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
  S.panel.appendChild(el("div", "space-tag", d.space + " · " + d.rel_path));

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
  const n = g.nodes.find((x) => x.rel_path === relPath);
  if (n && S._node) {
    const byId = new Map(g.nodes.map((x) => [x.id, x]));
    const adj = new Map();
    g.nodes.forEach((x) => adj.set(x.id, { out: [], in: [] }));
    g.edges.forEach((e) => { adj.get(e.source).out.push(e.target); adj.get(e.target).in.push(e.source); });
    select(n, adj, byId);
  }
}
