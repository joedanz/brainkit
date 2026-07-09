// Theme toggle. Three states cycle: system → light → dark → system. "system"
// removes the attribute so the CSS `prefers-color-scheme` media query drives it;
// light/dark set [data-theme] which wins over the query. The choice persists in
// localStorage and is applied at module load (app.js) before first paint, so a
// returning visitor sees at most a one-frame flip — the honest cost of a strict
// CSP with no <head> inline script.

const KEY = "brain-theme";
const SVGNS = "http://www.w3.org/2000/svg";

function svg(paths, extra) {
  const s = document.createElementNS(SVGNS, "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("fill", "none");
  s.setAttribute("stroke", "currentColor");
  s.setAttribute("stroke-width", "1.8");
  s.setAttribute("stroke-linecap", "round");
  s.setAttribute("stroke-linejoin", "round");
  s.setAttribute("aria-hidden", "true");
  paths.forEach(([tag, attrs]) => {
    const n = document.createElementNS(SVGNS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    s.appendChild(n);
  });
  if (extra) extra(s);
  return s;
}

// sun: circle + 8 rays
function sunIcon() {
  const rays = [[12, 2, 12, 4], [12, 20, 12, 22], [2, 12, 4, 12], [20, 12, 22, 12],
    [4.9, 4.9, 6.3, 6.3], [17.7, 17.7, 19.1, 19.1], [4.9, 19.1, 6.3, 17.7], [17.7, 6.3, 19.1, 4.9]];
  return svg([["circle", { cx: 12, cy: 12, r: 4 }],
    ...rays.map(([x1, y1, x2, y2]) => ["line", { x1, y1, x2, y2 }])]);
}
// moon: crescent
function moonIcon() {
  return svg([["path", { d: "M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z" }]]);
}
// system: a monitor
function systemIcon() {
  return svg([["rect", { x: 3, y: 4, width: 18, height: 12, rx: 2 }],
    ["line", { x1: 8, y1: 20, x2: 16, y2: 20 }], ["line", { x1: 12, y1: 16, x2: 12, y2: 20 }]]);
}

const ORDER = ["system", "light", "dark"];
const ICON = { system: systemIcon, light: sunIcon, dark: moonIcon };
const LABEL = { system: "Theme: system", light: "Theme: light", dark: "Theme: dark" };

function current() {
  const v = localStorage.getItem(KEY);
  return v === "light" || v === "dark" ? v : "system";
}

function apply(mode) {
  const root = document.documentElement;
  if (mode === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", mode);
}

export const mountTheme = {
  applyStored() { apply(current()); },
  button() {
    const btn = document.createElement("button");
    btn.className = "icon-btn";
    btn.type = "button";
    const paint = () => {
      const mode = current();
      clearChildren(btn);
      btn.appendChild(ICON[mode]());
      btn.setAttribute("aria-label", LABEL[mode] + " (click to change)");
      btn.title = LABEL[mode];
    };
    btn.addEventListener("click", () => {
      const next = ORDER[(ORDER.indexOf(current()) + 1) % ORDER.length];
      if (next === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, next);
      apply(next);
      paint();
    });
    paint();
    return btn;
  },
};

function clearChildren(n) { while (n.firstChild) n.removeChild(n.firstChild); }
