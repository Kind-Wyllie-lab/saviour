import React, { useEffect, useMemo, useRef, useState } from "react";

// Headstage accelerometer summary: three stacked charts on one time axis
// (acceleration per axis, head tilt, movement intensity), drawn from the
// reduced plot.json that src/controller/ephys_accel.py writes (min / mean /
// max per bucket). Hand-rolled SVG: the frontend has no charting library.

const PANELS = [
  {
    key: "acc", title: "Acceleration", unit: "g", height: 150,
    series: [
      { key: "ax_g", label: "X", color: "var(--viz-1)" },
      { key: "ay_g", label: "Y", color: "var(--viz-2)" },
      { key: "az_g", label: "Z", color: "var(--viz-3)" },
    ],
  },
  {
    key: "tilt", title: "Head tilt (from gravity)", unit: "°", height: 120,
    series: [
      { key: "pitch_deg", label: "Pitch", color: "var(--viz-4)" },
      { key: "roll_deg", label: "Roll", color: "var(--viz-5)" },
    ],
  },
  {
    key: "odba", title: "Movement intensity (ODBA, 0.5 s mean)", unit: "g", height: 90,
    zeroBase: true,
    series: [{ key: "odba_g", label: "ODBA", color: "var(--viz-6)" }],
  },
];

const M = { left: 52, right: 14, top: 8, bottom: 6 };
const AXIS_H = 26;

function niceTicks(lo, hi, count = 4) {
  if (!(hi > lo)) return [lo];
  const raw = (hi - lo) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) {
    out.push(Math.abs(v) < step * 1e-6 ? 0 : v);
  }
  return out;
}

function fmtNum(v, unit) {
  if (v == null || Number.isNaN(v)) return "–";
  const d = unit === "°" ? 1 : 2;
  return `${v.toFixed(d)}${unit === "°" ? "°" : ` ${unit}`}`;
}

