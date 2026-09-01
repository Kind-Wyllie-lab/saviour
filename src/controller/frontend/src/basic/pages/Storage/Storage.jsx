import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import socket from "/src/socket";
import useIsLoggedIn from "/src/hooks/useIsLoggedIn";
import MiniAreaChart from "../../components/MiniAreaChart/MiniAreaChart";
import { triggerDownload } from "../Recording/sessionFormat";
import "./Storage.css";

const GiB = 1024 ** 3;

const RANGES = [
  { label: "24h", hours: 24 },
  { label: "7d", hours: 168 },
  { label: "30d", hours: 720 },
  { label: "All", hours: null },
];

function timeAgo(ts) {
  if (!ts) return "never";
  const s = Math.floor(Date.now() / 1000 - ts);
  if (s < 10) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

function humanDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return null;
  const d = seconds / 86400;
  if (d >= 1.5) return `~${Math.round(d)} days`;
  const h = seconds / 3600;
  if (h >= 1.5) return `~${Math.round(h)} hours`;
  return "< 1 hour";
}

function humanMinutes(mins) {
  if (!Number.isFinite(mins) || mins <= 0) return "—";
  return humanDuration(mins * 60) || "< 1 hour";
}

// Least-squares slope of free bytes vs time over the visible window.
function projectFull(samples) {
  const pts = samples.filter((s) => s.length >= 3);
  if (pts.length < 4) return { text: "not enough history", trend: "flat" };
  const n = pts.length;
  const t0 = pts[0][0];
  let sx = 0, sy = 0, sxx = 0, sxy = 0;
  for (const [t, free] of pts) {
    const x = t - t0;
    sx += x; sy += free; sxx += x * x; sxy += x * free;
  }
  const denom = n * sxx - sx * sx;
  if (denom === 0) return { text: "stable", trend: "flat" };
  const slope = (n * sxy - sx * sy) / denom; // bytes per second
  const lastFree = pts[pts.length - 1][1];
  if (slope >= -1) {
    return { text: slope > 1 ? "free space increasing" : "stable", trend: "flat" };
  }
  const secsToZero = lastFree / -slope;
  const dur = humanDuration(secsToZero);
  return {
    text: dur ? `full in ${dur} at current rate` : "filling",
    trend: secsToZero < 3 * 86400 ? "danger" : "warn",
  };
}

function StatusPill({ status }) {
  const map = {
    ok: ["ok", "OK"],
    warn: ["warn", "Low"],
    error: ["danger", "Problem"],
    unconfigured: ["muted", "Not configured"],
    unknown: ["muted", "Unknown"],
  };
  const [cls, label] = map[status] || map.unknown;
  return <span className={`storage-pill storage-pill--${cls}`}>{label}</span>;
}

function CapacityBar({ usedPct, warnAt, dangerAt }) {
  if (usedPct == null) return null;
  const kind = usedPct >= (dangerAt ?? 95) ? "danger"
    : usedPct >= (warnAt ?? 85) ? "warn" : "ok";
  return (
    <div className="capacity-bar">
      <div className={`capacity-bar__fill capacity-bar__fill--${kind}`}
        style={{ width: `${Math.min(100, Math.max(0, usedPct))}%` }} />
    </div>
  );
}

