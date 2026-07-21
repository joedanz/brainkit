"""Self-contained HTML dashboard over the stats dataclasses.

One output file, zero runtime dependencies, zero network: styles and scripts
are inline, data is embedded as a JSON blob, and the context graph is a small
hand-rolled canvas force layout. The page must render offline — tests enforce
that no ``http://``/``https://`` reference ever appears in the output.

The template is a ``string.Template`` (not an f-string): the CSS/JS is full of
braces, and ``$``-substitution leaves them alone. Everything user-controlled
reaches the page through exactly two doors, each with one escaping rule:
``$title`` is ``html.escape``d, and ``$data_json`` has ``<`` encoded as
``\\u003c`` — valid JSON that cannot terminate the script element, which
neutralizes ``</script>`` breakout from note titles or warning strings. The
JS builds DOM via ``textContent`` only, so embedded data never becomes markup.
"""

from __future__ import annotations

import html
import json
from dataclasses import asdict
from pathlib import Path
from string import Template

from brain.stats import MasterStats, VaultStats

_PAGE = Template("""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$title</title>
<style>
:root {
  --bg: #101418; --panel: #1a2129; --panel-2: #212a34; --line: #2d3843;
  --text: #dce5ee; --dim: #8a99a8; --accent: #5aa9e6;
  --ok: #57c78f; --warn: #e6c05a; --err: #e66a5a;
}
* { box-sizing: border-box; margin: 0; }
body {
  background: var(--bg); color: var(--text);
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
        Helvetica, Arial, sans-serif;
  padding: 24px; max-width: 1200px; margin: 0 auto;
}
header { margin-bottom: 20px; }
h1 { font-size: 20px; font-weight: 650; }
h2 { font-size: 13px; font-weight: 600; text-transform: uppercase;
     letter-spacing: .08em; color: var(--dim); margin: 26px 0 10px; }
.meta { color: var(--dim); font-size: 12px; margin-top: 4px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
         gap: 10px; }
.tile { background: var(--panel); border: 1px solid var(--line);
        border-radius: 10px; padding: 14px; }
.tile .num { font-size: 24px; font-weight: 700; font-variant-numeric: tabular-nums; }
.tile .lbl { color: var(--dim); font-size: 12px; margin-top: 2px; }
.tile.warn .num { color: var(--warn); }
.tile.err .num { color: var(--err); }
.bar-row { display: grid; grid-template-columns: 180px 1fr 90px; gap: 10px;
           align-items: center; margin: 5px 0; font-size: 13px; }
.bar-track { background: var(--panel); border-radius: 5px; height: 12px;
             overflow: hidden; }
.bar-fill { height: 100%; border-radius: 5px; }
.bar-count { color: var(--dim); font-variant-numeric: tabular-nums; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--line);
         font-variant-numeric: tabular-nums; }
th { color: var(--dim); font-weight: 600; font-size: 12px; }
td.center, th.center { text-align: center; }
.badge { display: inline-block; padding: 1px 8px; border-radius: 9px;
         font-size: 11px; font-weight: 600; }
.badge.error { background: #4a2320; color: var(--err); }
.badge.warn  { background: #443a1c; color: var(--warn); }
.badge.info  { background: #1e3a4e; color: var(--accent); }
.badge.ok    { background: #1e3d2e; color: var(--ok); }
ul.plain { list-style: none; }
ul.plain li { padding: 5px 0; border-bottom: 1px solid var(--line); }
.commit-sha { color: var(--accent); font-family: ui-monospace, SFMono-Regular,
              Menlo, monospace; font-size: 12px; margin-right: 8px; }
.commit-date { color: var(--dim); font-size: 12px; margin-right: 8px; }
.graph-wrap { display: grid; grid-template-columns: 1fr 260px; gap: 12px; }
#graph { background: var(--panel); border: 1px solid var(--line);
         border-radius: 10px; width: 100%; height: 520px; cursor: pointer; }
#graph-panel { background: var(--panel); border: 1px solid var(--line);
               border-radius: 10px; padding: 14px; overflow-y: auto;
               max-height: 520px; font-size: 13px; }
#graph-panel .hint { color: var(--dim); }
#graph-panel h3 { font-size: 14px; margin-bottom: 2px; overflow-wrap: anywhere; }
#graph-panel .space-tag { color: var(--dim); font-size: 12px; }
#graph-panel ul { margin: 6px 0 12px 16px; }
#graph-panel li { overflow-wrap: anywhere; }
.legend { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 8px;
          font-size: 12px; color: var(--dim); }
.legend .dot { display: inline-block; width: 9px; height: 9px;
               border-radius: 50%; margin-right: 5px; }
.facts-bar { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px;
             align-items: center; font-size: 13px; }
.facts-bar input { background: var(--panel); color: var(--text);
                   border: 1px solid var(--line); border-radius: 6px;
                   padding: 5px 8px; font: inherit; }
.fact { background: var(--panel); border: 1px solid var(--line);
        border-radius: 8px; padding: 8px 12px; margin: 6px 0; }
.fact-meta { color: var(--dim); font-size: 12px; margin-top: 2px;
             overflow-wrap: anywhere; }
.warnings { margin-top: 20px; }
.warnings div { color: var(--warn); font-size: 13px; padding: 3px 0; }
@media (max-width: 800px) { .graph-wrap { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<header>
  <h1>$title</h1>
  <div class="meta" id="page-meta"></div>
</header>
<main id="app"></main>
<script id="brain-data" type="application/json">$data_json</script>
<script>
"use strict";
var DATA = JSON.parse(document.getElementById("brain-data").textContent);

/* ---- tiny DOM helpers: textContent only, data never becomes markup ---- */
function el(tag, cls, text) {
  var n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = String(text);
  return n;
}
function section(parent, titleText) {
  parent.appendChild(el("h2", null, titleText));
  var box = el("div");
  parent.appendChild(box);
  return box;
}
function tiles(parent, items) {
  var grid = el("div", "tiles");
  items.forEach(function (it) {
    var t = el("div", "tile" + (it.tone ? " " + it.tone : ""));
    t.appendChild(el("div", "num", it.value));
    t.appendChild(el("div", "lbl", it.label));
    grid.appendChild(t);
  });
  parent.appendChild(grid);
}
function table(parent, headers, rows, centerFrom) {
  var t = el("table"), tr = el("tr");
  headers.forEach(function (h, i) {
    tr.appendChild(el("th", centerFrom !== undefined && i >= centerFrom ? "center" : null, h));
  });
  t.appendChild(tr);
  rows.forEach(function (row) {
    var r = el("tr");
    row.forEach(function (cell, i) {
      var td = el("td", centerFrom !== undefined && i >= centerFrom ? "center" : null);
      if (cell && cell.nodeType) td.appendChild(cell); else td.textContent = cell;
      r.appendChild(td);
    });
    t.appendChild(r);
  });
  parent.appendChild(t);
}
function badge(kind, text) { return el("span", "badge " + kind, text); }
function fmtBytes(n) {
  var units = ["B", "KB", "MB", "GB"], i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return (i ? n.toFixed(1) : n) + " " + units[i];
}

/* space -> stable palette color */
var PALETTE = ["#5aa9e6", "#57c78f", "#e6c05a", "#c98ee6", "#e68a5a",
               "#6ad4c8", "#e65a8a", "#9fb85a", "#8a93e6", "#d4a86a"];
var spaceColors = {};
var nextColor = 0;
function colorFor(space) {
  if (!(space in spaceColors)) spaceColors[space] = PALETTE[nextColor++ % PALETTE.length];
  return spaceColors[space];
}

function barChart(parent, rows) { /* rows: [{label, count, color}] */
  var max = rows.reduce(function (m, r) { return Math.max(m, r.count); }, 1);
  rows.forEach(function (r) {
    var row = el("div", "bar-row");
    row.appendChild(el("div", null, r.label));
    var track = el("div", "bar-track");
    var fill = el("div", "bar-fill");
    fill.style.width = (100 * r.count / max) + "%";
    fill.style.background = r.color;
    track.appendChild(fill);
    row.appendChild(track);
    row.appendChild(el("div", "bar-count", r.count + " note(s)"));
    parent.appendChild(row);
  });
}

function warningsBlock(parent, warnings) {
  if (!warnings || !warnings.length) return;
  var box = el("div", "warnings");
  warnings.forEach(function (w) { box.appendChild(el("div", null, "⚠ " + w)); });
  parent.appendChild(box);
}

/* ---- context graph: hand-rolled canvas force layout ---- */
function renderGraph(parent, graph) {
  var wrap = el("div", "graph-wrap");
  var canvas = el("canvas");
  canvas.id = "graph";
  var panel = el("div");
  panel.id = "graph-panel";
  panel.appendChild(el("div", "hint", "Click a note to see its connections."));
  wrap.appendChild(canvas);
  wrap.appendChild(panel);
  parent.appendChild(wrap);

  var legend = el("div", "legend");
  var seen = {};
  graph.nodes.forEach(function (n) {
    if (seen[n.space]) return;
    seen[n.space] = true;
    var item = el("span");
    var dot = el("span", "dot");
    dot.style.background = colorFor(n.space);
    item.appendChild(dot);
    item.appendChild(document.createTextNode(n.space));
    legend.appendChild(item);
  });
  var etypes = {};
  graph.nodes.forEach(function (n) { if (n.entity) etypes[n.entity] = true; });
  Object.keys(etypes).sort().forEach(function (t) {
    var item = el("span");
    var dot = el("span", "dot");
    dot.style.background = colorFor("entity:" + t);
    item.appendChild(dot);
    item.appendChild(document.createTextNode(t + " (entity)"));
    legend.appendChild(item);
  });
  parent.appendChild(legend);
  if (graph.truncated) {
    parent.appendChild(el("div", "meta",
      "Graph truncated to the " + graph.nodes.length + " most-connected notes."));
  }

  var W = canvas.clientWidth || 900, H = 520;
  var dpr = window.devicePixelRatio || 1;
  canvas.width = W * dpr; canvas.height = H * dpr;
  var ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);

  var N = graph.nodes.length;
  var nodes = graph.nodes.map(function (n, i) {
    var angle = 2 * Math.PI * i / Math.max(N, 1);
    var r = Math.min(W, H) * 0.35 * (0.4 + 0.6 * ((i * 2654435761 % 1000) / 1000));
    return {
      d: n,
      x: W / 2 + r * Math.cos(angle), y: H / 2 + r * Math.sin(angle),
      vx: 0, vy: 0,
      radius: 4 + 2.5 * Math.sqrt(n.degree),
    };
  });
  var edges = graph.edges.map(function (e) {
    return { s: nodes[e.source], t: nodes[e.target] };
  });

  var ticks = 0, MAX_TICKS = 260;
  function step() {
    var alpha = 1 - ticks / MAX_TICKS;
    for (var i = 0; i < N; i++) {
      var a = nodes[i];
      for (var j = i + 1; j < N; j++) {
        var b = nodes[j];
        var dx = a.x - b.x, dy = a.y - b.y;
        var d2 = dx * dx + dy * dy + 0.01;
        var f = 9000 / d2 * alpha;
        var d = Math.sqrt(d2);
        dx /= d; dy /= d;
        a.vx += dx * f; a.vy += dy * f;
        b.vx -= dx * f; b.vy -= dy * f;
      }
      a.vx += (W / 2 - a.x) * 0.0012 * alpha;
      a.vy += (H / 2 - a.y) * 0.0012 * alpha;
    }
    edges.forEach(function (e) {
      var dx = e.t.x - e.s.x, dy = e.t.y - e.s.y;
      var d = Math.sqrt(dx * dx + dy * dy) + 0.01;
      var f = (d - 130) * 0.006 * alpha;
      dx /= d; dy /= d;
      e.s.vx += dx * f * d; e.s.vy += dy * f * d;
      e.t.vx -= dx * f * d; e.t.vy -= dy * f * d;
    });
    nodes.forEach(function (n) {
      n.vx *= 0.85; n.vy *= 0.85;
      n.x = Math.max(n.radius, Math.min(W - n.radius, n.x + n.vx));
      n.y = Math.max(n.radius, Math.min(H - n.radius, n.y + n.vy));
    });
  }

  var selected = null;
  function draw() {
    ctx.clearRect(0, 0, W, H);
    ctx.lineWidth = 1;
    edges.forEach(function (e) {
      var hot = selected && (e.s === selected || e.t === selected);
      ctx.strokeStyle = hot ? "#5aa9e6" : "#2d3843";
      ctx.beginPath();
      ctx.moveTo(e.s.x, e.s.y);
      ctx.lineTo(e.t.x, e.t.y);
      ctx.stroke();
    });
    nodes.forEach(function (n) {
      ctx.beginPath();
      ctx.arc(n.x, n.y, n.radius, 0, 2 * Math.PI);
      ctx.fillStyle = colorFor(n.d.space);
      ctx.fill();
      if (n.d.entity) {
        ctx.strokeStyle = colorFor("entity:" + n.d.entity);
        ctx.lineWidth = 2;
        ctx.stroke();
        ctx.lineWidth = 1;
      }
      if (n === selected) {
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 2;
        ctx.stroke();
        ctx.lineWidth = 1;
      }
    });
    if (selected) {
      ctx.fillStyle = "#dce5ee";
      ctx.font = "12px sans-serif";
      ctx.fillText(selected.d.title, selected.x + selected.radius + 4, selected.y + 4);
    }
  }
  function loop() {
    if (ticks < MAX_TICKS) { step(); ticks++; }
    draw();
    if (ticks < MAX_TICKS) requestAnimationFrame(loop);
  }
  requestAnimationFrame(loop);

  canvas.addEventListener("click", function (ev) {
    var rect = canvas.getBoundingClientRect();
    var x = ev.clientX - rect.left, y = ev.clientY - rect.top;
    var best = null, bestD = 18 * 18;
    nodes.forEach(function (n) {
      var dx = n.x - x, dy = n.y - y, d2 = dx * dx + dy * dy;
      if (d2 < bestD) { best = n; bestD = d2; }
    });
    selected = best;
    draw();
    panel.textContent = "";
    if (!best) {
      panel.appendChild(el("div", "hint", "Click a note to see its connections."));
      return;
    }
    panel.appendChild(el("h3", null, best.d.title));
    var tag = el("div", "space-tag", best.d.space + " · " + best.d.rel_path +
                 (best.d.entity ? " · " + best.d.entity : ""));
    panel.appendChild(tag);
    var out = [], inn = [];
    edges.forEach(function (e) {
      if (e.s === best) out.push(e.t.d.title);
      if (e.t === best) inn.push(e.s.d.title);
    });
    panel.appendChild(el("h3", null, "Links to (" + out.length + ")"));
    var ulo = el("ul");
    out.sort().forEach(function (t) { ulo.appendChild(el("li", null, t)); });
    panel.appendChild(ulo);
    panel.appendChild(el("h3", null, "Linked from (" + inn.length + ")"));
    var uli = el("ul");
    inn.sort().forEach(function (t) { uli.appendChild(el("li", null, t)); });
    panel.appendChild(uli);
  });
}

/* ---- facts (vault view only): baked rows + client-side as-of filter ---- */
function factRow(f) {
  var card = el("div", "fact");
  card.appendChild(el("div", null, f.statement));
  var bits = [f.from_date + " → " + (f.until_date || "")];
  if (f.sources && f.sources.length) bits.push(f.sources.join(" · "));
  bits.push(f.rel_path + ":" + f.line);
  card.appendChild(el("div", "fact-meta", bits.join("  ·  ")));
  return card;
}

function renderFacts(parent, facts, aliasIndex) {
  var bar = el("div", "facts-bar");
  var entity = el("input");
  entity.type = "search";
  entity.placeholder = "filter by entity, alias, or text…";
  entity.setAttribute("aria-label", "Filter facts");
  var asOf = el("input");
  asOf.type = "date";
  asOf.title = "facts true on this date";
  asOf.setAttribute("aria-label", "Facts true on this date");
  var ended = el("label");
  var endedBox = el("input");
  endedBox.type = "checkbox";
  ended.appendChild(endedBox);
  ended.appendChild(document.createTextNode(" include ended"));
  bar.appendChild(entity);
  bar.appendChild(asOf);
  bar.appendChild(ended);
  parent.appendChild(bar);
  var list = el("div");
  parent.appendChild(list);

  function paint() {
    list.textContent = "";
    var on = asOf.value || new Date().toISOString().slice(0, 10);
    var q = entity.value.trim().toLowerCase();
    var aliasRel = aliasIndex[q] || null;  // exact alias → entity rel_path
    var shown = facts.filter(function (f) {
      if (!endedBox.checked) {
        // mirrors query_facts: from <= on and (until is null or until >= on)
        if (f.from_date > on) return false;
        if (f.until_date !== null && f.until_date < on) return false;
      }
      if (!q) return true;
      if (aliasRel && (f.entities || []).indexOf(aliasRel) >= 0) return true;
      var inEnts = (f.entities || []).some(function (e) {
        return e.toLowerCase().indexOf(q) >= 0;
      });
      return inEnts || f.statement.toLowerCase().indexOf(q) >= 0;
    });
    list.appendChild(el("div", "meta",
      shown.length + " fact(s)" + (asOf.value ? " as of " + on : "")));
    shown.forEach(function (f) { list.appendChild(factRow(f)); });
  }
  entity.addEventListener("input", paint);
  asOf.addEventListener("change", paint);
  endedBox.addEventListener("change", paint);
  paint();
}

/* ---- user view ---- */
function renderVault(app, d) {
  document.getElementById("page-meta").textContent =
    d.vault + " · collected " + d.collected_at;

  var coverage = d.embedding_coverage === null ? "?" :
      Math.round(d.embedding_coverage * 100) + "%";
  tiles(app, [
    { value: d.notes_total, label: "notes" },
    { value: d.chunks_total, label: "chunks indexed" },
    { value: d.spaces.length, label: "spaces" },
    { value: d.inbox_count, label: "inbox items" },
    { value: d.open_actions, label: "open actions" },
    { value: d.facts_total, label: "facts" },
    { value: d.entities_total, label: "entities" },
    { value: coverage, label: "embedding coverage",
      tone: d.embedding_coverage === 1 ? "" : "warn" },
    { value: d.pending_reindex.length, label: "pending reindex",
      tone: d.pending_reindex.length ? "warn" : "" },
  ]);

  var sp = section(app, "Notes by space");
  barChart(sp, d.spaces.map(function (s) {
    return { label: s.space, count: s.notes, color: colorFor(s.space) };
  }));

  if (d.graph && d.graph.nodes.length) {
    var g = section(app, "Context graph");
    renderGraph(g, d.graph);
  }

  if (d.facts && d.facts.length) {
    var aliasIndex = {};
    if (d.graph) d.graph.nodes.forEach(function (n) {
      (n.aliases || []).forEach(function (a) {
        aliasIndex[a.toLowerCase()] = n.rel_path;
      });
    });
    var fs = section(app, "Facts");
    renderFacts(fs, d.facts, aliasIndex);
  }

  if (d.top_linked.length) {
    var tl = section(app, "Most linked notes");
    table(tl, ["note", "inbound links"], d.top_linked.map(function (n) {
      return [n.rel_path, String(n.inbound)];
    }));
  }

  if (d.recent_commits.length) {
    var ra = section(app, "Recent activity");
    var ul = el("ul", "plain");
    d.recent_commits.forEach(function (c) {
      var li = el("li");
      li.appendChild(el("span", "commit-sha", c.sha));
      li.appendChild(el("span", "commit-date", c.date));
      li.appendChild(document.createTextNode(c.subject));
      ul.appendChild(li);
    });
    ra.appendChild(ul);
  }

  warningsBlock(app, d.warnings);
}

/* ---- admin view ---- */
function renderMaster(app, d) {
  document.getElementById("page-meta").textContent =
    d.master + " · collected " + d.collected_at;

  var errs = d.findings.filter(function (f) { return f.severity === "error"; }).length;
  var warns = d.findings.filter(function (f) { return f.severity === "warn"; }).length;
  tiles(app, [
    { value: d.people_count, label: "people" },
    { value: d.spaces.length, label: "spaces" },
    { value: d.promotions_pending.length, label: "promotions pending",
      tone: d.promotions_pending.length ? "warn" : "" },
    { value: errs, label: "doctor errors", tone: errs ? "err" : "" },
    { value: warns, label: "doctor warnings", tone: warns ? "warn" : "" },
  ]);

  var pv = section(app, "Per-person vaults");
  if (d.out_root === null) {
    pv.appendChild(el("div", "meta",
      "Compiled root not provided — rerun with --out to inspect vaults."));
  } else {
    table(pv, ["person", "name", "size", "notes", "index built", "awaiting writeback"],
      d.people.map(function (p) {
        if (!p.compiled) return [p.person_id, p.name, "—", "—", "—",
                                 badge("warn", "not compiled")];
        var drift = p.drift_error ? badge("error", p.drift_error)
          : (p.drift ? badge("info", p.drift + " change(s)") : badge("ok", "clean"));
        return [p.person_id, p.name, fmtBytes(p.disk_bytes), String(p.notes),
                p.index_built_at || "never", drift];
      }));
  }

  if (d.promotions_pending.length) {
    var pq = section(app, "Promotion queue");
    table(pq, ["id", "from", "target"], d.promotions_pending.map(function (p) {
      return [p.id, p.person_id, p.target_path];
    }));
  }

  var pm = section(app, "Permissions (read / write)");
  var ids = d.people.map(function (p) { return p.person_id; });
  table(pm, ["space"].concat(ids), d.permissions.map(function (perm) {
    return [perm.space].concat(ids.map(function (id) {
      var r = perm.readers.indexOf(id) >= 0, w = perm.writers.indexOf(id) >= 0;
      return r && w ? "RW" : r ? "R" : w ? "W" : "—";
    }));
  }), 1);
  if (d.uncovered_spaces.length) {
    pm.appendChild(el("div", "meta",
      "Unreachable spaces (no rule matches): " + d.uncovered_spaces.join(", ")));
  }

  if (d.findings.length) {
    var df = section(app, "Doctor findings");
    table(df, ["severity", "check", "message"], d.findings.map(function (f) {
      return [badge(f.severity === "error" ? "error" : f.severity, f.severity),
              f.check, f.message];
    }));
  }

  warningsBlock(app, d.warnings);
}

var app = document.getElementById("app");
if (DATA.kind === "master") renderMaster(app, DATA);
else renderVault(app, DATA);
</script>
</body>
</html>
""")


def _embed_json(payload: dict) -> str:
    # ensure_ascii already escapes everything non-ASCII; encoding every "<"
    # as the JSON escape \\u003c is the one rule that makes untrusted strings
    # safe inside a <script> element: neither </script> nor <!-- can form.
    return json.dumps(payload, ensure_ascii=True).replace("<", "\\u003c")


def render_dashboard(stats: VaultStats | MasterStats) -> str:
    if stats.kind == "master":
        title = "brain — company dashboard"
    else:
        who = stats.person or Path(stats.vault).name
        title = f"brain — {who}'s vault"
    return _PAGE.substitute(
        title=html.escape(title),
        data_json=_embed_json(asdict(stats)),
    )


def write_dashboard(stats: VaultStats | MasterStats, out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_dashboard(stats), encoding="utf-8")
    return out_path
