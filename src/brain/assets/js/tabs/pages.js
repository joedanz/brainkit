import { el, clear, clickable, latest } from "../dom.js";
import { api } from "../api.js";
import { pageHash } from "../hash.js";
import { noteView } from "../note-view.js";
import { ancestors, findDir, crumbsFor, sortedChildren } from "../tree-model.js";

// Pages: browse the vault by folder. One tab, two layouts on the 900px
// breakpoint. Desktop: a 280px tree (collapsible folders with page counts,
// the current path expanded and highlighted) beside the reader; picking a
// page swaps only the reader, so the tree keeps its scroll and open state.
// Phone: a flat breadcrumb listing — folders first, then pages — and a page
// opens full width with a back crumb. The reader is the Query tab's note
// view (Markdown, wikilinks, backlinks). Deep link: #page=<encoded rel_path>,
// written with replaceState so a click never pushes history.

let S = null;
const PHONE = "(max-width: 899px)";

function person() { return S.ctx.meta.kind === "master" ? S.ctx.person : undefined; }

export function render(container, ctx) {
  clear(container);
  S = { ctx, container, root: null, open: new Set(), current: null, dir: "",
        loads: latest(), phone: matchMedia(PHONE).matches, tree: null, reader: null };
  if (ctx.pendingPage) {
    S.current = ctx.pendingPage;
    ctx.pendingPage = null;
    const up = ancestors(S.current);
    S.dir = up.length ? up[up.length - 1] : "";
    up.forEach((p) => S.open.add(p));
  }
  buildChrome();
  load();
}

// A live push re-reads the tree in place: open folders, scroll, and the open
// page all stay where they are.
export function onLive() { if (S) load(); }

export function dispose() { S = null; }

function buildChrome() {
  if (S.ctx.meta.kind === "master") {
    const bar = el("div", "filter-bar");
    const sel = el("select");
    sel.setAttribute("aria-label", "Person");
    S.ctx.meta.people.forEach((p) => {
      const o = el("option", null, p.name || p.id); o.value = p.id;
      if (p.id === S.ctx.person) o.selected = true;
      sel.appendChild(o);
    });
    sel.addEventListener("change", () => {
      S.ctx.person = sel.value;
      S.open = new Set(); S.current = null; S.dir = "";
      clear(S.reader);
      history.replaceState(null, "", "#pages");
      load();
    });
    bar.appendChild(sel);
    S.container.appendChild(bar);
  }
  const wrap = el("div", "pages-wrap");
  S.tree = el("nav", "pages-tree");
  S.tree.setAttribute("aria-label", "Folders");
  S.reader = el("div", "pages-reader");
  wrap.appendChild(S.tree);
  wrap.appendChild(S.reader);
  S.container.appendChild(wrap);
}

async function load() {
  const token = S.loads.begin();
  let body;
  try {
    body = await api.tree({ person: person() });
  } catch (e) {
    if (!S || !S.loads.current(token)) return;
    clear(S.reader);
    S.reader.appendChild(el("div", "error-banner", "Pages unavailable: " + e.message));
    return;
  }
  if (!S || !S.loads.current(token)) return;
  S.root = body.root;
  if (S.phone) {
    // An open note (S.current, reader already painted) keeps its DOM across a
    // live push — a rebuild would drop the raw/rendered toggle and in-note
    // scroll, and re-fetch /api/note for nothing. Only S.root refreshes; the
    // listing catches up next time the user goes back to it.
    if (!S.current || !S.reader.firstChild) paintPhone();
  } else {
    paintTree();
    if (S.current && !S.reader.firstChild) openPage(S.current);
    else if (!S.current && !S.reader.firstChild) hint();
  }
}

function hint() {
  clear(S.reader);
  if (!S.root.count) {
    S.reader.appendChild(el("div", "meta", "No pages are indexed yet — run `brain index` and they appear here."));
    return;
  }
  S.reader.appendChild(el("div", "meta", "Pick a page from the tree."));
}

// ---- desktop: tree pane --------------------------------------------------------

function paintTree() {
  const top = S.tree.scrollTop;
  clear(S.tree);
  S.tree.appendChild(dirBox(S.root, 0));
  S.tree.scrollTop = top;
}

