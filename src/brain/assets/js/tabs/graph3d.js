// 3D knowledge graph (three.js), loaded lazily by graph.js only when the user
// toggles "3D view". Layout is a small hand-rolled force integrator extended to
// a z axis — settled up front over a fixed number of iterations, then rendered
// statically while OrbitControls let the user orbit/zoom. Reusing our own force
// math avoids vendoring a third library (d3-force-3d).

import * as THREE from "../../vendor/three.module.min.js";
import { OrbitControls } from "../../vendor/OrbitControls.js";
import { colorFor, medianNearestGap } from "../dom.js";
import { layout } from "../graph/layout3d.js";
export { layout };

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
  const gap = medianNearestGap(pos);
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
