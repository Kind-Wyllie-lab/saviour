// Shared habitat-grid geometry (gate_map.py layout), so the Gates page map
// and the dashboard's live map put boxes and gates in the same places.

// Box edge in SVG units; SVGs using these scale to their container.
export const BOX = 120;

// Position of gate `index` of `count` along its wall: top to bottom on a
// vertical wall, left to right on a horizontal one (gate_map.py naming).
// `box` maps box id -> {col, row}.
export function gatePoint(wall, box, index, count) {
  const t = (index + 1) / (count + 1);
  const a = box[wall.boxes[0]];
  if (wall.orientation === "vertical") {
    return { x: (a.col + 1) * BOX, y: (a.row + t) * BOX };
  }
  return { x: (a.col + t) * BOX, y: (a.row + 1) * BOX };
}
