import { useMemo, useEffect, useState, useRef } from "react";
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

function memoryCell(usagePct, totalGb) {
  if (usagePct == null) return <span className="cell--muted">—</span>;
  const cls = usagePct >= 85 ? "val--danger" : usagePct >= 70 ? "val--warn" : "";
  if (totalGb != null) {
    const usedGb = (totalGb * usagePct / 100).toFixed(1);
    return <span className={cls || undefined}>{`${usedGb} / ${totalGb.toFixed(1)} GB`}</span>;
  }
  return <span className={cls || undefined}>{`${usagePct.toFixed(1)}%`}</span>;
}

function diskCell(usedPct, usedGb, totalGb) {
  if (usedPct == null && usedGb == null) return <span className="cell--muted">—</span>;
  const cls = (usedPct ?? 0) >= 90 ? "val--danger" : (usedPct ?? 0) >= 75 ? "val--warn" : "";
  if (usedGb != null && totalGb != null) {
    return <span className={cls || undefined}>{`${usedGb.toFixed(1)} / ${totalGb.toFixed(1)} GB`}</span>;
  }
  return <span className={cls || undefined}>{`${(usedPct ?? 0).toFixed(1)}%`}</span>;
}

function ptpVal(ns) {
  if (ns == null) return <span className="cell--muted">—</span>;
  const abs = Math.abs(ns);
  const cls = abs >= 10000 ? "val--danger" : abs >= 1000 ? "val--warn" : "val--ok";
  const display = abs >= 1000
    ? `${(ns / 1000).toFixed(1)} µs`
    : `${Math.round(ns)} ns`;
  return <span className={cls}>{display}</span>;
}

function ptpPairCell(ptp4l_ns, phc2sys_ns) {
  return (
    <div className="ptp-pair">
      <div className="ptp-pair__row">
        <span className="ptp-pair__label">ptp4l</span>
        {ptpVal(ptp4l_ns)}
      </div>
      <div className="ptp-pair__row">
        <span className="ptp-pair__label">phc2sys</span>
        {ptpVal(phc2sys_ns)}
      </div>
    </div>
  );
}

