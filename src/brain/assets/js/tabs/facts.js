import { el, clear, latest, clickable, warningsBlock } from "../dom.js";
import { api } from "../api.js";

// Facts tab: browse the vault's time-stamped fact lines. The one date control
// is valid time — "what was true on this date". Belief-time queries
// (--believed-on) stay CLI/MCP-only: they replay git history, which a browser
// view can't do. Live pushes are ignored (live: "ignore" in app.js) so a stats
// push never clobbers the filters mid-thought.

let S = null;

export function render(container, ctx) {
  clear(container);
  S = { ctx, container, results: null, runs: latest() };
  buildBar();
  S.results = el("div");
  container.appendChild(S.results);
  run();
}

export function dispose() { S = null; }

function buildBar() {
  const bar = el("div", "filter-bar");

  S.entity = el("input");
  S.entity.type = "search";
  S.entity.placeholder = "entity — title, alias, or path…";
  S.entity.setAttribute("aria-label", "Filter by entity");
  let timer = null;
  S.entity.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(run, 250); });
  bar.appendChild(S.entity);

  S.type = el("select");
  S.type.setAttribute("aria-label", "Filter by entity type");
  S.type.appendChild(new Option("all types", ""));
  ((S.ctx.stats && S.ctx.stats.entity_types) || []).forEach((t) =>
    S.type.appendChild(new Option(t, t)));
  S.type.addEventListener("change", run);
  bar.appendChild(S.type);

  S.asOf = el("input");
  S.asOf.type = "date";
  S.asOf.title = "facts true on this date";
  S.asOf.setAttribute("aria-label", "Facts true on this date");
  S.asOf.addEventListener("change", run);
  bar.appendChild(S.asOf);

  S.ended = el("span", "chip-toggle", "include ended");
  S.ended.dataset.on = "";
  S.ended.setAttribute("aria-pressed", "false");
  clickable(S.ended, () => {
    S.ended.dataset.on = S.ended.dataset.on ? "" : "1";
    S.ended.classList.toggle("on", !!S.ended.dataset.on);
    S.ended.setAttribute("aria-pressed", S.ended.dataset.on ? "true" : "false");
    run();
  });
  bar.appendChild(S.ended);

  S.container.appendChild(bar);
}

async function run() {
  if (!S) return;  // a debounced input can fire after dispose() nulls S
  const token = S.runs.begin();
  clear(S.results);
  try {
    const body = await api.facts({
      entity: S.entity.value.trim() || undefined,
      type: S.type.value || undefined,
      as_of: S.asOf.value || undefined,
      include_ended: S.ended.dataset.on ? "1" : undefined,
    });
    if (!S || !S.runs.current(token)) return;
    renderHits(body.hits, body.warnings);
  } catch (e) {
    if (!S || !S.runs.current(token)) return;
    S.results.appendChild(el("div", "error-banner", "Facts query failed: " + e.message));
  }
}

function renderHits(hits, warnings) {
  S.results.appendChild(el("div", "meta",
    hits.length ? hits.length + " fact(s)" : "no facts"));
  hits.forEach((h) => {
    const card = el("div", "result");
    card.appendChild(el("div", null, h.statement));
    const bits = [h.from_date + " → " + (h.until_date || "")];
    if (h.sources && h.sources.length) bits.push(h.sources.join(" · "));
    card.appendChild(el("div", "tags", bits.join("  ·  ")));
    const loc = el("a", "loc", h.rel_path + ":" + h.line);
    clickable(loc, () => S.ctx.openNote(h.rel_path));
    card.appendChild(loc);
    S.results.appendChild(card);
  });
  warningsBlock(S.results, warnings);
}
