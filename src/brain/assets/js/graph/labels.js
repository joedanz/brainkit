// Which node names get drawn. Three stops the person picks ("Labels" in the
// settings popover): hubs — the 12 busiest; more — about 25; all — every node
// the spacing rule admits. The spacing rule is on-screen SPACING, not zoom
// alone: a name needs k * medianGap of room, where k is the zoom and the gap
// is the typical distance between neighbours in layout units. Below that the
// names would sit on each other. The busiest HUBS are always named — they are
// the landmarks a person steers by, and they are worth an overlap.
//
// Pure, so both the 2D canvas and the 3D sprites ask the same question and
// node can test it.
export const HUBS = 12;
export const MORE = 25;
export const LABEL_ROOM_PX = 44;   // spacing between node centres, chosen by eye; not a text width
export const STOPS = ["hubs", "more", "all"];

export function labelBudget(stop, n) {
  if (stop === "all") return n;
  if (stop === "more") return Math.min(MORE, n);
  return Math.min(HUBS, n);
}

export function labelShown(rank, k, gap, budget) {
  if (rank >= budget) return false;
  if (rank < HUBS) return true;
  return k * gap >= LABEL_ROOM_PX;
}
