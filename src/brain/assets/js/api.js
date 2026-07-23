// Thin fetch wrappers over the JSON API. Every call is same-origin (the CSP
// forbids anything else). Non-2xx responses carry a `reason` the server set on
// the HTTP status line; surface it so 403/404 read clearly in the UI.

async function getJSON(path, params) {
  const url = new URL(path, location.origin);
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
    }
  }
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) {
    const detail = res.statusText || "";
    throw new Error(`${res.status} ${detail}`.trim());
  }
  return res.json();
}

// Writes are same-origin JSON POSTs. The server's non-GET guard requires exactly
// this content type + a local Origin (the browser always sends it), so a page
// the employee visits can't drive these endpoints.
async function postJSON(path, body) {
  const res = await fetch(new URL(path, location.origin), {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText || ""}`.trim());
  return res.json();
}

export const api = {
  meta: () => getJSON("/api/meta"),
  stats: () => getJSON("/api/stats"),
  graph: (params) => getJSON("/api/graph", params),
  search: (params) => getJSON("/api/search", params),
  facts: (params) => getJSON("/api/facts", params),
  notes: (params) => getJSON("/api/notes", params),
  note: (params) => getJSON("/api/note", params),
  inbox: (params) => getJSON("/api/inbox", params),
  actions: (params) => getJSON("/api/actions", params),
  promotion: (params) => getJSON("/api/promotion", params),
  capture: (body) => postJSON("/api/capture", body),
  promote: (body) => postJSON("/api/promote", body),
  approvePromotion: (id, body) => postJSON(`/api/promotions/${encodeURIComponent(id)}/approve`, body),
  rejectPromotion: (id, body) => postJSON(`/api/promotions/${encodeURIComponent(id)}/reject`, body),
  sweepPromotions: () => postJSON("/api/promotions/sweep", {}),
  approveShare: (id, body) => postJSON(`/api/shares/${encodeURIComponent(id)}/approve`, body),
  rejectShare: (id, body) => postJSON(`/api/shares/${encodeURIComponent(id)}/reject`, body),
};
