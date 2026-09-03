// The 3D painter (three.js), reached only through engine.js's dynamic
// import so its ~650KB parses when someone asks for it. Layout is the moved
// integrator in layout3d.js, settled up front; the scene is then static and
// OrbitControls let the person orbit and zoom. One finger orbits, pinch zooms
// (OrbitControls' defaults).
//
// Visual rules match view2d.js: nodes colored by space, size from inbound
// links (unit = gap * 0.14, hub scale capped at 3x), space names as
// billboarded sprites at each centroid, hub labels as sprites, light theme
// regions as soft translucent spheres, dark theme glow as additive halos,
// edges at 0.75 opacity, focus fading everything outside the neighbourhood.
import * as THREE from "../../vendor/three.module.min.js";
import { OrbitControls } from "../../vendor/OrbitControls.js";
import { layout } from "./layout3d.js";
import { labelBudget, labelShown } from "./labels.js";
import { colorFor } from "./palette.js";
import { medianNearestGap } from "../dom.js";

const FADE = 0.15;

// three.js Color cannot parse oklch(), and the products' tokens are whatever
// their stylesheet uses. Painting the color once resolves any CSS color.
export function cssToHex(css) {
  const c = document.createElement("canvas");
  c.width = c.height = 1;
  const g = c.getContext("2d");
  g.fillStyle = "#000000";
  g.fillStyle = css;
  g.fillRect(0, 0, 1, 1);
  const [r, gg, b] = g.getImageData(0, 0, 1, 1).data;
  return "#" + [r, gg, b].map((v) => v.toString(16).padStart(2, "0")).join("");
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

// A ring outline for the orphan / dead-end marks (dashed or dotted in 2D; a
// sprite cannot dash cheaply, so the two differ by ring weight here).
function ringTexture(weight) {
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const g = c.getContext("2d");
  g.strokeStyle = "rgba(255,255,255,1)";
  g.lineWidth = weight;
  g.beginPath(); g.arc(32, 32, 26, 0, Math.PI * 2); g.stroke();
  return new THREE.CanvasTexture(c);
}

// Billboarded text. Drawn at 2x for crispness; the caller sets the world
// height and the sprite keeps the text's aspect.
function textSprite(text, { px, color, alpha, font, weight }) {
  const c = document.createElement("canvas");
  const g = c.getContext("2d");
  const f = `${weight} ${px * 2}px ${font}`;
  g.font = f;
  const w = Math.ceil(g.measureText(text).width) + px;
  c.width = Math.max(2, w);
  c.height = Math.ceil(px * 2 * 1.35);
  g.font = f;
  g.textBaseline = "middle";
  g.fillStyle = color;
  g.fillText(text, px / 2, c.height / 2);
  const tex = new THREE.CanvasTexture(c);
  const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false, opacity: alpha });
  const sprite = new THREE.Sprite(mat);
  sprite.userData.aspect = c.width / c.height;
  return { sprite, dispose() { tex.dispose(); mat.dispose(); } };
}

function centroid3(points) {
  let x = 0, y = 0, z = 0, n = 0;
  for (const p of points) { x += p.x; y += p.y; z += p.z; n++; }
  return n ? { x: x / n, y: y / n, z: z / n } : { x: 0, y: 0, z: 0 };
}

