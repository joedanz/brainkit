// space -> stable palette color, shared by both products so a space is the
// same color in the graph legend, the overview bar chart, the 2D canvas and
// the three.js scene. Muted, uniform-lightness OKLCH mid-tones (~L 0.71,
// C 0.09, hue-spaced away from the terracotta accent) so no node shouts and
// the set reads on both the light and dark theme. Kept as hex, not CSS vars,
// because three.js Color can't parse oklch().
//
// Lives in the engine tree (not dom.js) so the portal, which loads only
// /assets/js/graph/*, colors spaces identically. dom.js re-exports it.
export const PALETTE = ["#d48b85", "#92b074", "#bcab67", "#78a3cf", "#64b5b0",
                        "#b88cc1", "#adac68", "#a48fcb", "#6db38e", "#d0878c"];
const spaceColors = {};
let nextColor = 0;
export function colorFor(space) {
  if (!(space in spaceColors)) spaceColors[space] = PALETTE[nextColor++ % PALETTE.length];
  return spaceColors[space];
}
