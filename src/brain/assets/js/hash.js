// The URL hash is the dashboard's only addressable state. Three grammars:
//
//   #<tab>                 a tab id (overview, pages, query, graph, …)
//   #note=<encoded path>   a vault note, opened on the query tab
//   #page=<encoded path>   a vault page, opened in the Pages tab's reader
//
// The path forms exist so a note can be linked from outside — an agent citing
// its sources, a message, a bookmark. Pure functions, no DOM, so the grammar
// is testable from node against the code that ships.
const NOTE_PREFIX = "note=";
const PAGE_PREFIX = "page=";

function decode(raw) {
  try { return decodeURIComponent(raw) || null; } catch { return null; }
}

export function parseHash(hash) {
  const h = (hash || "").replace(/^#/, "");
  if (h.startsWith(NOTE_PREFIX)) return { tab: "query", note: decode(h.slice(NOTE_PREFIX.length)), page: null };
  if (h.startsWith(PAGE_PREFIX)) return { tab: "pages", note: null, page: decode(h.slice(PAGE_PREFIX.length)) };
  return { tab: h || null, note: null, page: null };
}

export function noteHash(path) {
  return "#" + NOTE_PREFIX + encodeURIComponent(path);
}

export function pageHash(path) {
  return "#" + PAGE_PREFIX + encodeURIComponent(path);
}
