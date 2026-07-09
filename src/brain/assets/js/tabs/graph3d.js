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
    for (let i = 0; i < n; i++) {
      pos[i].vx += -pos[i].x * 0.002 * alpha;
      pos[i].vy += -pos[i].y * 0.002 * alpha;
      pos[i].vz += -pos[i].z * 0.002 * alpha;
      pos[i].vx *= 0.85; pos[i].vy *= 0.85; pos[i].vz *= 0.85;
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
  const camDist = Math.max(160, extent * 2.4);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x1a2129);
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
  const lineMat = new THREE.LineBasicMaterial({ color: 0x4a5a6e, transparent: true, opacity: 0.7 });
  const lines = new THREE.LineSegments(lineGeom, lineMat);
  scene.add(lines);

  // nodes as one instanced sphere mesh
  const geom = new THREE.SphereGeometry(1, 12, 12);
  const mat = new THREE.MeshLambertMaterial();
  const mesh = new THREE.InstancedMesh(geom, mat, graph.nodes.length);
  const dummy = new THREE.Object3D();
  const color = new THREE.Color();
  const baseR = camDist * 0.014; // ~constant on-screen size whatever the extent
  graph.nodes.forEach((node, i) => {
    dummy.position.set(pos[i].x, pos[i].y, pos[i].z);
    const r = baseR * (1 + 0.5 * Math.sqrt(node.degree));
    dummy.scale.setScalar(r);
    dummy.updateMatrix();
    mesh.setMatrixAt(i, dummy.matrix);
    mesh.setColorAt(i, color.set(colorFor(node.space)));
  });
  scene.add(mesh);

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
      renderer.dispose();
      if (renderer.domElement.parentNode) renderer.domElement.parentNode.removeChild(renderer.domElement);
    },
  };
}
