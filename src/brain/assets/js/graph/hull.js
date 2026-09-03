// Region geometry for the light theme's territory map: the convex hull of a
// space's nodes, padded so the tint clears the node circles. Padding is done
// by ringing every point with RING points at radius `pad` and hulling the
// lot — the same code handles one note, two notes on a line, and a cluster,
// with no special cases for degenerate shapes. Andrew's monotone chain; the
// result is counter-clockwise with no repeated vertex. The painter rounds it
// (quadratic curves through edge midpoints); this module stays pure.
const RING = 8;

export function hullFor(points, pad) {
  const pts = [];
  for (const p of points) {
    if (!Number.isFinite(p.x) || !Number.isFinite(p.y)) continue;
    for (let i = 0; i < RING; i++) {
      const a = (i / RING) * Math.PI * 2;
      pts.push({ x: p.x + Math.cos(a) * pad, y: p.y + Math.sin(a) * pad });
    }
  }
  if (!pts.length) return [];
  pts.sort((a, b) => a.x - b.x || a.y - b.y);
  const cross = (o, a, b) => (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
  const lower = [];
  for (const p of pts) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop();
    lower.push(p);
  }
  const upper = [];
  for (let i = pts.length - 1; i >= 0; i--) {
    const p = pts[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop();
    upper.push(p);
  }
  lower.pop(); upper.pop();
  return lower.concat(upper);
}

// Where a space's name goes: the mean of its nodes. z defaults to 0 so 2D
// points and 3D positions share the function.
export function centroidOf(points) {
  let x = 0, y = 0, z = 0, n = 0;
  for (const p of points) {
    if (!Number.isFinite(p.x) || !Number.isFinite(p.y)) continue;
    x += p.x; y += p.y; z += p.z || 0; n++;
  }
  return n ? { x: x / n, y: y / n, z: z / n } : { x: 0, y: 0, z: 0 };
}
