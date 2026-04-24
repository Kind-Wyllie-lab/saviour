import { useMemo, useEffect, useState } from "react";
import useHealth from "/src/hooks/useHealth";
import useModules from "/src/hooks/useModules";
import socket from "/src/socket";
import ClockModal from "../../components/ClockModal/ClockModal";
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

function diskCell(usedPct, freeGb) {
  if (usedPct == null) return <span className="cell--muted">—</span>;
  return (
    <span>
      {pctCell(usedPct, 75, 90)}
      {freeGb != null && <span className="cell--muted"> ({freeGb} GB free)</span>}
    </span>
  );
}

function ptpCell(ns) {
  if (ns == null) return <span className="cell--muted">—</span>;
  const abs = Math.abs(ns);
  const cls = abs >= 10000 ? "val--danger" : abs >= 1000 ? "val--warn" : "val--ok";
  const display = abs >= 1000
    ? `${(ns / 1000).toFixed(1)} µs`
    : `${Math.round(ns)} ns`;
  return <span className={cls}>{display}</span>;
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
  const { modules, moduleList } = useModules();

  // Auto-refresh every 30 seconds
  useEffect(() => {
    const id = setInterval(refresh, 30000);
    return () => clearInterval(id);
  }, [refresh]);

  // Build sorted rows: modules sorted by name
  const moduleRows = useMemo(() => {
    return Object.entries(moduleHealth)
      .map(([id, h]) => ({ id, name: modules[id]?.name ?? id, ...h }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [moduleHealth, modules]);

  // ── Remove module ─────────────────────────────────────────────────────────
  const [removeTarget, setRemoveTarget] = useState(null); // { id, name, online }

  const handleRemoveConfirm = () => {
    if (!removeTarget) return;
    socket.emit("remove_module", { id: removeTarget.id });
    setRemoveTarget(null);
  };

  // ── Set controller time ───────────────────────────────────────────────────
  const [showClockModal, setShowClockModal] = useState(false);

  const controllerDriftMs = controllerHealth?.controller_time
    ? new Date(controllerHealth.controller_time).getTime() - Date.now()
    : null;

  // ── Update all devices ────────────────────────────────────────────────────
  const [showUpdateConfirm, setShowUpdateConfirm] = useState(false);
  const [deviceStatuses, setDeviceStatuses] = useState({}); // id → "updating" | { success, output }

  useEffect(() => {
    const onModuleResult = (data) => {
      setDeviceStatuses(prev => ({ ...prev, [data.module_id]: { success: data.success, output: data.output } }));
    };
    const onControllerResult = (data) => {
      setDeviceStatuses(prev => ({ ...prev, controller: { success: data.success, output: data.output } }));
    };
    socket.on("module_update_result", onModuleResult);
    socket.on("update_saviour_controller_result", onControllerResult);
    return () => {
      socket.off("module_update_result", onModuleResult);
      socket.off("update_saviour_controller_result", onControllerResult);
    };
  }, []);

  const handleUpdateAll = () => {
    const initial = { controller: "updating" };
    moduleList.forEach(m => { initial[m.id] = "updating"; });
    setDeviceStatuses(initial);
    setShowUpdateConfirm(false);
    socket.emit("update_saviour_controller");
    socket.emit("send_command", { module_id: "all", type: "update_saviour", params: {} });
  };

  const updateDevices = useMemo(() => {
    if (Object.keys(deviceStatuses).length === 0) return [];
    const rows = [{ id: "controller", name: "Controller" }];
    moduleList.forEach(m => rows.push({ id: m.id, name: m.name }));
    return rows;
  }, [deviceStatuses, moduleList]);

  return (
    <main className="system-page">
      <div className="system-header">
        <h2>System Health</h2>
        <button className="refresh-btn" type="button" onClick={() => {
          refresh();
          socket.emit("send_command", { module_id: "all", type: "get_health", params: {} });
        }}>
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
              <th>Version</th>
              <th>CPU</th>
              <th>Temp</th>
              <th>Memory</th>
              <th>Disk</th>
              <th>PTP offset</th>
              <th>Last seen</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {/* Controller row */}
            <tr className="system-table__controller-row">
              <td>
                <span className="device-name">Controller</span>
              </td>
              <td>{statusCell(controllerHealth ? "online" : "suspected")}</td>
              <td className="cell--muted">{controllerHealth?.ip ?? "—"}</td>
              <td className="cell--muted">{controllerHealth?.version ?? "—"}</td>
              <td>{cpuCell(controllerHealth?.cpu_usage)}</td>
              <td>{tempCell(controllerHealth?.cpu_temp)}</td>
              <td>{pctCell(controllerHealth?.memory_usage, 70, 85)}</td>
              <td>{diskCell(controllerHealth?.disk_used_pct, controllerHealth?.disk_free_gb)}</td>
              <td className="cell--muted">—</td>
              <td className="cell--muted">—</td>
              <td></td>
            </tr>

            {/* Module rows */}
            {moduleRows.map((row) => {
              const isOnline = modules[row.id]?.online ?? false;
              return (
                <tr key={row.id}>
                  <td>
                    <span className="device-name">{row.name}</span>
                    <span className="device-id">{row.id}</span>
                  </td>
                  <td>{statusCell(row.status ?? "offline")}</td>
                  <td className="cell--muted">{modules[row.id]?.ip ?? "—"}</td>
                  <td className="cell--muted">{modules[row.id]?.version ?? "—"}</td>
                  <td>{cpuCell(row.cpu_usage)}</td>
                  <td>{tempCell(row.cpu_temp)}</td>
                  <td>{pctCell(row.memory_usage, 70, 85)}</td>
                  <td>{pctCell(row.disk_space, 75, 90)}</td>
                  <td>{ptpCell(row.ptp4l_offset)}</td>
                  <td className="cell--muted">{timeAgo(row.last_heartbeat)}</td>
                  <td>
                    <button
                      type="button"
                      className="remove-btn"
                      onClick={() => setRemoveTarget({ id: row.id, name: row.name, online: isOnline })}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              );
            })}

            {moduleRows.length === 0 && (
              <tr>
                <td colSpan={10} className="system-table__empty">
                  No module health data yet — waiting for first heartbeat
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {/* ── Update all devices ── */}
      <div className="system-update-section">
        <div className="system-header">
          <h2>Update All Devices</h2>
          <button
            className="refresh-btn"
            type="button"
            onClick={() => setShowUpdateConfirm(true)}
            disabled={Object.values(deviceStatuses).some(s => s === "updating")}
          >
            {Object.values(deviceStatuses).some(s => s === "updating") ? "Updating…" : "Update All"}
          </button>
        </div>

        {updateDevices.length > 0 && (
          <div className="system-table-wrapper">
            <table className="system-table">
              <thead>
                <tr>
                  <th>Device</th>
                  <th>Result</th>
                  <th>Output</th>
                </tr>
              </thead>
              <tbody>
                {updateDevices.map(({ id, name }) => {
                  const s = deviceStatuses[id];
                  return (
                    <tr key={id} className={id === "controller" ? "system-table__controller-row" : ""}>
                      <td><span className="device-name">{name}</span></td>
                      <td>
                        {s === "updating"
                          ? <span className="cell--muted">Updating…</span>
                          : s?.success
                            ? <span className="val--ok">&#10003; Updated</span>
                            : <span className="val--danger">&#10007; Failed</span>
                        }
                      </td>
                      <td className="cell--muted update-output">
                        {s && s !== "updating" ? s.output : ""}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── Controller Time ── */}
      <div className="system-update-section">
        <div className="system-header">
          <h2>Controller Time</h2>
          <button className="refresh-btn" type="button" onClick={() => setShowClockModal(true)}>
            Set Time
          </button>
        </div>
        {controllerHealth?.controller_time ? (
          <p className="system-clock-display">
            {new Date(controllerHealth.controller_time).toUTCString().replace(/GMT$/, "UTC")}
            {controllerDriftMs != null && Math.abs(controllerDriftMs) >= 5000 && (
              <span className={`hsw-drift ${Math.abs(controllerDriftMs) >= 120000 ? "val--danger" : "val--warn"}`}>
                {" "}({Math.abs(controllerDriftMs) >= 60000
                  ? `${Math.round(Math.abs(controllerDriftMs) / 60000)}m`
                  : `${Math.round(Math.abs(controllerDriftMs) / 1000)}s`} vs browser)
              </span>
            )}
          </p>
        ) : (
          <p className="cell--muted">Waiting for health data…</p>
        )}
      </div>

      {showClockModal && (
        <ClockModal
          driftMs={controllerDriftMs}
          controllerTime={controllerHealth?.controller_time}
          onClose={() => setShowClockModal(false)}
        />
      )}

      {removeTarget && (
        <div className="modal-overlay" onClick={() => setRemoveTarget(null)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <p>Remove <strong>{removeTarget.name}</strong> from tracking?</p>
            {removeTarget.online ? (
              <p className="modal-subtext modal-subtext--warn">
                This module is currently <strong>online</strong>. A shutdown command will be sent before it is removed — this will stop any active recording and power off the device. If it reconnects later it will be re-added automatically.
              </p>
            ) : (
              <p className="modal-subtext">
                This module is offline and will be removed from the system. If it comes back online it will be re-added automatically.
              </p>
            )}
            <div className="modal-buttons">
              <button className="reset-button" type="button" onClick={handleRemoveConfirm}>
                {removeTarget.online ? "Shutdown & Remove" : "Remove"}
              </button>
              <button className="save-button" type="button" onClick={() => setRemoveTarget(null)}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {showUpdateConfirm && (
        <div className="modal-overlay" onClick={() => setShowUpdateConfirm(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <p>Update SAVIOUR on all <strong>{moduleList.length + 1}</strong> devices?</p>
            <p className="modal-subtext">Runs <code>git pull</code> on the controller and all connected modules. Restart services afterwards to apply changes.</p>
            <div className="modal-buttons">
              <button className="save-button" type="button" onClick={handleUpdateAll}>Update All</button>
              <button className="reset-button" type="button" onClick={() => setShowUpdateConfirm(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
