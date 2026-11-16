import { useEffect, useMemo, useRef, useState } from "react";
import { fmtTime } from "../../utils/sessionProtocol";
import "./ProtocolTimeline.css";

// A Gantt-style session timeline: one row per output (rows), bars where it
// is high (spans), time from session start on x (mm:ss). Used for the
// protocol preview now; the live and after-session views (planned vs
// actual, plans/session-protocol.md) reuse it with a second span set.
//
// rows:  [{ key, label, sublabel? }]
// spans: [{ row, start, end (null = held to the end), label }]
// duration: seconds the axis must cover
//
// Drag across the plot to zoom, double-click or "Reset zoom" to go back.
// Bars closer than a pixel at the current zoom merge into one (its tooltip
// says how many), so a long fast train stays drawable and readable.

const LABEL_W = 92;
const ROW_H = 30;
const BAR_H = 12;
const AXIS_H = 26;
const PAD_R = 12;
const TICK_STEPS = [0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5,
  10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 14400];

function niceStep(span, target) {
  const raw = span / Math.max(1, target);
  return TICK_STEPS.find((s) => s >= raw) ?? TICK_STEPS[TICK_STEPS.length - 1];
}

function describe(bar) {
  const dur = (e, s) => {
    const d = e - s;
    return d < 1 ? `${(d * 1000).toFixed(d < 0.01 ? 1 : 0)} ms` : `${d.toFixed(3)} s`;
  };
  if (bar.count > 1) {
    return `${bar.count} pulses, ${fmtTime(bar.start, true)} → ${fmtTime(bar.end, true)}`
      + ` (zoom in to separate)`;
  }
  const end = bar.held ? "end of protocol (held)" : fmtTime(bar.end, true);
  const len = bar.held ? "" : ` · ${dur(bar.end, bar.start)}`;
  return `${bar.label} · ${fmtTime(bar.start, true)} → ${end}${len}`;
}

