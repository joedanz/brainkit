// 3D knowledge graph (three.js), loaded lazily by graph.js only when the user
// toggles "3D view". Layout is a small hand-rolled force integrator extended to
// a z axis — settled up front over a fixed number of iterations, then rendered
// statically while OrbitControls let the user orbit/zoom. Reusing our own force
// math avoids vendoring a third library (d3-force-3d).

import * as THREE from "../../vendor/three.module.min.js";
import { OrbitControls } from "../../vendor/OrbitControls.js";
import { colorFor } from "../dom.js";

function layout(graph) {
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

// How far a node sits from its nearest neighbour, typically — what a node's
// radius should be built on, since it is the only quantity that says whether
// two spheres will overlap. A median rather than a mean: one note parked far
// off on its own would otherwise inflate the gap and shrink every node to a
// speck. Sampled past a few hundred nodes, where the answer is a median anyway.
function medianNeighbourGap(pos) {
  const n = pos.length;
  if (n < 2) return 0;
  const step = Math.ceil(n / 400);
  const gaps = [];
  for (let i = 0; i < n; i += step) {
    let best = Infinity;
    for (let j = 0; j < n; j++) {
      if (i === j) continue;
      const dx = pos[i].x - pos[j].x, dy = pos[i].y - pos[j].y, dz = pos[i].z - pos[j].z;
      const d2 = dx * dx + dy * dy + dz * dz;
      if (d2 < best) best = d2;
    }
    if (best < Infinity) gaps.push(Math.sqrt(best));
  }
  if (!gaps.length) return 0;
  gaps.sort((a, b) => a - b);
  return gaps[Math.floor(gaps.length / 2)];
}

// Soft radial sprite drawn on a throwaway canvas — no image asset, stays
// offline. Additive-blended points behind the spheres read as glow.
function haloTexture() {
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const g = c.getContext("2d");
  const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  grad.addColorStop(0, "rgba(255,255,255,0.85)");
  grad.addColorStop(0.4, "rgba(255,255,255,0.25)");
  grad.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grad;
  g.fillRect(0, 0, 64, 64);
  return new THREE.CanvasTexture(c);
}

export function mount(host, graph, onNodeClick) {
  host.textContent = "";
  const W = host.clientWidth || 800, H = host.clientHeight || 560;
  const pos = layout(graph);

  // Fit the camera to the settled layout: small graphs cluster near the origin
  // and would otherwise be pixel-sized dots from a fixed-distance camera.
  let extent = 0;
  pos.forEach((p) => {
    extent = Math.max(extent, Math.hypot(p.x, p.y, p.z));
  });
  // The node that decides the framing is not the one directly behind the origin
  // but the one off to the side, so this is a sphere problem rather than a flat
  // one: over a sphere of the graph's own extent, viewed from d times that
  // extent, the largest projected angle is 1/sqrt(d*d - 1). Setting that equal
  // to tan(30) — half of the 60 degree vertical field of view — gives exactly
  // d = 2, below which the widest node falls outside the frustum. 2.1 keeps a
  // little margin; 2.4 left the graph filling barely a third of the frame.
  const camDist = Math.max(160, extent * 2.1);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x14110d); // warm near-black, matches the 2D canvas
  const camera = new THREE.PerspectiveCamera(60, W / H, 1, camDist * 10);
  camera.position.set(0, 0, camDist);
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  renderer.setSize(W, H);
  host.appendChild(renderer.domElement);

  scene.add(new THREE.AmbientLight(0xffffff, 0.7));
  const key = new THREE.DirectionalLight(0xffffff, 0.6);
  key.position.set(1, 1, 1);
  scene.add(key);

  // edges
  const linePos = [];
  graph.edges.forEach((e) => {
    linePos.push(pos[e.source].x, pos[e.source].y, pos[e.source].z);
    linePos.push(pos[e.target].x, pos[e.target].y, pos[e.target].z);
  });
  const lineGeom = new THREE.BufferGeometry();
  lineGeom.setAttribute("position", new THREE.Float32BufferAttribute(linePos, 3));
  const lineMat = new THREE.LineBasicMaterial({ color: 0x6e6459, transparent: true, opacity: 0.32 });
  const lines = new THREE.LineSegments(lineGeom, lineMat);
  scene.add(lines);

  // nodes as one instanced sphere mesh
  const geom = new THREE.SphereGeometry(1, 12, 12);
  const mat = new THREE.MeshLambertMaterial();
  const mesh = new THREE.InstancedMesh(geom, mat, graph.nodes.length);
  const dummy = new THREE.Object3D();
  const color = new THREE.Color();
  // Size from the gap between neighbours, not from the camera distance. camDist
  // measures the whole cloud, which says nothing about whether two spheres will
  // touch — and on a 300-note vault every one of them did, so the graph drew as
  // a single mass. A typical node is now a bit over a quarter of the distance
  // to its nearest neighbour, which reads as a node. The fallback covers a
  // graph with no gap to measure: one note, or every note on the same spot.
  const gap = medianNeighbourGap(pos);
  const baseR = gap > 0 ? gap * 0.14 : camDist * 0.014;
  graph.nodes.forEach((node, i) => {
    dummy.position.set(pos[i].x, pos[i].y, pos[i].z);
    // Damped and capped: the old curve let a degree-192 hub reach five and a
    // half times the base and swallow its own neighbourhood. A hub is now
    // three times a leaf and still smaller than the gap.
    const r = baseR * Math.min(3, 1 + 0.35 * Math.sqrt(node.degree));
    dummy.scale.setScalar(r);
    dummy.updateMatrix();
    mesh.setMatrixAt(i, dummy.matrix);
    mesh.setColorAt(i, color.set(colorFor(node.space)));
  });
  scene.add(mesh);

  const haloPos = new Float32Array(graph.nodes.length * 3);
  const haloCol = new Float32Array(graph.nodes.length * 3);
  graph.nodes.forEach((node, i) => {
    haloPos.set([pos[i].x, pos[i].y, pos[i].z], i * 3);
    color.set(colorFor(node.space));
    haloCol.set([color.r, color.g, color.b], i * 3);
  });
  const haloGeom = new THREE.BufferGeometry();
  haloGeom.setAttribute("position", new THREE.BufferAttribute(haloPos, 3));
  haloGeom.setAttribute("color", new THREE.BufferAttribute(haloCol, 3));
  const haloTex = haloTexture();
  const haloMat = new THREE.PointsMaterial({
    map: haloTex, vertexColors: true, transparent: true, depthWrite: false,
    blending: THREE.AdditiveBlending, size: baseR * 7, sizeAttenuation: true,
  });
  scene.add(new THREE.Points(haloGeom, haloMat));

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;

  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  function onClick(ev) {
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
    pointer.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(pointer, camera);
    const hit = raycaster.intersectObject(mesh)[0];
    if (hit && hit.instanceId != null && onNodeClick) onNodeClick(graph.nodes[hit.instanceId]);
  }
  renderer.domElement.addEventListener("click", onClick);

  let running = true;
  function animate() {
    if (!running) return;
    requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
  }
  animate();

  const ro = new ResizeObserver(() => {
    const w = host.clientWidth || W, h = host.clientHeight || H;
    camera.aspect = w / h; camera.updateProjectionMatrix();
    renderer.setSize(w, h);
  });
  ro.observe(host);

  return {
    dispose() {
      running = false;
      ro.disconnect();
      renderer.domElement.removeEventListener("click", onClick);
      controls.dispose();
      geom.dispose(); mat.dispose(); lineGeom.dispose(); lineMat.dispose();
      haloGeom.dispose(); haloMat.dispose(); haloTex.dispose();
      renderer.dispose();
      if (renderer.domElement.parentNode) renderer.domElement.parentNode.removeChild(renderer.domElement);
    },
  };
}
