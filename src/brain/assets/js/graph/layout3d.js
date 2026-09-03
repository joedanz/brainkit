// 3D force layout: a small hand-rolled integrator extended to a z axis,
// settled up front over a fixed number of iterations. Pure — no DOM, no
// three.js — so node can import it (tests/test_graph_layout_js.py) and so the
// engine's 3D view (graph/view3d.js) and nothing else depends on it.
//
// Moved verbatim from the old 3D tab module. The displacement cap and its comment
// are the only bound against the float32 overflow; keep both exactly.

// Exported so a test can import and run the SHIPPED function rather than a
// re-implementation of it — see tests/test_graph_layout_js.py. Nothing else
// imports it.
export function layout(graph) {
  const n = graph.nodes.length;
  const pos = graph.nodes.map((_, i) => ({
    // deterministic spread so the same graph settles the same way each toggle
    x: Math.sin(i * 12.9898) * 200,
    y: Math.sin(i * 78.233) * 200,
    z: Math.sin(i * 37.719) * 200,
    vx: 0, vy: 0, vz: 0,
  }));
  const edges = graph.edges.map((e) => [e.source, e.target]);
  const iters = n > 800 ? 60 : 160;
  const repel = n <= 700; // O(n^2) repulsion only while it stays cheap
  for (let t = 0; t < iters; t++) {
    const alpha = 1 - t / iters;
    if (repel) {
      for (let i = 0; i < n; i++) {
        for (let j = i + 1; j < n; j++) {
          let dx = pos[i].x - pos[j].x, dy = pos[i].y - pos[j].y, dz = pos[i].z - pos[j].z;
          const d2 = dx * dx + dy * dy + dz * dz + 0.01;
          const f = 24000 / d2 * alpha;
          const d = Math.sqrt(d2); dx /= d; dy /= d; dz /= d;
          pos[i].vx += dx * f; pos[i].vy += dy * f; pos[i].vz += dz * f;
          pos[j].vx -= dx * f; pos[j].vy -= dy * f; pos[j].vz -= dz * f;
        }
      }
    }
    edges.forEach(([a, b]) => {
      let dx = pos[b].x - pos[a].x, dy = pos[b].y - pos[a].y, dz = pos[b].z - pos[a].z;
      const d = Math.sqrt(dx * dx + dy * dy + dz * dz) + 0.01;
      // linear spring toward rest length 80 — force quadratic in d (the 2D
      // code's `f * d` idiom) diverges at this spread and overflows to NaN
      const f = (d - 80) * 0.05 * alpha;
      dx /= d; dy /= d; dz /= d;
      pos[a].vx += dx * f; pos[a].vy += dy * f; pos[a].vz += dz * f;
      pos[b].vx -= dx * f; pos[b].vy -= dy * f; pos[b].vz -= dz * f;
    });
    // How far any one node may travel in a single pass, cooling with alpha.
    // This is the ONLY bound in the integrator, and without it the layout runs
    // away: measured against a real 300-note vault, reach grew 9.8e2 at 100
    // nodes, 6.7e34 at 146, 1.2e47 at 300 — all FINITE, so the guard below
    // never fired and nothing looked wrong. three.js then narrows positions to
    // float32 for the GPU, where anything past 3.4e38 becomes Infinity; 850 of
    // 900 coordinates did, and the tab drew an empty frame.
    //
    // It is NOT the repulsion running away, which is the natural guess.
    // Deleting that loop outright still diverges (7.6e61 at 200 nodes),
    // softening it by six orders of magnitude changes nothing, and above 700
    // nodes it never runs at all yet the layout still explodes. A pair sitting
    // exactly on top of each other in fact receives ZERO repulsion: the
    // direction is 0/0.1, so the large magnitude multiplies by nothing. The
    // driver is the linear spring summed over a high-degree hub — forward
    // Euler at an effective stiffness of degree * 0.05 leaves its stable
    // region, and the spring is linear in a distance that is itself growing.
    //
    // Which is the argument for capping displacement rather than any one
    // force: reach becomes bounded by construction — the seed spread plus
    // iterations times the step — and it holds for forces nobody has written
    // yet. Taming the spring instead only moves the number.
    const step = 50 * alpha + 1;
    for (let i = 0; i < n; i++) {
      pos[i].vx += -pos[i].x * 0.002 * alpha;
      pos[i].vy += -pos[i].y * 0.002 * alpha;
      pos[i].vz += -pos[i].z * 0.002 * alpha;
      pos[i].vx *= 0.85; pos[i].vy *= 0.85; pos[i].vz *= 0.85;
      const speed = Math.sqrt(pos[i].vx ** 2 + pos[i].vy ** 2 + pos[i].vz ** 2);
      if (speed > step) {
        const brake = step / speed;
        pos[i].vx *= brake; pos[i].vy *= brake; pos[i].vz *= brake;
      }
      pos[i].x += pos[i].vx; pos[i].y += pos[i].vy; pos[i].z += pos[i].vz;
    }
  }
  // Never hand NaN to the camera fit: a non-finite integrate would render an
  // empty scene with no error. Fall back to the deterministic initial spread.
  if (!pos.every((p) => isFinite(p.x) && isFinite(p.y) && isFinite(p.z))) {
    return graph.nodes.map((_, i) => ({
      x: Math.sin(i * 12.9898) * 200,
      y: Math.sin(i * 78.233) * 200,
      z: Math.sin(i * 37.719) * 200,
    }));
  }
  return pos;
}