function connectionCell(status) {
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

function activityCell(status) {
  if (!status) return <span className="cell--muted">—</span>;
  const cls = status === "RECORDING" ? "activity-badge--recording"
            : status === "READY"     ? "activity-badge--ready"
            : status === "NOT_READY" ? "activity-badge--warn"
            : status === "FAULT"     ? "activity-badge--fault"
            : "activity-badge--idle";
  return <span className={`activity-badge ${cls}`}>{status}</span>;
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
    return Object.entries(modules)
      .map(([id, m]) => ({ id, name: m.name ?? id, ...moduleHealth[id] }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [moduleHealth, modules]);

  // ── Remove module ─────────────────────────────────────────────────────────
  // ── Bug report ────────────────────────────────────────────────────────────
  const [bugReportState, setBugReportState] = useState(null); // null | "collecting" | "ready"

  useEffect(() => {
    const onStatus = ({ status }) => {
      if (status === "collecting") setBugReportState("collecting");
    };
    const onReady = ({ token, filename }) => {
      setBugReportState(null);
      const a = document.createElement("a");
      a.href = `/api/bug_report/${token}`;
      a.download = filename;
      a.click();
    };
    socket.on("bug_report_status", onStatus);
    socket.on("bug_report_ready", onReady);
    return () => {
      socket.off("bug_report_status", onStatus);
      socket.off("bug_report_ready", onReady);
    };
  }, []);

  const handleBugReport = () => {
    setBugReportState("collecting");
    socket.emit("get_bug_report");
  };

  const [removeTarget, setRemoveTarget] = useState(null); // { id, name, online }

  const handleRemoveConfirm = () => {
    if (!removeTarget) return;
    socket.emit("remove_module", { id: removeTarget.id });
    setRemoveTarget(null);
  };

  // ── Module actions modal ──────────────────────────────────────────────────
  const [actionTarget, setActionTarget] = useState(null); // { id, name, isOnline }
  const [rebootTarget, setRebootTarget] = useState(null);
  const [restartTarget, setRestartTarget] = useState(null);

  const handleRebootConfirm = () => {
    if (!rebootTarget) return;
    socket.emit("send_command", { module_id: rebootTarget.id, type: "reboot", params: {} });
    setRebootTarget(null);
  };

  const handleRestartConfirm = () => {
    if (!restartTarget) return;
    socket.emit("send_command", { module_id: restartTarget.id, type: "restart_service", params: {} });
    setRestartTarget(null);
  };

  // ── Shutdown module ───────────────────────────────────────────────────────
  const [shutdownTarget, setShutdownTarget] = useState(null); // { id, name }
  const [shutdownStates, setShutdownStates] = useState({}); // { module_id: "sent" | "acked" }
  const shutdownTimers = useRef({});

  const clearShutdownState = (id) => {
    setShutdownStates(prev => { const n = { ...prev }; delete n[id]; return n; });
    clearTimeout(shutdownTimers.current[id]);
    delete shutdownTimers.current[id];
  };

  const handleShutdownConfirm = () => {
    if (!shutdownTarget) return;
    const id = shutdownTarget.id;
    socket.emit("send_command", { module_id: id, type: "shutdown", params: {} });
    setShutdownStates(prev => ({ ...prev, [id]: "sent" }));
    setShutdownTarget(null);
    // Fallback: clear after 90 s (heartbeat timeout) in case the offline
    // transition never fires in this browser session
    clearTimeout(shutdownTimers.current[id]);
    shutdownTimers.current[id] = setTimeout(() => clearShutdownState(id), 90000);
  };

  useEffect(() => {
    const onAck = ({ module_id }) => {
      setShutdownStates(prev => ({ ...prev, [module_id]: "acked" }));
    };
    socket.on("module_shutdown_ack", onAck);
    return () => socket.off("module_shutdown_ack", onAck);
  }, []);

  // Clear shutdown state once a module goes offline (shutdown complete)
  useEffect(() => {
    setShutdownStates(prev => {
      const updated = { ...prev };
      let changed = false;
      Object.keys(updated).forEach(id => {
        if (!modules[id]?.online) {
          delete updated[id];
          changed = true;
          clearTimeout(shutdownTimers.current[id]);
          delete shutdownTimers.current[id];
        }
      });
      return changed ? updated : prev;
    });
  }, [modules]);

  // ── Controller actions ────────────────────────────────────────────────────
  const [showControllerActions, setShowControllerActions] = useState(false);
  const [controllerActionTarget, setControllerActionTarget] = useState(null); // "restart_service" | "reboot" | "shutdown"

  const handleControllerActionConfirm = () => {
    if (!controllerActionTarget) return;
    if (controllerActionTarget === "restart_service") {
      socket.emit("restart_saviour_controller_service");
      setDeviceStatuses({ controller: "restarting" });
    } else if (controllerActionTarget === "reboot") {
      socket.emit("reboot_controller");
    } else if (controllerActionTarget === "shutdown") {
      socket.emit("shutdown_controller");
    }
    setControllerActionTarget(null);
  };

  // ── Set controller time ───────────────────────────────────────────────────
  const [showClockModal, setShowClockModal] = useState(false);

  // Anchor controller time when health arrives, then tick every second so the
  // displayed time and drift stay live between 30 s health polls.
  const [clockRef, setClockRef] = useState(null);
  useEffect(() => {
    if (controllerHealth?.controller_time) {
      setClockRef({
        controllerMs: new Date(controllerHealth.controller_time).getTime(),
        browserMs: Date.now(),
      });
    }
  }, [controllerHealth?.controller_time]);

  const [, setTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTick(n => n + 1), 1000);
    return () => clearInterval(t);
  }, []);

  const displayedControllerMs = clockRef
    ? clockRef.controllerMs + (Date.now() - clockRef.browserMs)
    : null;
  const controllerDriftMs = clockRef ? clockRef.controllerMs - clockRef.browserMs : null;

  // ── Update all devices (ZIP-based deploy) ────────────────────────────────
  const [stagedMeta, setStagedMeta] = useState(null); // { version, size, filename } or null
  const [deviceStatuses, setDeviceStatuses] = useState({}); // id → "updating" | "restarting" | { success, output }

  useEffect(() => {
    socket.emit("get_update_info");
    const onUpdateInfo = (data) => {
      setStagedMeta(data?.staged ?? null);
    };
    socket.on("update_info", onUpdateInfo);
    return () => socket.off("update_info", onUpdateInfo);
  }, []);

  useEffect(() => {
    const onModuleResult = (data) => {
      setDeviceStatuses(prev => ({ ...prev, [data.module_id]: { success: data.success, output: data.output } }));
    };
    const onDeployStatus = (data) => {
      if (data.stage === "modules_notified") {
        // Sidebar triggered a full deploy — initialise all module rows as updating
        setDeviceStatuses(prev => {
          const next = { ...prev, controller: "restarting" };
          moduleList.forEach(m => { if (!next[m.id]) next[m.id] = "updating"; });
          return next;
        });
      }
    };
    const onDeployError = (data) => {
      setDeviceStatuses(prev => ({ ...prev, controller: { success: false, output: data.error } }));
    };
    const onReconnect = () => {
      setDeviceStatuses(prev => {
        if (prev.controller === "restarting" || prev.controller === "updating") {
          return { ...prev, controller: { success: true, output: "Service restarted" } };
        }
        return prev;
      });
      socket.emit("get_update_info");
    };
    socket.on("module_update_result", onModuleResult);
    socket.on("deploy_update_status", onDeployStatus);
    socket.on("deploy_update_error", onDeployError);
    socket.on("connect", onReconnect);
    return () => {
      socket.off("module_update_result", onModuleResult);
      socket.off("deploy_update_status", onDeployStatus);
      socket.off("deploy_update_error", onDeployError);
      socket.off("connect", onReconnect);
    };
  }, [moduleList]);

  const handleDeployToModule = (moduleId) => {
    setDeviceStatuses(prev => ({ ...prev, [moduleId]: "updating" }));
    socket.emit("deploy_update_to_module", { module_id: moduleId });
  };

  const updateDevices = useMemo(() => {
    if (Object.keys(deviceStatuses).length === 0) return [];
    const rows = [];
    if (deviceStatuses.controller !== undefined) rows.push({ id: "controller", name: "Controller" });
    moduleList.forEach(m => { if (deviceStatuses[m.id] !== undefined) rows.push({ id: m.id, name: m.name }); });
    return rows;
  }, [deviceStatuses, moduleList]);

  return (
    <main className="system-page">
      <div className="system-header">
        <h2>System Health</h2>
        <div className="system-header-actions">
          <button className="refresh-btn" type="button" onClick={() => {
            refresh();
            socket.emit("send_command", { module_id: "all", type: "get_health", params: {} });
          }}>
            Refresh
          </button>
          <button
            className="refresh-btn"
            type="button"
            onClick={handleBugReport}
            disabled={bugReportState === "collecting"}
            title="Collect logs and system state from all devices and download as a ZIP"
          >
            {bugReportState === "collecting" ? "Collecting…" : "Export Bug Report"}
          </button>
        </div>
      </div>

      <div className="system-table-wrapper">
        <table className="system-table">
          <thead>
            <tr>
              <th>Device</th>
              <th>Connection</th>
              <th>Status</th>
              <th>IP</th>
              <th className="th--version">
                Version
                <button
                  className="th-update-btn"
                  type="button"
                  onClick={() => window.dispatchEvent(new CustomEvent("saviour:open-update-modal"))}
                  disabled={Object.values(deviceStatuses).some(s => s === "updating" || s === "restarting")}
                  title={stagedMeta ? `Staged: ${stagedMeta.version ?? "update"} — click to open update panel` : "Open update panel"}
                >
                  {Object.values(deviceStatuses).some(s => s === "updating" || s === "restarting")
                    ? "Deploying…"
                    : "Update"}
                </button>
              </th>
              <th>CPU</th>
              <th>Temp</th>
              <th>Memory</th>
              <th>Disk</th>
              <th>Time Sync</th>
              <th>Last seen</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {/* Controller row */}
            <tr className="system-table__controller-row">
              <td>
                <span className="device-name">Controller</span>
              </td>
              <td>{connectionCell(controllerHealth ? "online" : "suspected")}</td>
              <td><span className="cell--muted">—</span></td>
              <td className="cell--muted">{controllerHealth?.ip ?? "—"}</td>
              <td className="cell--muted">{controllerHealth?.version ?? "—"}</td>
              <td>{cpuCell(controllerHealth?.cpu_usage)}</td>
              <td>{tempCell(controllerHealth?.cpu_temp)}</td>
              <td>{memoryCell(controllerHealth?.memory_usage, controllerHealth?.memory_total_gb)}</td>
              <td>{diskCell(controllerHealth?.disk_used_pct, controllerHealth?.disk_used_gb, controllerHealth?.disk_total_gb)}</td>
              <td className="system-time-cell">
                {displayedControllerMs != null ? (
                  <>
                    <span className="system-time-value">
                      {new Date(displayedControllerMs).toISOString().slice(11, 19)} UTC
                    </span>
                    <span className="system-time-date">
                      {new Date(displayedControllerMs).toISOString().slice(0, 10)}
                      {controllerDriftMs != null && Math.abs(controllerDriftMs) >= 5000 && (
                        <span className={`hsw-drift ${Math.abs(controllerDriftMs) >= 120000 ? "val--danger" : "val--warn"}`}>
                          {" "}({Math.abs(controllerDriftMs) >= 60000
                            ? `${Math.round(Math.abs(controllerDriftMs) / 60000)}m`
                            : `${Math.round(Math.abs(controllerDriftMs) / 1000)}s`} drift)
                        </span>
                      )}
                    </span>
                  </>
                ) : (
                  <span className="cell--muted">—</span>
                )}
              </td>
              <td className="cell--muted">—</td>
              <td>
                <button
                  type="button"
                  className="action-menu-btn"
                  onClick={() => setShowControllerActions(true)}
                >
                  Actions ▾
                </button>
              </td>
            </tr>

            {/* Module rows */}
            {moduleRows.map((row) => {
              const isOnline = modules[row.id]?.online ?? false;
              const connStatus = isOnline ? (row.status ?? "online") : "offline";
              const moduleStatus = modules[row.id]?.status ?? null;
              return (
                <tr key={row.id} className={!isOnline ? "system-table__offline-row" : ""}>
                  <td>
                    <span className="device-name">{row.name}</span>
                    <span className="device-id">{row.id}</span>
                  </td>
                  <td>{connectionCell(connStatus)}</td>
                  <td>{isOnline ? activityCell(moduleStatus) : <span className="cell--muted">—</span>}</td>
                  <td className="cell--muted">{modules[row.id]?.ip ?? "—"}</td>
                  <td className="cell--muted">{modules[row.id]?.version ?? "—"}</td>
                  <td>{isOnline ? cpuCell(row.cpu_usage)    : <span className="cell--muted">—</span>}</td>
                  <td>{isOnline ? tempCell(row.cpu_temp)    : <span className="cell--muted">—</span>}</td>
                  <td>{isOnline ? memoryCell(row.memory_usage, row.memory_total_gb) : <span className="cell--muted">—</span>}</td>
                  <td>{isOnline ? diskCell(row.disk_space, row.disk_used_gb, row.disk_total_gb) : <span className="cell--muted">—</span>}</td>
                  <td>{isOnline ? ptpPairCell(row.ptp4l_offset_ns, row.phc2sys_offset) : <span className="cell--muted">—</span>}</td>
                  <td className="cell--muted">{timeAgo(row.last_heartbeat)}</td>
                  <td>
                    {shutdownStates[row.id] ? (
                      <span className="shutdown-progress">
                        {shutdownStates[row.id] === "acked" ? "Powering off…" : "Shutting down…"}
                      </span>
                    ) : (
                      <button
                        type="button"
                        className="action-menu-btn"
                        onClick={() => setActionTarget({ id: row.id, name: row.name, isOnline })}
                      >
                        Actions ▾
                      </button>
                    )}
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
      {/* ── Update results (shown only while/after update runs) ── */}
      {updateDevices.length > 0 && (
        <div className="system-update-section">
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
                  const isInProgress = s === "updating" || s === "restarting";
                  return (
                    <tr key={id} className={id === "controller" ? "system-table__controller-row" : ""}>
                      <td><span className="device-name">{name}</span></td>
                      <td>
                        {isInProgress
                          ? <span className="cell--muted">{s === "restarting" ? "Restarting…" : "Updating…"}</span>
                          : s?.success
                            ? <span className="val--ok">&#10003; Updated</span>
                            : <span className="val--danger">&#10007; Failed</span>
                        }
                      </td>
                      <td className="cell--muted update-output">
                        {s && !isInProgress ? s.output : ""}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}


      {showControllerActions && (
        <div className="modal-overlay" onClick={() => setShowControllerActions(false)}>
          <div className="modal actions-modal" onClick={e => e.stopPropagation()}>
            <p className="actions-modal__title">Controller</p>
            <div className="actions-modal__list">
              <button type="button" className="actions-modal__item"
                onClick={() => { setShowClockModal(true); setShowControllerActions(false); }}>
                <span>Set Time</span>
                <span className="actions-modal__hint">Manually set the controller clock</span>
              </button>
              <button type="button" className="actions-modal__item"
                onClick={() => { setControllerActionTarget("restart_service"); setShowControllerActions(false); }}>
                <span>Restart service</span>
                <span className="actions-modal__hint">Restarts the SAVIOUR program — controller does not reboot, reconnects automatically</span>
              </button>
              <button type="button" className="actions-modal__item"
                onClick={() => { setControllerActionTarget("reboot"); setShowControllerActions(false); }}>
                <span>Reboot</span>
                <span className="actions-modal__hint">Reboots the controller Pi — reconnects automatically</span>
              </button>
              <div className="actions-modal__divider" />
              <button type="button" className="actions-modal__item actions-modal__item--danger"
                onClick={() => { setControllerActionTarget("shutdown"); setShowControllerActions(false); }}>
                <span>Shutdown</span>
                <span className="actions-modal__hint">Powers off the controller — requires manual power cycle to restart</span>
              </button>
            </div>
            <div className="modal-buttons" style={{ marginTop: "8px" }}>
              <button className="save-button" type="button" onClick={() => setShowControllerActions(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {controllerActionTarget && (
        <div className="modal-overlay" onClick={() => setControllerActionTarget(null)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            {controllerActionTarget === "restart_service" && <>
              <p>Restart the controller service?</p>
              <p className="modal-subtext">The SAVIOUR program will restart. The controller will briefly disconnect then reconnect automatically.</p>
            </>}
            {controllerActionTarget === "reboot" && <>
              <p>Reboot the controller?</p>
              <p className="modal-subtext">The controller Pi will reboot. It will reconnect automatically after restart. Any active recording sessions will be interrupted.</p>
            </>}
            {controllerActionTarget === "shutdown" && <>
              <p>Shut down the controller?</p>
              <p className="modal-subtext modal-subtext--warn">The controller will power off. A manual power cycle is required to bring it back online. Any active recording sessions will be interrupted.</p>
            </>}
            <div className="modal-buttons">
              <button className="reset-button" type="button" onClick={handleControllerActionConfirm}>
                {controllerActionTarget === "restart_service" ? "Restart" : controllerActionTarget === "reboot" ? "Reboot" : "Shutdown"}
              </button>
              <button className="save-button" type="button" onClick={() => setControllerActionTarget(null)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {actionTarget && (
        <div className="modal-overlay" onClick={() => setActionTarget(null)}>
          <div className="modal actions-modal" onClick={e => e.stopPropagation()}>
            <p className="actions-modal__title">{actionTarget.name}</p>
            <div className="actions-modal__list">
              {actionTarget.isOnline ? (<>
                {stagedMeta && (
                  <button type="button" className="actions-modal__item"
                    onClick={() => { handleDeployToModule(actionTarget.id); setActionTarget(null); }}>
                    <span>Update</span>
                    <span className="actions-modal__hint">Deploy staged package {stagedMeta.version ?? ""} to this module only</span>
                  </button>
                )}
                <button type="button" className="actions-modal__item"
                  onClick={() => { setRestartTarget({ id: actionTarget.id, name: actionTarget.name }); setActionTarget(null); }}>
                  <span>Restart service</span>
                  <span className="actions-modal__hint">Restarts the SAVIOUR program — module does not reboot, reconnects automatically</span>
                </button>
                <button type="button" className="actions-modal__item"
                  onClick={() => { setRebootTarget({ id: actionTarget.id, name: actionTarget.name }); setActionTarget(null); }}>
                  <span>Reboot</span>
                  <span className="actions-modal__hint">Reboots the module — reconnects automatically</span>
                </button>
                <div className="actions-modal__divider" />
                <button type="button" className="actions-modal__item actions-modal__item--danger"
                  onClick={() => { setShutdownTarget({ id: actionTarget.id, name: actionTarget.name }); setActionTarget(null); }}>
                  <span>Shutdown</span>
                  <span className="actions-modal__hint">Powers off — reconnects when switched back on</span>
                </button>
              </>) : (
                <button type="button" className="actions-modal__item actions-modal__item--danger"
                  onClick={() => { setRemoveTarget({ id: actionTarget.id, name: actionTarget.name, online: false }); setActionTarget(null); }}>
                  <span>Remove</span>
                  <span className="actions-modal__hint">Remove offline module from tracking</span>
                </button>
              )}
            </div>
            <div className="modal-buttons" style={{ marginTop: "8px" }}>
              <button className="save-button" type="button" onClick={() => setActionTarget(null)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {rebootTarget && (
        <div className="modal-overlay" onClick={() => setRebootTarget(null)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <p>Reboot <strong>{rebootTarget.name}</strong>?</p>
            <p className="modal-subtext">The module will reboot and reconnect automatically. Any active recording will be interrupted.</p>
            <div className="modal-buttons">
              <button className="reset-button" type="button" onClick={handleRebootConfirm}>Reboot</button>
              <button className="save-button" type="button" onClick={() => setRebootTarget(null)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {restartTarget && (
        <div className="modal-overlay" onClick={() => setRestartTarget(null)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <p>Restart service on <strong>{restartTarget.name}</strong>?</p>
            <p className="modal-subtext">The saviour service will restart. The module will briefly go offline then reconnect automatically.</p>
            <div className="modal-buttons">
              <button className="reset-button" type="button" onClick={handleRestartConfirm}>Restart</button>
              <button className="save-button" type="button" onClick={() => setRestartTarget(null)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {shutdownTarget && (
        <div className="modal-overlay" onClick={() => setShutdownTarget(null)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <p>Shut down <strong>{shutdownTarget.name}</strong>?</p>
            <p className="modal-subtext">
              The module will power off. It will be re-added automatically when it comes back online.
            </p>
            <div className="modal-buttons">
              <button className="reset-button" type="button" onClick={handleShutdownConfirm}>
                Shutdown
              </button>
              <button className="save-button" type="button" onClick={() => setShutdownTarget(null)}>
                Cancel
              </button>
            </div>
          </div>
        </div>
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

      {showClockModal && (
        <ClockModal
          driftMs={controllerDriftMs}
          controllerTime={displayedControllerMs ? new Date(displayedControllerMs).toISOString() : controllerHealth?.controller_time}
          onClose={() => setShowClockModal(false)}
        />
      )}

    </main>
  );
}
