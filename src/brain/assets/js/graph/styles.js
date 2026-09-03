// The engine's chrome styles, shipped as a string and installed as ONE
// constructable stylesheet per document (spec Appendix A): CSSOM insertion is
// outside CSP style-src, so brainkit's `default-src 'self'` needs no
// relaxation and the portal loads no brainkit stylesheet. The sheet is
// module-level, so a remount does not adopt it twice, and destroy leaves it
// in place — it is inert without a .ge host.
//
// Every color is a --ge-* custom property the engine sets on the host with
// el.style.setProperty (CSSOM again; a `style=` attribute would be blocked),
// and every per-element dynamic value (a chip's dot color, the tooltip's
// position) is likewise a custom property read here with var(). Fonts
// inherit from the host.
let sheet = null;

export function adoptStyles() {
  if (!sheet) {
    sheet = new CSSStyleSheet();
    sheet.replaceSync(ENGINE_CSS);
  }
  if (!document.adoptedStyleSheets.includes(sheet)) {
    document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
  }
}

export const ENGINE_CSS = `
.ge { position: relative; display: flex; flex-direction: column; height: 100%; min-height: 320px;
      background: var(--ge-bg); color: var(--ge-fg); font-size: 13px; overflow: hidden; }
.ge-surface { position: relative; flex: 1 1 auto; min-height: 0; }
.ge-canvas { display: block; width: 100%; height: 100%; touch-action: none; }
.ge-canvas.ge-hit { cursor: pointer; }
.ge-toolbar { position: absolute; top: 8px; right: 8px; z-index: 3; display: flex; gap: 6px;
              align-items: center; flex-wrap: wrap; justify-content: flex-end; max-width: calc(100% - 16px); }
.ge-toolbar input[type=search] { width: 150px; background: var(--ge-panel); color: var(--ge-fg);
              border: 1px solid var(--ge-line); border-radius: 6px; padding: 4px 8px; font: inherit; }
.ge-btn { background: var(--ge-panel); color: var(--ge-fg); border: 1px solid var(--ge-line);
          border-radius: 6px; padding: 4px 9px; font: inherit; cursor: pointer; white-space: nowrap; }
.ge-btn:hover { border-color: var(--ge-fg); }
.ge-btn.on { border-color: var(--ge-fg); box-shadow: inset 0 0 0 1px var(--ge-fg); }
.ge-legend { position: absolute; left: 8px; bottom: 8px; z-index: 3; display: flex; flex-wrap: wrap;
             gap: 6px; max-width: 70%; }
.ge-chip { display: inline-flex; align-items: center; gap: 6px; padding: 3px 9px; border-radius: 999px;
           border: 1px solid var(--ge-line); background: var(--ge-panel); color: var(--ge-fg);
           cursor: pointer; font: inherit; white-space: nowrap; }
.ge-chip .dot { width: 9px; height: 9px; border-radius: 50%; flex: none; background: var(--ge-dot); }
.ge-chip .n { color: var(--ge-muted); font-variant-numeric: tabular-nums; }
.ge-chip.off { opacity: 0.45; }
.ge-chip.off .dot { background: transparent !important; box-shadow: inset 0 0 0 1.5px var(--ge-muted); }
.ge-chip.orphans { border-style: dashed; }
.ge-chip.orphans.on { border-color: var(--ge-fg); box-shadow: inset 0 0 0 1px var(--ge-fg); }
.ge-tip { position: absolute; left: var(--ge-tip-x, 0px); top: var(--ge-tip-y, 0px); z-index: 4;
          pointer-events: none; padding: 3px 8px; border-radius: 6px;
          background: var(--ge-fg); color: var(--ge-bg); font: 12px ui-monospace, SFMono-Regular, Menlo, monospace;
          max-width: 60%; overflow-wrap: anywhere; }
.ge-tip[hidden] { display: none; }
.ge-settings { position: absolute; top: 44px; right: 8px; z-index: 5; width: 250px; padding: 10px 12px;
               border-radius: 8px; border: 1px solid var(--ge-line); background: var(--ge-bg);
               display: grid; gap: 8px; }
.ge-settings[hidden] { display: none; }
.ge-seg { display: inline-flex; border: 1px solid var(--ge-line); border-radius: 6px; overflow: hidden; }
.ge-seg button { background: none; border: 0; color: var(--ge-fg); padding: 3px 9px; font: inherit; cursor: pointer; }
.ge-seg button.on { background: var(--ge-fg); color: var(--ge-bg); }
.ge-row { display: grid; grid-template-columns: 96px 1fr; align-items: center; gap: 6px; }
.ge-row input[type=range] { width: 100%; }
.ge-check { display: flex; gap: 6px; align-items: center; cursor: pointer; }
.ge-note { position: absolute; left: 8px; top: 8px; z-index: 3; color: var(--ge-muted); font-size: 12px; max-width: 55%; }
.ge-note:empty { display: none; }
.ge-phone .ge-legend { position: static; order: -1; flex-wrap: nowrap; overflow-x: auto; max-width: none;
                       padding: 6px 8px; scrollbar-width: none; }
.ge-phone .ge-legend::-webkit-scrollbar { display: none; }
.ge-phone .ge-desktop-only { display: none; }
.ge-phone .ge-toolbar input[type=search] { width: 120px; }
.ge-phone .ge-settings { width: calc(100% - 16px); }
`;
