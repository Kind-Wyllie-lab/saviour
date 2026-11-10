import { useEffect, useMemo, useRef, useState } from "react";
import socket from "/src/socket";
import useIsLoggedIn from "/src/hooks/useIsLoggedIn";
import StatusChip from "./StatusChip";
import { fmtAgo, fmtClock, fmtSpan } from "./format";
import "./SyncPanel.css";
import "./NetworkPanel.css";

// System → Network tab. Read-only view over `get_network_overview`
// (src/controller/network_overview.py): the controller's interfaces and DHCP
// pool, and every host on the rig network -- each DHCP lease and ARP
// neighbour, not only registered modules -- with ping RTT, module link
// counters and heartbeat regularity.

const POLL_MS = 15000;

const KIND = {
  module:               { label: "Module",                cls: "ok" },
  share:                { label: "Export share",          cls: "ok" },
  saviour_unregistered: { label: "SAVIOUR Pi, not registered", cls: "warn" },
  unprovisioned:        { label: "Unprovisioned Pi",      cls: "warn" },
  raspberry_pi:         { label: "Raspberry Pi",          cls: "idle" },
  other:                { label: "Other device",          cls: "idle" },
};

const FILTERS = [
  ["all", "All"],
  ["module", "Modules"],
  ["attention", "Needs attention"],
  ["other", "Other devices"],
];

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmtMs(ms) {
  if (ms == null) return "–";
  return ms < 1 ? `${ms.toFixed(2)} ms` : ms < 10 ? `${ms.toFixed(1)} ms` : `${Math.round(ms)} ms`;
}

