import { el, clear, clickable } from "./dom.js";
import { renderMarkdown } from "./md.js";
import { api } from "./api.js";

// One note, rendered: the path, a raw/rendered toggle, the Markdown body with
// live [[wikilinks]], and the backlinks block. Shared by the Query tab (which
// adds "Propose to share" through `extras`) and the Pages tab's reader, so the
// two never drift on how a note reads.
//
//   noteView({ path, person, onOpen, onClose, extras }) -> { element, ready }
//     onOpen(rel_path)        a wikilink or backlink was activated
//     onClose()               optional; adds a close button when given
//     extras(toolbar, body)   optional; called once the note is fetched
//   `element` is appended synchronously; `ready` resolves to the /api/note
//   body (or null after an error). A view replaced before its fetch returns
//   is detached, and a detached view never paints — that is the whole
//   out-of-order guard.

export function noteView({ path, person, onOpen, onClose, extras }) {
  const view = el("div", "note-view");
  if (onClose) {
    const close = el("button", "btn close", "close");
    close.addEventListener("click", onClose);
    view.appendChild(close);
  }
  const toolbar = el("div", "toolbar");
  toolbar.appendChild(el("h3", null, path));
  view.appendChild(toolbar);
  const bodyHost = el("div");
  view.appendChild(bodyHost);

  const ready = (async () => {
    let body;
    try {
      body = await api.note({ path, person });
    } catch (e) {
      if (view.isConnected) bodyHost.appendChild(el("div", "error-banner", "Cannot read " + path + ": " + e.message));
      return null;
    }
    if (!view.isConnected) return body;
    const links = body.links || { inbound: [], outbound: [], unresolved_out: [] };
    const resolve = buildResolver(links.outbound);
    const rawToggle = el("button", "btn", "view raw");
    let raw = false;
    const paint = () => {
      clear(bodyHost);
      if (raw) {
        const pre = el("pre", "raw"); pre.textContent = body.text; bodyHost.appendChild(pre);
        rawToggle.textContent = "view rendered";
      } else {
        bodyHost.appendChild(renderMarkdown(body.text, { resolve, onLink: (rel) => onOpen(rel) }));
        rawToggle.textContent = "view raw";
      }
      renderLinks(bodyHost, links, onOpen);
    };
    rawToggle.addEventListener("click", () => { raw = !raw; paint(); });
    toolbar.appendChild(rawToggle);
    if (extras) extras(toolbar, body);
    paint();
    return body;
  })();

  return { element: view, ready };
}

// map a [[wikilink]] target to a resolved outbound rel_path by matching either
// the full rel_path, the file stem, or a trailing path segment.
export function buildResolver(outbound) {
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

export function renderLinks(host, links, onOpen) {
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
  list("Linked from", inbound, onOpen);
  list("Links to", outbound, onOpen);
  list("Unresolved", unresolved_out, null);
  host.appendChild(box);
}
