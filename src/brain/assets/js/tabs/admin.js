import { el, section, table, badge, fmtBytes, warningsBlock, clickable } from "../dom.js";
import { renderMarkdown } from "../md.js";
import { api } from "../api.js";

// Admin (master-lens) tabs. Each is a full re-render on live push, so per-person
// drift, the promotion queue, and doctor findings stay current as `brain cycle`
// runs. All four read the same MasterStats already in ctx.stats.

function guard(container, ctx) {
  const d = ctx.stats;
  if (!d || d.kind !== "master") {
    container.appendChild(el("div", "meta", "Loading…"));
    return null;
  }
  return d;
}

export function renderPeople(container, ctx) {
  const d = guard(container, ctx); if (!d) return;
  if (d.out_root === null) {
    container.appendChild(el("div", "meta",
      "Compiled root not provided — rerun with --out to inspect per-person vaults."));
    return;
  }
  const rows = d.people.map((p) => {
    if (!p.compiled) return [p.person_id, p.name, "—", "—", "—", badge("warn", "not compiled")];
    const drift = p.drift_error ? badge("error", p.drift_error)
      : (p.drift ? badge("info", p.drift + " change(s)") : badge("ok", "clean"));
    return [p.person_id, p.name, fmtBytes(p.disk_bytes), String(p.notes),
            p.index_built_at || "never", drift];
  });
  table(container, ["person", "name", "size", "notes", "index built", "awaiting writeback"], rows);
}

export function renderPermissions(container, ctx) {
  const d = guard(container, ctx); if (!d) return;
  const ids = d.people.map((p) => p.person_id);
  table(container, ["space"].concat(ids), d.permissions.map((perm) =>
    [perm.space].concat(ids.map((id) => {
      const r = perm.readers.indexOf(id) >= 0, w = perm.writers.indexOf(id) >= 0;
      return r && w ? "RW" : r ? "R" : w ? "W" : "—";
    }))), 1);
  if (d.uncovered_spaces.length) {
    container.appendChild(el("div", "meta",
      "Unreachable spaces (no rule matches): " + d.uncovered_spaces.join(", ")));
  }
}

export function renderPromotions(container, ctx) {
  const d = guard(container, ctx); if (!d) return;

  const head = el("div"); head.style.display = "flex";
  head.style.justifyContent = "space-between"; head.style.alignItems = "baseline";
  head.appendChild(el("h2", null, "Promotions awaiting approval"));
  const sweep = el("button", "btn", "Sweep drafts");
  sweep.title = "Move agent/person-drafted promotions into the queue";
  sweep.addEventListener("click", async () => {
    sweep.disabled = true; sweep.textContent = "Sweeping…";
    try { await api.sweepPromotions(); } catch (e) { /* surfaced by next stats push */ }
    sweep.disabled = false; sweep.textContent = "Sweep drafts";
  });
  head.appendChild(sweep);
  container.appendChild(head);

  if (!d.promotions_pending.length) {
    container.appendChild(el("div", "meta", "The queue is empty — nothing waiting to be shared."));
    return;
  }
  // Each promotion is the ONLY path from a private space to a shared one, and a
  // human gates every one. Review the body + destination before deciding.
  d.promotions_pending.forEach((p) => container.appendChild(promoCard(p, d.people)));
}

// The approver select is the only identity signal on approve, so it is
// constrained to the org roster; the last choice is remembered per browser.
const APPROVER_KEY = "brain.approver";

function approverSelect(people) {
  const sel = el("select");
  sel.setAttribute("aria-label", "Approver");
  const ph = el("option", null, "approving as…");
  ph.value = ""; ph.disabled = true;
  sel.appendChild(ph);
  people.forEach((per) => {
    const o = el("option", null, per.name + " (" + per.person_id + ")");
    o.value = per.person_id;
    sel.appendChild(o);
  });
  const stored = localStorage.getItem(APPROVER_KEY);
  sel.value = (stored && people.some((per) => per.person_id === stored)) ? stored : "";
  return sel;
}

