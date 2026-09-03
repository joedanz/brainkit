// Health signals derived client-side from the /api/graph payload — no server
// change. Indexed like data.nodes:
//   inbound[i]  links pointing at node i (what sizes a node)
//   orphan[i]   degree 0: nothing links in or out. The server's `degree`
//               counts links to notes the cap may have cut, so it is trusted
//               over the visible edge list; a node with no `degree` field
//               falls back to the edges.
//   deadEnd[i]  linked to but linking nowhere (no outbound). An orphan is not
//               also a dead end — the two flags name different problems.
export function healthFlags(data) {
  const n = data.nodes.length;
  const inbound = new Array(n).fill(0);
  const outbound = new Array(n).fill(0);
  for (const e of data.edges) {
    if (e.source === e.target) continue;
    if (e.source < 0 || e.source >= n || e.target < 0 || e.target >= n) continue;
    inbound[e.target] += 1;
    outbound[e.source] += 1;
  }
  const orphan = data.nodes.map((node, i) => {
    const degree = typeof node.degree === "number" ? node.degree : inbound[i] + outbound[i];
    return degree === 0;
  });
  const deadEnd = data.nodes.map((_, i) => !orphan[i] && outbound[i] === 0);
  return { inbound, orphan, deadEnd };
}
