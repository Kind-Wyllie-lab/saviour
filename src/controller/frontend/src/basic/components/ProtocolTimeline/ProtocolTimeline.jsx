import { useEffect, useMemo, useRef, useState } from "react";
import { fmtTime } from "../../utils/sessionProtocol";
import "./ProtocolTimeline.css";

// A Gantt-style session timeline: one row per output (rows), bars where it
// is high (spans), time from session start on x (mm:ss). Used for the
// protocol preview now; the live and after-session views (planned vs
// actual, plans/session-protocol.md) reuse it with a second span set.
//
// rows:  [{ key, label, sublabel? }] (sublabel: a smaller second line)
// spans: [{ row, start, end (null = held to the end), label }]
// duration: seconds the axis must cover
// minDuration: the axis covers at least this much (an empty protocol still
//   gets a grid); alwaysShow: with no rows, draw one placeholder row
//   (emptyText) instead of only the text; minRows: pad with blank rows to
//   this many, so the chart keeps its height before outputs exist.
// Plot on black, time axis along the bottom.
//
// Zoom: drag across the plot, the − / + buttons, or Ctrl+scroll at the
// pointer (out to 6 h or the whole protocol); Shift+scroll pans. Double-click
// or "Reset zoom" goes back to the default view.
// Bars closer than a pixel at the current zoom merge into one (its tooltip
// says how many), so a long fast train stays drawable and readable.
//
// bands: [{ key, start, end, group, label, title }] -- coloured marks in a
//   strip above the rows (one per trial of a trial block; group picks the
//   colour, label is drawn inside when it fits).
// endT: a dashed "end" line (the protocol's declared duration), or null.
// Editing (the designer): onCreate(rowKey, start, end|null) when a line is
//   clicked (end null) or dragged across, times snapped to the grid (hold
//   Alt for none); onPick(span) when a bar with a `step` is clicked;
//   canCreate(row) says which rows take new steps. Spans with `selected`
//   are drawn highlighted. With onCreate, dragging on the rows creates, so
//   zoom-by-drag moves to the time axis (and the band strip).

const LABEL_W = 112;
const LABEL_CHARS = 15;
const clip = (t) => (t.length > LABEL_CHARS ? `${t.slice(0, LABEL_CHARS - 1)}…` : t);
const ROW_H = 30;
const BAR_H = 12;
const AXIS_H = 24;   // below the rows
const TOP = 6;       // above the first row
const BAND_H = 16;   // the trial strip, when there are bands
const BAND_COLOURS = 6;
const PAD_R = 12;
const MIN_SPAN_S = 0.002;
const MAX_SPAN_S = 6 * 3600;
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
  if (bar.ref) return bar.held ? bar.label : `${bar.label} · ${fmtTime(bar.start, true)}`;
  const end = bar.held ? "end of protocol (held)" : fmtTime(bar.end, true);
  const len = bar.held ? "" : ` · ${dur(bar.end, bar.start)}`;
  return `${bar.label} · ${fmtTime(bar.start, true)} → ${end}${len}`;
}

