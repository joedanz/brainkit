import { el, clear, clickable } from "../dom.js";
import { api } from "../api.js";

// Inbox and Actions: the two "what needs me" worklists. Each fetches its own
// list (the counts on Overview drill in here) and every row opens the source
// note in Query. Vault-lens only — a person's Inbox/Actions live in their slice.

function person(ctx) { return ctx.meta.kind === "master" ? ctx.person : undefined; }

export async function renderInbox(container, ctx) {
  clear(container);
  container.appendChild(el("h2", null, "Inbox"));
  const host = el("div");
  container.appendChild(host);
  host.appendChild(el("div", "meta", "Loading…"));
  let body;
  try { body = await api.inbox({ person: person(ctx) }); }
  catch (e) { clear(host); host.appendChild(el("div", "error-banner", "Inbox unavailable: " + e.message)); return; }
  clear(host);
  if (!body.notes.length) {
    host.appendChild(emptyState("Your inbox is clear.",
      "New notes arrive here from email, chat, voice, and uploads — nothing waiting right now."));
    return;
  }
  body.notes.forEach((n) => {
    const card = el("div", "result");
    card.appendChild(el("div", "loc", n.rel_path));
    card.appendChild(el("div", "tags", "added " + n.mtime));
    clickable(card, () => ctx.openNote(n.rel_path));
    host.appendChild(card);
  });
}

export async function renderActions(container, ctx) {
  clear(container);
  container.appendChild(el("h2", null, "Open actions"));
  const host = el("div");
  container.appendChild(host);
  host.appendChild(el("div", "meta", "Loading…"));
  let body;
  try { body = await api.actions({ person: person(ctx) }); }
  catch (e) { clear(host); host.appendChild(el("div", "error-banner", "Actions unavailable: " + e.message)); return; }
  clear(host);
  if (!body.actions.length) {
    host.appendChild(emptyState("No open actions.",
      "Unchecked `- [ ]` items across your Actions notes show up here as a single worklist."));
    return;
  }
  body.actions.forEach((a) => {
    const card = el("div", "result");
    card.appendChild(el("div", null, a.text || "(untitled action)"));
    card.appendChild(el("div", "loc", a.rel_path + " · line " + a.line));
    clickable(card, () => ctx.openNote(a.rel_path));
    host.appendChild(card);
  });
}

function emptyState(title, detail) {
  const box = el("div");
  box.style.padding = "var(--sp-8) 0";
  box.appendChild(el("div", null, title));
  const d = el("div", "meta", detail);
  d.style.marginTop = "var(--sp-2)";
  d.style.maxWidth = "56ch";
  box.appendChild(d);
  return box;
}