export default function ProtocolTimeline({ rows, spans, duration, emptyText }) {
  const wrapRef = useRef(null);
  const [width, setWidth] = useState(600);
  const fullEnd = Math.max(duration * 1.02, duration + 0.05, 1);
  const [view, setView] = useState(null); // null = whole protocol
  const [drag, setDrag] = useState(null);
  const [hover, setHover] = useState(null);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return undefined;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.max(280, entry.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // A changed protocol can end before the zoomed window: fall back to it all.
  const [v0, v1] = view && view[0] < fullEnd ? view : [0, fullEnd];
  const plotW = width - LABEL_W - PAD_R;
  const height = AXIS_H + rows.length * ROW_H + 6;
  const xOf = (t) => LABEL_W + ((t - v0) / (v1 - v0)) * plotW;
  const tOf = (x) => v0 + ((x - LABEL_W) / plotW) * (v1 - v0);

  const rowIndex = useMemo(
    () => Object.fromEntries(rows.map((r, i) => [r.key, i])), [rows]);

  // Bars in screen space for the current view, merged below ~1 px apart.
  const bars = useMemo(() => {
    const out = [];
    const last = {};
    for (const s of spans) {
      const ri = rowIndex[s.row];
      if (ri === undefined) continue;
      const held = s.end === null || s.end === undefined;
      const end = held ? v1 : s.end;
      if (end < v0 || s.start > v1) continue;
      const x0 = xOf(Math.max(s.start, v0));
      const x1 = Math.max(xOf(Math.min(end, v1)), x0 + 2);
      const prev = last[ri];
      if (prev && !held && !prev.held && x0 <= prev.x1 + 1) {
        prev.x1 = Math.max(prev.x1, x1);
        prev.end = end;
        prev.count += 1;
        continue;
      }
      const bar = { ri, x0, x1, start: s.start, end, held, count: 1, label: s.label };
      out.push(bar);
      last[ri] = bar;
    }
    return out;
    // xOf depends only on v0/v1/plotW, listed instead of the closure.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spans, rowIndex, v0, v1, plotW]);

  const step = niceStep(v1 - v0, Math.max(3, Math.floor(plotW / 90)));
  const ticks = [];
  for (let t = Math.ceil(v0 / step) * step; t <= v1 + 1e-9; t += step) ticks.push(t);
  const withMs = step < 1;

  const svgPoint = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  };

  const onMove = (e) => {
    const { x, y } = svgPoint(e);
    if (drag) {
      setDrag({ ...drag, x1: Math.min(Math.max(x, LABEL_W), LABEL_W + plotW) });
      return;
    }
    const ri = Math.floor((y - AXIS_H) / ROW_H);
    if (ri < 0 || ri >= rows.length || x < LABEL_W) { setHover(null); return; }
    // Hit target wider than the mark: ±4 px around each bar.
    let best = null;
    for (const b of bars) {
      if (b.ri !== ri || x < b.x0 - 4 || x > b.x1 + 4) continue;
      const d = x < b.x0 ? b.x0 - x : x > b.x1 ? x - b.x1 : 0;
      if (!best || d < best.d) best = { b, d };
    }
    setHover(best ? { x, y: AXIS_H + ri * ROW_H, text: `${rows[ri].label} · ${describe(best.b)}` }
                  : { x, y: AXIS_H + ri * ROW_H, text: `${rows[ri].label} · ${fmtTime(tOf(x), withMs)}`,
                      quiet: true });
  };

  const onDown = (e) => {
    const { x } = svgPoint(e);
    if (x < LABEL_W) return;
    setDrag({ x0: x, x1: x });
  };

  const onUp = () => {
    if (drag && Math.abs(drag.x1 - drag.x0) > 4) {
      const a = tOf(Math.min(drag.x0, drag.x1));
      const b = tOf(Math.max(drag.x0, drag.x1));
      if (b - a > 0.001) setView([Math.max(0, a), b]);
    }
    setDrag(null);
  };

  if (!rows.length) {
    return <div className="ptl-empty">{emptyText ?? "Nothing to show."}</div>;
  }

  const zoomed = !!view && view[0] < fullEnd;
  return (
    <div className="ptl" ref={wrapRef}>
      <div className="ptl-toolbar">
        <span className="ptl-range">
          {zoomed ? `${fmtTime(v0, withMs)} – ${fmtTime(v1, withMs)}` : `Whole protocol · ${fmtTime(duration, duration < 60)}`}
        </span>
        <span className="ptl-hint">Drag across the chart to zoom</span>
        {zoomed && (
          <button type="button" className="ptl-reset" onClick={() => setView(null)}>Reset zoom</button>
        )}
      </div>
      <div className="ptl-plot">
        <svg
          width={width}
          height={height}
          role="img"
          aria-label={`Timeline of ${rows.length} output(s) over ${fmtTime(duration)}`}
          onMouseMove={onMove}
          onMouseLeave={() => { setHover(null); setDrag(null); }}
          onMouseDown={onDown}
          onMouseUp={onUp}
          onDoubleClick={() => setView(null)}
        >
          {/* grid + axis */}
          {ticks.map((t) => (
            <g key={t}>
              <line className="ptl-grid" x1={xOf(t)} x2={xOf(t)} y1={AXIS_H - 4} y2={height - 6} />
              <text className="ptl-tick" x={xOf(t)} y={AXIS_H - 9} textAnchor="middle">
                {fmtTime(t, withMs)}
              </text>
            </g>
          ))}
          {/* rows */}
          {rows.map((r, i) => {
            const y = AXIS_H + i * ROW_H;
            return (
              <g key={r.key}>
                <line className="ptl-baseline" x1={LABEL_W} x2={LABEL_W + plotW}
                  y1={y + ROW_H / 2 + BAR_H / 2} y2={y + ROW_H / 2 + BAR_H / 2} />
                <text className="ptl-label" x={8} y={y + ROW_H / 2 + 4}>{r.label}</text>
                {r.sublabel && (
                  <title>{r.sublabel}</title>
                )}
              </g>
            );
          })}
          {/* bars */}
          {bars.map((b, i) => (
            <rect
              key={i}
              className={`ptl-bar${b.held ? " ptl-bar--held" : ""}${b.count > 1 ? " ptl-bar--dense" : ""}`}
              x={b.x0}
              y={AXIS_H + b.ri * ROW_H + (ROW_H - BAR_H) / 2}
              width={Math.max(2, b.x1 - b.x0)}
              height={BAR_H}
              rx={Math.min(2, (b.x1 - b.x0) / 2)}
            />
          ))}
          {/* hover crosshair */}
          {hover && !drag && (
            <line className="ptl-cursor" x1={hover.x} x2={hover.x} y1={AXIS_H - 4} y2={height - 6} />
          )}
          {drag && (
            <rect className="ptl-brush" x={Math.min(drag.x0, drag.x1)} y={AXIS_H - 4}
              width={Math.abs(drag.x1 - drag.x0)} height={height - AXIS_H - 2} />
          )}
        </svg>
        {hover && !drag && (
          <div
            className={`ptl-tooltip${hover.quiet ? " ptl-tooltip--quiet" : ""}`}
            style={{
              left: Math.min(hover.x + 12, Math.max(0, width - 280)),
              top: hover.y + ROW_H,
            }}
          >
            {hover.text}
          </div>
        )}
      </div>
    </div>
  );
}
