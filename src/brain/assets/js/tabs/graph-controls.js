import { el, clear, clickable } from "../dom.js";

// The Obsidian-style control panel overlaid on the graph canvas: Filters
// (spaces, orphans, search), Display (node size, link width, text fade) and
// Forces (center pull, repel, link distance). This module owns the DOM and
// localStorage persistence; graph.js owns applying settings to the scene.
// All labels/space names reach the DOM via textContent (el/dom.js) only.

export const DEFAULTS = {
  nodeSize: 1, linkWidth: 1, textFade: 1,
  centerPull: 1, repel: 1, linkDist: 1,
  orphans: true, spacesOff: [],
};

export function loadSettings(key) {
  try {
    const raw = localStorage.getItem(key);
    if (raw) return Object.assign({}, DEFAULTS, JSON.parse(raw));
  } catch (e) { /* private mode / quota / bad JSON → defaults */ }
  return Object.assign({}, DEFAULTS);
}

export function saveSettings(key, settings) {
  try { localStorage.setItem(key, JSON.stringify(settings)); } catch (e) { /* best effort */ }
}

const SLIDERS = {
  display: [
    { k: "nodeSize",  label: "Node size",     min: 0.4, max: 2.5, step: 0.05 },
    { k: "linkWidth", label: "Link width",    min: 0.3, max: 3,   step: 0.05 },
    { k: "textFade",  label: "Text fade",     min: 0.3, max: 2,   step: 0.05 },
  ],
  forces: [
    { k: "centerPull", label: "Center pull",   min: 0.1, max: 1, step: 0.05 },
    { k: "repel",      label: "Repel force",   min: 0.2, max: 3, step: 0.05 },
    { k: "linkDist",   label: "Link distance", min: 0.4, max: 3, step: 0.05 },
  ],
};

export function mountControls(host, opts) {
  const s = opts.settings;
  const box = el("div", "graph-controls");

  const head = el("div", "gc-head");
  head.appendChild(el("span", null, "Graph controls"));
  const caret = el("span", "gc-caret", "–");
  head.appendChild(caret);
  clickable(head, () => {
    box.classList.toggle("collapsed");
    caret.textContent = box.classList.contains("collapsed") ? "+" : "–";
  });
  box.appendChild(head);

  const body = el("div", "gc-body");
  box.appendChild(body);

  function group(title) {
    const d = document.createElement("details");
    d.open = true;
    const sm = document.createElement("summary");
    sm.textContent = title;
    d.appendChild(sm);
    body.appendChild(d);
    return d;
  }

  // ---- Filters ----
  const filters = group("Filters");
  const search = el("input");
  search.type = "search";
  search.placeholder = "search notes…";
  search.setAttribute("aria-label", "Search notes in the graph");
  search.addEventListener("input", () => opts.onSearch(search.value));
  filters.appendChild(search);

  const orphanRow = el("label", "gc-check");
  const orphanBox = el("input");
  orphanBox.type = "checkbox";
  orphanBox.checked = s.orphans;
  orphanBox.addEventListener("change", () => { s.orphans = orphanBox.checked; opts.onFilter(); });
  orphanRow.appendChild(orphanBox);
  orphanRow.appendChild(el("span", null, "Show orphans"));
  filters.appendChild(orphanRow);

  const spacesBox = el("div", "gc-spaces");
  filters.appendChild(spacesBox);
  const truncBox = el("div", "gc-trunc");
  filters.appendChild(truncBox);

  function updateSpaces(spaces, truncatedNote) {
    clear(spacesBox);
    // drop stale persisted names so a renamed space can't stay invisibly off
    const known = new Set(spaces.map((sp) => sp.name));
    s.spacesOff = s.spacesOff.filter((n) => known.has(n));
    spaces.forEach((sp) => {
      const row = el("label", "gc-check");
      const cb = el("input");
      cb.type = "checkbox";
      cb.checked = !s.spacesOff.includes(sp.name);
      cb.addEventListener("change", () => {
        s.spacesOff = cb.checked
          ? s.spacesOff.filter((n) => n !== sp.name)
          : s.spacesOff.concat(sp.name);
        opts.onFilter();
      });
      row.appendChild(cb);
      const dot = el("span", "dot");
      dot.style.background = sp.color;
      row.appendChild(dot);
      row.appendChild(el("span", null, sp.name));
      spacesBox.appendChild(row);
    });
    clear(truncBox);
    if (truncatedNote) truncBox.appendChild(el("div", "gc-note", truncatedNote));
  }
  updateSpaces(opts.spaces, opts.truncatedNote);

  // ---- Display + Forces ----
  function sliderGroup(title, defs, cb) {
    const d = group(title);
    defs.forEach((def) => {
      const row = el("div", "gc-slider");
      row.appendChild(el("span", "gc-label", def.label));
      const input = el("input");
      input.type = "range";
      input.min = def.min; input.max = def.max; input.step = def.step;
      input.value = s[def.k];
      input.setAttribute("aria-label", def.label);
      input.addEventListener("input", () => { s[def.k] = Number(input.value); cb(); });
      row.appendChild(input);
      d.appendChild(row);
    });
  }
  sliderGroup("Display", SLIDERS.display, opts.onDisplay);
  sliderGroup("Forces", SLIDERS.forces, opts.onForces);

  host.appendChild(box);
  return { updateSpaces };
}
