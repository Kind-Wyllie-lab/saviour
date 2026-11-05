import "./HabitatGrid.css";

// Box edge in SVG units; the SVG scales to its container.
const BOX = 120;
const PAD = 14;
const GATE_R = 7;

// Gate colour class from its layout + map state.
function gateTone(gate, entry) {
  if (!gate.fitted) return "unfitted";
  if (!entry) return "unmapped";
  return entry.status;              // mapped | closed | not_fitted | faulty
}

// Position of gate `index` of `count` along its wall: top to bottom on a
// vertical wall, left to right on a horizontal one (gate_map.py naming).
function gatePoint(wall, box, index, count) {
  const t = (index + 1) / (count + 1);
  const [a] = wall.boxes.map((id) => box[id]);
  if (wall.orientation === "vertical") {
    return { x: (a.col + 1) * BOX, y: (a.row + t) * BOX };
  }
  return { x: (a.col + t) * BOX, y: (a.row + 1) * BOX };
}

/**
 * The habitat grid seen from above: boxes, walls and each wall's gates,
 * coloured by mapping status. Used by every Gates page mode, and meant as
 * the base of a later layout-aware habitat view (plans/rfid-gate-mapping.md).
 *
 * Props:
 *   layout     gate_map.py layout (boxes / walls / gates)
 *   entries    gate id -> map entry (status, module_id, unit, ...)
 *   current    gate id to highlight as "do this one now"
 *   flash      Set of gate ids to flash (a live read just landed there)
 *   selected   Set of gate ids shown as selected (scope picking)
 *   onGateClick(gateId)  makes gates clickable
 *   labelFor(gate, entry) optional tooltip text
 */
function HabitatGrid({ layout, entries = {}, current, flash, selected, onGateClick, labelFor }) {
  if (!layout) return null;
  const box = Object.fromEntries(layout.boxes.map((b) => [b.id, b]));
  const walls = Object.fromEntries(layout.walls.map((w) => [w.id, w]));
  const perWall = layout.gates_per_wall || 4;
  const w = layout.cols * BOX;
  const h = layout.rows * BOX;

  return (
    <svg
      className="habitat-map"
      viewBox={`${-PAD} ${-PAD} ${w + 2 * PAD} ${h + 2 * PAD}`}
      role="img"
      aria-label="Habitat grid"
    >
      {layout.boxes.map((b) => (
        <g key={b.id}>
          <rect
            className="habitat-map-box"
            x={b.col * BOX} y={b.row * BOX} width={BOX} height={BOX}
          />
          <text className="habitat-map-box-label" x={b.col * BOX + BOX / 2} y={b.row * BOX + BOX / 2}>
            {b.id}
          </text>
        </g>
      ))}
      {layout.gates.map((g) => {
        const wall = walls[g.wall];
        const p = gatePoint(wall, box, g.index, perWall);
        const entry = entries[g.id];
        const tone = gateTone(g, entry);
        const classes = [
          "habitat-map-gate",
          `habitat-map-gate--${tone}`,
          g.id === current && "habitat-map-gate--current",
          flash?.has(g.id) && "habitat-map-gate--flash",
          selected?.has(g.id) && "habitat-map-gate--selected",
          onGateClick && "habitat-map-gate--clickable",
        ].filter(Boolean).join(" ");
        const title = labelFor ? labelFor(g, entry) : g.id;
        return (
          <g key={g.id} className={classes}
             onClick={onGateClick ? () => onGateClick(g.id) : undefined}>
            {g.id === current && (
              <circle className="habitat-map-gate-ring" cx={p.x} cy={p.y} r={GATE_R + 7} />
            )}
            <circle className="habitat-map-gate-dot" cx={p.x} cy={p.y} r={GATE_R} />
            <title>{title}</title>
          </g>
        );
      })}
    </svg>
  );
}

export default HabitatGrid;
