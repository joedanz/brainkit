// Shared between the person tab (their own corrections) and the admin tab
// (everyone's, gated by an approver select) — same card shape either way.
// Built with textContent only: rule text is written by an agent.
import { el } from "./dom.js";

export function correctionCard({ slug, text, badge, buildActions }) {
  const box = el("div", "promo");
  if (badge) {
    const h = el("div", "promo-head");
    h.appendChild(el("span", "promo-target", slug));
    h.appendChild(badge);
    box.appendChild(h);
  } else {
    box.appendChild(el("div", "promo-target", slug));
  }
  box.appendChild(el("div", null, text));
  const actions = el("div", "promo-actions");
  buildActions(box, actions);
  box.appendChild(actions);
  return box;
}

// Disable every button in `actions` while `run()` is in flight. `run` does
// the API call plus whatever happens on success (re-render, remove the
// card); on failure the buttons are re-enabled and `onError` shows why.
export function runCorrectionAction(actions, run, onError) {
  actions.querySelectorAll("button").forEach((b) => { b.disabled = true; });
  return run().catch((e) => {
    onError(e);
    actions.querySelectorAll("button").forEach((b) => { b.disabled = false; });
  });
}
