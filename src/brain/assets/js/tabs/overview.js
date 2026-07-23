import { el, section, barChart, colorFor, table, warningsBlock, clickable } from "../dom.js";

// Overview: a calm summary, not a tile wall. One lead number in the display
// face, then a "needs attention" list that only appears when something is
// actually pending, then index internals demoted to a quiet diagnostics strip.
// Re-rendered wholesale on live push (cheap, stateless).
export function render(container, ctx) {
  const d = ctx.stats;
  if (!d) { container.appendChild(el("div", "meta", "Loading…")); return; }
  if (d.kind === "master") renderMaster(container, d, ctx);
  else renderVault(container, d, ctx);
}

function lead(app, big, unit, asides) {
  const row = el("div", "lead");
  const b = el("div", "big");
  b.appendChild(document.createTextNode(String(big)));
  b.appendChild(el("span", "unit", unit));
  row.appendChild(b);
  asides.filter(Boolean).forEach(([n, label]) => {
    const a = el("div", "aside");
    a.appendChild(el("b", null, String(n)));
    a.appendChild(document.createTextNode(" " + label));
    row.appendChild(a);
  });
  app.appendChild(row);
}

function attention(app, items) {
  const live = items.filter((it) => it && it.n);
  if (!live.length) return;
  const box = el("div", "attention");
  live.forEach((it) => {
    const row = el("div", "attention-item");
    const dot = el("span", "dot"); dot.style.background = it.tone || "var(--accent)";
    row.appendChild(dot);
    row.appendChild(el("span", "n", String(it.n)));
    row.appendChild(document.createTextNode(" " + it.label));
    if (it.onClick) clickable(row, it.onClick); else row.style.cursor = "default";
    box.appendChild(row);
  });
  app.appendChild(box);
}

function diagnostics(app, pairs) {
  const strip = el("div", "diagnostics");
  pairs.filter(Boolean).forEach(([k, v]) => {
    const span = el("span");
    span.appendChild(el("span", "k", k + " "));
    span.appendChild(document.createTextNode(String(v)));
    strip.appendChild(span);
  });
  app.appendChild(strip);
}

function renderVault(app, d, ctx) {
  lead(app, d.notes_total, d.notes_total === 1 ? "note" : "notes", [
    [d.spaces.length, d.spaces.length === 1 ? "space" : "spaces"],
    d.inbox_count ? [d.inbox_count, "in inbox"] : null,
  ]);

  const warn = "var(--warn)";
  attention(app, [
    { n: d.inbox_count, label: "waiting in your inbox", tone: warn,
      onClick: () => ctx.goTab("inbox") },
    { n: d.open_actions, label: "open actions", tone: "var(--accent)",
      onClick: () => ctx.goTab("actions") },
    { n: d.pending_reindex.length, label: "notes awaiting reindex", tone: warn },
    { n: d.facts_total, label: "facts on record", tone: "var(--accent)",
      onClick: () => ctx.goTab("facts") },
  ]);

  const coverage = d.embedding_coverage === null ? "—"
    : Math.round(d.embedding_coverage * 100) + "%";
  diagnostics(app, [
    ["chunks", d.chunks_total],
    ["embedding coverage", coverage],
    ["facts", d.facts_total],
    ["entities", d.entities_total],
    d.pending_reindex.length ? ["pending reindex", d.pending_reindex.length] : null,
  ]);

  if (d.spaces.length) {
    const sp = section(app, "Notes by space");
    barChart(sp, d.spaces.map((s) => ({ label: s.space, count: s.notes, color: colorFor(s.space) })));
  }

  if (d.top_linked.length) {
    const tl = section(app, "Most linked notes");
    table(tl, ["note", "inbound links"], d.top_linked.map((n) => {
      const link = el("a", null, n.rel_path);
      clickable(link, () => ctx.openNote(n.rel_path));
      return [link, String(n.inbound)];
    }));
  }

  if (d.recent_commits.length) {
    const ra = section(app, "Recent activity");
    const ul = el("ul", "plain");
    d.recent_commits.forEach((c) => {
      const li = el("li");
      li.appendChild(el("span", "commit-sha", c.sha));
      li.appendChild(el("span", "commit-date", c.date));
      li.appendChild(document.createTextNode(c.subject));
      ul.appendChild(li);
    });
    ra.appendChild(ul);
  }

  warningsBlock(app, d.warnings);
}

function renderMaster(app, d, ctx) {
  const errs = d.findings.filter((f) => f.severity === "error").length;
  const warns = d.findings.filter((f) => f.severity === "warn").length;

  lead(app, d.people_count, d.people_count === 1 ? "person" : "people", [
    [d.spaces.length, d.spaces.length === 1 ? "space" : "spaces"],
  ]);

  attention(app, [
    { n: d.promotions_pending.length, label: "promotions awaiting approval", tone: "var(--warn)",
      onClick: () => ctx.goTab("promotions") },
    { n: d.shares_pending ? d.shares_pending.length : 0, label: "share requests pending", tone: "var(--warn)",
      onClick: () => ctx.goTab("shares") },
    { n: errs, label: "doctor errors", tone: "var(--err)", onClick: () => ctx.goTab("doctor") },
    { n: warns, label: "doctor warnings", tone: "var(--warn)", onClick: () => ctx.goTab("doctor") },
    { n: d.uncovered_spaces.length, label: "unreachable spaces", tone: "var(--err)" },
  ]);

  if (d.uncovered_spaces.length) {
    const s = section(app, "Unreachable spaces");
    s.appendChild(el("div", "meta",
      "No rule matches (invisible to everyone): " + d.uncovered_spaces.join(", ")));
  }

  warningsBlock(app, d.warnings);
}
