import { el, clear } from "./dom.js";
import { api } from "./api.js";
import { connectWS } from "./ws.js";
import { mountTheme } from "./theme.js";
import { mountCapture } from "./capture.js";
import * as overview from "./tabs/overview.js";
import * as graph from "./tabs/graph.js";
import * as query from "./tabs/query.js";
import * as admin from "./tabs/admin.js";
import * as worklists from "./tabs/worklists.js";
import * as facts from "./tabs/facts.js";

// Apply the persisted theme before anything renders (the CSS media query covers
// the un-chosen case, so a first-time visitor sees their OS theme with no flip).
mountTheme.applyStored();

const appEl = document.getElementById("app");
const tabsEl = document.getElementById("tabs");

const ctx = {
  meta: null,
  stats: null,
  person: null,      // selected person for master graph/query
  pendingNote: null, // rel_path to open when the query tab next renders
  openNote(path) { ctx.pendingNote = path; showTab("query"); },
  goTab(id) { showTab(id, true); },
};

let TABS = [];
let activeId = null;
const buttons = new Map();

// Each tab declares how it reacts to a live stats push:
//   "rerender" — cheap stateless subtree, rebuilt on every push (default)
//   "onLive"   — owns its update (graph reloads in place, keeps zoom/positions)
//   "ignore"   — holds user input/scroll a blind rebuild would clobber (query)
function tabsFor(kind) {
  const overviewTab = { id: "overview", label: "Overview", render: overview.render, live: "rerender" };
  const inboxTab = { id: "inbox", label: "Inbox", render: worklists.renderInbox, live: "ignore" };
  const actionsTab = { id: "actions", label: "Actions", render: worklists.renderActions, live: "ignore" };
  const graphTab = { id: "graph", label: "Graph", render: graph.render, live: "onLive", onLive: graph.onLive, dispose: graph.dispose };
  const queryTab = { id: "query", label: "Query", render: query.render, live: "ignore", dispose: query.dispose };
  const factsTab = { id: "facts", label: "Facts", render: facts.render, live: "ignore", dispose: facts.dispose };
  if (kind === "master") {
    return [
      overviewTab,
      { id: "people", label: "People", render: admin.renderPeople, live: "rerender" },
      { id: "permissions", label: "Permissions", render: admin.renderPermissions, live: "rerender" },
      { id: "promotions", label: "Promotions", render: admin.renderPromotions, live: "rerender" },
      { id: "doctor", label: "Doctor", render: admin.renderDoctor, live: "rerender" },
      graphTab, queryTab,
    ];
  }
  return [overviewTab, inboxTab, actionsTab, graphTab, queryTab, factsTab];
}

function buildTabs() {
  clear(tabsEl);
  buttons.clear();
  TABS.forEach((tab) => {
    const b = el("button", null, tab.label);
    b.setAttribute("role", "tab");
    b.id = "tab-" + tab.id;
    b.setAttribute("aria-selected", "false");
    b.addEventListener("click", () => showTab(tab.id));
    tabsEl.appendChild(b);
    buttons.set(tab.id, b);
  });
}

function renderTab(tab) {
  clear(appEl);
  tab.render(appEl, ctx);
}

function showTab(id, focusPanel) {
  const tab = TABS.find((t) => t.id === id) || TABS[0];
  if (activeId && activeId !== tab.id) {
    const outgoing = TABS.find((t) => t.id === activeId);
    if (outgoing && outgoing.dispose) outgoing.dispose();
  }
  activeId = tab.id;
  if (location.hash.slice(1) !== tab.id) location.hash = tab.id;
  buttons.forEach((b, tid) => {
    const on = tid === tab.id;
    b.classList.toggle("active", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
  });
  appEl.setAttribute("aria-labelledby", "tab-" + tab.id);
  renderTab(tab);
  if (focusPanel) appEl.focus();
}

// A live stats push updates the active tab according to its declared `live`
// policy (see tabsFor): rerender the stateless ones, let the graph reload in
// place, leave the query/worklist tabs alone. Scroll position is preserved so a
// push never yanks the reader.
function onStats(reason) {
  updateBadges();
  // A backgrounded tab needn't repaint; refresh once when it becomes visible.
  if (document.hidden) { pendingRefresh = true; return; }
  const tab = TABS.find((t) => t.id === activeId);
  if (!tab) return;
  if (tab.live === "onLive") { if (reason !== "promotions") tab.onLive(); }
  else if (tab.live === "ignore") { /* user-driven; leave it */ }
  else {
    const top = appEl.scrollTop;
    renderTab(tab);
    appEl.scrollTop = top;
  }
}

let pendingRefresh = false;
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && pendingRefresh) { pendingRefresh = false; onStats("refresh"); }
});

// Badge the Promotions tab with the pending count so an admin on any tab sees
// the queue grow — the one event that most wants to be ambient.
function updateBadges() {
  const b = buttons.get("promotions");
  if (!b || !ctx.stats || ctx.stats.kind !== "master") return;
  const n = (ctx.stats.promotions_pending || []).length;
  let badge = b.querySelector(".tab-count");
  if (!n) { if (badge) badge.remove(); return; }
  if (!badge) { badge = document.createElement("span"); badge.className = "tab-count"; b.appendChild(badge); }
  badge.textContent = String(n);
}

function setStatus(state) {
  const s = document.getElementById("status");
  s.className = "status " + state;
  const label = state === "open" ? "live"
    : state === "connecting" ? "connecting…" : "reconnecting…";
  document.getElementById("status-text").textContent = label;
  setStale(state === "closed");
}

// A thin, honest notice while disconnected: the numbers on screen are frozen.
// The server pushes fresh "initial" stats on reconnect, so it self-heals.
function setStale(on) {
  let bar = document.getElementById("stale-bar");
  if (!on) { if (bar) bar.remove(); return; }
  if (bar) return;
  bar = el("div", "stale-bar", "Disconnected — data may be stale. Reconnecting…");
  bar.id = "stale-bar";
  tabsEl.parentNode.insertBefore(bar, tabsEl.nextSibling);
}

function showBanner(message) {
  clear(appEl);
  appEl.appendChild(el("div", "error-banner", message));
}

async function boot() {
  let meta;
  try {
    meta = await api.meta();
  } catch (e) {
    showBanner("Cannot reach the brain server: " + e.message);
    return;
  }
  ctx.meta = meta;
  document.getElementById("title").textContent = "brain — " + meta.title;
  document.getElementById("page-meta").textContent =
    meta.kind === "master" ? "company (admin) lens" : (meta.person || "vault");
  if (meta.kind === "master" && meta.people.length) ctx.person = meta.people[0].id;

  // Header cluster (left → right): Capture, theme toggle, status dot.
  const hr = document.getElementById("header-right");
  hr.insertBefore(mountTheme.button(), hr.firstChild);
  hr.insertBefore(mountCapture(ctx), hr.firstChild);

  TABS = tabsFor(meta.kind);
  buildTabs();

  connectWS({
    onStatus: setStatus,
    onMessage: (msg) => {
      if (msg.type === "stats") { ctx.stats = msg.data; onStats(msg.reason); }
      else if (msg.type === "error") { ctx.stats = null; showBanner("Stats error: " + msg.message); }
    },
  });

  window.addEventListener("hashchange", () => {
    const id = location.hash.slice(1);
    if (id && id !== activeId) showTab(id);
  });

  showTab(location.hash.slice(1) || TABS[0].id);
}

boot();
