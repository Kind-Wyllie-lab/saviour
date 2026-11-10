import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import socket from "/src/socket";
import useModules from "/src/hooks/useModules";
import {
  SYNC_CLASS, SYNC_LABEL, SYNC_TITLE, worstSummary, reportDownloadUrl,
} from "../Recording/syncFormat";
import StatusChip from "./StatusChip";
import { fmtAgo, fmtClock } from "./format";
import "./SyncPanel.css";

// System → Sync tab. Read-only view over `get_sync_overview`
// (src/controller/sync_overview.py): module + controller PTP with a trend per
// row, camera FrameSync roles, recent post-hoc framesync verdicts.

const POLL_MS = 15000;
const WINDOWS = [
  [3600, "1 h"],
  [6 * 3600, "6 h"],
  [24 * 3600, "24 h"],
];
// Matches health.py's offline timeout: past this the numbers are stale.
const STALE_S = 90;

// ── Formatting ───────────────────────────────────────────────────────────────

function fmtNs(ns) {
  if (ns == null || !Number.isFinite(ns)) return "–";
  const abs = Math.abs(ns);
  if (abs >= 1e6) return `${(ns / 1e6).toFixed(2)} ms`;
  if (abs >= 1000) return `${(ns / 1000).toFixed(abs >= 1e5 ? 0 : 1)} µs`;
  return `${Math.round(ns)} ns`;
}

