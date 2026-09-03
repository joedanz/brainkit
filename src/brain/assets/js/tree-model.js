// Pure helpers over the /api/tree payload — no DOM, so node can test them and
// the Pages tab's two layouts (tree pane, phone listing) share one answer to
// "where is this page" and "what is in this folder".

// Directory paths above a page, top down: "A/B/c.md" → ["A", "A/B"].
export function ancestors(relPath) {
  const parts = relPath.split("/");
  parts.pop();
  const out = [];
  let acc = "";
  for (const p of parts) { acc = acc ? acc + "/" + p : p; out.push(acc); }
  return out;
}

// The Dir at a path, or null. "" is the root.
export function findDir(root, path) {
  if (!path) return root;
  let cur = root;
  for (const name of path.split("/")) {
    cur = (cur.dirs || []).find((d) => d.name === name);
    if (!cur) return null;
  }
  return cur;
}

// Breadcrumb entries for a directory path, root first.
export function crumbsFor(path) {
  const out = [{ name: "", path: "" }];
  let acc = "";
  for (const p of path ? path.split("/") : []) { acc = acc ? acc + "/" + p : p; out.push({ name: p, path: acc }); }
  return out;
}

// Folders first, then pages, each group by localeCompare (the server sorted
// with casefold; this is the client-side rule the portal also applies).
export function sortedChildren(dir) {
  const dirs = [...(dir.dirs || [])].sort((a, b) => a.name.localeCompare(b.name));
  const pages = [...(dir.pages || [])].sort((a, b) => a.title.localeCompare(b.title));
  return { dirs, pages };
}
