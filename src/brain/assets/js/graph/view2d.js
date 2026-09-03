// The 2D painter: d3-force lays the nodes out, one <canvas> paints them. SVG
// was retired because glow (a filter per node) and regions (a path per space
// redrawn every tick) were not cheap at 2,000 nodes; a canvas draws both in
// one pass. Owned by engine.js — see its E-state contract. Writes E.pos so
// positions survive a data update, and calls E.setHover / E.select / E.open.
import { labelBudget, labelShown } from "./labels.js";
import { hullFor, centroidOf } from "./hull.js";
import { colorFor } from "./palette.js";
import { medianNearestGap } from "../dom.js";

const FIT_PAD = 24;          // clear space kept on every edge
const HULL_PAD = 26;         // layout units a space's region clears around its nodes
const SPACE_NAME_PX = 26;    // screen px; counter-scaled so the name reads at any zoom
const LABEL_PX = 11;
const FADE = 0.15;           // everything outside the focused neighbourhood

// Size = inbound links, times the person's node-size slider.
export function radius2d(inbound, nodeSize) {
  return (4 + Math.min(8, Math.sqrt(inbound) * 2.2)) * nodeSize;
}

export function createView2d(E, d3) {
  const canvas = document.createElement("canvas");
  canvas.className = "ge-canvas";
  E.surface.appendChild(canvas);
  const g = canvas.getContext("2d");
  const sel = d3.select(canvas);
  let W = 0, H = 0, dpr = 1;
  let nodes = [], links = [], sim = null, gap = 0, raf = 0;
  let isolates = new Set();   // node ids with no edge in the shipped data
  let transform = d3.zoomIdentity;
  let autoFit = true;      // until a real gesture takes the view

  function schedule() { if (!raf) raf = requestAnimationFrame(() => { raf = 0; paint(); }); }

  function measure() {
    dpr = window.devicePixelRatio || 1;
    W = E.surface.clientWidth || 800;
    H = E.surface.clientHeight || 560;
    // The bitmap follows the device pixel ratio; the CSS size is .ge-canvas's
    // 100% x 100% (no style attribute is ever written — CSP).
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    if (sim) sim.force("center").x(W / 2).y(H / 2);
    if (autoFit && nodes.length) fitNow(false);
    schedule();
  }
  const ro = new ResizeObserver(measure);
  ro.observe(E.surface);
  measure();

  function radius(n) { return radius2d(E.flags.inbound[n.i], E.prefs.nodeSize); }

  // ---- layout ---------------------------------------------------------------
  function setData() {
    if (sim) sim.stop();
    const returning = nodes.length > 0;
    nodes = E.data.nodes.map((n, i) => {
      const node = Object.assign({ i }, n);
      const p = E.pos.get(n.rel_path);
      if (p) { node.x = p.x; node.y = p.y; }
      return node;
    });
    links = E.data.edges.map((e) => ({ source: e.source, target: e.target }));
    // A node with no edge in the shipped data (a real orphan, or one
    // truncation left dangling) has nothing to spring it toward the mass —
    // see the isolate-ring force below.
    const connected = new Set();
    for (const l of links) { connected.add(l.source); connected.add(l.target); }
    isolates = new Set(nodes.filter((n) => !connected.has(n.id)).map((n) => n.id));
    gap = 0;   // a new layout has a new spacing; the old one must not decide labels
    sim = d3.forceSimulation(nodes)
      .force("link", d3.forceLink(links).id((d) => d.id).distance(60 * E.prefs.linkDist).strength(0.4))
      .force("charge", d3.forceManyBody().strength(-90 * E.prefs.repel).distanceMax(chargeDistanceMax()))
      .force("center", d3.forceCenter(W / 2, H / 2).strength(E.prefs.centerPull))
      // A handful of nodes edgeless purely by truncation (or genuine orphans)
      // have no spring pulling them toward the mass, so unbounded repulsion
      // flings them far enough to blow out bounds()/fit for everyone else.
      // Pin them instead to a visible ring around the connected cluster.
      .force("isolateRing", d3.forceRadial(0, W / 2, H / 2).strength((d) => isolates.has(d.id) ? 0.06 : 0))
      .on("tick", () => {
        for (const n of nodes) E.pos.set(n.rel_path, { x: n.x, y: n.y });
        if (isolates.size) updateIsolateRing();
        // Re-fitting each tick makes the graph look like it holds still while
        // it organises itself, instead of growing past the edges of its frame.
        if (autoFit) fitNow(false);
        schedule();
      })
      .on("end", () => { gap = medianNearestGap(nodes); schedule(); });
    // A live update keeps the settled positions and lets only the new notes
    // spring in; a first load runs the whole settle from alpha 1.
    if (returning) sim.alpha(0.3).restart();
  }

  function chargeDistanceMax() { return 240 * E.prefs.linkDist; }   // ~4x link distance

  // The ring's center/radius track the connected cluster's own current
  // extent (~1.15x its radius around its centroid), so isolates land just
  // outside the mass instead of at a fixed, eventually-wrong guess.
  function updateIsolateRing() {
    let cx = 0, cy = 0, n = 0;
    for (const node of nodes) {
      if (isolates.has(node.id) || !Number.isFinite(node.x) || !Number.isFinite(node.y)) continue;
      cx += node.x; cy += node.y; n++;
    }
    if (!n) return;
    cx /= n; cy /= n;
    let r = 0;
    for (const node of nodes) {
      if (isolates.has(node.id) || !Number.isFinite(node.x) || !Number.isFinite(node.y)) continue;
      r = Math.max(r, Math.hypot(node.x - cx, node.y - cy));
    }
    sim.force("isolateRing").x(cx).y(cy).radius((r || 40) * 1.15);
  }

  function applyForces() {
    if (!sim) return;
    sim.force("link").distance(60 * E.prefs.linkDist);
    sim.force("charge").strength(-90 * E.prefs.repel).distanceMax(chargeDistanceMax());
    sim.force("center").strength(E.prefs.centerPull);
    sim.alpha(0.4).restart();
  }

  // ---- fit ------------------------------------------------------------------
  // The drawn extent of the visible, placed nodes, radii included. A node the
  // simulation has not placed yet has no coordinates, and one gone non-finite
  // would drag the bounds out and blank the view, so both are skipped.
  function bounds() {
    const b = { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity };
    for (const n of nodes) {
      if (!Number.isFinite(n.x) || !Number.isFinite(n.y) || !E.visible(n.i)) continue;
      const r = radius(n);
      b.minX = Math.min(b.minX, n.x - r); b.maxX = Math.max(b.maxX, n.x + r);
      b.minY = Math.min(b.minY, n.y - r); b.maxY = Math.max(b.maxY, n.y + r);
    }
    return b;
  }
  // The transform that puts those bounds inside the part of the frame the
  // chrome does not cover. forceCenter only moves the centroid to the middle
  // and says nothing about spread: a 300-note vault settles to ~1500 units
  // inside an 800 px frame and a third of it would sit outside the edges.
  function fitTransform(b) {
    const inset = E.insets();
    const availW = Math.max(40, W - inset.left - inset.right - FIT_PAD * 2);
    const availH = Math.max(40, H - inset.top - inset.bottom - FIT_PAD * 2);
    const spanX = b.maxX - b.minX, spanY = b.maxY - b.minY;
    // An axis with no span does not constrain the scale, which is what
    // Infinity says exactly. Both without a span means nothing to fit.
    const kx = spanX > 0 ? availW / spanX : Infinity;
    const ky = spanY > 0 ? availH / spanY : Infinity;
    let k = Math.min(kx, ky);
    if (k === Infinity) k = 1;
    // Negated comparisons: bounds that are not numbers land on a limit rather
    // than propagating a NaN into the transform and blanking the view.
    if (!(k > 0.1)) k = 0.1;
    if (!(k < 8)) k = 8;
    let cx = (b.minX + b.maxX) / 2, cy = (b.minY + b.maxY) / 2;
    if (!Number.isFinite(cx)) cx = 0;
    if (!Number.isFinite(cy)) cy = 0;
    return d3.zoomIdentity
      .translate(inset.left + FIT_PAD + availW / 2 - k * cx, inset.top + FIT_PAD + availH / 2 - k * cy)
      .scale(k);
  }
  function fitNow(animate) {
    const t = fitTransform(bounds());
    zoom.transform(animate ? sel.transition().duration(220) : sel, t);
  }

  // ---- interaction ----------------------------------------------------------
  function toWorld(px, py) { return [(px - transform.x) / transform.k, (py - transform.y) / transform.k]; }
  function nodeAt(px, py) {
    const [x, y] = toWorld(px, py);
    const slack = 3 / transform.k;
    let best = null, bestD = Infinity;
    for (const n of nodes) {
      if (!Number.isFinite(n.x) || !E.visible(n.i)) continue;
      const d = Math.hypot(n.x - x, n.y - y) - radius(n);
      if (d <= slack && d < bestD) { best = n; bestD = d; }
    }
    return best;
  }
  function local(ev) {
    const r = canvas.getBoundingClientRect();
    const src = ev.touches && ev.touches[0] ? ev.touches[0] : ev;
    return [src.clientX - r.left, src.clientY - r.top];
  }

  const zoom = d3.zoom().scaleExtent([0.1, 8])
    // A press on a node is a drag, not a pan; wheel and empty-canvas presses pan/zoom.
    .filter((ev) => ev.type === "wheel" || (!ev.button && !nodeAt(...local(ev))))
    .on("zoom", (ev) => {
      transform = ev.transform;
      // d3 leaves sourceEvent null for a transform the page applied to itself
      // and carries the wheel or drag event for one a person made. That is
      // the only thing separating "the layout moved" from "they went looking".
      if (ev.sourceEvent) autoFit = false;
      schedule();
    });
  sel.call(zoom).on("dblclick.zoom", null);

  sel.call(d3.drag()
    .container(canvas)
    .subject((ev) => nodeAt(ev.x, ev.y))
    .on("start", (ev) => {
      autoFit = false;
      if (!ev.active) sim.alphaTarget(0.3).restart();
      ev.subject.fx = ev.subject.x; ev.subject.fy = ev.subject.y;
    })
    .on("drag", (ev) => { const [x, y] = toWorld(ev.x, ev.y); ev.subject.fx = x; ev.subject.fy = y; })
    .on("end", (ev) => { if (!ev.active) sim.alphaTarget(0); ev.subject.fx = null; ev.subject.fy = null; }));

  function onClick(ev) { const n = nodeAt(...local(ev)); E.select(n ? n.i : null); }
  function onDblClick(ev) { const n = nodeAt(...local(ev)); if (n) E.open(n.i); }
  function onMove(ev) {
    if (ev.pointerType && ev.pointerType !== "mouse") return;
    const n = nodeAt(...local(ev));
    canvas.classList.toggle("ge-hit", !!n);
    E.setHover(n ? n.i : null, ev.clientX, ev.clientY);
  }
  function onLeave() { E.setHover(null, 0, 0); }
  canvas.addEventListener("click", onClick);
  canvas.addEventListener("dblclick", onDblClick);
  canvas.addEventListener("pointermove", onMove);
  canvas.addEventListener("pointerleave", onLeave);

  // ---- paint ----------------------------------------------------------------
  function ring(n, r, dash, color) {
    g.save();
    g.setLineDash(dash);
    g.lineWidth = 1.5 / transform.k;
    g.strokeStyle = color;
    g.beginPath(); g.arc(n.x, n.y, r, 0, Math.PI * 2); g.stroke();
    g.restore();
  }
  // A closed polygon drawn through the midpoints of its edges with the
  // vertices as control points — the "rounded" in "padded and rounded".
  function roundedPath(pts) {
    const n = pts.length;
    const mid = (a, b) => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
    const start = mid(pts[n - 1], pts[0]);
    g.moveTo(start.x, start.y);
    for (let i = 0; i < n; i++) {
      const next = mid(pts[i], pts[(i + 1) % n]);
      g.quadraticCurveTo(pts[i].x, pts[i].y, next.x, next.y);
    }
  }
  function paint() {
    const t = E.tokens, dark = t.theme === "dark", k = transform.k;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.fillStyle = t.bg; g.fillRect(0, 0, W, H);
    g.translate(transform.x, transform.y); g.scale(k, k);

    const vis = [];
    for (const n of nodes) if (Number.isFinite(n.x) && Number.isFinite(n.y) && E.visible(n.i)) vis.push(n);
    const bySpace = new Map();
    for (const n of vis) { let a = bySpace.get(n.space); if (!a) bySpace.set(n.space, a = []); a.push(n); }

    // Regions — the light theme's territory map: one tinted hull per space.
    if (!dark) {
      g.globalAlpha = 0.13;
      for (const [space, pts] of bySpace) {
        const hull = hullFor(pts, HULL_PAD);
        if (hull.length < 3) continue;
        g.beginPath(); roundedPath(hull); g.closePath();
        g.fillStyle = colorFor(space); g.fill();
      }
      g.globalAlpha = 1;
    }
    // Edges: constant screen width whatever the zoom (the SVG's vector-effect).
    g.lineWidth = E.prefs.linkWidth / k;
    for (const l of links) {
      const a = l.source, b = l.target;
      if (typeof a !== "object" || !Number.isFinite(a.x) || !Number.isFinite(b.x)) continue;
      if (!E.visible(a.i) || !E.visible(b.i)) continue;
      const inHood = !E.hood || (E.hood.has(a.i) && E.hood.has(b.i));
      const hot = E.selected != null && (a.i === E.selected || b.i === E.selected);
      g.globalAlpha = inHood ? 1 : FADE;
      g.strokeStyle = hot ? t.fg : t.line;
      g.beginPath(); g.moveTo(a.x, a.y); g.lineTo(b.x, b.y); g.stroke();
    }
    // Nodes. Glow is a canvas shadow — cheap enough to keep to 800 nodes.
    const glow = dark && vis.length <= 800;
    for (const n of vis) {
      const c = colorFor(n.space), r = radius(n);
      g.globalAlpha = E.hood && !E.hood.has(n.i) ? FADE : 1;
      if (glow) { g.shadowColor = c; g.shadowBlur = 10 * dpr; }
      g.beginPath(); g.arc(n.x, n.y, r, 0, Math.PI * 2);
      g.fillStyle = c; g.fill();
      g.shadowBlur = 0;
      if (E.prefs.orphans && E.flags.orphan[n.i]) ring(n, r + 3 / k, [4 / k, 3 / k], c);
      else if (E.prefs.deadEnds && E.flags.deadEnd[n.i]) ring(n, r + 3 / k, [1.5 / k, 2.5 / k], c);
      if (n.i === E.selected) ring(n, r + 4 / k, [], t.fg);
    }
    // Labels: screen-sized text in world space, so font and lift counter-scale.
    const budget = labelBudget(E.prefs.labels, vis.length);
    g.font = `${LABEL_PX / k}px ${E.font}`;
    g.textAlign = "center"; g.textBaseline = "top"; g.lineJoin = "round";
    g.lineWidth = 3 / k; g.strokeStyle = t.bg;
    for (const n of vis) {
      if (!labelShown(E.rank[n.i], k, gap, budget)) continue;
      g.globalAlpha = E.hood && !E.hood.has(n.i) ? FADE : 1;
      const y = n.y + radius(n) + 4 / k;
      g.strokeText(n.title, n.x, y);
      g.fillStyle = t.fg; g.fillText(n.title, n.x, y);
    }
    // Space names: large and translucent at each cluster's centroid, always on.
    g.font = `600 ${SPACE_NAME_PX / k}px ${E.font}`;
    g.textBaseline = "middle";
    g.globalAlpha = dark ? 0.35 : 0.5;
    for (const [space, pts] of bySpace) {
      const c = centroidOf(pts);
      g.fillStyle = colorFor(space);
      g.fillText(space, c.x, c.y);
    }
    g.globalAlpha = 1;
    g.setTransform(1, 0, 0, 1, 0, 0);
  }

  return {
    setData,
    applyForces,
    refresh: schedule,
    setTokens: schedule,
    fit(animate) { autoFit = true; fitNow(animate); },
    destroy() {
      ro.disconnect();
      if (sim) sim.stop();
      cancelAnimationFrame(raf);
      canvas.removeEventListener("click", onClick);
      canvas.removeEventListener("dblclick", onDblClick);
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerleave", onLeave);
      canvas.remove();
    },
  };
}
