import { useMemo, useRef, useState } from "react";
import "./MiniAreaChart.css";

/**
 * Dependency-free time-series area chart (single series).
 *
 * props:
 *   data:      [{ t: epochSeconds, v: number }]  (sorted by t ascending)
 *   height:    px, default 160
 *   unit:      string appended to value labels, default ""
 *   decimals:  value label precision, default 1
 *   thresholds:[{ v: number, label: string, kind: "warn"|"danger" }]
 *   yMax / yMin: optional fixed axis bounds (else derived from data + thresholds)
 */
export default function MiniAreaChart({
  data = [],
  height = 160,
  unit = "",
  decimals = 1,
  thresholds = [],
  yMax: yMaxProp,
  yMin: yMinProp,
}) {
  const wrapRef = useRef(null);
  const [hoverX, setHoverX] = useState(null);

  const W = 600; // viewBox width; scales to container via CSS
  const H = height;
  const padL = 44;
  const padR = 10;
  const padT = 10;
  const padB = 20;

  const { pts, xMin, xMax, yMin, yMax } = useMemo(() => {
    const clean = data.filter((d) => d && Number.isFinite(d.t) && Number.isFinite(d.v));
    if (clean.length === 0) {
      return { pts: [], xMin: 0, xMax: 1, yMin: 0, yMax: 1 };
    }
    const xs = clean.map((d) => d.t);
    const vs = clean.map((d) => d.v).concat(thresholds.map((t) => t.v));
    let lo = yMinProp ?? Math.min(...vs);
    let hi = yMaxProp ?? Math.max(...vs);
    if (lo === hi) { hi = lo + 1; }
    const pad = (hi - lo) * 0.08;
    return {
      pts: clean,
      xMin: Math.min(...xs),
      xMax: Math.max(...xs),
      yMin: yMinProp ?? lo - pad,
      yMax: yMaxProp ?? hi + pad,
    };
  }, [data, thresholds, yMinProp, yMaxProp]);

  const sx = (t) =>
    padL + ((t - xMin) / (xMax - xMin || 1)) * (W - padL - padR);
  const sy = (v) =>
    padT + (1 - (v - yMin) / (yMax - yMin || 1)) * (H - padT - padB);

  const linePath = pts.map((d, i) => `${i ? "L" : "M"}${sx(d.t)},${sy(d.v)}`).join(" ");
  const areaPath = pts.length
    ? `${linePath} L${sx(pts[pts.length - 1].t)},${H - padB} L${sx(pts[0].t)},${H - padB} Z`
    : "";

  const yTicks = useMemo(() => {
    const n = 4;
    return Array.from({ length: n + 1 }, (_, i) => yMin + ((yMax - yMin) * i) / n);
  }, [yMin, yMax]);

  const fmtV = (v) => `${v.toFixed(decimals)}${unit}`;
  const fmtT = (t) => {
    const d = new Date(t * 1000);
    return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  };

  const nearest = useMemo(() => {
    if (hoverX == null || pts.length === 0) return null;
    const tGuess = xMin + (hoverX / W) * (xMax - xMin);
    let best = pts[0];
    for (const d of pts) {
      if (Math.abs(d.t - tGuess) < Math.abs(best.t - tGuess)) best = d;
    }
    return best;
  }, [hoverX, pts, xMin, xMax]);

  function onMove(e) {
    const rect = wrapRef.current.getBoundingClientRect();
    setHoverX(((e.clientX - rect.left) / rect.width) * W);
  }

  return (
    <div className="mini-area-chart" ref={wrapRef}>
      {pts.length === 0 ? (
        <div className="mini-area-chart__empty">No history yet</div>
      ) : (
        <svg
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="none"
          onMouseMove={onMove}
          onMouseLeave={() => setHoverX(null)}
        >
          {yTicks.map((v, i) => (
            <g key={i}>
              <line
                className="mac-grid"
                x1={padL} x2={W - padR} y1={sy(v)} y2={sy(v)}
              />
              <text className="mac-ylabel" x={padL - 6} y={sy(v) + 3} textAnchor="end">
                {v.toFixed(decimals)}
              </text>
            </g>
          ))}

          {thresholds.map((th, i) => (
            <g key={`th-${i}`}>
              <line
                className={`mac-threshold mac-threshold--${th.kind || "warn"}`}
                x1={padL} x2={W - padR} y1={sy(th.v)} y2={sy(th.v)}
              />
              <text
                className={`mac-threshold-label mac-threshold-label--${th.kind || "warn"}`}
                x={W - padR} y={sy(th.v) - 3} textAnchor="end"
              >
                {th.label}
              </text>
            </g>
          ))}

          <path className="mac-area" d={areaPath} />
          <path className="mac-line" d={linePath} />

          {nearest && (
            <g>
              <line
                className="mac-cursor"
                x1={sx(nearest.t)} x2={sx(nearest.t)} y1={padT} y2={H - padB}
              />
              <circle className="mac-dot" cx={sx(nearest.t)} cy={sy(nearest.v)} r="3" />
            </g>
          )}
        </svg>
      )}

      {nearest && (
        <div className="mini-area-chart__tip">
          <strong>{fmtV(nearest.v)}</strong>
          <span>{fmtT(nearest.t)}</span>
        </div>
      )}
    </div>
  );
}
