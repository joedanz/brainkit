// Tiny DOM helpers, ported from the static dashboard. The one rule: data
// reaches the page through textContent only, so an untrusted note title or
// warning can never become markup. Nothing here uses innerHTML.

export function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = String(text);
  return n;
}

export function clear(node) { node.textContent = ""; return node; }

export function section(parent, titleText) {
  parent.appendChild(el("h2", null, titleText));
  const box = el("div");
  parent.appendChild(box);
  return box;
}

export function tiles(parent, items) {
  const grid = el("div", "tiles");
  items.forEach((it) => {
    const t = el("div", "tile" + (it.tone ? " " + it.tone : ""));
    t.appendChild(el("div", "num", it.value));
    t.appendChild(el("div", "lbl", it.label));
    grid.appendChild(t);
  });
  parent.appendChild(grid);
}

export function table(parent, headers, rows, centerFrom) {
  const scroll = el("div", "table-scroll");
  const t = el("table");
  const head = el("tr");
  headers.forEach((h, i) => {
    head.appendChild(el("th", centerFrom !== undefined && i >= centerFrom ? "center" : null, h));
  });
  t.appendChild(head);
  rows.forEach((row) => {
    const r = el("tr");
    row.forEach((cell, i) => {
      const td = el("td", centerFrom !== undefined && i >= centerFrom ? "center" : null);
      if (cell && cell.nodeType) td.appendChild(cell);
      else td.textContent = cell;
      r.appendChild(td);
    });
    t.appendChild(r);
  });
  scroll.appendChild(t);
  parent.appendChild(scroll);
}

export function badge(kind, text) { return el("span", "badge " + kind, text); }

// Monotonic request guard: begin() stamps a token, current(token) is true only
// while no later begin() has run. Lets an async load bail after each await when
// a newer load (or a tab dispose) has superseded it — kills out-of-order
// responses without cancellation plumbing.
export function latest() {
  let seq = 0;
  return { begin() { return ++seq; }, current(token) { return token === seq; } };
}

// Make a non-button element behave like one for keyboard + assistive tech:
// role, tab stop, and Enter/Space activation. Use for cards/chips we can't make
// real <button>s without restyling.
export function clickable(node, onActivate) {
  node.setAttribute("role", "button");
  node.setAttribute("tabindex", "0");
  node.addEventListener("click", onActivate);
  node.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); onActivate(ev); }
  });
  return node;
}

export function fmtBytes(n) {
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return (i ? n.toFixed(1) : n) + " " + units[i];
}

// space -> stable palette color (shared across tabs so the graph legend, the
// bar chart, and the three.js graph agree). Muted, uniform-lightness OKLCH
// mid-tones (~L 0.71, C 0.09, hue-spaced away from the terracotta accent) so no
// node shouts and the set reads on both the light and dark theme. Kept as hex,
// not CSS vars, because three.js Color can't parse oklch().
const PALETTE = ["#d48b85", "#92b074", "#bcab67", "#78a3cf", "#64b5b0",
                 "#b88cc1", "#adac68", "#a48fcb", "#6db38e", "#d0878c"];
const spaceColors = {};
let nextColor = 0;
export function colorFor(space) {
  if (!(space in spaceColors)) spaceColors[space] = PALETTE[nextColor++ % PALETTE.length];
  return spaceColors[space];
}

export function barChart(parent, rows) {
  const max = rows.reduce((m, r) => Math.max(m, r.count), 1);
  rows.forEach((r) => {
    const row = el("div", "bar-row");
    row.appendChild(el("div", null, r.label));
    const track = el("div", "bar-track");
    const fill = el("div", "bar-fill");
    fill.style.width = (100 * r.count / max) + "%";
    fill.style.background = r.color;
    track.appendChild(fill);
    row.appendChild(track);
    row.appendChild(el("div", "bar-count", r.count + " note(s)"));
    parent.appendChild(row);
  });
}

export function warningsBlock(parent, warnings) {
  if (!warnings || !warnings.length) return;
  const box = el("div", "warnings");
  warnings.forEach((w) => box.appendChild(el("div", null, "⚠ " + w)));
  parent.appendChild(box);
}

// Render an FTS snippet whose matches are wrapped in ** .. ** as <strong>,
// splitting on the markers and alternating text nodes — never innerHTML.
export function snippet(text) {
  const span = el("span", "snip");
  const parts = String(text).split("**");
  parts.forEach((part, i) => {
    if (i % 2 === 1) span.appendChild(el("strong", null, part));
    else span.appendChild(document.createTextNode(part));
  });
  return span;
}

// The typical distance from a point to its nearest neighbour. Multiplied by the
// zoom it gives the spacing ON SCREEN, which is what decides whether labels
// collide; in 3D it decides whether two spheres touch. A median rather than a
// mean, so one note parked far off on its own cannot speak for the rest.
//
// Shared by the 2D and 3D graphs for the same reason colorFor is: two copies of
// a numerically fiddly median are exactly the thing that drifts. The z term
// defaults to zero, so 2D points with only x and y get the right answer from
// the same code.
export function medianNearestGap(points) {
  const p = points.filter((q) => Number.isFinite(q.x) && Number.isFinite(q.y));
  const n = p.length;
  if (n < 2) return 0;
  const step = Math.ceil(n / 400);   // past a few hundred, sample: it is a median anyway
  const gaps = [];
  for (let i = 0; i < n; i += step) {
    let best = Infinity;
    for (let j = 0; j < n; j++) {
      if (i === j) continue;
      const dx = p[i].x - p[j].x, dy = p[i].y - p[j].y, dz = (p[i].z || 0) - (p[j].z || 0);
      const d2 = dx * dx + dy * dy + dz * dz;
      if (d2 < best) best = d2;
    }
    if (best < Infinity) gaps.push(Math.sqrt(best));
  }
  if (!gaps.length) return 0;
  gaps.sort((a, b) => a - b);
  return gaps[Math.floor(gaps.length / 2)];
}