function dirBox(dir, depth) {
  const box = el("div", "tree-dir");
  const { dirs, pages } = sortedChildren(dir);
  if (dir.path) {
    const isOpen = S.open.has(dir.path);
    const row = el("button", "tree-row folder" + (isOpen ? " open" : ""));
    row.type = "button";
    row.style.paddingLeft = (6 + depth * 12) + "px";
    row.setAttribute("aria-expanded", isOpen ? "true" : "false");
    row.appendChild(el("span", "caret", isOpen ? "▾" : "▸"));
    row.appendChild(el("span", null, dir.name));
    row.appendChild(el("span", "n", String(dir.count)));
    row.addEventListener("click", () => {
      if (S.open.has(dir.path)) S.open.delete(dir.path); else S.open.add(dir.path);
      paintTree();
    });
    box.appendChild(row);
    if (!isOpen) return box;
  }
  const kids = el("div", "tree-kids");
  dirs.forEach((d) => kids.appendChild(dirBox(d, depth + 1)));
  pages.forEach((p) => {
    const row = el("button", "tree-row page" + (p.rel_path === S.current ? " current" : ""));
    row.type = "button";
    row.dataset.path = p.rel_path;
    row.style.paddingLeft = (6 + (depth + 1) * 12 + 10) + "px";
    row.appendChild(el("span", null, p.title));
    row.addEventListener("click", () => openPage(p.rel_path));
    kids.appendChild(row);
  });
  box.appendChild(kids);
  return box;
}

function markCurrent() {
  S.tree.querySelectorAll(".tree-row.page").forEach((r) => r.classList.toggle("current", r.dataset.path === S.current));
}

// ---- both layouts: open a page ---------------------------------------------------

function openPage(relPath) {
  S.current = relPath;
  history.replaceState(null, "", pageHash(relPath));
  if (S.phone) { paintPhone(); return; }
  // Expand the path if it is not already; a rebuild keeps scroll, and when
  // nothing needs opening only the highlight moves.
  let opened = false;
  for (const p of ancestors(relPath)) if (!S.open.has(p)) { S.open.add(p); opened = true; }
  if (opened) paintTree(); else markCurrent();
  clear(S.reader);
  S.reader.appendChild(noteView({ path: relPath, person: person(), onOpen: (rel) => openPage(rel) }).element);
}

// ---- phone: breadcrumb listing or full-width reader ------------------------------

function paintPhone() {
  clear(S.reader);
  if (S.current) {
    const up = ancestors(S.current);
    const parent = up.length ? up[up.length - 1] : "";
    const back = el("button", "crumb-back", "← " + (parent ? parent.split("/").pop() : "Pages"));
    back.type = "button";
    back.addEventListener("click", () => {
      S.current = null; S.dir = parent;
      history.replaceState(null, "", "#pages");
      paintPhone();
    });
    S.reader.appendChild(back);
    S.reader.appendChild(noteView({ path: S.current, person: person(), onOpen: (rel) => openPage(rel) }).element);
    return;
  }
  const dir = findDir(S.root, S.dir) || S.root;
  const crumbs = el("div", "crumbs");
  crumbsFor(dir.path).forEach((c, i, all) => {
    if (i) crumbs.appendChild(el("span", "sep", "/"));
    const b = el("button", null, c.name || "Pages");
    b.type = "button";
    if (i === all.length - 1) b.disabled = true;
    b.addEventListener("click", () => { S.dir = c.path; paintPhone(); });
    crumbs.appendChild(b);
  });
  S.reader.appendChild(crumbs);
  const { dirs, pages } = sortedChildren(dir);
  if (!dirs.length && !pages.length) {
    S.reader.appendChild(el("div", "meta", S.root.count ? "Empty folder." : "No pages are indexed yet."));
    return;
  }
  dirs.forEach((d) => {
    const card = el("div", "result");
    card.appendChild(el("div", null, "📁 " + d.name));
    card.appendChild(el("div", "tags", d.count + " page(s)"));
    clickable(card, () => { S.dir = d.path; paintPhone(); });
    S.reader.appendChild(card);
  });
  pages.forEach((p) => {
    const card = el("div", "result");
    card.appendChild(el("div", null, p.title));
    card.appendChild(el("div", "tags", p.mtime ? "modified " + p.mtime : p.rel_path));
    clickable(card, () => openPage(p.rel_path));
    S.reader.appendChild(card);
  });
}
