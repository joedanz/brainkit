// The URL hash is the dashboard's only addressable state. Two grammars:
//
//   #<tab>                 a tab id (overview, query, graph, …)
//   #note=<encoded path>   a vault note, opened on the query tab
//
// The second exists so a note can be linked from outside — an agent citing
// its sources, a message, a bookmark. Pure functions, no DOM, so the grammar
// is testable from node against the code that ships.
const NOTE_PREFIX = "note=";

export function parseHash(hash) {
  const h = (hash || "").replace(/^#/, "");
  if (!h.startsWith(NOTE_PREFIX)) return { tab: h || null, note: null };
  let note = null;
  try { note = decodeURIComponent(h.slice(NOTE_PREFIX.length)) || null; } catch { note = null; }
  return { tab: "query", note };
}

export function noteHash(path) {
  return "#" + NOTE_PREFIX + encodeURIComponent(path);
}
