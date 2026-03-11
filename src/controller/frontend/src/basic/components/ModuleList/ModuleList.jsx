import { useState } from "react";
import socket from "/src/socket";
import "./ModuleList.css";

function ModuleList({ modules }) {
  const [showRebootConfirm, setShowRebootConfirm] = useState(false);

  const handleUpdateAll = () => {
    socket.emit("send_command", { module_id: "all", type: "update_saviour", params: {} });
  };

  const handleRebootAll = () => {
    socket.emit("send_command", { module_id: "all", type: "reboot", params: {} });
    setShowRebootConfirm(false);
  };

  // Shorten "v0.1.6-8-gabcd1234" to "v0.1.6 +8" so it fits the column.
  const formatVersion = (v) => {
    if (v == null || typeof v !== "string" || v === "UNKNOWN_VERSION") return "—";
    const parts = v.split("-");
    if (parts.length === 1) return parts[0]; // clean tag e.g. "v0.1.6"
    return `${parts[0]} +${parts[1]}`;       // e.g. "v0.1.6 +8"
  };

  return (
    <div className="module-list-container card">
      <h2>Module List</h2>

      <div className="module-list">
        {/* Header row */}
        <div className="module-list-header">
          <span>Module</span>
          <span>Status</span>
          <span>IP</span>
          <span>Version</span>
        </div>

        {modules.map((module) => (
          <div className="module-list-item" key={module.id}>
            <div className="module-list-item-start">
              <div className={`status-icon ${module.status.toLowerCase()}`} />
              <span>{module.name} ({module.type})</span>
            </div>
            <span>{module.status}</span>
            <span>{module.ip}</span>
            <span
              className="module-version"
              title={module.version}
            >
              {formatVersion(module.version)}
            </span>
          </div>
        ))}
      </div>

      {modules.length > 0 && (
        <div className="bulk-actions">
          <button className="bulk-btn" type="button" onClick={handleUpdateAll}>
            Update All
          </button>
          <button className="bulk-btn bulk-btn--danger" type="button" onClick={() => setShowRebootConfirm(true)}>
            Reboot All
          </button>
        </div>
      )}

      {showRebootConfirm && (
        <div className="modal-overlay" onClick={() => setShowRebootConfirm(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <p>Reboot all <strong>{modules.length}</strong> module{modules.length !== 1 ? "s" : ""}?</p>
            <p className="modal-subtext">Any active recordings will be interrupted.</p>
            <div className="modal-buttons">
              <button className="reset-button" type="button" onClick={handleRebootAll}>
                Reboot All
              </button>
              <button className="save-button" type="button" onClick={() => setShowRebootConfirm(false)}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default ModuleList;