function fmtIso(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

const absMax = (...vals) => {
  const xs = vals.filter((v) => v != null && Number.isFinite(v)).map(Math.abs);
  return xs.length ? Math.max(...xs) : null;
};

// Worst |offset| this module reported on its last heartbeat, ranges included:
// that's what the start gate tests (recording.ptp_start_gate_us on both).
function worstNow(m) {
  return absMax(
    m.ptp4l_offset_ns, m.ptp4l_offset_ns_min, m.ptp4l_offset_ns_max,
    m.phc2sys_offset_ns, m.phc2sys_offset_ns_min, m.phc2sys_offset_ns_max,
  );
}

// Status ships with a symbol + word, never colour alone.
function ptpStatus(worstNs, gateNs, warnNs, stale) {
  if (stale) return { cls: "idle", label: "– stale" };
  if (worstNs == null) return { cls: "idle", label: "– no data" };
  if (worstNs > warnNs) return { cls: "danger", label: "✕ over limit" };
  if (worstNs > gateNs) return { cls: "warn", label: "▲ above gate" };
  return { cls: "ok", label: "✓ in gate" };
}

const SEVERITY = { danger: 3, warn: 2, idle: 1, ok: 0 };

// ── Sparkline ────────────────────────────────────────────────────────────────

// points: [{ t, v, lo, hi, extra }] -- one series (signed offset, ns) with its
// min/max band. Symmetric y around 0 so a bias reads as an offset from the
// centre line; dashed ±gate lines give the scale.
function Sparkline({ points, gateNs, since, until, tooltip }) {
  const wrapRef = useRef(null);
  const [hover, setHover] = useState(null);
  const W = 220, H = 40, pad = 3;

  const geom = useMemo(() => {
    const pts = points.filter((p) => p.v != null && Number.isFinite(p.v));
    if (pts.length === 0) return null;
    const peak = Math.max(
      gateNs * 1.25,
      ...pts.map((p) => absMax(p.v, p.lo, p.hi) ?? 0),
    );
    const x = (t) => pad + ((t - since) / Math.max(until - since, 1)) * (W - 2 * pad);
    const y = (v) => H / 2 - (v / peak) * (H / 2 - pad);
    const line = pts.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join("");
    const top = pts.map((p) => `${x(p.t).toFixed(1)},${y(p.hi ?? p.v).toFixed(1)}`);
    const bot = pts.slice().reverse().map((p) => `${x(p.t).toFixed(1)},${y(p.lo ?? p.v).toFixed(1)}`);
    return { pts, x, y, line, band: `M${top.join("L")}L${bot.join("L")}Z`, peak };
  }, [points, gateNs, since, until]);

  if (!geom) return <span className="cell--muted">no history</span>;

  const onMove = (e) => {
    const rect = wrapRef.current.getBoundingClientRect();
    const fx = ((e.clientX - rect.left) / rect.width) * W;
    let best = geom.pts[0];
    for (const p of geom.pts) {
      if (Math.abs(geom.x(p.t) - fx) < Math.abs(geom.x(best.t) - fx)) best = p;
    }
    setHover(best);
  };

  const gateVisible = gateNs < geom.peak;
  return (
    <div className="sync-spark" ref={wrapRef}
         onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
           role="img" aria-label="ptp4l offset trend">
        <line className="sync-spark__zero" x1={0} x2={W} y1={H / 2} y2={H / 2} />
        {gateVisible && <>
          <line className="sync-spark__gate" x1={0} x2={W} y1={geom.y(gateNs)} y2={geom.y(gateNs)} />
          <line className="sync-spark__gate" x1={0} x2={W} y1={geom.y(-gateNs)} y2={geom.y(-gateNs)} />
        </>}
        <path className="sync-spark__band" d={geom.band} />
        <path className="sync-spark__line" d={geom.line} />
        {hover && (
          <line className="sync-spark__cursor" x1={geom.x(hover.t)} x2={geom.x(hover.t)} y1={0} y2={H} />
        )}
      </svg>
      {hover && (
        <div className="sync-spark__tip"
             style={{ left: `${(geom.x(hover.t) / W) * 100}%` }}>
          {tooltip(hover)}
        </div>
      )}
    </div>
  );
}

// ── Sections ─────────────────────────────────────────────────────────────────

function PtpTable({ data, names }) {
  const gateNs = (data.thresholds?.ptp_start_gate_us ?? 50) * 1000;
  const warnNs = (data.thresholds?.ptp_threshold_us ?? 1000) * 1000;
  const until = data.generated_at;
  const since = until - data.window_s;
  const nowS = Date.now() / 1000;

  const rows = Object.entries(data.modules || {})
    .map(([id, m]) => {
      const stale = !m.last_heartbeat || nowS - m.last_heartbeat > STALE_S;
      const worst = worstNow(m);
      return { id, m, stale, worst, status: ptpStatus(worst, gateNs, warnNs, stale) };
    })
    // Problems first (over limit, above gate, stale), then worst offset, then name.
    .sort((a, b) => SEVERITY[b.status.cls] - SEVERITY[a.status.cls]
      || (b.worst ?? -1) - (a.worst ?? -1)
      || (names[a.id] ?? a.id).localeCompare(names[b.id] ?? b.id));

  const c = data.controller;
  const svcOk = (s) => s === "active";
  const ctrlStatus = !c ? { cls: "idle", label: "– unavailable" }
    : !svcOk(c.ptp4l_service) || !svcOk(c.phc2sys_service)
      ? { cls: "danger", label: "✕ service down" }
      : ptpStatus(absMax(c.phc2sys_offset_ns), gateNs, warnNs, false);

  const windowLabel = WINDOWS.find(([s]) => s === data.window_s)?.[1] ?? `${Math.round(data.window_s / 60)} min`;

  return (
    <section className="sync-section">
      <div className="sync-section__head">
        <h3>Clock sync (PTP)</h3>
        <span className="sync-muted">
          Start gate ±{fmtNs(gateNs)} on ptp4l and phc2sys · mid-recording limit ±{fmtNs(warnNs)}
        </span>
      </div>
      <div className="system-table-wrapper">
        <table className="system-table sync-table">
          <thead>
            <tr>
              <th>Device</th>
              <th>Status</th>
              <th title="Network: this Pi's PTP hardware clock vs the controller (grandmaster)">ptp4l offset</th>
              <th title="Local: system clock vs this Pi's PTP hardware clock">phc2sys offset</th>
              <th title="phc2sys frequency correction; 20–30k ppb is normal">Freq</th>
              <th title="PTP service restarts by the health monitor">Restarts</th>
              <th>ptp4l, last {windowLabel}</th>
            </tr>
          </thead>
          <tbody>
            <tr className="system-table__controller-row">
              <td>
                <span className="device-name">Controller</span>
                <span className="device-id">grandmaster{c?.interface ? ` · ${c.interface}` : ""}</span>
              </td>
              <td><StatusChip {...ctrlStatus} /></td>
              <td>
                {c ? (
                  <span className="sync-svc">
                    ptp4l <b className={svcOk(c.ptp4l_service) ? "val--ok" : "val--danger"}>{c.ptp4l_service ?? "?"}</b>
                  </span>
                ) : "–"}
              </td>
              <td>
                {c ? <>
                  <div>{fmtNs(c.phc2sys_offset_ns)}</div>
                  <div className="sync-muted">
                    service <b className={svcOk(c.phc2sys_service) ? "val--ok" : "val--danger"}>{c.phc2sys_service ?? "?"}</b>
                  </div>
                </> : "–"}
              </td>
              <td>{c?.phc2sys_freq != null ? `${c.phc2sys_freq.toLocaleString()} ppb` : "–"}</td>
              <td className="cell--muted">–</td>
              <td>
                {c?.history?.length ? (
                  <Sparkline
                    points={c.history.map(([t, v, lo, hi]) => ({ t, v, lo, hi }))}
                    gateNs={gateNs} since={since} until={until}
                    tooltip={(p) => <>{fmtClock(p.t)} · phc2sys {fmtNs(p.v)}</>}
                  />
                ) : <span className="cell--muted">no history</span>}
              </td>
            </tr>
            {rows.length === 0 && (
              <tr><td colSpan={7} className="system-table__empty">No module PTP data yet</td></tr>
            )}
            {rows.map(({ id, m, stale, status }) => (
              <tr key={id} className={stale ? "sync-row--stale" : undefined}>
                <td>
                  <span className="device-name">{names[id] ?? id}</span>
                  <span className="device-id">{id}</span>
                </td>
                <td>
                  <StatusChip {...status}
                    title={stale ? `Last heartbeat ${fmtAgo(m.last_heartbeat)}` : undefined} />
                </td>
                <td>
                  <div>{fmtNs(m.ptp4l_offset_ns)}</div>
                  {(m.ptp4l_offset_ns_min != null || m.ptp4l_offset_ns_max != null) && (
                    <div className="sync-muted">{fmtNs(m.ptp4l_offset_ns_min)} … {fmtNs(m.ptp4l_offset_ns_max)}</div>
                  )}
                </td>
                <td>
                  <div>{fmtNs(m.phc2sys_offset_ns)}</div>
                  {(m.phc2sys_offset_ns_min != null || m.phc2sys_offset_ns_max != null) && (
                    <div className="sync-muted">{fmtNs(m.phc2sys_offset_ns_min)} … {fmtNs(m.phc2sys_offset_ns_max)}</div>
                  )}
                </td>
                <td>{m.phc2sys_freq != null ? `${m.phc2sys_freq.toLocaleString()} ppb` : "–"}</td>
                <td title={m.last_ptp_restart ? `Last ${fmtAgo(m.last_ptp_restart)}` : undefined}>
                  {m.ptp_restarts ?? "–"}
                </td>
                <td>
                  <Sparkline
                    points={(m.history || []).map(([t, v, lo, hi, phc]) => ({ t, v, lo, hi, phc }))}
                    gateNs={gateNs} since={since} until={until}
                    tooltip={(p) => <>{fmtClock(p.t)} · ptp4l {fmtNs(p.v)} · phc2sys {fmtNs(p.phc)}</>}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function roleNote(id, cam, transmitter) {
  if (!cam.online) return { cls: "idle", text: "offline" };
  if (cam.deferred) return { cls: "warn", text: `becomes ${cam.expected_role} when recording stops` };
  if (cam.waiting_for_server) return { cls: "warn", text: `waiting for ${transmitter ?? "a server"} to confirm` };
  if (cam.pending_sync_mode && cam.pending_sync_mode !== cam.sync_mode) {
    return { cls: "warn", text: `switching to ${cam.pending_sync_mode}…` };
  }
  if (cam.expected_role && cam.expected_role !== cam.sync_mode) {
    return { cls: "warn", text: `should be ${cam.expected_role}` };
  }
  if (id === transmitter) return { cls: "ok", text: "transmitter" };
  if (cam.sync_mode === "client") return { cls: "ok", text: "following transmitter" };
  return { cls: "idle", text: cam.framesync_enabled ? "" : "frame sync off" };
}

function FramesyncTable({ framesync, names }) {
  const cams = Object.entries(framesync?.cameras || {})
    .sort(([a, ca], [b, cb]) => {
      const rank = (id, c) => (id === framesync.transmitter ? 0 : c.sync_mode === "client" ? 1 : 2);
      return rank(a, ca) - rank(b, cb) || (names[a] ?? a).localeCompare(names[b] ?? b);
    });
  const tx = framesync?.transmitter;
  const txCam = tx ? framesync.cameras?.[tx] : null;
  const mismatched = cams.filter(([, c]) =>
    c.online && c.sync_mode === "client" && txCam
    && (c.fps !== txCam.fps || c.sensor_mode_index !== txCam.sensor_mode_index));

  return (
    <section className="sync-section">
      <div className="sync-section__head">
        <h3>Camera frame sync</h3>
        <span className="sync-muted">
          {tx ? <>Transmitter: <b>{names[tx] ?? tx}</b></> : "No transmitter elected"}
          {txCam ? ` · ${txCam.fps ?? "?"} fps, sensor mode ${txCam.sensor_mode_index ?? "?"}` : ""}
        </span>
      </div>
      {cams.length === 0 ? (
        <p className="sync-muted">No cameras connected.</p>
      ) : (
        <div className="system-table-wrapper">
          <table className="system-table sync-table">
            <thead>
              <tr>
                <th>Camera</th>
                <th>Frame sync</th>
                <th>Role</th>
                <th>State</th>
                <th>FPS</th>
                <th>Sensor mode</th>
              </tr>
            </thead>
            <tbody>
              {cams.map(([id, cam]) => {
                const note = roleNote(id, cam, tx);
                const off = mismatched.some(([mid]) => mid === id);
                return (
                  <tr key={id} className={cam.online ? undefined : "sync-row--stale"}>
                    <td>
                      <span className="device-name">{names[id] ?? id}</span>
                      <span className="device-id">{id}</span>
                    </td>
                    <td>{cam.framesync_enabled ? "on" : <span className="cell--muted">off</span>}</td>
                    <td><span className={`sync-role sync-role--${cam.sync_mode}`}>{cam.sync_mode}</span></td>
                    <td>{note.text ? <StatusChip cls={note.cls} label={note.text} /> : null}</td>
                    <td className={off ? "val--warn" : undefined}
                        title={off ? "Differs from the transmitter" : undefined}>
                      {cam.fps ?? "–"}{off ? " ▲" : ""}
                    </td>
                    <td className={off ? "val--warn" : undefined}>{cam.sensor_mode_index ?? "–"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="sync-muted sync-footnote">
        Live per-frame lock isn't reported yet; the post-recording check below measures how
        well the cameras actually stayed in step.
      </p>
    </section>
  );
}

function VerdictList({ verdicts }) {
  return (
    <section className="sync-section">
      <div className="sync-section__head">
        <h3>Recent sync checks</h3>
        <span className="sync-muted">Run automatically once a session's files reach the share</span>
      </div>
      {!verdicts?.length ? (
        <p className="sync-muted">No sessions have been checked yet.</p>
      ) : (
        <ul className="sync-verdicts">
          {verdicts.map((v) => {
            const url = reportDownloadUrl(v.session_name, v.report_rel);
            const summary = worstSummary(v);
            return (
              <li key={`${v.session_name}-${v.date_dir ?? ""}-${v.generated_at}`} className="sync-verdict">
                <span className={`config-sync-badge config-sync-badge--${SYNC_CLASS[v.status] ?? "muted"}`}
                      title={SYNC_TITLE[v.status]}>
                  {SYNC_LABEL[v.status] ?? v.status}
                </span>
                <div className="sync-verdict__body">
                  <div>
                    <Link to={`/recording/sessions/${encodeURIComponent(v.session_name)}`}>{v.session_name}</Link>
                    {v.date_dir ? <span className="sync-muted"> · {v.date_dir}</span> : null}
                    {v.sync_mode ? <span className="sync-muted"> · {v.sync_mode}</span> : null}
                  </div>
                  {summary && <div className="sync-muted">{summary}</div>}
                  {v.status !== "green" && v.reasons?.[0] && v.reasons[0] !== summary && (
                    <div className="sync-verdict__reason">{v.reasons[0]}</div>
                  )}
                </div>
                <div className="sync-verdict__meta">
                  <span className="sync-muted">{fmtIso(v.generated_at)}</span>
                  {url && <a href={url} className="sync-link">report</a>}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

// ── Panel ────────────────────────────────────────────────────────────────────

export default function SyncPanel() {
  const { modules } = useModules();
  const [windowS, setWindowS] = useState(WINDOWS[0][0]);
  const [data, setData] = useState(null);
  const [, setTick] = useState(0);

  useEffect(() => {
    const onData = (d) => setData(d);
    const ask = () => socket.emit("get_sync_overview", { window_s: windowS });
    socket.on("sync_overview", onData);
    socket.on("connect", ask);
    ask();
    const id = setInterval(ask, POLL_MS);
    return () => {
      clearInterval(id);
      socket.off("sync_overview", onData);
      socket.off("connect", ask);
    };
  }, [windowS]);

  // Keep "updated Xs ago" honest between polls.
  useEffect(() => {
    const id = setInterval(() => setTick((n) => n + 1), 5000);
    return () => clearInterval(id);
  }, []);

  const names = useMemo(() => Object.fromEntries(
    Object.entries(modules || {}).map(([id, m]) => [id, m.name || id]),
  ), [modules]);

  return (
    <div className="sync-panel">
      <div className="sync-toolbar">
        <div className="sync-window" role="group" aria-label="Trend window">
          {WINDOWS.map(([s, label]) => (
            <button key={s} type="button"
                    className={`sync-window__btn ${windowS === s ? "sync-window__btn--active" : ""}`}
                    aria-pressed={windowS === s}
                    onClick={() => setWindowS(s)}>
              {label}
            </button>
          ))}
        </div>
        <span className="sync-muted">
          {data?.generated_at ? `Updated ${fmtAgo(data.generated_at)}` : "Loading…"}
        </span>
        <button type="button" className="refresh-btn"
                onClick={() => socket.emit("get_sync_overview", { window_s: windowS })}>
          Refresh
        </button>
      </div>

      {data?.error && <div className="sync-error" role="alert">Couldn't load sync data: {data.error}</div>}

      {data && !data.error && <>
        <PtpTable data={data} names={names} />
        <FramesyncTable framesync={data.framesync} names={names} />
        <VerdictList verdicts={data.recent_verdicts} />
      </>}
    </div>
  );
}