// highlightRow: a row key to emphasise (the others fade), or null.
// cursorT: a "now" line at this many seconds (a running test), or null.
export default function ProtocolTimeline({ rows: rowsIn, spans, duration, emptyText, highlightRow = null,
  cursorT = null, minDuration = 0, alwaysShow = false, minRows = 0, bands = [], endT = null,
  onCreate = null, onPick = null, canCreate = null }) {
  const placeholder = alwaysShow && !rowsIn.length;
  const rows = useMemo(() => {
    const out = placeholder ? [{ key: "__none", label: "No outputs" }] : [...rowsIn];
    for (let i = out.length; i < minRows; i += 1) out.push({ key: `__pad${i}`, label: "", pad: true });
    return out;
  }, [placeholder, rowsIn, minRows]);
  const wrapRef = useRef(null);
  const [width, setWidth] = useState(600);
  const fullEnd = Math.max(duration * 1.02, duration + 0.05, 1, minDuration);
  const [view, setView] = useState(null); // null = whole protocol
  const plotRef = useRef(null);
  const [drag, setDrag] = useState(null);
  const [hover, setHover] = useState(null);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return undefined;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.max(280, entry.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const maxSpan = Math.max(fullEnd, MAX_SPAN_S);
  const [v0, v1] = view ?? [0, fullEnd];
  const plotW = width - LABEL_W - PAD_R;
  const hasBands = bands.length > 0;
  const rowsTop = TOP + (hasBands ? BAND_H : 0);
  const plotBottom = rowsTop + rows.length * ROW_H;
  const editable = !!onCreate;
  // Grid snap for new steps: about 60 snaps across the view.
  const snapStep = niceStep(v1 - v0, 60);
  const snapT = (t, free) => Math.max(0, free ? Math.round(t * 1000) / 1000
    : Math.round(t / snapStep) * snapStep);
  const height = plotBottom + AXIS_H;
  const xOf = (t) => LABEL_W + ((t - v0) / (v1 - v0)) * plotW;
  const tOf = (x) => v0 + ((x - LABEL_W) / plotW) * (v1 - v0);

  // Scale the window by factor about time c, kept within [0, ...).
  const zoomAbout = (factor, c, [a, b] = [v0, v1]) => {
    const span = Math.min(Math.max((b - a) * factor, MIN_SPAN_S), maxSpan);
    let n0 = c - (c - a) * (span / (b - a));
    if (n0 < 0) n0 = 0;
    setView([n0, n0 + span]);
  };
  const panBy = (dt, [a, b] = [v0, v1]) => {
    const n0 = Math.max(0, a + dt);
    setView([n0, n0 + (b - a)]);
  };

  // Wheel needs a non-passive listener to stop the page scrolling; it reads
  // the latest window through a ref.
  const live = useRef(null);
  live.current = { v0, v1, plotW, zoomAbout, panBy };
  useEffect(() => {
    const el = plotRef.current;
    if (!el) return undefined;
    const onWheel = (e) => {
      const { v0: a, v1: b, plotW: w, zoomAbout: z, panBy: p } = live.current;
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        const x = e.clientX - el.getBoundingClientRect().left;
        const c = a + (Math.min(Math.max(x - LABEL_W, 0), w) / w) * (b - a);
        z(e.deltaY > 0 ? 1.25 : 0.8, c, [a, b]);
      } else if (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
        e.preventDefault();
        const d = e.shiftKey && !e.deltaX ? e.deltaY : e.deltaX;
        p((d / w) * (b - a), [a, b]);
      }
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [rows.length]);

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
      if (prev && !held && !prev.held && !s.ref && !s.selected && !prev.selected
          && x0 <= prev.x1 + 1) {
        prev.x1 = Math.max(prev.x1, x1);
        prev.end = end;
        prev.count += 1;
        continue;
      }
      const bar = { ri, x0, x1, start: s.start, end, held, count: 1, label: s.label, ref: !!s.ref,
        step: s.step, selected: !!s.selected, span: s };
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
  // Unlabelled grid lines on every minute between the labelled ticks, while
  // they stay at least 6 px apart.
  const minuteLines = [];
  if (step > 60 && (60 / (v1 - v0)) * plotW >= 6) {
    for (let t = Math.ceil(v0 / 60) * 60; t <= v1 + 1e-9; t += 60) {
      if (Math.abs(t / step - Math.round(t / step)) > 1e-9) minuteLines.push(t);
    }
  }

  const svgPoint = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  };

  const rowAt = (y) => {
    const ri = Math.floor((y - rowsTop) / ROW_H);
    return ri >= 0 && ri < rows.length && y >= rowsTop ? ri : -1;
  };
  const usable = (r) => r && !r.pad && r.key !== "__none";
  const creatable = (r) => usable(r) && !r.ref && (canCreate ? canCreate(r) : true);
  const barAt = (ri, x) => {
    // Hit target wider than the mark: ±4 px around each bar.
    let best = null;
    for (const b of bars) {
      if (b.ri !== ri || x < b.x0 - 4 || x > b.x1 + 4) continue;
      const d = x < b.x0 ? b.x0 - x : x > b.x1 ? x - b.x1 : 0;
      if (!best || d < best.d) best = { b, d };
    }
    return best?.b ?? null;
  };
  const clampX = (x) => Math.min(Math.max(x, LABEL_W), LABEL_W + plotW);

  const onMove = (e) => {
    const { x, y } = svgPoint(e);
    if (drag) {
      setDrag({ ...drag, x1: clampX(x), free: e.altKey });
      return;
    }
    if (hasBands && y >= TOP && y < rowsTop && x >= LABEL_W) {
      const band = bands.find((bd) => x >= xOf(bd.start) - 2 && x <= xOf(bd.end ?? bd.start) + 2);
      setHover(band ? { x, y: TOP - ROW_H + BAND_H, text: band.title ?? band.label } : null);
      return;
    }
    const ri = rowAt(y);
    if (ri < 0 || x < LABEL_W || !usable(rows[ri])) { setHover(null); return; }
    const b = barAt(ri, x);
    const y0 = rowsTop + ri * ROW_H;
    const at = editable ? snapT(tOf(x), e.altKey) : tOf(x);
    setHover(b ? { x, y: y0, text: `${rows[ri].label} · ${describe(b)}`, onBar: !!(onPick && b.step !== undefined && !b.ref) }
               : { x: editable && creatable(rows[ri]) ? xOf(at) : x, y: y0, quiet: true,
                   text: `${rows[ri].label} · ${fmtTime(at, withMs || snapStep < 1)}`
                     + (editable && creatable(rows[ri]) ? " · click or drag to add a step" : "") });
  };

  const onDown = (e) => {
    const { x, y } = svgPoint(e);
    if (x < LABEL_W) return;
    const ri = rowAt(y);
    if (editable && ri >= 0 && usable(rows[ri])) {
      const b = barAt(ri, x);
      if (b) {
        if (onPick && b.step !== undefined && !b.ref) setDrag({ kind: "pick", bar: b, x0: x, x1: x });
        return;
      }
      if (creatable(rows[ri])) setDrag({ kind: "create", ri, x0: x, x1: x, free: e.altKey });
      return;
    }
    setDrag({ kind: "zoom", x0: x, x1: x });
  };

  const onUp = () => {
    if (!drag) return;
    const moved = Math.abs(drag.x1 - drag.x0) > 4;
    if (drag.kind === "zoom" && moved) {
      const a = tOf(Math.min(drag.x0, drag.x1));
      const b = tOf(Math.max(drag.x0, drag.x1));
      if (b - a > 0.001) setView([Math.max(0, a), b]);
    } else if (drag.kind === "pick" && !moved) {
      onPick(drag.bar.span);
    } else if (drag.kind === "create") {
      const key = rows[drag.ri].key;
      if (!moved) onCreate(key, snapT(tOf(drag.x0), drag.free), null);
      else {
        const a = snapT(tOf(Math.min(drag.x0, drag.x1)), drag.free);
        const b = snapT(tOf(Math.max(drag.x0, drag.x1)), drag.free);
        onCreate(key, a, b > a ? b : null);
      }
    }
    setDrag(null);
  };

  if (!rows.length) {
    return <div className="ptl-empty">{emptyText ?? "Nothing to show."}</div>;
  }

  const zoomed = !!view;
  const mid = (v0 + v1) / 2;
  return (
    <div className="ptl" ref={wrapRef}>
      <div className="ptl-toolbar">
        <span className="ptl-range">
          {zoomed ? `${fmtTime(v0, withMs)} – ${fmtTime(v1, withMs)}`
            : duration > 0 ? `Whole protocol · ${fmtTime(duration, duration < 60)}`
              : `No steps yet · showing ${fmtTime(fullEnd)}`}
        </span>
        <span className="ptl-hint">
          {editable
            ? `Click or drag on a line to add a step (snaps to ${fmtTime(snapStep, snapStep < 1)}, Alt for none) · click a step to edit · drag the time axis to zoom · Ctrl+scroll to zoom`
            : "Drag to zoom in · Ctrl+scroll to zoom · Shift+scroll to pan"}
        </span>
        <button type="button" className="ptl-reset" title="Zoom out" aria-label="Zoom out"
          disabled={v1 - v0 >= maxSpan - 1e-9} onClick={() => zoomAbout(2, zoomed ? mid : 0)}>−</button>
        <button type="button" className="ptl-reset" title="Zoom in" aria-label="Zoom in"
          disabled={v1 - v0 <= MIN_SPAN_S + 1e-12} onClick={() => zoomAbout(0.5, zoomed ? mid : 0)}>+</button>
        {zoomed && (
          <button type="button" className="ptl-reset" onClick={() => setView(null)}>Reset zoom</button>
        )}
      </div>
      <div className={`ptl-plot${editable ? " ptl-plot--editable" : ""}${hover?.onBar ? " ptl-plot--onbar" : ""}`} ref={plotRef}>
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
          {minuteLines.map((t) => (
            <line key={`m${t}`} className="ptl-grid ptl-grid--minor" x1={xOf(t)} x2={xOf(t)} y1={rowsTop} y2={plotBottom} />
          ))}
          {ticks.map((t) => (
            <g key={t}>
              <line className="ptl-grid" x1={xOf(t)} x2={xOf(t)} y1={rowsTop} y2={plotBottom + 4} />
              <text className="ptl-tick" x={xOf(t)} y={plotBottom + 17} textAnchor="middle">
                {fmtTime(t, withMs)}
              </text>
            </g>
          ))}
          <line className="ptl-axis" x1={LABEL_W} x2={LABEL_W + plotW} y1={plotBottom} y2={plotBottom} />
          {/* rows */}
          {rows.map((r, i) => {
            const y = rowsTop + i * ROW_H;
            return (
              <g key={r.key}
                className={[r.ref ? "ptl-row--ref" : "",
                  highlightRow === null ? "" : r.key === highlightRow ? "ptl-row--focus" : "ptl-row--faded"]
                  .filter(Boolean).join(" ") || undefined}>
                <line className="ptl-baseline" x1={LABEL_W} x2={LABEL_W + plotW}
                  y1={y + ROW_H / 2 + BAR_H / 2} y2={y + ROW_H / 2 + BAR_H / 2} />
                <title>{r.sublabel ? `${r.label} · ${r.sublabel}` : r.label}</title>
                {r.sublabel ? (
                  <>
                    <text className="ptl-label" x={8} y={y + ROW_H / 2 - 1}>{clip(r.label)}</text>
                    <text className="ptl-sublabel" x={8} y={y + ROW_H / 2 + 10}>{r.sublabel}</text>
                  </>
                ) : (
                  <text className="ptl-label" x={8} y={y + ROW_H / 2 + 4}>{clip(r.label)}</text>
                )}
              </g>
            );
          })}
          {placeholder && (
            <text className="ptl-sublabel" x={LABEL_W + 8} y={rowsTop + ROW_H / 2 + 4}>{emptyText}</text>
          )}
          {/* bars */}
          {bars.map((b, i) => (
            <rect
              key={i}
              className={`ptl-bar${b.ref ? " ptl-bar--ref" : ""}${b.held ? " ptl-bar--held" : ""}${b.count > 1 ? " ptl-bar--dense" : ""}${b.selected ? " ptl-bar--selected" : ""}${
                highlightRow !== null && rows[b.ri].key !== highlightRow ? " ptl-bar--faded" : ""}`}
              x={b.x0}
              y={rowsTop + b.ri * ROW_H + (ROW_H - BAR_H) / 2}
              width={Math.max(2, b.x1 - b.x0)}
              height={BAR_H}
              rx={Math.min(2, (b.x1 - b.x0) / 2)}
            />
          ))}
          {/* trial strip */}
          {hasBands && <text className="ptl-sublabel" x={8} y={TOP + BAND_H / 2 + 3}>Trials</text>}
          {bands.map((bd) => {
            const end = bd.end ?? bd.start;
            if (end < v0 || bd.start > v1) return null;
            const x0 = xOf(Math.max(bd.start, v0));
            const w = Math.max(3, xOf(Math.min(end, v1)) - x0);
            return (
              <g key={bd.key}>
                <rect className={`ptl-band ptl-band--c${(bd.group ?? 0) % BAND_COLOURS}`}
                  x={x0} y={TOP + 2} width={w} height={BAND_H - 4} rx={2} />
                {bd.label && w >= 7 * String(bd.label).length + 4 && (
                  <text className="ptl-band-label" x={x0 + w / 2} y={TOP + BAND_H / 2 + 3} textAnchor="middle">
                    {bd.label}
                  </text>
                )}
              </g>
            );
          })}
          {endT !== null && endT >= v0 && endT <= v1 && (
            <g>
              <line className="ptl-end" x1={xOf(endT)} x2={xOf(endT)} y1={TOP} y2={plotBottom} />
              <text className="ptl-end-label" x={xOf(endT) - 4} y={plotBottom - 4} textAnchor="end">end</text>
            </g>
          )}
          {drag?.kind === "create" && (() => {
            const a = xOf(snapT(tOf(Math.min(drag.x0, drag.x1)), drag.free));
            const b = xOf(snapT(tOf(Math.max(drag.x0, drag.x1)), drag.free));
            return (
              <rect className="ptl-create" x={a} width={Math.max(2, b - a)}
                y={rowsTop + drag.ri * ROW_H + (ROW_H - BAR_H) / 2} height={BAR_H} />
            );
          })()}
          {cursorT !== null && cursorT >= v0 && cursorT <= v1 && (
            <line className="ptl-now" x1={xOf(cursorT)} x2={xOf(cursorT)} y1={TOP} y2={plotBottom} />
          )}
          {/* hover crosshair */}
          {hover && !drag && (
            <line className="ptl-cursor" x1={hover.x} x2={hover.x} y1={TOP} y2={plotBottom} />
          )}
          {drag?.kind === "zoom" && (
            <rect className="ptl-brush" x={Math.min(drag.x0, drag.x1)} y={TOP}
              width={Math.abs(drag.x1 - drag.x0)} height={plotBottom - TOP} />
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
