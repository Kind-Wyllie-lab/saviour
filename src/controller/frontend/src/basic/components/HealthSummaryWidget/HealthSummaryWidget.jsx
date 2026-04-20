import { Link } from "react-router-dom";
import useHealth from "/src/hooks/useHealth";
import useModules from "/src/hooks/useModules";
import "./HealthSummaryWidget.css";

function tempClass(t) {
  if (t == null) return "";
  if (t >= 75) return "val--danger";
  if (t >= 60) return "val--warn";
  return "val--ok";
}

function diskClass(pct) {
  if (pct == null) return "";
  if (pct >= 90) return "val--danger";
  if (pct >= 75) return "val--warn";
  return "val--ok";
}

// ptp4l_offset is in nanoseconds throughout the stack
function ptpClass(ns) {
  const abs = Math.abs(ns);
  if (abs >= 10000) return "val--danger";
  if (abs >= 1000)  return "val--warn";
  return "val--ok";
}

function ptpDisplay(ns) {
  const abs = Math.abs(ns);
  return abs >= 1000
    ? `${(ns / 1000).toFixed(1)} µs`
    : `${Math.round(ns)} ns`;
}

export default function HealthSummaryWidget() {
  const { moduleHealth, controllerHealth } = useHealth();
  const { modules } = useModules();

  const entries = Object.entries(moduleHealth);
  const online  = entries.filter(([, h]) => h.status === "online").length;
  const total   = entries.length;
  const offline = entries.filter(([, h]) => h.status === "offline");
  const suspected = entries.filter(([, h]) => h.status === "suspected");

  // Highest temp across modules + controller
  let maxTemp = null;
  let maxTempId = null;
  entries.forEach(([id, h]) => {
    if (h.cpu_temp != null && (maxTemp == null || h.cpu_temp > maxTemp)) {
      maxTemp = h.cpu_temp;
      maxTempId = modules[id]?.name ?? id;
    }
  });
  if (controllerHealth?.cpu_temp != null &&
      (maxTemp == null || controllerHealth.cpu_temp > maxTemp)) {
    maxTemp = controllerHealth.cpu_temp;
    maxTempId = "Controller";
  }

  // Highest disk usage across modules + controller
  let maxDisk = null;
  let maxDiskId = null;
  entries.forEach(([id, h]) => {
    if (h.disk_space != null && (maxDisk == null || h.disk_space > maxDisk)) {
      maxDisk = h.disk_space;
      maxDiskId = modules[id]?.name ?? id;
    }
  });
  if (controllerHealth?.disk_used_pct != null &&
      (maxDisk == null || controllerHealth.disk_used_pct > maxDisk)) {
    maxDisk = controllerHealth.disk_used_pct;
    maxDiskId = "Controller";
  }

  // Worst PTP offset across modules (nanoseconds, modules only — controller is PTP master)
  let worstPtp = null;
  let worstPtpId = null;
  entries.forEach(([id, h]) => {
    if (h.ptp4l_offset != null) {
      const abs = Math.abs(h.ptp4l_offset);
      if (worstPtp == null || abs > Math.abs(worstPtp)) {
        worstPtp = h.ptp4l_offset;
        worstPtpId = modules[id]?.name ?? id;
      }
    }
  });

  const hasIssues = offline.length > 0 || suspected.length > 0 ||
                    (maxTemp != null && maxTemp >= 60) ||
                    (maxDisk != null && maxDisk >= 75) ||
                    (worstPtp != null && Math.abs(worstPtp) >= 1000);

  return (
    <div className={`health-summary-widget card ${hasIssues ? "health-summary-widget--issues" : ""}`}>
      <div className="hsw-header">
        <h2>System Health</h2>
        <Link to="/system" className="hsw-view-link">View all →</Link>
      </div>

      <div className="hsw-row">
        <span className="hsw-label">Modules online</span>
        <span className={`hsw-value ${online < total ? "val--warn" : "val--ok"}`}>
          {online} / {total}
        </span>
      </div>

      {offline.length > 0 && (
        <div className="hsw-row hsw-alert">
          <span className="hsw-label">Offline</span>
          <span className="hsw-value val--danger">
            {offline.map(([id]) => modules[id]?.name ?? id).join(", ")}
          </span>
        </div>
      )}

      {suspected.length > 0 && (
        <div className="hsw-row hsw-alert">
          <span className="hsw-label">No heartbeat</span>
          <span className="hsw-value val--warn">
            {suspected.map(([id]) => modules[id]?.name ?? id).join(", ")}
          </span>
        </div>
      )}

      {maxTemp != null && (
        <div className="hsw-row">
          <span className="hsw-label">Hottest device</span>
          <span className={`hsw-value ${tempClass(maxTemp)}`}>
            {maxTemp}°C — {maxTempId}
          </span>
        </div>
      )}

      {maxDisk != null && (
        <div className="hsw-row">
          <span className="hsw-label">Fullest disk</span>
          <span className={`hsw-value ${diskClass(maxDisk)}`}>
            {maxDisk}% — {maxDiskId}
          </span>
        </div>
      )}

      {worstPtp != null && (
        <div className="hsw-row">
          <span className="hsw-label">PTP sync (worst)</span>
          <span className={`hsw-value ${ptpClass(worstPtp)}`}>
            {ptpDisplay(worstPtp)} — {worstPtpId}
          </span>
        </div>
      )}

      {total === 0 && !controllerHealth && (
        <p className="hsw-empty">No health data yet</p>
      )}
    </div>
  );
}
