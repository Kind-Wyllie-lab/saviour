import { useEffect, useState } from "react";
import socket from "/src/socket";
import "./ModuleList.css";

function ModuleList({ modules }) {
  const [showRebootConfirm, setShowRebootConfirm] = useState(false);
  const [updateStatuses, setUpdateStatuses] = useState({}); // { module_id: "updating" | { success, output } }

  useEffect(() => {
    const handler = (data) => {
      setUpdateStatuses(prev => ({
        ...prev,
        [data.module_id]: { success: data.success, output: data.output },
      }));
    };
    socket.on("module_update_result", handler);
    return () => socket.off("module_update_result", handler);
  }, []);

  const handleUpdateAll = () => {
    const pending = {};
    modules.forEach(m => { pending[m.id] = "updating"; });
    setUpdateStatuses(pending);
    socket.emit("send_command", { module_id: "all", type: "update_saviour", params: {} });
  };

  const handleRebootAll = () => {
    socket.emit("send_command", { module_id: "all", type: "reboot", params: {} });
    setShowRebootConfirm(false);
  };

  // Full IP is available via tooltip; the column only needs the last octet
  // to tell same-subnet devices apart at a glance.
  const lastOctet = (ip) => {
    if (!ip || typeof ip !== "string") return "-";
    const parts = ip.split(".");
    return parts.length === 4 ? `.${parts[3]}` : ip;
  };

  // Minimised version: just the tag, no "+N commits"/hash suffix — full
  // string is still available via the title tooltip.
  const formatVersion = (v) => {
    if (v == null || typeof v !== "string" || v === "UNKNOWN_VERSION") return "-";
    return v.split("-")[0];
  };

  // Sort modules: grouped ones first (alphabetically by group then name),
  // ungrouped at the end alphabetically.
  const sortedModules = [...modules].sort((a, b) => {
    const ga = a.group || "";
    const gb = b.group || "";
    if (ga !== gb) {
      if (!ga) return 1;
      if (!gb) return -1;
      return ga.localeCompare(gb);
    }
    return (a.name || a.id).localeCompare(b.name || b.id);
  });

  // Track which group labels we've already rendered
  let lastGroup = undefined;

  return (
    <div className="module-list-container card">
      <h2>Module List</h2>

      <div className="module-list">
        {/* Header row */}
        <div className="module-list-header">
          <span>Module</span>
          <span>Status</span>
          <span>Group</span>
          <span>IP</span>
          <span>Ver</span>
        </div>

        {sortedModules.map((module) => {
          const upd = updateStatuses[module.id];
          const group = module.group || "";

          // Emit a group separator row when the group changes
          const showGroupHeader = group !== lastGroup;
          lastGroup = group;

          return (
            <div key={module.id}>
              {showGroupHeader && group && (
                <div className="module-group-header">{group}</div>
              )}
              {showGroupHeader && !group && sortedModules.some(m => m.group) && (
                <div className="module-group-header module-group-header--ungrouped">No group</div>
              )}
              <div className="module-list-item">
                <div className="module-list-item-start">
                  <div className={`status-icon ${module.status?.toLowerCase()}`} />
                  <span>{module.name}</span>
                </div>
                <span>{module.status}</span>
                <span className="module-group-cell">
                  {group
                    ? <span className="module-group-badge">{group}</span>
                    : <span className="cell--muted">-</span>
                  }
                </span>
                <span className="module-ip" title={module.ip}>{lastOctet(module.ip)}</span>
                <span className="module-version" title={module.version}>
                  {formatVersion(module.version)}
                </span>
                {upd && (
                  <span
                    className={`module-update-status ${upd === "updating" ? "module-update-status--pending" : upd.success ? "module-update-status--success" : "module-update-status--error"}`}
                    title={upd !== "updating" ? upd.output : undefined}
                  >
                    {upd === "updating" ? "Updating…" : upd.success ? `\u2713 ${upd.output}` : `\u2717 ${upd.output}`}
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {modules.length > 0 && (
        <div className="bulk-actions">
          <button className="bulk-btn" type="button" onClick={handleUpdateAll}
            disabled={modules.some(m => updateStatuses[m.id] === "updating")}>
            {modules.some(m => updateStatuses[m.id] === "updating") ? "Updating…" : "Update All"}
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
