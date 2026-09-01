import { useMemo, useEffect, useState } from "react";
import useHealth from "/src/hooks/useHealth";
import useModules from "/src/hooks/useModules";
import socket from "/src/socket";
import ClockModal from "../../components/ClockModal/ClockModal";
import ModuleActionsMenu from "../../components/ModuleActionsMenu/ModuleActionsMenu";
import useIsLoggedIn from "/src/hooks/useIsLoggedIn";
import { triggerDownload } from "../Recording/sessionFormat";
import "./System.css";

// ── Helpers ───────────────────────────────────────────────────────────────────

function timeAgo(ts) {
  if (!ts) return "-";
  const secs = Math.floor(Date.now() / 1000 - ts);
  if (secs < 5)   return "just now";
  if (secs < 60)  return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  return `${Math.floor(secs / 3600)}h ago`;
}

function fmt(val, unit, decimals = 0) {
  if (val == null) return <span className="cell--muted">-</span>;
  return `${Number(val).toFixed(decimals)}${unit}`;
}

// Pi `vcgencmd get_throttled` bitmask -> short flag lists (mirror of
// src/shared/health.py decode_throttled; kept inline to avoid a shared JS dep).
function decodeThrottled(v) {
  if (!v) return { now: [], sinceBoot: [] };
  const bits = { 0: "under-voltage", 1: "freq-capped", 2: "throttled", 3: "soft-temp-limit" };
  const now = [], sinceBoot = [];
  for (const b of [0, 1, 2, 3]) {
    if (v & (1 << b)) now.push(bits[b]);
    if (v & (1 << (b + 16))) sinceBoot.push(bits[b]);
  }
  return { now, sinceBoot };
}

function tempCell(t, throttled) {
  const { now, sinceBoot } = decodeThrottled(throttled);
  const marker = now.length ? (
    <span className="val--danger" title={`Throttling now: ${now.join(", ")}`}> ⚡</span>
  ) : sinceBoot.length ? (
    <span className="val--warn" title={`Occurred since boot: ${sinceBoot.join(", ")}`}> ⚡</span>
  ) : null;
  if (t == null) {
    return marker ? <span>-{marker}</span> : <span className="cell--muted">-</span>;
  }
  const cls = now.length || t >= 75 ? "val--danger" : t >= 60 ? "val--warn" : "val--ok";
  return <span className={cls}>{t.toFixed(1)}°C{marker}</span>;
}

function pctCell(pct, warnAt = 70, dangerAt = 85) {
  if (pct == null) return <span className="cell--muted">-</span>;
  const cls = pct >= dangerAt ? "val--danger" : pct >= warnAt ? "val--warn" : "";
  return <span className={cls}>{pct.toFixed(1)}%</span>;
}

function cpuCell(pct) {
  if (pct == null) return <span className="cell--muted">-</span>;
  const cls = pct >= 80 ? "val--danger" : pct >= 60 ? "val--warn" : "";
  return <span className={cls}>{pct.toFixed(1)}%</span>;
}

function memoryCell(usagePct, totalGb) {
  if (usagePct == null) return <span className="cell--muted">-</span>;
  const cls = usagePct >= 85 ? "val--danger" : usagePct >= 70 ? "val--warn" : "";
  if (totalGb != null) {
    const usedGb = (totalGb * usagePct / 100).toFixed(1);
    return <span className={cls || undefined}>{`${usedGb} / ${totalGb.toFixed(1)} GB`}</span>;
  }
  return <span className={cls || undefined}>{`${usagePct.toFixed(1)}%`}</span>;
}

function diskCell(usedPct, usedGb, totalGb) {
  if (usedPct == null && usedGb == null) return <span className="cell--muted">-</span>;
  const cls = (usedPct ?? 0) >= 90 ? "val--danger" : (usedPct ?? 0) >= 75 ? "val--warn" : "";
  if (usedGb != null && totalGb != null) {
    return <span className={cls || undefined}>{`${usedGb.toFixed(1)} / ${totalGb.toFixed(1)} GB`}</span>;
  }
  return <span className={cls || undefined}>{`${(usedPct ?? 0).toFixed(1)}%`}</span>;
}

