import { useMemo, useRef, useState, useEffect } from "react";
import { dayCurve, fmtMinute, DAY_MIN } from "./lightingSchedule";

// One-day (00:00-24:00) preview of the visible and IR output for the config being edited,
// with a "now" marker in the schedule's time zone. Redrawn on every edit,
// before saving.

const H = 150;
const M = { left: 40, right: 12, top: 10, bottom: 24 };
const SERIES = [
  { key: "visible", label: "Visible", color: "var(--viz-1)" },
  { key: "ir", label: "IR", color: "var(--viz-2)" },
];

// Minutes after midnight now, in tz ("" = the browser's own zone).
function nowMinute(tz) {
  try {
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone: tz || undefined, hour: "2-digit", minute: "2-digit", hour12: false,
    }).formatToParts(new Date());
    const h = Number(parts.find((p) => p.type === "hour")?.value) % 24;
    const m = Number(parts.find((p) => p.type === "minute")?.value);
    return h * 60 + m;
  } catch {
    return null;
  }
}

export default function SchedulePreview({ cfg, valid }) {
  const ref = useRef(null);
  const [w, setW] = useState(560);
  const [hover, setHover] = useState(null);
  const [now, setNow] = useState(() => nowMinute(cfg.timezone));

  useEffect(() => {
    if (!ref.current) return undefined;
    const ro = new ResizeObserver(([e]) => setW(Math.max(280, e.contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  useEffect(() => {
    setNow(nowMinute(cfg.timezone));
    const t = setInterval(() => setNow(nowMinute(cfg.timezone)), 30000);
    return () => clearInterval(t);
  }, [cfg.timezone]);

  const data = useMemo(() => (valid ? dayCurve(cfg, 2) : []), [cfg, valid]);
  const iw = w - M.left - M.right;
  const ih = H - M.top - M.bottom;
  const x = (m) => M.left + (m / DAY_MIN) * iw;
  const y = (v) => M.top + ih - (v / 100) * ih;

  const path = (key) => data.map((d, i) => `${i ? "L" : "M"}${x(d.minute).toFixed(1)},${y(d[key]).toFixed(1)}`).join("");
  const nightBands = useMemo(() => {
    const out = [];
    let start = null;
    data.forEach((d) => {
      const dark = d.phase === "night";
      if (dark && start == null) start = d.minute;
      if (!dark && start != null) { out.push([start, d.minute]); start = null; }
    });
    if (start != null) out.push([start, DAY_MIN]);
    return out;
  }, [data]);

  const onMove = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - r.left;
    if (px < M.left || px > w - M.right || !data.length) return setHover(null);
    const m = ((px - M.left) / iw) * DAY_MIN;
    setHover(data[Math.round(m / 2)] ?? null);
  };

  const hovered = hover;
  return (
    <div className="dmx-preview" ref={ref}>
      <div className="dmx-preview__legend">
        <span className="dmx-preview__title">Daily cycle</span>
        {SERIES.map((s) => (
          <span key={s.key} className="dmx-preview__key"><i style={{ background: s.color }} />{s.label}</span>
        ))}
        <span className="dmx-preview__key"><i className="dmx-preview__night-swatch" />Night</span>
      </div>
      {!valid ? (
        <p className="dmx-muted">Fix the schedule errors to see the preview.</p>
      ) : (
        <svg width={w} height={H} onMouseMove={onMove} onMouseLeave={() => setHover(null)}
          role="img" aria-label="Visible and IR output over one day">
          {nightBands.map(([a, b]) => (
            <rect key={a} className="dmx-preview__night" x={x(a)} y={M.top} width={x(b) - x(a)} height={ih} />
          ))}
          {[0, 50, 100].map((v) => (
            <g key={v}>
              <line className="dmx-preview__grid" x1={M.left} x2={w - M.right} y1={y(v)} y2={y(v)} />
              <text className="dmx-preview__tick" x={M.left - 6} y={y(v)} dy="0.32em" textAnchor="end">{v}%</text>
            </g>
          ))}
          {[0, 6, 12, 18, 24].map((hr) => (
            <text key={hr} className="dmx-preview__tick" x={x(hr * 60)} y={H - 6}
              textAnchor={hr === 0 ? "start" : hr === 24 ? "end" : "middle"}>
              {String(hr).padStart(2, "0")}:00
            </text>
          ))}
          {SERIES.map((s) => (
            <path key={s.key} d={path(s.key)} fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round" />
          ))}
          {now != null && (
            <g>
              <line className="dmx-preview__now" x1={x(now)} x2={x(now)} y1={M.top} y2={M.top + ih} />
              <text className="dmx-preview__now-label" x={x(now)} y={M.top - 2}
                textAnchor={now > DAY_MIN * 0.9 ? "end" : "middle"}>now</text>
            </g>
          )}
          {hovered && (
            <g>
              <line className="dmx-preview__cursor" x1={x(hovered.minute)} x2={x(hovered.minute)} y1={M.top} y2={M.top + ih} />
              {SERIES.map((s) => (
                <circle key={s.key} cx={x(hovered.minute)} cy={y(hovered[s.key])} r="4"
                  fill={s.color} className="dmx-preview__dot" />
              ))}
            </g>
          )}
        </svg>
      )}
      {hovered && (
        <div className="dmx-preview__tip"
          style={x(hovered.minute) > w * 0.6 ? { right: w - x(hovered.minute) + 10 } : { left: x(hovered.minute) + 10 }}>
          <div className="dmx-preview__tip-time">{fmtMinute(hovered.minute)} · {hovered.phase}</div>
          {SERIES.map((s) => (
            <div key={s.key} className="dmx-preview__tip-row">
              <i style={{ background: s.color }} /><span>{s.label}</span><b>{hovered[s.key].toFixed(1)}%</b>
            </div>
          ))}
        </div>
      )}
      <div className="dmx-muted dmx-preview__note">
        Times are {cfg.timezone ? `${cfg.timezone} wall-clock time` : "the module's local time (shown here in your browser's zone)"}.
      </div>
    </div>
  );
}
