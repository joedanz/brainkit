import { el, clear } from "./dom.js";
import { api } from "./api.js";

// Quick-capture: a calm composer that writes a note into an Inbox. Under the
// vault lens it lands in your own slice Inbox (write-back carries it to master
// on the next cycle); under the master lens an admin picks a person and it's
// ingested into master immediately. The caption always names the exact
// destination — "privacy made visible".

export function mountCapture(ctx) {
  const btn = el("button", "icon-btn", null);
  btn.type = "button";
  btn.title = "Capture a note (c)";
  btn.setAttribute("aria-label", "Capture a note");
  btn.appendChild(plusIcon());
  btn.addEventListener("click", () => open(ctx));
  document.addEventListener("keydown", (ev) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
    if (ev.key === "c" && !typing && !document.querySelector(".composer")) { ev.preventDefault(); open(ctx); }
  });
  return btn;
}

function plusIcon() {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("fill", "none");
  s.setAttribute("stroke", "currentColor"); s.setAttribute("stroke-width", "1.8");
  s.setAttribute("stroke-linecap", "round"); s.setAttribute("aria-hidden", "true");
  [["12", "6", "12", "18"], ["6", "12", "18", "12"]].forEach(([x1, y1, x2, y2]) => {
    const l = document.createElementNS("http://www.w3.org/2000/svg", "line");
    l.setAttribute("x1", x1); l.setAttribute("y1", y1); l.setAttribute("x2", x2); l.setAttribute("y2", y2);
    s.appendChild(l);
  });
  return s;
}

function open(ctx) {
  if (document.querySelector(".composer")) return;
  const overlay = el("div", "composer");
  const card = el("div", "composer-card");
  card.appendChild(el("h3", null, "Capture a note"));

  let personSel = null;
  if (ctx.meta.kind === "master") {
    personSel = el("select");
    personSel.setAttribute("aria-label", "Capture for person");
    ctx.meta.people.forEach((p) => {
      const o = el("option", null, p.name || p.id); o.value = p.id;
      if (p.id === ctx.person) o.selected = true;
      personSel.appendChild(o);
    });
    card.appendChild(personSel);
  }

  const title = el("input"); title.type = "text"; title.placeholder = "title (optional)";
  title.setAttribute("aria-label", "Note title");
  const bodyEl = el("textarea"); bodyEl.placeholder = "what's on your mind…";
  bodyEl.setAttribute("aria-label", "Note body");
  const caption = el("div", "composer-caption");
  const paintCaption = () => {
    const pid = ctx.meta.kind === "master" ? (personSel.value || "?") : (ctx.meta.person || "?");
    clear(caption);
    caption.appendChild(document.createTextNode("lands in "));
    caption.appendChild(el("code", null, `People/${pid}/Inbox`));
    caption.appendChild(document.createTextNode(
      ctx.meta.kind === "master" ? " — committed to master now" : " — appears after the next sync"));
  };
  paintCaption();
  if (personSel) personSel.addEventListener("change", paintCaption);

  const actions = el("div", "composer-actions");
  const cancel = el("button", "btn", "Cancel");
  const save = el("button", "btn primary", "Capture");
  actions.appendChild(cancel); actions.appendChild(save);

  card.appendChild(title);
  card.appendChild(bodyEl);
  card.appendChild(caption);
  card.appendChild(actions);
  overlay.appendChild(card);
  document.body.appendChild(overlay);
  bodyEl.focus();

  const close = () => overlay.remove();
  cancel.addEventListener("click", close);
  overlay.addEventListener("click", (ev) => { if (ev.target === overlay) close(); });
  overlay.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") close();
    if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) submit();
  });

  async function submit() {
    const body = bodyEl.value.trim();
    if (!body) { bodyEl.focus(); return; }
    save.disabled = true; save.textContent = "Capturing…";
    try {
      const payload = { title: title.value.trim(), body, source: "dashboard" };
      if (personSel) payload.person = personSel.value;
      const res = await api.capture(payload);
      close();
      toast(res.rel_path, ctx);
    } catch (e) {
      save.disabled = false; save.textContent = "Capture";
      let err = card.querySelector(".error-banner");
      if (!err) { err = el("div", "error-banner"); card.insertBefore(err, actions); }
      err.textContent = "Capture failed: " + e.message;
    }
  }
  save.addEventListener("click", submit);
}

function toast(relPath, ctx) {
  const t = el("div", "toast");
  t.appendChild(document.createTextNode("Captured to your Inbox."));
  const open = el("a", null, "open");
  open.setAttribute("role", "button"); open.setAttribute("tabindex", "0");
  open.style.cursor = "pointer";
  const go = () => { t.remove(); ctx.openNote(relPath); };
  open.addEventListener("click", go);
  open.addEventListener("keydown", (ev) => { if (ev.key === "Enter") go(); });
  t.appendChild(open);
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 6000);
}
