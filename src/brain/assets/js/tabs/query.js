import { el, clear, snippet, latest, clickable } from "../dom.js";
import { renderMarkdown } from "../md.js";
import { api } from "../api.js";

// Query tab: free-text hybrid search plus structured filter chips. A text query
// hits /api/search; chips-only browse hits /api/notes; both run search then keep
// only hits that also satisfy the chip filters (intersect by rel_path). Clicking
// a result renders the note (Markdown, with its backlinks) through the
// path-scoped /api/note endpoint.
//
// Keyboard: "/" focuses search, ArrowUp/Down move the highlight, Enter opens,
// Escape closes the note. Live pushes are ignored here so an update never wipes
// what the user is typing or reading.

let S = null;
const PAGE = 25;

export function render(container, ctx) {
  clear(container);
  S = { ctx, container, results: null, note: null, runs: latest(), noteLoads: latest(),
        hits: [], sel: -1, k: PAGE, keywordOnly: false };
  buildBar();
  S.results = el("div");
  container.appendChild(S.results);
  S.noteHost = el("div");
  container.appendChild(S.noteHost);

  document.addEventListener("keydown", onKey);
  populateSpaces();
  if (ctx.pendingNote) { const p = ctx.pendingNote; ctx.pendingNote = null; showNote(p); }
  else run();
}

export function dispose() {
  document.removeEventListener("keydown", onKey);
  S = null;
}

function onKey(ev) {
  if (!S) return;
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
  if (ev.key === "/" && !typing) { ev.preventDefault(); S.q.focus(); S.q.select(); return; }
  if (ev.key === "Escape") { if (S.noteHost.firstChild) { clear(S.noteHost); return; } }
  if (typing && document.activeElement !== S.q) return;
  if (ev.key === "ArrowDown") { ev.preventDefault(); moveSel(1); }
  else if (ev.key === "ArrowUp") { ev.preventDefault(); moveSel(-1); }
  else if (ev.key === "Enter" && S.sel >= 0 && S.hits[S.sel]) { ev.preventDefault(); showNote(S.hits[S.sel].rel_path); }
}

function moveSel(delta) {
  if (!S.hits.length) return;
  S.sel = Math.max(0, Math.min(S.hits.length - 1, S.sel + delta));
  const cards = S.results.querySelectorAll(".result");
  cards.forEach((c, i) => c.classList.toggle("sel", i === S.sel));
  const cur = cards[S.sel];
  if (cur) cur.scrollIntoView({ block: "nearest" });
}

function person() { return S.ctx.meta.kind === "master" ? S.ctx.person : undefined; }

function buildBar() {
  const bar = el("div", "filter-bar");

  S.q = el("input"); S.q.type = "search"; S.q.placeholder = "search notes…  (press /)";
  S.q.setAttribute("aria-label", "Search notes");
  let timer = null;
  S.q.addEventListener("input", () => { clearTimeout(timer); S.k = PAGE; timer = setTimeout(run, 250); });
  bar.appendChild(S.q);

  if (S.ctx.meta.kind === "master") {
    S.personSel = el("select");
    S.personSel.setAttribute("aria-label", "Person");
    S.ctx.meta.people.forEach((p) => {
      const o = el("option", null, p.name || p.id); o.value = p.id;
      if (p.id === S.ctx.person) o.selected = true;
      S.personSel.appendChild(o);
    });
    S.personSel.addEventListener("change", () => { S.ctx.person = S.personSel.value; populateSpaces(); run(); });
    bar.appendChild(S.personSel);
  }

  S.space = el("select");
  S.space.setAttribute("aria-label", "Filter by space");
  S.space.appendChild(new Option("all spaces", ""));
  S.space.addEventListener("change", run);
  bar.appendChild(S.space);

  S.contains = el("input"); S.contains.type = "search"; S.contains.placeholder = "path contains…";
  S.contains.setAttribute("aria-label", "Filter by path substring");
  S.contains.style.flex = "0 1 160px";
  let ctimer = null;
  S.contains.addEventListener("input", () => { clearTimeout(ctimer); ctimer = setTimeout(run, 250); });
  bar.appendChild(S.contains);

  S.after = el("input"); S.after.type = "date"; S.after.title = "modified on/after";
  S.after.setAttribute("aria-label", "Modified on or after");
  S.after.addEventListener("change", run);
  bar.appendChild(S.after);

  S.unresolved = chip(bar, "unresolved links");
  S.pending = chip(bar, "pending reindex");
  S.keyword = chip(bar, "keyword only");
  S.keyword.addEventListener("click", () => { S.keywordOnly = !!S.keyword.dataset.on; });

  S.container.appendChild(bar);
}

