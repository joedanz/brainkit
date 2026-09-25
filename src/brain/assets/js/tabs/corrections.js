import { el, clear } from "../dom.js";
import { api } from "../api.js";

// The person's own standing corrections. A correction reaches their agent
// only after they confirm the exact text shown here; the hash of that text
// goes back with the click, so a rule edited since the page loaded is refused.
// Built with textContent only: rule text is written by an agent.
export async function render(container) {
  clear(container);
  container.appendChild(el("h2", null, "Corrections"));
  container.appendChild(el("div", "meta",
    "Your agent writes a correction when you tell it it got something wrong. "
    + "It only takes effect after you confirm it here."));
  const host = el("div");
  container.appendChild(host);
  host.appendChild(el("div", "meta", "Loading…"));
  let body;
  try { body = await api.corrections(); }
  catch (e) { clear(host); host.appendChild(el("div", "error-banner", "Corrections unavailable: " + e.message)); return; }
  clear(host);
  if (body.record_error) {
    host.appendChild(el("div", "error-banner",
      "Your confirmations can't be read right now, so nothing new can be confirmed. Ask whoever runs your brain to take a look."));
  }
  group(host, "Waiting for you", body.pending, true, container);
  group(host, "Cannot be used", body.rejected, false, container);
  group(host, "In effect", body.active, false, container);
  group(host, "Held back: this wording could interfere with how your agent works",
    body.flagged || [], false, container);
  if (!body.pending.length && !body.rejected.length && !body.active.length
      && !(body.flagged || []).length) {
    host.appendChild(el("div", "meta", "No corrections yet."));
  }
}

function group(host, title, items, canConfirm, container) {
  if (!items.length) return;
  host.appendChild(el("h3", null, title));
  items.forEach((c) => host.appendChild(card(c, canConfirm, container)));
}

function card(c, canConfirm, container) {
  const box = el("div", "promo");
  box.appendChild(el("div", "promo-target", c.slug));
  box.appendChild(el("div", null, c.rule !== undefined ? c.rule : c.reason));
  const actions = el("div", "promo-actions");
  if (canConfirm) {
    const ok = el("button", "btn primary", "Confirm");
    ok.addEventListener("click", () => act(box, actions, () =>
      api.confirmCorrection(c.slug, { sha256: c.sha256 }), container));
    actions.appendChild(ok);
  }
  const drop = el("button", "btn", "Dismiss");
  drop.addEventListener("click", () => act(box, actions, () =>
    api.dismissCorrection(c.slug, {}), container));
  actions.appendChild(drop);
  box.appendChild(actions);
  return box;
}

async function act(box, actions, call, container) {
  actions.querySelectorAll("button").forEach((b) => { b.disabled = true; });
  try { await call(); await render(container); }
  catch (e) {
    box.appendChild(el("div", "error-banner", "That didn't work: " + e.message));
    actions.querySelectorAll("button").forEach((b) => { b.disabled = false; });
  }
}