function fmtBytes(n) {
  if (n == null) return "–";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, v = n;
  while (v >= 1000 && i < u.length - 1) { v /= 1000; i++; }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${u[i]}`;
}

function leaseText(lease, now) {
  if (!lease) return { text: "no lease (static?)", cls: "cell--muted" };
  if (lease.state === "infinite") return { text: "permanent", cls: "" };
  if (lease.state === "expired") return { text: `expired ${fmtSpan(now - lease.expiry)} ago`, cls: "val--warn" };
  return { text: `expires in ${fmtSpan(lease.expiry - now)}`, cls: "" };
}

// Link problems worth flagging: < 1 Gb/s, half duplex, errors/drops/carrier
// flaps in the last hour. Returns [] when the link looks healthy.
function linkIssues(link) {
  if (!link) return [];
  const out = [];
  if (link.speed_mbps != null && link.speed_mbps < 1000) out.push(`${link.speed_mbps} Mb/s`);
  if (link.duplex && link.duplex !== "full") out.push(`${link.duplex} duplex`);
  const errs = (link.rx_errors_1h ?? 0) + (link.tx_errors_1h ?? 0);
  if (errs > 0) out.push(`${errs} errors`);
  const drops = (link.rx_dropped_1h ?? 0) + (link.tx_dropped_1h ?? 0);
  if (drops > 0) out.push(`${drops} drops`);
  if ((link.carrier_changes_1h ?? 0) > 0) out.push(`link dropped ${link.carrier_changes_1h}×`);
  return out;
}

// Short "why look at this host" notes; each one implies the row needs attention.
function hostNotes(h) {
  const notes = [];
  const m = h.module;
  if (m && !m.online && h.reachable) notes.push("module offline but host answers");
  else if (m && !m.online) notes.push("module offline");
  if (!h.reachable) notes.push(h.lease?.state === "active" ? "no reply (lease still active)" : "no reply");
  if (h.ping?.loss_pct > 0 && h.ping.loss_pct < 100) notes.push(`${h.ping.loss_pct}% ping loss`);
  if (m?.mac_mismatch) notes.push(`reports MAC ${m.mac_mismatch}`);
  if (m && linkIssues(m.link).length) notes.push("link problems");
  const hb = m?.heartbeat;
  if (hb && hb.mean_gap_s && hb.max_gap_s > hb.mean_gap_s * 1.8) notes.push(`heartbeat gap ${Math.round(hb.max_gap_s)} s`);
  if (h.kind === "unprovisioned") notes.push("run saviour-config to provision");
  if (h.kind === "saviour_unregistered") notes.push("not registered with this controller");
  return notes;
}

// Release lease: only for a lease that isn't a module or the share. Returns
// null (no button) or {enabled, reason}. The backend re-checks all of this
// (network_overview.release_lease) and pings the host again before acting.
function releaseState(h, loggedIn, available) {
  if (!h.lease || h.module || h.kind === "share") return null;
  if (h.reachable) return { enabled: false, reason: "Still answering on the network: unplug or power it off first, or it will just renew" };
  if (!available) return { enabled: false, reason: "dhcp_release isn't installed on the controller (sudo apt install dnsmasq-utils, or run mend.sh)" };
  if (!loggedIn) return { enabled: false, reason: "Login required for this action" };
  return { enabled: true, reason: "Remove this device's DHCP lease from the controller" };
}

function hostName(h) {
  if (h.module) return { name: h.module.name, sub: h.module.id };
  return { name: h.hostname || "(no hostname)", sub: null };
}

// ── RTT sparkline ────────────────────────────────────────────────────────────

// history: [[t, ms|null]]. Line of RTT from 0; a lost reply is a tick on the
// baseline drawn in the danger colour (with the word "lost" in the tooltip).
function RttSpark({ history }) {
  const ref = useRef(null);
  const [hover, setHover] = useState(null);
  const W = 140, H = 28, pad = 2;
  const geom = useMemo(() => {
    if (!history?.length) return null;
    const t0 = history[0][0], t1 = history[history.length - 1][0];
    const peak = Math.max(1, ...history.map(([, r]) => r ?? 0)) * 1.15;
    const x = (t) => pad + ((t - t0) / Math.max(t1 - t0, 1)) * (W - 2 * pad);
    const y = (v) => H - pad - (v / peak) * (H - 2 * pad);
    let d = "", pen = false;
    for (const [t, r] of history) {
      if (r == null) { pen = false; continue; }
      d += `${pen ? "L" : "M"}${x(t).toFixed(1)},${y(r).toFixed(1)}`;
      pen = true;
    }
    return { x, y, d, peak, lost: history.filter(([, r]) => r == null).map(([t]) => t) };
  }, [history]);
  if (!geom) return null;

  const onMove = (e) => {
    const rect = ref.current.getBoundingClientRect();
    const fx = ((e.clientX - rect.left) / rect.width) * W;
    let best = history[0];
    for (const p of history) if (Math.abs(geom.x(p[0]) - fx) < Math.abs(geom.x(best[0]) - fx)) best = p;
    setHover(best);
  };

  return (
    <div className="net-spark" ref={ref} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="Ping round-trip time, last hour">
        <line className="sync-spark__zero" x1={0} x2={W} y1={H - pad} y2={H - pad} />
        <path className="sync-spark__line" d={geom.d} />
        {geom.lost.map((t) => (
          <line key={t} className="net-spark__lost" x1={geom.x(t)} x2={geom.x(t)} y1={H - pad} y2={H - pad - 7} />
        ))}
        {hover && <line className="sync-spark__cursor" x1={geom.x(hover[0])} x2={geom.x(hover[0])} y1={0} y2={H} />}
      </svg>
      {hover && (
        <div className="sync-spark__tip" style={{ left: `${(geom.x(hover[0]) / W) * 100}%` }}>
          {fmtClock(hover[0])} · {hover[1] == null ? "lost" : fmtMs(hover[1])}
        </div>
      )}
    </div>
  );
}

// ── Sections ─────────────────────────────────────────────────────────────────

function Summary({ data }) {
  const hosts = data.hosts || [];
  const mods = hosts.filter((h) => h.kind === "module");
  const modsUp = mods.filter((h) => h.reachable).length;
  const others = hosts.filter((h) => h.kind === "other" || h.kind === "raspberry_pi").length;
  const unprov = (data.counts?.unprovisioned ?? 0) + (data.counts?.saviour_unregistered ?? 0);
  const pool = data.dhcp;
  const tiles = [
    { k: "Rig network", v: data.controller?.network ?? "–", sub: `${data.controller?.iface ?? "eth0"} · controller ${data.controller?.ip ?? "?"}` },
    { k: "DHCP leases", v: pool ? `${pool.in_use} / ${pool.size}` : "–", sub: pool ? `${pool.start} – ${pool.end}` : "no DHCP config found" },
    { k: "Modules answering", v: `${modsUp} / ${mods.length}`, status: modsUp < mods.length ? "warn" : null },
    { k: "Other devices", v: String(others), sub: "PCs, NAS, anything else" },
    { k: "Unregistered SAVIOUR Pis", v: String(unprov), status: unprov ? "warn" : null },
  ];
  return (
    <div className="net-tiles">
      {tiles.map((t) => (
        <div key={t.k} className={`net-tile ${t.status ? `net-tile--${t.status}` : ""}`}>
          <div className="net-tile__k">{t.k}</div>
          <div className="net-tile__v">{t.status === "warn" ? "▲ " : ""}{t.v}</div>
          {t.sub && <div className="net-tile__sub">{t.sub}</div>}
        </div>
      ))}
    </div>
  );
}

function Interfaces({ data }) {
  const pool = data.dhcp;
  const link = data.controller?.link;
  const issues = linkIssues(link);
  const fw = data.firewall;
  return (
    <section className="sync-section">
      <div className="sync-section__head">
        <h3>Controller</h3>
        {fw && (
          <StatusChip
            cls={fw.ok === false ? "danger" : fw.ok ? "ok" : "idle"}
            label={fw.ok === false ? "✕ wlan firewall not active" : fw.ok ? "✓ firewall" : "– firewall unknown"}
            title={fw.detail}
          />
        )}
      </div>
      <div className="system-table-wrapper">
        <table className="system-table sync-table">
          <thead>
            <tr><th>Interface</th><th>Role</th><th>Address</th><th>State</th><th>Details</th></tr>
          </thead>
          <tbody>
            {(data.interfaces || []).map((i) => (
              <tr key={i.name}>
                <td><span className="device-name">{i.name}</span><span className="device-id">{i.mac ?? ""}</span></td>
                <td>{i.role === "rig" ? "Rig network" : i.role === "uplink" ? "Uplink" : <span className="cell--muted">–</span>}</td>
                <td>{i.ipv4?.length ? i.ipv4.join(", ") : <span className="cell--muted">none</span>}</td>
                <td>{i.operstate === "UP" ? <span className="val--ok">up</span> : <span className="cell--muted">{(i.operstate || "?").toLowerCase()}</span>}</td>
                <td className="net-details">
                  {i.role === "rig" && <>
                    {link && (
                      <div>
                        {link.speed_mbps ? `${link.speed_mbps} Mb/s ${link.duplex ?? ""}` : "no link speed"}
                        {issues.length ? <span className="val--warn"> · ▲ {issues.join(", ")}</span> : null}
                        <span className="sync-muted"> · rx {fmtBytes(link.rx_bytes_1h)} / tx {fmtBytes(link.tx_bytes_1h)} last {fmtSpan(link.window_s || 0)}</span>
                      </div>
                    )}
                    {pool ? (
                      <div className="sync-muted">
                        DHCP {pool.start} – {pool.end} ({pool.in_use} of {pool.size} in use)
                        {pool.lease_time ? ` · lease ${pool.lease_time}` : ""}
                        {pool.gateway ? ` · hands out gateway ${pool.gateway}` : ""}
                        {pool.authoritative ? " · authoritative" : ""}
                      </div>
                    ) : (
                      <div className="val--warn">No DHCP pool found in /etc/dnsmasq.conf</div>
                    )}
                  </>}
                  {i.role === "uplink" && i.default_route && (
                    <div className="sync-muted">Default route via {i.default_route}. Not part of the rig network.</div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data.share && (
        <p className="sync-muted sync-footnote">
          Exports go to {data.share.kind === "nas" ? `the NAS at ${data.share.ip}` : "this controller"}
          {data.share.path ? ` (${data.share.path})` : ""}
          {data.share.health?.status ? ` · ${data.share.health.status}` : ""}
          {data.share.health?.error ? ` · ${data.share.health.error}` : ""}
        </p>
      )}
    </section>
  );
}

function ReleaseModal({ host, onCancel, onConfirm }) {
  const { name } = hostName(host);
  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <p className="actions-modal__title">Release DHCP lease?</p>
        <p className="modal-subtext">
          Removes the controller's lease for <b>{name}</b> ({host.ip}, <span className="net-mono">{host.mac}</span>),
          so the address goes back into the pool.
        </p>
        <p className="modal-subtext">
          The controller pings it again first and refuses if it answers. If the device comes back
          later it just asks for a new lease, possibly with a different address.
        </p>
        <div className="modal-buttons">
          <button className="reset-button" type="button" onClick={onConfirm}>Release lease</button>
          <button className="save-button" type="button" onClick={onCancel}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

function HostsTable({ data, onReleased }) {
  const loggedIn = useIsLoggedIn();
  const [filter, setFilter] = useState("all");
  const [confirmHost, setConfirmHost] = useState(null);
  const [releasing, setReleasing] = useState(null); // ip in flight
  const [result, setResult] = useState(null);

  useEffect(() => {
    const onResult = (r) => {
      setReleasing(null);
      setResult(r);
      onReleased();
    };
    socket.on("release_dhcp_lease_result", onResult);
    return () => socket.off("release_dhcp_lease_result", onResult);
  }, [onReleased]);

  const doRelease = () => {
    const h = confirmHost;
    setConfirmHost(null);
    setResult(null);
    setReleasing(h.ip);
    socket.emit("release_dhcp_lease", { ip: h.ip, mac: h.mac });
  };
  const now = data.generated_at;
  const rows = useMemo(() => (data.hosts || []).map((h) => ({ h, notes: hostNotes(h) })), [data.hosts]);
  const shown = rows.filter(({ h, notes }) =>
    filter === "all" ? true
      : filter === "module" ? h.kind === "module"
        : filter === "attention" ? notes.length > 0
          : h.kind !== "module");
  const attention = rows.filter((r) => r.notes.length).length;

  return (
    <section className="sync-section">
      <div className="sync-section__head">
        <h3>Hosts on the rig network</h3>
        <span className="sync-muted">Every DHCP lease and ARP neighbour on eth0, plus registered modules</span>
      </div>
      {result && (
        <div className={result.ok ? "net-result net-result--ok" : "sync-error"} role="status">
          {result.ok ? "✓ " : "✕ "}
          {result.ok ? `Released the lease for ${result.hostname || result.ip}` : `Couldn't release ${result.ip ?? "the lease"}`}
          {result.ok ? "" : `: ${result.detail}`}
          <button type="button" className="net-dismiss" onClick={() => setResult(null)} aria-label="Dismiss">×</button>
        </div>
      )}
      <div className="sync-window net-filter" role="group" aria-label="Filter hosts">
        {FILTERS.map(([k, label]) => (
          <button key={k} type="button"
                  className={`sync-window__btn ${filter === k ? "sync-window__btn--active" : ""}`}
                  aria-pressed={filter === k} onClick={() => setFilter(k)}>
            {label}{k === "attention" && attention ? ` (${attention})` : ""}
          </button>
        ))}
      </div>
      <div className="system-table-wrapper">
        <table className="system-table sync-table net-hosts">
          <thead>
            <tr>
              <th>Device</th>
              <th>Kind</th>
              <th>IP</th>
              <th>MAC</th>
              <th>DHCP lease</th>
              <th title="Last reply; median and loss over the last 5 min">Ping</th>
              <th>Last hour</th>
              <th title="Modules only: eth0 link speed and errors in the last hour">Link</th>
              <th title="Modules only: gap between heartbeats over the last 15 min">Heartbeat</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {shown.length === 0 && (
              <tr><td colSpan={10} className="system-table__empty">No hosts match</td></tr>
            )}
            {shown.map(({ h, notes }) => {
              const { name, sub } = hostName(h);
              const kind = KIND[h.kind] ?? KIND.other;
              const lease = leaseText(h.lease, now);
              const m = h.module;
              const link = m?.link;
              const issues = linkIssues(link);
              // Speed/duplex already show on the first line; the rest are counters.
              const counterIssues = issues.filter((i) => !i.includes("Mb/s") && !i.includes("duplex"));
              const hb = m?.heartbeat;
              const p = h.ping;
              const rel = releaseState(h, loggedIn, data.dhcp_release_available);
              return (
                <tr key={h.ip} className={!h.reachable ? "sync-row--stale" : undefined}>
                  <td>
                    <span className="device-name">{name}</span>
                    {sub && <span className="device-id">{sub}</span>}
                    {notes.length > 0 && (
                      <div className="net-notes">{notes.map((n) => <span key={n} className="net-note">▲ {n}</span>)}</div>
                    )}
                  </td>
                  <td><StatusChip cls={kind.cls} label={kind.label} /></td>
                  <td className="net-mono">{h.ip}</td>
                  <td>
                    <span className="net-mono">{h.mac ?? "–"}</span>
                    {(h.vendor || h.randomised) && (
                      <span className="device-id">{h.vendor ?? "randomised (phone/laptop)"}</span>
                    )}
                  </td>
                  <td className={lease.cls}>{lease.text}</td>
                  <td>
                    {!p ? <span className="cell--muted">not pinged yet</span>
                      : p.last_ms == null
                        ? <StatusChip cls="danger" label="✕ no reply" title={p.last_reply_at ? `Last reply ${fmtAgo(p.last_reply_at)}` : "No reply this hour"} />
                        : <>
                          <div>{fmtMs(p.last_ms)}</div>
                          <div className="sync-muted">
                            med {fmtMs(p.median_ms)}{p.loss_pct ? ` · ${p.loss_pct}% loss` : ""}
                          </div>
                        </>}
                  </td>
                  <td><RttSpark history={p?.history} /></td>
                  <td>
                    {!m ? <span className="cell--muted">–</span>
                      : !link ? <span className="cell--muted" title="Module software predates link reporting">not reported</span>
                        : <>
                          <div className={issues.length ? "val--warn" : undefined}>
                            {link.speed_mbps ? `${link.speed_mbps} Mb/s` : "no link"}{link.duplex && link.duplex !== "full" ? ` ${link.duplex}` : ""}
                          </div>
                          {counterIssues.length > 0 && (
                            <div className="val--warn">▲ {counterIssues.join(", ")}</div>
                          )}
                        </>}
                  </td>
                  <td>
                    {!m ? <span className="cell--muted">–</span> : <>
                      <div>{fmtAgo(m.last_heartbeat)}</div>
                      {hb && (
                        <div className="sync-muted" title={`${hb.beats} heartbeats in the last 15 min; longest gap ${hb.max_gap_s} s`}>
                          every {hb.mean_gap_s.toFixed(1)} s ± {hb.jitter_s.toFixed(1)}
                        </div>
                      )}
                    </>}
                  </td>
                  <td>
                    {rel && (
                      <button type="button" className="action-menu-btn"
                              disabled={!rel.enabled || releasing === h.ip}
                              title={rel.reason}
                              onClick={() => setConfirmHost(h)}>
                        {releasing === h.ip ? "Releasing…" : "Release lease"}
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {confirmHost && (
        <ReleaseModal host={confirmHost} onCancel={() => setConfirmHost(null)} onConfirm={doRelease} />
      )}
    </section>
  );
}

// ── Panel ────────────────────────────────────────────────────────────────────

const askOverview = () => socket.emit("get_network_overview");

export default function NetworkPanel() {
  const [data, setData] = useState(null);
  const [, setTick] = useState(0);

  useEffect(() => {
    const onData = (d) => setData(d);
    const ask = () => socket.emit("get_network_overview");
    socket.on("network_overview", onData);
    socket.on("connect", ask);
    ask();
    const id = setInterval(ask, POLL_MS);
    const tick = setInterval(() => setTick((n) => n + 1), 5000);
    return () => {
      clearInterval(id);
      clearInterval(tick);
      socket.off("network_overview", onData);
      socket.off("connect", ask);
    };
  }, []);

  return (
    <div className="sync-panel">
      <div className="sync-toolbar">
        <span className="sync-muted">
          {data?.generated_at ? `Updated ${fmtAgo(data.generated_at)} · hosts pinged every 10 s` : "Loading…"}
        </span>
        <button type="button" className="refresh-btn" onClick={() => socket.emit("get_network_overview")}>
          Refresh
        </button>
      </div>
      {data?.error && <div className="sync-error" role="alert">Couldn't load network data: {data.error}</div>}
      {data && !data.error && <>
        <Summary data={data} />
        <Interfaces data={data} />
        <HostsTable data={data} onReleased={askOverview} />
      </>}
    </div>
  );
}
