import { useEffect, useState } from "react";
import socket from "/src/socket";

/**
 * Export target section for the controller config card.
 * Dropdown selects "Controller Share" (auto-fills from controller's own Samba)
 * or "Custom" (free-form fields). A button syncs the current config to all modules.
 */
function ExportConfigSection({ exportConfig, handleChange }) {
  const [mode, setMode] = useState(null); // "controller" | "custom" — null until loaded
  const [controllerShare, setControllerShare] = useState(null);
  const [syncStatus, setSyncStatus] = useState(null); // null | "syncing" | { success_count, total }

  // Fetch the controller's own Samba details on mount
  useEffect(() => {
    socket.emit("get_controller_samba_info");
    const handler = (data) => setControllerShare(data);
    socket.on("controller_samba_info_response", handler);
    return () => socket.off("controller_samba_info_response", handler);
  }, []);

  // Derive initial mode once we have both the config and the controller's share info
  useEffect(() => {
    if (mode !== null || !controllerShare) return;
    const ip = exportConfig?.share_ip ?? "";
    setMode(ip === "" || ip === controllerShare.share_ip ? "controller" : "custom");
  }, [controllerShare, exportConfig, mode]);

  useEffect(() => {
    const handler = (data) =>
      setSyncStatus({ success_count: data.success_count, total: data.total });
    socket.on("export_sync_all_result", handler);
    return () => socket.off("export_sync_all_result", handler);
  }, []);

  const applyValues = (values) => {
    const fields = ["share_ip", "share_path", "share_username", "share_password"];
    fields.forEach((f) =>
      handleChange(["export", f], { target: { value: values[f] ?? "" } })
    );
  };

  const handleModeChange = (newMode) => {
    setMode(newMode);
    if (newMode === "controller" && controllerShare) {
      applyValues(controllerShare);
    }
  };

  const syncAll = () => {
    setSyncStatus("syncing");
    socket.emit("sync_export_to_all", {
      share_ip:       exp.share_ip       ?? "",
      share_path:     exp.share_path     ?? "",
      share_username: exp.share_username ?? "",
      share_password: exp.share_password ?? "",
    });
  };

  const exp = exportConfig ?? {};

  return (
    <fieldset className="nested-fieldset">
      <legend className="nested-fieldset-legend" style={{ cursor: "default" }}>
        export
      </legend>
      <div className="nested">
        <div className="form-field">
          <label>Target:</label>
          <select
            value={mode ?? "controller"}
            onChange={(e) => handleModeChange(e.target.value)}
          >
            <option value="controller">Controller Share</option>
            <option value="custom">Custom</option>
          </select>
        </div>

        {mode === "controller" && controllerShare && (
          <div className="export-config-preview">
            {controllerShare.share_ip} / {controllerShare.share_path}
            {controllerShare.share_username && ` (${controllerShare.share_username})`}
          </div>
        )}

        {mode === "custom" && (
          <>
            <div className="form-field">
              <label>IP:</label>
              <input
                type="text"
                value={exp.share_ip ?? ""}
                onChange={(e) => handleChange(["export", "share_ip"], e)}
              />
            </div>
            <div className="form-field">
              <label>Share path:</label>
              <input
                type="text"
                value={exp.share_path ?? ""}
                onChange={(e) => handleChange(["export", "share_path"], e)}
              />
            </div>
            <div className="form-field">
              <label>Username:</label>
              <input
                type="text"
                value={exp.share_username ?? ""}
                onChange={(e) => handleChange(["export", "share_username"], e)}
              />
            </div>
            <div className="form-field">
              <label>Password:</label>
              <input
                type="password"
                value={exp.share_password ?? ""}
                onChange={(e) => handleChange(["export", "share_password"], e)}
              />
            </div>
          </>
        )}

        <div className="export-sync-row">
          <button
            type="button"
            className="save-button"
            onClick={syncAll}
            disabled={syncStatus === "syncing"}
          >
            {syncStatus === "syncing" ? "Syncing…" : "Sync to All Modules"}
          </button>
          {syncStatus && syncStatus !== "syncing" && (
            <span className={`config-sync-badge ${syncStatus.success_count === syncStatus.total ? "config-sync-badge--synced" : "config-sync-badge--failed"}`}>
              {syncStatus.success_count}/{syncStatus.total} sent
              {syncStatus.success_count < syncStatus.total && ` (${syncStatus.total - syncStatus.success_count} unreachable)`}
            </span>
          )}
        </div>
      </div>
    </fieldset>
  );
}

export default ExportConfigSection;