function chip(bar, label) {
  const c = el("span", "chip-toggle", label);
  c.dataset.on = "";
  c.setAttribute("aria-pressed", "false");
  clickable(c, () => {
    c.dataset.on = c.dataset.on ? "" : "1";
    c.classList.toggle("on", !!c.dataset.on);
    c.setAttribute("aria-pressed", c.dataset.on ? "true" : "false");
    run();
  });
  bar.appendChild(c);
  return c;
}

function filters() {
  return {
    space: S.space.value || undefined,
    contains: S.contains.value.trim() || undefined,
    unresolved: S.unresolved.dataset.on ? "1" : undefined,
    pending: S.pending.dataset.on ? "1" : undefined,
    after: S.after.value || undefined,
  };
}

function anyFilter(f) {
  return f.space || f.contains || f.unresolved || f.pending || f.after;
}

async function populateSpaces() {
  try {
    const body = await api.notes({ person: person(), limit: 1000 });
    const spaces = [...new Set(body.notes.map((n) => n.space))].filter(Boolean).sort();
    const cur = S.space.value;
    clear(S.space);
    S.space.appendChild(new Option("all spaces", ""));
    spaces.forEach((sp) => S.space.appendChild(new Option(sp, sp)));
    S.space.value = cur;
  } catch { /* leave the "all spaces" option only */ }
}

async function run() {
  const token = S.runs.begin();
  const q = S.q.value.trim();
  const f = filters();
  const kw = S.keywordOnly ? "1" : undefined;
  clear(S.results);
  S.sel = -1;
  try {
    if (q) {
      const res = await api.search({ q, person: person(), k: S.k, keyword_only: kw });
      if (!S || !S.runs.current(token)) return;
      let hits = res.hits;
      let capped = false;
      if (anyFilter(f)) {
        const body = await api.notes(Object.assign({ person: person(), limit: 1000 }, f));
        if (!S || !S.runs.current(token)) return;
        capped = body.notes.length >= 1000;
        const keep = new Set(body.notes.map((n) => n.rel_path));
        hits = hits.filter((h) => keep.has(h.rel_path));
      }
      renderHits(hits, res.mode, res.warnings, capped);
    } else {
      const body = await api.notes(Object.assign({ person: person(), limit: 200 }, f));
      if (!S || !S.runs.current(token)) return;
      renderNotes(body.notes);
    }
  } catch (e) {
    if (!S || !S.runs.current(token)) return;
    S.results.appendChild(el("div", "error-banner", "Query failed: " + e.message));
  }
}

function renderHits(hits, mode, warnings, capped) {
  S.hits = hits;
  S.results.appendChild(el("div", "meta",
    hits.length + " result(s)" + (mode ? " · " + mode : "")));
  (warnings || []).forEach((w) => S.results.appendChild(el("div", "meta", "⚠ " + w)));
  if (capped) S.results.appendChild(el("div", "meta",
    "⚠ filter matched the first 1000 notes — some results may be hidden"));
  hits.forEach((h, i) => {
    const card = el("div", "result");
    card.appendChild(el("div", "loc", h.rel_path + (h.heading_path ? " — " + h.heading_path : "")));
    card.appendChild(snippet(h.snippet));
    if (h.sources && h.sources.length) card.appendChild(el("div", "tags", h.sources.join(" · ")));
    clickable(card, () => { S.sel = i; showNote(h.rel_path); });
    S.results.appendChild(card);
  });
  // "show more" when the page was full (there may be more)
  if (hits.length >= S.k) {
    const more = el("button", "btn more-btn", "Show more");
    more.addEventListener("click", () => { S.k += PAGE; run(); });
    S.results.appendChild(more);
  }
}

function renderNotes(notes) {
  S.hits = notes;
  S.results.appendChild(el("div", "meta", notes.length + " note(s)"));
  notes.forEach((n, i) => {
    const card = el("div", "result");
    card.appendChild(el("div", "loc", n.rel_path));
    const bits = [n.space, n.chunks + " chunk(s)", n.inbound + " inbound"];
    if (n.unresolved_out) bits.push(n.unresolved_out + " unresolved");
    if (n.mtime) bits.push("modified " + n.mtime);
    card.appendChild(el("div", "tags", bits.join(" · ")));
    clickable(card, () => { S.sel = i; showNote(n.rel_path); });
    S.results.appendChild(card);
  });
}