function fmtClock(ns) {
  return new Date(ns / 1e6).toLocaleTimeString([], {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
}

function fmtSeconds(s) {
  const whole = Math.round(s);
  if (whole < 120) return `${whole} s`;
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

function useWidth(ref) {
  const [w, setW] = useState(800);
  useEffect(() => {
    if (!ref.current) return undefined;
    const ro = new ResizeObserver(([e]) => setW(Math.max(320, e.contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, [ref]);
  return w;
}

export default function AccelChart({ plot }) {
  const wrapRef = useRef(null);
  const width = useWidth(wrapRef);
  const [hover, setHover] = useState(null); // bucket index | null
  const n = plot?.t_s?.length || 0;
  const aligned = Array.isArray(plot?.t_ns);
  const innerW = width - M.left - M.right;

  const xOf = (i) => M.left + (n > 1 ? (i / (n - 1)) * innerW : 0);

  const layout = useMemo(() => {
    let y = 0;
    return PANELS.map((p) => {
      const lows = p.series.flatMap((s) => plot.series[s.key]?.min || []);
      const highs = p.series.flatMap((s) => plot.series[s.key]?.max || []);
      let lo = p.zeroBase ? 0 : Math.min(...lows);
      let hi = Math.max(...highs);
      if (!(hi > lo)) { lo -= 1; hi += 1; }
      const pad = (hi - lo) * 0.06;
      const domain = [p.zeroBase ? 0 : lo - pad, hi + pad];
      const top = y + M.top;
      const h = p.height;
      y += h + M.top + M.bottom + 22;
      const yOf = (v) => top + h - ((v - domain[0]) / (domain[1] - domain[0])) * h;
      return { ...p, top, h, domain, yOf, ticks: niceTicks(domain[0], domain[1]) };
    });
  }, [plot]);

  const totalH = layout.length
    ? layout[layout.length - 1].top + layout[layout.length - 1].h + M.bottom + AXIS_H
    : 0;

  const xTicks = useMemo(() => {
    if (!n) return [];
    const count = Math.max(2, Math.floor(innerW / 110));
    return Array.from({ length: count + 1 }, (_, k) => Math.round((k / count) * (n - 1)));
  }, [n, innerW]);

  const pathFor = (s, yOf) => {
    const d = plot.series[s.key];
    if (!d) return { band: "", line: "" };
    let band = "";
    for (let i = 0; i < n; i++) band += `${i ? "L" : "M"}${xOf(i).toFixed(1)},${yOf(d.max[i]).toFixed(1)}`;
    for (let i = n - 1; i >= 0; i--) band += `L${xOf(i).toFixed(1)},${yOf(d.min[i]).toFixed(1)}`;
    let line = "";
    for (let i = 0; i < n; i++) line += `${i ? "L" : "M"}${xOf(i).toFixed(1)},${yOf(d.mean[i]).toFixed(1)}`;
    return { band: `${band}Z`, line };
  };

  const onMove = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - r.left;
    if (x < M.left || x > width - M.right || n < 2) return setHover(null);
    setHover(Math.round(((x - M.left) / innerW) * (n - 1)));
  };

  const timeLabel = (i) => (aligned ? fmtClock(plot.t_ns[i]) : fmtSeconds(plot.t_s[i]));

  if (!n) return null;
  const tipLeft = hover != null && xOf(hover) > width * 0.6;

  return (
    <div className="accel-chart" ref={wrapRef}>
      {layout.map((p) => (
        <div
          key={p.key}
          className="accel-chart__legend"
          style={{ top: p.top - M.top, left: M.left }}
        >
          <span className="accel-chart__title">{p.title}</span>
          {p.series.length > 1 && p.series.map((s) => (
            <span key={s.key} className="accel-chart__key">
              <i style={{ background: s.color }} />
              {s.label}
            </span>
          ))}
        </div>
      ))}
      <svg
        width={width}
        height={totalH}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
        role="img"
        aria-label="Headstage acceleration, head tilt and movement intensity over time"
      >
        {layout.map((p) => (
          <g key={p.key} transform={`translate(0, 18)`}>
            {p.ticks.map((t) => (
              <g key={t}>
                <line
                  className={t === 0 ? "accel-chart__zero" : "accel-chart__grid"}
                  x1={M.left} x2={width - M.right} y1={p.yOf(t)} y2={p.yOf(t)}
                />
                <text className="accel-chart__tick" x={M.left - 6} y={p.yOf(t)} dy="0.32em"
                  textAnchor="end">
                  {Number(t.toFixed(2))}{p.unit === "°" ? "°" : ""}
                </text>
              </g>
            ))}
            {p.series.map((s) => {
              const { band, line } = pathFor(s, p.yOf);
              return (
                <g key={s.key}>
                  <path d={band} fill={s.color} opacity={p.series.length > 1 ? 0.18 : 0.28} />
                  <path d={line} fill="none" stroke={s.color} strokeWidth={p.series.length > 1 ? 1.2 : 1.6}
                    strokeLinejoin="round" />
                </g>
              );
            })}
            {hover != null && p.series.map((s) => (
              <circle key={s.key} cx={xOf(hover)} cy={p.yOf(plot.series[s.key].mean[hover])}
                r="4" fill={s.color} className="accel-chart__dot" />
            ))}
          </g>
        ))}
        {xTicks.map((i) => (
          <text key={i} className="accel-chart__tick" x={xOf(i)} y={totalH - 8}
            textAnchor={i === 0 ? "start" : i === n - 1 ? "end" : "middle"}>
            {timeLabel(i)}
          </text>
        ))}
        {hover != null && (
          <line className="accel-chart__cursor" x1={xOf(hover)} x2={xOf(hover)}
            y1={18} y2={totalH - AXIS_H} />
        )}
      </svg>
      {hover != null && (
        <div
          className="accel-chart__tip"
          style={tipLeft
            ? { right: width - xOf(hover) + 12 }
            : { left: xOf(hover) + 12 }}
        >
          <div className="accel-chart__tip-time">
            {timeLabel(hover)}
            {aligned && <span> · {fmtSeconds(plot.t_s[hover])} into ephys</span>}
          </div>
          {PANELS.flatMap((p) => p.series.map((s) => (
            <div key={s.key} className="accel-chart__tip-row">
              <i style={{ background: s.color }} />
              <span>{p.key === "acc" ? `${s.label} accel` : s.label}</span>
              <b>{fmtNum(plot.series[s.key].mean[hover], p.unit)}</b>
            </div>
          )))}
        </div>
      )}
      <div className="accel-chart__axis-label">
        {aligned ? "time of day (session clock)" : "time from ephys recording start (no clock model)"}
      </div>
    </div>
  );
}
