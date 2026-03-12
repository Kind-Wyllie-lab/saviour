import { useMemo } from "react";
import useHealth from "/src/hooks/useHealth";
import useModules from "/src/hooks/useModules";
import "./System.css";

// ── Helpers ───────────────────────────────────────────────────────────────────

function timeAgo(ts) {
  if (!ts) return "—";
  const secs = Math.floor(Date.now() / 1000 - ts);
  if (secs < 5)   return "just now";
  if (secs < 60)  return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  return `${Math.floor(secs / 3600)}h ago`;
}

function fmt(val, unit, decimals = 0) {
  if (val == null) return <span className="cell--muted">—</span>;
  return `${Number(val).toFixed(decimals)}${unit}`;
}

function tempCell(t) {
  if (t == null) return <span className="cell--muted">—</span>;
  const cls = t >= 75 ? "val--danger" : t >= 60 ? "val--warn" : "val--ok";
  return <span className={cls}>{t.toFixed(1)}°C</span>;
}

function pctCell(pct, warnAt = 70, dangerAt = 85) {
  if (pct == null) return <span className="cell--muted">—</span>;
  const cls = pct >= dangerAt ? "val--danger" : pct >= warnAt ? "val--warn" : "";
  return <span className={cls}>{pct.toFixed(1)}%</span>;
}

function cpuCell(pct) {
  if (pct == null) return <span className="cell--muted">—</span>;
  const cls = pct >= 80 ? "val--danger" : pct >= 60 ? "val--warn" : "";
  return <span className={cls}>{pct.toFixed(1)}%</span>;
}

function statusCell(status) {
  const cls = status === "online"    ? "status-dot--online"
            : status === "suspected" ? "status-dot--suspected"
            : "status-dot--offline";
  return (
    <span className="status-dot-wrapper" title={status}>
      <span className={`status-dot ${cls}`} />
      {status}
    </span>
  );
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function System() {
  const { moduleHealth, controllerHealth, refresh } = useHealth();
  const { modules } = useModules();

  // Build sorted rows: modules sorted by name
  const moduleRows = useMemo(() => {
    return Object.entries(moduleHealth)
      .map(([id, h]) => ({ id, name: modules[id]?.name ?? id, ...h }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [moduleHealth, modules]);

  return (
    <main className="system-page">
      <div className="system-header">
        <h2>System Health</h2>
        <button className="refresh-btn" type="button" onClick={refresh}>
          Refresh
        </button>
      </div>

      <div className="system-table-wrapper">
        <table className="system-table">
          <thead>
            <tr>
              <th>Device</th>
              <th>Status</th>
              <th>IP</th>
              <th>CPU</th>
              <th>Temp</th>
              <th>Memory</th>
              <th>Disk</th>
              <th>Last seen</th>
            </tr>
          </thead>
          <tbody>
            {/* Controller row */}
            <tr className="system-table__controller-row">
              <td>
                <span className="device-name">Controller</span>
              </td>
              <td>{statusCell("online")}</td>
              <td className="cell--muted">{controllerHealth?.ip ?? "—"}</td>
              <td>{cpuCell(controllerHealth?.cpu_usage)}</td>
              <td>{tempCell(controllerHealth?.cpu_temp)}</td>
              <td>{pctCell(controllerHealth?.memory_usage, 70, 85)}</td>
              <td>
                {controllerHealth?.disk_used_pct != null ? (
                  <span>
                    {pctCell(controllerHealth.disk_used_pct, 75, 90)}
                    {controllerHealth.disk_free_gb != null && (
                      <span className="cell--muted"> ({controllerHealth.disk_free_gb} GB free)</span>
                    )}
                  </span>
                ) : <span className="cell--muted">—</span>}
              </td>
              <td className="cell--muted">—</td>
            </tr>

            {/* Module rows */}
            {moduleRows.map((row) => (
              <tr key={row.id}>
                <td>
                  <span className="device-name">{row.name}</span>
                  <span className="device-id">{row.id}</span>
                </td>
                <td>{statusCell(row.status ?? "offline")}</td>
                <td className="cell--muted">{modules[row.id]?.ip ?? "—"}</td>
                <td>{cpuCell(row.cpu_usage)}</td>
                <td>{tempCell(row.cpu_temp)}</td>
                <td>{pctCell(row.memory_usage, 70, 85)}</td>
                <td>{pctCell(row.disk_space, 75, 90)}</td>
                <td className="cell--muted">{timeAgo(row.last_heartbeat)}</td>
              </tr>
            ))}

            {moduleRows.length === 0 && (
              <tr>
                <td colSpan={8} className="system-table__empty">
                  No module health data yet — waiting for first heartbeat
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </main>
  );
}