export function createView3d(E) {
  const W = E.surface.clientWidth || 800, H = E.surface.clientHeight || 560;
  // Throws without WebGL; engine.js turns that into the 2D fallback + note.
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  // updateStyle=false: .ge-canvas sizes the element with CSS; three must not
  // write a style attribute (blocked by brainkit's CSP).
  renderer.setSize(W, H, false);
  renderer.domElement.className = "ge-canvas";
  E.surface.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(60, W / H, 1, 100000);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  let autoFit = true;
  // Any OrbitControls change is a real gesture: the view is theirs from here.
  const onGesture = () => { autoFit = false; };
  controls.addEventListener("start", onGesture);
  scene.add(new THREE.AmbientLight(0xffffff, 0.7));
  const key = new THREE.DirectionalLight(0xffffff, 0.6);
  key.position.set(1, 1, 1);
  scene.add(key);

  let pos = [], gap = 0, baseR = 1, fitDist = 160, radii = [];
  const built = [];             // {obj, dispose} for everything a build adds
  let mesh = null, bright = null, dim = null, halos = null, marks = null;
  let labelSprites = [], lastK = -1;
  const halo = haloTexture(), ringThin = ringTexture(3), ringThick = ringTexture(6);

  function add(obj, dispose) { scene.add(obj); built.push({ obj, dispose }); return obj; }
  function clearBuilt() {
    for (const b of built.splice(0)) { scene.remove(b.obj); b.dispose(); }
    mesh = bright = dim = halos = marks = null;
    labelSprites = []; lastK = -1;
  }
  function hex(css) { return new THREE.Color(cssToHex(css)); }
  function nodeRadius(i) {
    // Damped and capped: a hub is three times a leaf and still smaller than the gap.
    return baseR * Math.min(3, 1 + 0.35 * Math.sqrt(E.flags.inbound[i])) * E.prefs.nodeSize;
  }

  // ---- build ------------------------------------------------------------------------
  function build() {
    clearBuilt();
    const data = E.data, n = data.nodes.length, dark = E.tokens.theme === "dark";
    scene.background = hex(E.tokens.bg);
    pos = layout(data);
    gap = medianNearestGap(pos);
    const extent = pos.reduce((m, p) => Math.max(m, Math.hypot(p.x, p.y, p.z)), 0);
    // Size from the gap between neighbours, not from the camera distance —
    // camDist measures the whole cloud and says nothing about whether two
    // spheres touch. The fallback covers a graph with no gap to measure.
    baseR = gap > 0 ? gap * 0.14 : Math.max(160, extent * 2.1) * 0.014;
    radii = data.nodes.map((_, i) => nodeRadius(i));

    // nodes: one instanced sphere mesh
    const geom = new THREE.SphereGeometry(1, 12, 12);
    const mat = new THREE.MeshLambertMaterial();
    mesh = add(new THREE.InstancedMesh(geom, mat, n), () => { geom.dispose(); mat.dispose(); });
    const dummy = new THREE.Object3D(), color = new THREE.Color();
    for (let i = 0; i < n; i++) {
      dummy.position.set(pos[i].x, pos[i].y, pos[i].z);
      dummy.scale.setScalar(E.visible(i) ? radii[i] : 0);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
      mesh.setColorAt(i, color.set(colorFor(data.nodes[i].space)));
    }

    // edges: two segment sets, so focus can dim the rest without per-vertex alpha
    const lineColor = hex(E.tokens.line);
    const mkLines = (opacity) => {
      const g = new THREE.BufferGeometry();
      const m = new THREE.LineBasicMaterial({ color: lineColor, transparent: true, opacity });
      return add(new THREE.LineSegments(g, m), () => { g.dispose(); m.dispose(); });
    };
    bright = mkLines(0.75);
    dim = mkLines(0.11);

    if (dark) {
      const hp = new Float32Array(n * 3), hc = new Float32Array(n * 3);
      for (let i = 0; i < n; i++) {
        hp.set([pos[i].x, pos[i].y, pos[i].z], i * 3);
        color.set(colorFor(data.nodes[i].space));
        hc.set([color.r, color.g, color.b], i * 3);
      }
      const hg = new THREE.BufferGeometry();
      hg.setAttribute("position", new THREE.BufferAttribute(hp, 3));
      hg.setAttribute("color", new THREE.BufferAttribute(hc, 3));
      const hm = new THREE.PointsMaterial({ map: halo, vertexColors: true, transparent: true,
        depthWrite: false, blending: THREE.AdditiveBlending, size: baseR * 7, sizeAttenuation: true });
      halos = add(new THREE.Points(hg, hm), () => { hg.dispose(); hm.dispose(); });
    } else {
      // Regions: a soft translucent sphere per space, radius from the cluster's spread.
      const bySpace = new Map();
      data.nodes.forEach((node, i) => { if (!E.visible(i)) return; let a = bySpace.get(node.space); if (!a) bySpace.set(node.space, a = []); a.push(pos[i]); });
      for (const [space, pts] of bySpace) {
        const c = centroid3(pts);
        let spread = 0;
        for (const p of pts) spread += (p.x - c.x) ** 2 + (p.y - c.y) ** 2 + (p.z - c.z) ** 2;
        const r = Math.sqrt(spread / pts.length) * 1.6 + baseR * 3;
        const sg = new THREE.SphereGeometry(r, 24, 16);
        const sm = new THREE.MeshBasicMaterial({ color: new THREE.Color(colorFor(space)), transparent: true, opacity: 0.13, depthWrite: false });
        const s = add(new THREE.Mesh(sg, sm), () => { sg.dispose(); sm.dispose(); });
        s.position.set(c.x, c.y, c.z);
      }
    }

    // Space names: billboarded sprites at each centroid, translucent, always on.
    const bySpaceAll = new Map();
    data.nodes.forEach((node, i) => { let a = bySpaceAll.get(node.space); if (!a) bySpaceAll.set(node.space, a = []); a.push(pos[i]); });
    const nameH = Math.max(baseR * 6, gap * 1.4);
    for (const [space, pts] of bySpaceAll) {
      const c = centroid3(pts);
      const t = textSprite(space, { px: 26, color: colorFor(space), alpha: dark ? 0.35 : 0.5, font: E.font, weight: 600 });
      t.sprite.position.set(c.x, c.y, c.z);
      t.sprite.scale.set(nameH * t.sprite.userData.aspect, nameH, 1);
      t.sprite.visible = !E.hidden.has(space);
      add(t.sprite, t.dispose);
    }

    // Orphan / dead-end marks as ring sprites over the sphere.
    const mp = [];
    let thick = false;
    data.nodes.forEach((node, i) => {
      if (!E.visible(i)) return;
      if (E.prefs.orphans && E.flags.orphan[i]) { mp.push(pos[i].x, pos[i].y, pos[i].z); thick = true; }
      else if (E.prefs.deadEnds && E.flags.deadEnd[i]) mp.push(pos[i].x, pos[i].y, pos[i].z);
    });
    if (mp.length) {
      const mg = new THREE.BufferGeometry();
      mg.setAttribute("position", new THREE.Float32BufferAttribute(mp, 3));
      const mm = new THREE.PointsMaterial({ map: thick ? ringThick : ringThin, color: hex(E.tokens.fg), transparent: true,
        depthWrite: false, size: baseR * 3.2, sizeAttenuation: true });
      marks = add(new THREE.Points(mg, mm), () => { mg.dispose(); mm.dispose(); });
    }

    paintFocus();
    if (autoFit) fitNow();
    relabel(true);
  }

  // Focus: neighbourhood edges stay bright, the rest fade; nodes outside the
  // neighbourhood lerp toward the background (reads as 15% opacity).
  function paintFocus() {
    const data = E.data, color = new THREE.Color(), bg = hex(E.tokens.bg), dummy = new THREE.Object3D();
    for (let i = 0; i < data.nodes.length; i++) {
      color.set(colorFor(data.nodes[i].space));
      if (E.hood && !E.hood.has(i)) color.lerp(bg, 1 - FADE);
      mesh.setColorAt(i, color);
      dummy.position.set(pos[i].x, pos[i].y, pos[i].z);
      dummy.scale.setScalar(E.visible(i) ? radii[i] * (i === E.selected ? 1.25 : 1) : 0);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
    }
    mesh.instanceColor.needsUpdate = true;
    mesh.instanceMatrix.needsUpdate = true;
    const hot = [], rest = [];
    for (const e of data.edges) {
      if (!E.visible(e.source) || !E.visible(e.target)) continue;
      const a = pos[e.source], b = pos[e.target];
      const inHood = !E.hood || (E.hood.has(e.source) && E.hood.has(e.target));
      (inHood ? hot : rest).push(a.x, a.y, a.z, b.x, b.y, b.z);
    }
    bright.geometry.setAttribute("position", new THREE.Float32BufferAttribute(hot, 3));
    dim.geometry.setAttribute("position", new THREE.Float32BufferAttribute(rest, 3));
    if (halos) halos.material.opacity = E.hood ? 0.35 : 1;
  }

  // Hub labels: sprites above the nodes the label rule admits. k is how far
  // in the camera is relative to the fitted distance — the 3D stand-in for zoom.
  function relabel(force) {
    const k = fitDist / Math.max(1, camera.position.distanceTo(controls.target));
    if (!force && Math.abs(k - lastK) < lastK * 0.05) return;
    lastK = k;
    for (const l of labelSprites) { scene.remove(l.sprite); l.dispose(); }
    labelSprites = [];
    const data = E.data, vis = data.nodes.filter((_, i) => E.visible(i)).length;
    const budget = labelBudget(E.prefs.labels, vis);
    const h = baseR * 2.2;
    data.nodes.forEach((node, i) => {
      if (!E.visible(i) || !labelShown(E.rank[i], k, gap, budget)) return;
      const faded = E.hood && !E.hood.has(i);
      const t = textSprite(node.title, { px: 14, color: cssToHex(E.tokens.fg), alpha: faded ? FADE : 1, font: E.font, weight: 500 });
      t.sprite.position.set(pos[i].x, pos[i].y + radii[i] + h * 0.7, pos[i].z);
      t.sprite.scale.set(h * t.sprite.userData.aspect, h, 1);
      scene.add(t.sprite);
      labelSprites.push(t);
    });
  }

  // Fit to MEASURED bounds: the centroid and radius of the visible nodes, not
  // OrbitControls' captured home. Over a sphere of radius r viewed from d*r,
  // the widest node subtends 1/sqrt(d*d - 1); equal to tan(30°), half of the
  // 60° field of view, gives d = 2. 2.1 keeps a little margin.
  function fitNow() {
    const vis = pos.filter((_, i) => E.visible(i));
    const c = centroid3(vis.length ? vis : pos);
    let radius = 0;
    for (const p of (vis.length ? vis : pos)) radius = Math.max(radius, Math.hypot(p.x - c.x, p.y - c.y, p.z - c.z));
    fitDist = Math.max(160, radius * 2.1);
    controls.target.set(c.x, c.y, c.z);
    camera.position.set(c.x, c.y, c.z + fitDist);
    camera.near = 1; camera.far = fitDist * 10;
    camera.updateProjectionMatrix();
    controls.update();
    relabel(true);
  }

  // ---- picking ------------------------------------------------------------------------
  const raycaster = new THREE.Raycaster(), pointer = new THREE.Vector2();
  function pick(ev) {
    if (!mesh) return null;
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
    pointer.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(pointer, camera);
    const hit = raycaster.intersectObject(mesh)[0];
    return hit && hit.instanceId != null ? hit.instanceId : null;
  }
  const onClick = (ev) => E.select(pick(ev));
  const onDbl = (ev) => { const i = pick(ev); if (i != null) E.open(i); };
  let moveRaf = 0;
  const onMove = (ev) => {
    if (ev.pointerType && ev.pointerType !== "mouse") return;
    if (moveRaf) return;
    moveRaf = requestAnimationFrame(() => {
      moveRaf = 0;
      const i = pick(ev);
      renderer.domElement.classList.toggle("ge-hit", i != null);
      E.setHover(i, ev.clientX, ev.clientY);
    });
  };
  const onLeave = () => E.setHover(null, 0, 0);
  renderer.domElement.addEventListener("click", onClick);
  renderer.domElement.addEventListener("dblclick", onDbl);
  renderer.domElement.addEventListener("pointermove", onMove);
  renderer.domElement.addEventListener("pointerleave", onLeave);

  let running = true;
  function animate() {
    if (!running) return;
    requestAnimationFrame(animate);
    controls.update();
    if (mesh) relabel(false);
    renderer.render(scene, camera);
  }
  animate();

  const ro = new ResizeObserver(() => {
    const w = E.surface.clientWidth || W, h = E.surface.clientHeight || H;
    camera.aspect = w / h; camera.updateProjectionMatrix();
    renderer.setSize(w, h, false);
  });
  ro.observe(E.surface);

  return {
    setData: build,
    applyForces() { /* the 3D layout is settled up front; force sliders shape the 2D view only */ },
    refresh() { if (!mesh) return; radii = E.data.nodes.map((_, i) => nodeRadius(i)); build(); },
    setTokens: build,                 // sprites and halos/regions are redrawn on a theme change
    fit() { autoFit = true; fitNow(); },
    destroy() {
      running = false;
      ro.disconnect();
      cancelAnimationFrame(moveRaf);
      renderer.domElement.removeEventListener("click", onClick);
      renderer.domElement.removeEventListener("dblclick", onDbl);
      renderer.domElement.removeEventListener("pointermove", onMove);
      renderer.domElement.removeEventListener("pointerleave", onLeave);
      controls.removeEventListener("start", onGesture);
      controls.dispose();
      clearBuilt();
      for (const l of labelSprites) { scene.remove(l.sprite); l.dispose(); }
      halo.dispose(); ringThin.dispose(); ringThick.dispose();
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}