function ptpVal(ns) {
  if (ns == null) return <span className="cell--muted">-</span>;
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
  if (!status) return <span className="cell--muted">-</span>;
  const cls = status === "RECORDING" ? "activity-badge--recording"
            : status === "READY"     ? "activity-badge--ready"
            : status === "NOT_READY" ? "activity-badge--warn"
            : status === "FAULT"     ? "activity-badge--fault"
            : "activity-badge--idle";
  return <span className={`activity-badge ${cls}`}>{status}</span>;
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function System() {
  const loggedIn = useIsLoggedIn();
  const { moduleHealth, controllerHealth, refresh } = useHealth();
  const { modules, moduleList } = useModules();

  // Auto-refresh every 30 seconds
  useEffect(() => {
    const id = setInterval(refresh, 30000);
    return () => clearInterval(id);
  }, [refresh]);

  // Build rows grouped by group (defaults to type), then sorted by name within each group
  const groupedModuleRows = useMemo(() => {
    const rows = Object.entries(modules)
      .map(([id, m]) => ({
        id,
        name: m.name ?? id,
        group: m.group || m.type || id,
        ...moduleHealth[id],
      }))
      .sort((a, b) => a.group.localeCompare(b.group) || a.name.localeCompare(b.name));

    // Build [{group, rows:[]}] structure
    const groups = [];
    for (const row of rows) {
      if (!groups.length || groups[groups.length - 1].group !== row.group) {
        groups.push({ group: row.group, rows: [] });
      }
      groups[groups.length - 1].rows.push(row);
    }
    return groups;
  }, [moduleHealth, modules]);

  // ── Remove module ─────────────────────────────────────────────────────────
  // ── Bug report ────────────────────────────────────────────────────────────
  const [bugReportState, setBugReportState] = useState(null); // null | "collecting" | "ready"
  const [ptpHistoryHours, setPtpHistoryHours] = useState("24");
  const [showDiagnosticsModal, setShowDiagnosticsModal] = useState(false);

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
    setShowDiagnosticsModal(false);
    socket.emit("get_bug_report");
  };

  const handleDownloadPtpHistory = () => {
    const hours = parseFloat(ptpHistoryHours);
    const url = hours > 0
      ? `/api/ptp_history.csv?hours=${hours}`
      : "/api/ptp_history.csv?hours=all";
    triggerDownload(url, `ptp_history_${hours > 0 ? hours : "all"}h.csv`);
    setShowDiagnosticsModal(false);
  };

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

  const handleUpdateController = () => {
    setDeviceStatuses(prev => ({ ...prev, controller: "updating" }));
    setShowControllerActions(false);
    socket.emit("deploy_update_to_controller");
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
        // Sidebar's "Deploy to All Modules" targets modules only — the
        // controller is updated via its own deliberate action
        // (handleUpdateController → deploy_update_to_controller), so it is
        // not swept into this broadcast and gets no row here.
        setDeviceStatuses(prev => {
          const next = { ...prev };
          moduleList.forEach(m => { if (!next[m.id]) next[m.id] = "updating"; });
          return next;
        });
      }
    };
    const onDeployError = (data) => {
      setDeviceStatuses(prev => ({ ...prev, controller: { success: false, output: data.error } }));
    };
    const onAuthRequired = () => {
      // A gated action (e.g. "Update") was rejected because the session's
      // login lapsed server-side even though the client still shows
      // logged-in (AuthGate reopens the login form for this). Without this,
      // any row left in the transient "updating"/"restarting" state here
      // would spin forever, since only deploy_update_error used to clear it.
      setDeviceStatuses(prev => {
        const next = { ...prev };
        for (const id of Object.keys(next)) {
          if (next[id] === "updating" || next[id] === "restarting") {
            next[id] = { success: false, output: "Login required — please log in and retry" };
          }
        }
        return next;
      });
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
    socket.on("auth_required", onAuthRequired);
    socket.on("connect", onReconnect);
    return () => {
      socket.off("module_update_result", onModuleResult);
      socket.off("deploy_update_status", onDeployStatus);
      socket.off("deploy_update_error", onDeployError);
      socket.off("auth_required", onAuthRequired);
      socket.off("connect", onReconnect);
    };
  }, [moduleList]);

  const updateDevices = useMemo(() => {
    if (Object.keys(deviceStatuses).length === 0) return [];
    const rows = [];
    if (deviceStatuses.controller !== undefined) rows.push({ id: "controller", name: "Controller" });
    moduleList.forEach(m => { if (deviceStatuses[m.id] !== undefined) rows.push({ id: m.id, name: m.name }); });
    return rows;
  }, [deviceStatuses, moduleList]);

  // ── Mend all modules ──────────────────────────────────────────────────────
  // Broadcasts via send_command's module_id: "all", which only ever reaches
  // self.facade.get_modules() on the backend -- the controller is a
  // structurally separate object there and can never be swept into this,
  // same guarantee the existing Refresh button already relies on.
  const [mendAllTarget, setMendAllTarget] = useState(false);
  const [mendAllStatus, setMendAllStatus] = useState(null); // null | "sent"

  const handleMendAllConfirm = () => {
    socket.emit("send_command", { module_id: "all", type: "run_mend", params: {} });
    setMendAllTarget(false);
    setMendAllStatus("sent");
    setTimeout(() => setMendAllStatus(null), 5000);
  };

  return (
    <main className="system-page">
      <div className="system-header">
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
            onClick={() => setShowDiagnosticsModal(true)}
            disabled={bugReportState === "collecting"}
            title="Diagnostics bundle or PTP offset history"
          >
            {bugReportState === "collecting" ? "Collecting…" : "Export Diagnostics"}
          </button>
          <button
            className="refresh-btn"
            type="button"
            onClick={() => setMendAllTarget(true)}
            disabled={!loggedIn || moduleList.length === 0}
            title={!loggedIn ? "Login required for this action" : "Run mend.sh on every module (not the controller)"}
          >
            {mendAllStatus === "sent" ? "Mend requested" : "Mend All Modules"}
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
                  disabled={!loggedIn || Object.values(deviceStatuses).some(s => s === "updating" || s === "restarting")}
                  title={!loggedIn ? "Login required for this action" : stagedMeta ? `Staged: ${stagedMeta.version ?? "update"} - click to open update panel` : "Open update panel"}
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
              <td><span className="cell--muted">-</span></td>
              <td className="cell--muted">{controllerHealth?.ip ?? "-"}</td>
              <td className="cell--muted">{controllerHealth?.version ?? "-"}</td>
              <td>{cpuCell(controllerHealth?.cpu_usage)}</td>
              <td>{tempCell(controllerHealth?.cpu_temp, controllerHealth?.throttled)}</td>
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
                  <span className="cell--muted">-</span>
                )}
              </td>
              <td className="cell--muted">-</td>
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

            {/* Module rows, grouped by group (defaults to type) */}
            {groupedModuleRows.length === 0 ? (
              <tr>
                <td colSpan={12} className="system-table__empty">
                  No module health data yet - waiting for first heartbeat
                </td>
              </tr>
            ) : groupedModuleRows.map(({ group, rows }) => [
              <tr key={`group-${group}`} className="system-table__group-header">
                <td colSpan={12}>{group}</td>
              </tr>,
              ...rows.map((row) => {
                const isOnline = modules[row.id]?.online ?? false;
                const connStatus = isOnline ? (row.status ?? "online") : "offline";
                const moduleStatus = modules[row.id]?.status ?? null;
                return (
                  <tr key={row.id} className={!isOnline ? "system-table__offline-row" : ""}>
                    <td>
                      <span className="device-name">{row.name}</span>
                      {isOnline && row.audio_clip_pct != null && row.audio_clip_pct >= 0.5 && (
                        <span className="val--danger" style={{ marginLeft: 6, fontWeight: 600 }}
                          title="Audio is clipping on the loudest AudioMoth — lower the gain">
                          CLIP {row.audio_clip_pct.toFixed(1)}%
                        </span>
                      )}
                      {isOnline && row.frame_clip_pct != null && row.frame_clip_pct >= 5 && (
                        <span className="val--danger" style={{ marginLeft: 6, fontWeight: 600 }}
                          title="Frame is over/under-exposed — check exposure and gain">
                          EXPOSURE {row.frame_clip_pct.toFixed(0)}%
                        </span>
                      )}
                      <span className="device-id">{row.id}</span>
                    </td>
                    <td>{connectionCell(connStatus)}</td>
                    <td>{isOnline ? activityCell(moduleStatus) : <span className="cell--muted">-</span>}</td>
                    <td className="cell--muted">{modules[row.id]?.ip ?? "-"}</td>
                    <td className="cell--muted">{modules[row.id]?.version ?? "-"}</td>
                    <td>{isOnline ? cpuCell(row.cpu_usage)    : <span className="cell--muted">-</span>}</td>
                    <td>{isOnline ? tempCell(row.cpu_temp, row.throttled) : <span className="cell--muted">-</span>}</td>
                    <td>{isOnline ? memoryCell(row.memory_usage, row.memory_total_gb) : <span className="cell--muted">-</span>}</td>
                    <td>{isOnline ? diskCell(row.disk_space, row.disk_used_gb, row.disk_total_gb) : <span className="cell--muted">-</span>}</td>
                    <td>{isOnline ? ptpPairCell(row.ptp4l_offset_ns, row.phc2sys_offset_ns) : <span className="cell--muted">-</span>}</td>
                    <td className="cell--muted">{timeAgo(row.last_heartbeat)}</td>
                    <td>
                      <ModuleActionsMenu id={row.id} name={row.name} isOnline={isOnline} />
                    </td>
                  </tr>
                );
              }),
            ])}
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


      {showDiagnosticsModal && (
        <div className="modal-overlay" onClick={() => setShowDiagnosticsModal(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <p className="actions-modal__title">Export Diagnostics</p>

            <p className="modal-subtext">
              Logs, config, health and recording state from the controller and
              every online module, plus the last 24 h of PTP offset history, as
              a single ZIP.
            </p>
            <div className="modal-buttons">
              <button className="save-button" type="button"
                onClick={handleBugReport}
                disabled={bugReportState === "collecting"}>
                {bugReportState === "collecting" ? "Collecting…" : "Download diagnostics bundle"}
              </button>
            </div>

            <div className="actions-modal__divider" style={{ margin: "14px 0" }} />

            <p className="actions-modal__title">PTP offset history</p>
            <p className="modal-subtext">
              Per-module PTP offset samples as CSV, for plotting fleet sync
              quality over an unattended run. Blank or 0 exports the entire
              retained history.
            </p>
            <div className="modal-buttons" style={{ alignItems: "center", gap: "6px" }}>
              <input
                type="number"
                min="0"
                step="1"
                className="ptp-hours-input"
                value={ptpHistoryHours}
                onChange={(e) => setPtpHistoryHours(e.target.value)}
                title="How many hours of PTP history to include (blank/0 = entire retained history)"
              />
              <span>hours</span>
              <button className="save-button" type="button" onClick={handleDownloadPtpHistory}>
                Download CSV
              </button>
            </div>

            <div className="modal-buttons" style={{ marginTop: "12px" }}>
              <button className="save-button" type="button" onClick={() => setShowDiagnosticsModal(false)}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {showControllerActions && (
        <div className="modal-overlay" onClick={() => setShowControllerActions(false)}>
          <div className="modal actions-modal" onClick={e => e.stopPropagation()}>
            <p className="actions-modal__title">Controller</p>
            <div className="actions-modal__list">
              <button type="button" className="actions-modal__item"
                onClick={() => { setShowDiagnosticsModal(true); setShowControllerActions(false); }}
                disabled={bugReportState === "collecting"}>
                <span>{bugReportState === "collecting" ? "Collecting…" : "Export Diagnostics"}</span>
                <span className="actions-modal__hint">Diagnostics bundle, or PTP offset history</span>
              </button>
              <div className="actions-modal__divider" />
              <button type="button" className="actions-modal__item" disabled={!loggedIn}
                title={loggedIn ? undefined : "Login required for this action"}
                onClick={() => { setShowClockModal(true); setShowControllerActions(false); }}>
                <span>Set Time</span>
                <span className="actions-modal__hint">Manually set the controller clock</span>
              </button>
              {stagedMeta && (
                <button type="button" className="actions-modal__item" disabled={!loggedIn}
                  title={loggedIn ? undefined : "Login required for this action"}
                  onClick={handleUpdateController}>
                  <span>Update</span>
                  <span className="actions-modal__hint">Deploy staged package {stagedMeta.version ?? ""} to the controller only</span>
                </button>
              )}
              <button type="button" className="actions-modal__item" disabled={!loggedIn}
                title={loggedIn ? undefined : "Login required for this action"}
                onClick={() => { setControllerActionTarget("restart_service"); setShowControllerActions(false); }}>
                <span>Restart service</span>
                <span className="actions-modal__hint">Restarts the SAVIOUR program - controller does not reboot, reconnects automatically</span>
              </button>
              <button type="button" className="actions-modal__item" disabled={!loggedIn}
                title={loggedIn ? undefined : "Login required for this action"}
                onClick={() => { setControllerActionTarget("reboot"); setShowControllerActions(false); }}>
                <span>Reboot</span>
                <span className="actions-modal__hint">Reboots the controller Pi - reconnects automatically</span>
              </button>
              <div className="actions-modal__divider" />
              <button type="button" className="actions-modal__item actions-modal__item--danger" disabled={!loggedIn}
                title={loggedIn ? undefined : "Login required for this action"}
                onClick={() => { setControllerActionTarget("shutdown"); setShowControllerActions(false); }}>
                <span>Shutdown</span>
                <span className="actions-modal__hint">Powers off the controller - requires manual power cycle to restart</span>
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

      {showClockModal && (
        <ClockModal
          driftMs={controllerDriftMs}
          controllerTime={displayedControllerMs ? new Date(displayedControllerMs).toISOString() : controllerHealth?.controller_time}
          onClose={() => setShowClockModal(false)}
        />
      )}

      {mendAllTarget && (
        <div className="modal-overlay" onClick={() => setMendAllTarget(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <p>Run mend on all {moduleList.length} module(s)?</p>
            <p className="modal-subtext">
              Rebuilds dependencies, regenerates each module's service file, and restarts its
              service. Takes a few minutes per module; each will briefly go offline then
              reconnect automatically. The controller itself is not included -- use its own
              Actions menu to mend/update it separately.
            </p>
            <div className="modal-buttons">
              <button className="reset-button" type="button" onClick={handleMendAllConfirm}>Run mend</button>
              <button className="save-button" type="button" onClick={() => setMendAllTarget(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

    </main>
  );
}