async function showNote(path) {
  const token = S.noteLoads.begin();
  clear(S.noteHost);
  const view = el("div", "note-view");
  const toolbar = el("div", "toolbar");
  const close = el("button", "btn close", "close");
  close.addEventListener("click", () => clear(S.noteHost));
  const rawToggle = el("button", "btn", "view raw");
  toolbar.appendChild(el("h3", null, path));
  view.appendChild(close);
  view.appendChild(toolbar);
  const bodyHost = el("div");
  view.appendChild(bodyHost);
  S.noteHost.appendChild(view);
  S.noteHost.scrollIntoView({ behavior: "smooth", block: "nearest" });

  try {
    const body = await api.note({ path, person: person() });
    if (!S || !S.noteLoads.current(token)) return;
    const links = body.links || { inbound: [], outbound: [], unresolved_out: [] };
    const resolve = buildResolver(links.outbound);
    let raw = false;
    const paint = () => {
      clear(bodyHost);
      if (raw) {
        const pre = el("pre", "raw"); pre.textContent = body.text; bodyHost.appendChild(pre);
        rawToggle.textContent = "view rendered";
      } else {
        bodyHost.appendChild(renderMarkdown(body.text, { resolve, onLink: (rel) => showNote(rel) }));
        rawToggle.textContent = "view raw";
      }
      renderLinks(bodyHost, links);
    };
    rawToggle.addEventListener("click", () => { raw = !raw; paint(); });
    toolbar.appendChild(rawToggle);
    toolbar.appendChild(proposeButton(path, body.text));
    paint();
  } catch (e) {
    if (!S || !S.noteLoads.current(token)) return;
    bodyHost.appendChild(el("div", "error-banner", "Cannot read " + path + ": " + e.message));
  }
}

// "Propose to share": draft a promotion from the open note. Reveals an inline
// target-path row (a shared-space file path) rather than a native prompt.
function proposeButton(path, text) {
  const btn = el("button", "btn", "Propose to share");
  btn.addEventListener("click", () => {
    if (btn.dataset.open) return;
    btn.dataset.open = "1";
    const row = el("div", "promo-actions");
    const target = el("input"); target.type = "text";
    target.placeholder = "target path in a shared space, e.g. Company/Shared/Note.md";
    target.setAttribute("aria-label", "Promotion target path");
    const go = el("button", "btn primary", "Draft");
    const cancel = el("button", "btn", "Cancel");
    const msg = el("span", "meta");
    const cleanup = () => { row.remove(); delete btn.dataset.open; };
    cancel.addEventListener("click", cleanup);
    go.addEventListener("click", async () => {
      if (!target.value.trim()) { target.focus(); return; }
      go.disabled = true; msg.textContent = "drafting…";
      try {
        await api.promote({ target_path: target.value.trim(), source: path, body: text, person: person() });
        msg.textContent = "drafted — an admin approves it from Promotions.";
        go.remove(); cancel.textContent = "Done";
      } catch (e) { go.disabled = false; msg.textContent = "Failed: " + e.message; }
    });
    row.appendChild(target); row.appendChild(go); row.appendChild(cancel); row.appendChild(msg);
    btn.parentNode.parentNode.insertBefore(row, btn.parentNode.nextSibling);
  });
  return btn;
}

// map a [[wikilink]] target to a resolved outbound rel_path by matching either
// the full rel_path, the file stem, or a trailing path segment.
function buildResolver(outbound) {
  const byStem = new Map();
  const byPath = new Map();
  (outbound || []).forEach((o) => { byPath.set(o.rel_path, o.rel_path); byStem.set(o.title, o.rel_path); });
  return (target) => {
    const t = target.trim();
    if (byPath.has(t)) return byPath.get(t);
    if (byStem.has(t)) return byStem.get(t);
    const stem = t.split("/").pop().replace(/\.md$/, "");
    return byStem.get(stem) || null;
  };
}

function renderLinks(host, links) {
  const { inbound, outbound, unresolved_out } = links;
  if (!inbound.length && !outbound.length && !unresolved_out.length) return;
  const box = el("div", "note-links");
  const list = (label, refs, onClick) => {
    if (!refs.length) return;
    box.appendChild(el("h4", null, label + " (" + refs.length + ")"));
    const ul = el("ul");
    refs.forEach((r) => {
      const li = el("li");
      if (onClick) {
        const a = el("a", "wikilink", r.title || r);
        clickable(a, () => onClick(r.rel_path));
        li.appendChild(a);
      } else {
        li.appendChild(el("span", "meta", r));
      }
      ul.appendChild(li);
    });
    box.appendChild(ul);
  };
  list("Linked from", inbound, (rel) => showNote(rel));
  list("Links to", outbound, (rel) => showNote(rel));
  list("Unresolved", unresolved_out, null);
  host.appendChild(box);
}
