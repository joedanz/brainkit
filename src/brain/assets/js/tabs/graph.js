import { el, clear, latest } from "../dom.js";
import { api } from "../api.js";
import { mountGraph } from "../graph/engine.js";

// The Graph tab. The engine (graph/engine.js) owns the canvas, legend,
// toolbar, search, settings, focus and hover; this tab owns what is
// brainkit's: the person picker on the admin lens, the theme tokens, the
// fetch, and the node card (facts, "Open in Query", links). On a phone the
// card is a bottom sheet over the canvas, shown on select and dismissed by a
// canvas tap (the engine calls onSelect(null)).

let S = null;
const PHONE = "(max-width: 820px)";
const FULL_CAP = 2000;

// The engine paints with whatever the page's stylesheet says the theme is,
// so a theme flip repaints the graph to match the chrome around it.
function themeTokens() {
  const cs = getComputedStyle(document.documentElement);
  const explicit = document.documentElement.getAttribute("data-theme");
  const theme = explicit === "dark" || explicit === "light" ? explicit
    : (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  return {
    bg: cs.getPropertyValue("--surface-0").trim(),
    fg: cs.getPropertyValue("--text-primary").trim(),
    muted: cs.getPropertyValue("--text-muted").trim(),
    line: cs.getPropertyValue("--border-strong").trim(),
    theme,
  };
}

export function render(container, ctx) {
  clear(container);
  S = {
    ctx, container,
    engine: null, data: null,
    phone: matchMedia(PHONE).matches,
    loads: latest(), factLoads: latest(), facts: null,
    host: null, panel: null,
  };
  buildChrome();
  watchTheme();
  load();
}

export function onLive() { if (S) load(); }

export function dispose() {
  if (S) {
    if (S.engine) S.engine.destroy();
    if (S.themeWatch) S.themeWatch.disconnect();
    if (S.mq) S.mq.removeEventListener("change", S.onTheme);
  }
  S = null;
}

function buildChrome() {
  if (S.ctx.meta.kind === "master") {
    const bar = el("div", "graph-toolbar");
    const sel = el("select");
    sel.setAttribute("aria-label", "Person");
    S.ctx.meta.people.forEach((p) => {
      const o = el("option", null, p.name || p.id);
      o.value = p.id;
      if (p.id === S.ctx.person) o.selected = true;
      sel.appendChild(o);
    });
    // A different person is a different graph: positions and card start over.
    sel.addEventListener("change", () => {
      S.ctx.person = sel.value;
      if (S.engine) { S.engine.destroy(); S.engine = null; }
      S.facts = null;
      renderCard(null, []);
      load();
    });
    bar.appendChild(sel);
    S.container.appendChild(bar);
  }
  const wrap = el("div", "graph-wrap" + (S.phone ? " phone" : ""));
  S.host = el("div", "graph-host");
  S.panel = el("div", S.phone ? "graph-sheet" : null);
  S.panel.id = "graph-panel";
  S.panel.appendChild(el("div", "hint", S.phone ? "Tap a note to see its connections." : "Click a note to see its connections."));
  wrap.appendChild(S.host);
  wrap.appendChild(S.panel);
  S.container.appendChild(wrap);
}

function watchTheme() {
  S.onTheme = () => { if (S && S.engine) S.engine.update({ tokens: themeTokens() }); };
  S.themeWatch = new MutationObserver(S.onTheme);
  S.themeWatch.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  S.mq = matchMedia("(prefers-color-scheme: dark)");
  S.mq.addEventListener("change", S.onTheme);
}

function params(cap) {
  const p = { cap };
  if (S.ctx.meta.kind === "master") p.person = S.ctx.person;
  return p;
}

async function load() {
  const token = S.loads.begin();
  let g;
  try {
    g = await api.graph(params(300));
  } catch (e) {
    if (!S || !S.loads.current(token)) return;
    clear(S.host);
    S.host.appendChild(el("div", "error-banner", "Graph unavailable: " + e.message));
    return;
  }
  if (!S || !S.loads.current(token)) return;
  S.data = g;
  if (S.engine) { S.engine.update({ data: g }); return; }   // live reload: positions and view stay
  clear(S.host);
  S.engine = mountGraph(S.host, {
    data: g,
    tokens: themeTokens(),
    viewport: S.phone ? "phone" : "desktop",
    lens: S.ctx.meta.kind === "master" ? "master" : "vault",
    onSelect: (node, neighbours) => renderCard(node, neighbours),
    onOpen: (node) => S.ctx.openNote(node.rel_path),
    // Full graph: the engine shows the button while `truncated`, we fetch.
    loadFull: async () => {
      const full = await api.graph(params(FULL_CAP));
      if (S) S.data = full;
      return full;
    },
  });
}

// ---- the node card -----------------------------------------------------------

function renderCard(node, neighbours) {
  if (!S) return;
  clear(S.panel);
  if (!node) {
    S.panel.classList.remove("open");
    S.panel.appendChild(el("div", "hint", S.phone ? "Tap a note to see its connections." : "Click a note to see its connections."));
    return;
  }
  S.panel.classList.add("open");
  S.panel.appendChild(el("h3", null, node.title));
  S.panel.appendChild(el("div", "space-tag",
    node.space + " · " + node.rel_path + (node.entity ? " · " + node.entity : "")));
  if (node.entity && node.aliases && node.aliases.length) {
    S.panel.appendChild(el("div", "space-tag", "aka " + node.aliases.join(", ")));
  }
  const open = el("button", "btn", "Open in Query");
  open.style.margin = "8px 0";
  open.addEventListener("click", () => S.ctx.openNote(node.rel_path));
  S.panel.appendChild(open);

  // Direction from the edge list: the engine hands over neighbours, the card
  // says which way each link points.
  const out = new Set(), inn = new Set();
  for (const e of S.data.edges) {
    if (e.source === node.id && e.target !== node.id) out.add(e.target);
    if (e.target === node.id && e.source !== node.id) inn.add(e.source);
  }
  const byId = new Map(neighbours.map((n) => [n.id, n]));
  const list = (label, ids) => {
    S.panel.appendChild(el("h3", "sub", label + " (" + ids.length + ")"));
    const ul = el("ul");
    ids.map((i) => (byId.get(i) || S.data.nodes[i] || { title: "?" }).title).sort()
       .forEach((t) => ul.appendChild(el("li", null, t)));
    S.panel.appendChild(ul);
  };
  list("Links to", [...out]);
  list("Linked from", [...inn]);
  if (node.entity) {
    if (S.facts && S.facts.relPath === node.rel_path) renderFacts(S.facts.hits);
    else loadFacts(node);
  }
}

// For an entity node, fetch its current facts and cache them by rel_path. The
// token guard drops replies that arrive after another selection (or a
// dispose); on any failure nothing is appended — the card stays useful.
async function loadFacts(node) {
  const token = S.factLoads.begin();
  try {
    const p = { entity: node.rel_path };
    if (S.ctx.meta.kind === "master") p.person = S.ctx.person;
    const body = await api.facts(p);
    if (!S || !S.factLoads.current(token)) return;
    S.facts = { relPath: node.rel_path, hits: body.hits };
    renderFacts(body.hits);
  } catch { /* no facts block on error — never disrupt the card */ }
}

function renderFacts(hits) {
  if (!hits.length) return;
  const host = el("div");
  host.appendChild(el("h3", "sub", "Facts (" + hits.length + ")"));
  const ul = el("ul");
  hits.forEach((h) => {
    ul.appendChild(el("li", null, h.statement + "  (" + h.from_date + " → " + (h.until_date || "") + ")"));
  });
  host.appendChild(ul);
  S.panel.appendChild(host);
}