export default function Storage() {
  const loggedIn = useIsLoggedIn();
  const [overview, setOverview] = useState(null);
  const [history, setHistory] = useState([]);
  const [rangeIdx, setRangeIdx] = useState(1); // 7d

  const range = RANGES[rangeIdx];

  const requestHistory = (hours) =>
    socket.emit("get_nas_history", { hours: hours ?? undefined });

  useEffect(() => {
    const onOverview = (d) => setOverview(d);
    const onHistory = (d) => setHistory(d?.samples || []);
    const onNasHealth = () => socket.emit("get_storage_overview");

    socket.on("storage_overview", onOverview);
    socket.on("nas_history", onHistory);
    socket.on("nas_health_update", onNasHealth);

    socket.emit("get_storage_overview");
    requestHistory(range.hours);
    const poll = setInterval(() => socket.emit("get_storage_overview"), 60000);

    return () => {
      clearInterval(poll);
      socket.off("storage_overview", onOverview);
      socket.off("nas_history", onHistory);
      socket.off("nas_health_update", onNasHealth);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { requestHistory(range.hours); }, [rangeIdx]); // eslint-disable-line react-hooks/exhaustive-deps

  const nas = overview?.nas || {};
  const exports = overview?.exports || { pending: 0, failed: 0, sessions: [] };
  const disks = overview?.disks || [];
  const dataRate = overview?.data_rate || {};

  const pctSeries = useMemo(
    () => history
      .filter((s) => s.length >= 3 && s[2])
      .map(([t, free, total]) => ({ t, v: (free / total) * 100 })),
    [history]
  );
  const projection = useMemo(() => projectFull(history), [history]);

  const usedPct = nas.free_pct != null ? 100 - nas.free_pct : null;

  const downloadCsv = () => {
    const q = range.hours ? `?hours=${range.hours}` : "?hours=all";
    triggerDownload(`/api/nas_history.csv${q}`, `nas_history_${range.label}.csv`);
  };

  return (
    <div className="storage-page">
      <div className="storage-header">
        <h1>Storage</h1>
        <button className="refresh-btn" onClick={() => {
          socket.emit("get_storage_overview");
          requestHistory(range.hours);
        }}>Refresh</button>
      </div>

      {!overview && <p className="storage-muted">Loading…</p>}

      {overview && (
        <div className="storage-grid">
          {/* ── Destination ─────────────────────────────────────────── */}
          <section className="storage-card">
            <div className="storage-card__head">
              <h2>Export destination</h2>
              <StatusPill status={nas.status || "unknown"} />
            </div>
            <div className="storage-dest">
              <code>{nas.destination || "—"}</code>
              <span className="storage-muted">checked {timeAgo(nas.checked_at)}</span>
            </div>

            {nas.free_pct != null ? (
              <>
                <div className="storage-bignum">
                  {nas.free_gb?.toLocaleString()} GB free
                  <span className="storage-bignum__sub">
                    {nas.free_pct}% of {nas.total_gb?.toLocaleString()} GB
                  </span>
                </div>
                <CapacityBar
                  usedPct={usedPct}
                  warnAt={100 - (nas.warn_free_pct ?? 15)}
                  dangerAt={100 - (nas.min_free_pct ?? 5)}
                />
                <div className="storage-thresholds storage-muted">
                  warn at {nas.warn_free_pct}% free · block new sessions below {nas.min_free_pct}%
                </div>
              </>
            ) : (
              <p className="storage-muted">
                {nas.status === "unconfigured"
                  ? "No external share configured — modules export to the controller's local share."
                  : "Free space unavailable."}
              </p>
            )}

            {nas.error && <div className="storage-alert">{nas.error}</div>}

            <Link to="/settings" className="storage-link">Configure in Settings →</Link>
          </section>

          {/* ── Free-space trend ────────────────────────────────────── */}
          <section className="storage-card">
            <div className="storage-card__head">
              <h2>Free space over time</h2>
              <div className="storage-range">
                {RANGES.map((r, i) => (
                  <button
                    key={r.label}
                    className={`storage-range__btn ${i === rangeIdx ? "is-active" : ""}`}
                    onClick={() => setRangeIdx(i)}
                  >{r.label}</button>
                ))}
              </div>
            </div>

            <MiniAreaChart
              data={pctSeries}
              unit="%"
              yMin={0}
              thresholds={[
                nas.min_free_pct != null && { v: nas.min_free_pct, label: `min ${nas.min_free_pct}%`, kind: "danger" },
                nas.warn_free_pct != null && { v: nas.warn_free_pct, label: `warn ${nas.warn_free_pct}%`, kind: "warn" },
              ].filter(Boolean)}
            />

            <div className={`storage-projection storage-projection--${projection.trend}`}>
              {projection.text}
            </div>
            <button className="refresh-btn" onClick={downloadCsv}>Download CSV</button>
          </section>

          {/* ── Recording data rate (config estimate) ───────────────── */}
          <section className="storage-card">
            <div className="storage-card__head"><h2>Recording data rate</h2></div>
            {dataRate.recording_module_count > 0 ? (
              <>
                <div className="storage-bignum">
                  {dataRate.recording_mb_per_min?.toLocaleString()} MB/min
                  <span className="storage-bignum__sub">
                    {dataRate.recording_module_count} module
                    {dataRate.recording_module_count === 1 ? "" : "s"} recording ·
                    {" "}{((dataRate.recording_mb_per_min || 0) * 60 / 1024).toFixed(1)} GB/hour
                  </span>
                </div>
                <div className={`storage-projection storage-projection--${
                  dataRate.share_runway_hours == null ? "flat"
                    : dataRate.share_runway_hours < 72 ? "danger"
                    : dataRate.share_runway_hours < 168 ? "warn" : "flat"}`}>
                  {dataRate.share_runway_hours == null
                    ? "share free space unknown"
                    : `share holds ${humanDuration(dataRate.share_runway_hours * 3600)} at this rate`}
                </div>
              </>
            ) : (
              <p className="storage-muted">
                No modules recording. Estimated from each module's config
                (bitrate / sample rate) — a worst-case ceiling.
              </p>
            )}
          </section>

          {/* ── Export backlog ──────────────────────────────────────── */}
          <section className="storage-card">
            <div className="storage-card__head"><h2>Export backlog</h2></div>
            <div className="storage-stat-row">
              <div className="storage-stat">
                <span className="storage-stat__num">{exports.pending}</span>
                <span className="storage-stat__lbl">pending</span>
              </div>
              <div className={`storage-stat ${exports.failed ? "storage-stat--bad" : ""}`}>
                <span className="storage-stat__num">{exports.failed}</span>
                <span className="storage-stat__lbl">failed</span>
              </div>
            </div>

            {exports.sessions.length === 0 ? (
              <p className="storage-muted">All exports up to date.</p>
            ) : (
              <ul className="storage-session-list">
                {exports.sessions.map((s) => (
                  <li key={s.session_name}>
                    <div className="storage-session-list__row">
                      <span className="storage-session-list__name" title={s.session_name}>
                        {s.session_name}
                      </span>
                      <span className="storage-muted">
                        {s.pending > 0 && `${s.pending} pending`}
                        {s.pending > 0 && s.failed > 0 && ", "}
                        {s.failed > 0 && <span className="storage-bad">{s.failed} failed</span>}
                      </span>
                    </div>
                    {loggedIn && s.failed > 0 && (
                      <button
                        className="storage-link storage-link--btn"
                        onClick={() => socket.emit("retry_failed_exports", { session_name: s.session_name })}
                      >Retry failed</button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* ── Local disks ─────────────────────────────────────────── */}
          <section className="storage-card storage-card--wide">
            <div className="storage-card__head"><h2>Module local disks</h2></div>
            {disks.length === 0 ? (
              <p className="storage-muted">No module disk data.</p>
            ) : (
              <div className="storage-table-wrap">
                <table className="storage-table">
                  <thead>
                    <tr>
                      <th>Module</th><th>Type</th><th>Used</th><th>Free</th>
                      <th title="Estimated recording output, from this module's config (worst-case ceiling)">Rate</th>
                      <th title="How long local free space lasts at that rate if export fully stalls">Buffer</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {disks.map((d) => (
                      <tr key={d.module_id}>
                        <td>{d.name}{d.recording && <span className="storage-rec-dot" title="recording" />}</td>
                        <td className="storage-muted">{d.type || "—"}</td>
                        <td>{d.used_pct != null ? `${d.used_pct.toFixed(0)}%` : "—"}</td>
                        <td>{d.free_gb != null ? `${d.free_gb} GB` : "—"}</td>
                        <td title={d.est_note || ""}>
                          {d.measured_mb_per_min != null ? (
                            <>
                              {d.measured_mb_per_min} MB/min
                              {d.est_mb_per_min != null && (
                                <span className="storage-muted"> (est {d.est_mb_per_min})</span>
                              )}
                            </>
                          ) : d.est_mb_per_min != null ? (
                            <>~{d.est_mb_per_min} MB/min</>
                          ) : "—"}
                        </td>
                        <td className={
                          d.local_buffer_min != null && d.local_buffer_min < 60 ? "storage-bad" : undefined
                        }>
                          {d.local_buffer_min != null ? humanMinutes(d.local_buffer_min) : "—"}
                        </td>
                        <td className="storage-table__bar">
                          <CapacityBar usedPct={d.used_pct} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