function promoCard(p, people) {
  const card = el("div", "promo");
  const h = el("div", "promo-head");
  h.appendChild(el("span", "promo-target", p.target_path));
  h.appendChild(el("span", "meta", "from " + p.person_id + " · " + p.created));
  if (p.mode && p.mode !== "create") {
    h.appendChild(badge(p.mode === "patch" ? "warn" : "info", p.mode));
  }
  card.appendChild(h);

  const space = p.target_path.split("/").slice(0, 2).join("/");
  card.appendChild(el("div", "promo-visibility",
    "⚠ approving makes this visible to everyone who can read " + space));

  const bodyHost = el("div");
  card.appendChild(bodyHost);
  const reviewBtn = el("button", "btn", "Review contents");
  let loaded = false;
  reviewBtn.addEventListener("click", async () => {
    if (loaded) { bodyHost.firstChild ? clearNode(bodyHost) : null; return; }
    reviewBtn.textContent = "Loading…";
    try {
      const full = await api.promotion({ id: p.id });
      if (full.diff != null) {
        bodyHost.appendChild(full.diff ? diffBlock(full.diff)
          : el("div", "meta", "(no changes — proposed page is identical to the current one)"));
      } else {
        if (full.mode === "patch") {
          bodyHost.appendChild(el("div", "meta",
            "(target missing — cannot diff; approval will fail closed)"));
        }
        const doc = renderMarkdown(full.body || "", {});
        doc.className = "note-doc promo-body";
        bodyHost.appendChild(doc);
      }
      loaded = true;
      reviewBtn.textContent = "Contents";
      reviewBtn.disabled = true;
    } catch (e) {
      bodyHost.appendChild(el("div", "error-banner", "Cannot load: " + e.message));
      reviewBtn.textContent = "Review contents";
    }
  });

  const actions = el("div", "promo-actions");
  actions.appendChild(reviewBtn);
  const approver = approverSelect(people);
  const approve = el("button", "btn primary", "Approve");
  const reject = el("button", "btn", "Reject");
  approve.disabled = !approver.value;
  approver.addEventListener("change", () => {
    approve.disabled = !approver.value;
    if (approver.value) localStorage.setItem(APPROVER_KEY, approver.value);
  });
  approve.addEventListener("click", async () => {
    setBusy(actions, true);
    try { await api.approvePromotion(p.id, { approver: approver.value }); card.remove(); }
    catch (e) { cardError(card, actions, "Approve failed: " + e.message); setBusy(actions, false); }
  });

  // Reject reveals an inline reason row (a required reason, no native prompt).
  const rejectRow = el("div", "promo-actions"); rejectRow.style.display = "none";
  const reason = el("input"); reason.type = "text"; reason.placeholder = "reason for rejecting (required)";
  reason.setAttribute("aria-label", "Rejection reason");
  const confirmReject = el("button", "btn", "Confirm reject");
  const cancelReject = el("button", "btn", "Cancel");
  rejectRow.appendChild(reason); rejectRow.appendChild(confirmReject); rejectRow.appendChild(cancelReject);
  reject.addEventListener("click", () => { rejectRow.style.display = "flex"; reason.focus(); });
  cancelReject.addEventListener("click", () => { rejectRow.style.display = "none"; });
  confirmReject.addEventListener("click", async () => {
    if (!reason.value.trim()) { reason.focus(); return; }
    setBusy(rejectRow, true);
    try { await api.rejectPromotion(p.id, { reason: reason.value.trim() }); card.remove(); }
    catch (e) { cardError(card, rejectRow, "Reject failed: " + e.message); setBusy(rejectRow, false); }
  });

  actions.appendChild(approver);
  actions.appendChild(approve);
  actions.appendChild(reject);
  card.appendChild(actions);
  card.appendChild(rejectRow);
  return card;
}

// A patch promotion is a full-page replace — the reviewable artifact is the
// diff against the live page, not the proposed body on its own.
function diffBlock(diff) {
  const pre = el("pre", "promo-body");
  pre.style.overflowX = "auto";
  diff.split("\n").forEach((line) => {
    const row = el("div", null, line);
    if (line.startsWith("+") && !line.startsWith("+++")) row.style.color = "var(--ok, #3fb950)";
    else if (line.startsWith("-") && !line.startsWith("---")) row.style.color = "var(--err, #f85149)";
    else if (line.startsWith("@@")) row.style.color = "var(--warn, #d29922)";
    pre.appendChild(row);
  });
  return pre;
}

function clearNode(n) { while (n.firstChild) n.removeChild(n.firstChild); }
function setBusy(actions, on) { actions.querySelectorAll("button").forEach((b) => b.disabled = on); }
function cardError(card, before, msg) {
  let e = card.querySelector(".error-banner");
  if (!e) { e = el("div", "error-banner"); card.insertBefore(e, before); }
  e.textContent = msg;
}

export function renderDoctor(container, ctx) {
  const d = guard(container, ctx); if (!d) return;
  if (!d.findings.length) {
    container.appendChild(el("div", "meta", "No findings — brain is healthy."));
    return;
  }
  table(container, ["severity", "check", "message"], d.findings.map((f) =>
    [badge(f.severity === "error" ? "error" : f.severity, f.severity), f.check, f.message]));
  warningsBlock(container, d.warnings);
}
