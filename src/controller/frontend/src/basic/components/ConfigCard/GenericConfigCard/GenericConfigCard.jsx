import { useEffect, useState } from "react";
import socket from "/src/socket";
import LivestreamCard from "/src/basic/components/LivestreamCard/LivestreamCard";
import { useConfigForm } from "../useConfigForm";
import { filterPrivateKeys } from "../configUtils";
import ConfigFields from "../ConfigFields";
import { useModuleUpdate } from "/src/hooks/useModuleUpdate";
import { useExportSync } from "/src/hooks/useExportSync";

function GenericConfigCard({ id, module, clipboard, onCopy }) {
  const { formData, setFormData, handleChange } = useConfigForm(module.config);
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [showRebootConfirm, setShowRebootConfirm] = useState(false);
  const [hasSaved, setHasSaved] = useState(false);
  const { updateStatus, handleUpdate } = useModuleUpdate(module.id);
  const { syncStatus, syncExport } = useExportSync(module.id);

  // Request fresh config from the module on mount.
  useEffect(() => {
    socket.emit("get_module_config", { module_id: module.id });
  }, [module.id]);

  const handlePaste = () => {
    if (!clipboard) return;
    setFormData(prev => {
      const cloned = structuredClone(prev);
      for (const [key, value] of Object.entries(clipboard.data)) {
        cloned[key] = structuredClone(value);
      }
      return cloned;
    });
  };

  const sections = Object.keys(filterPrivateKeys(formData) ?? {}).filter(
    k => formData[k] !== null && typeof formData[k] === "object"
  );

  const capitalize = s => s.charAt(0).toUpperCase() + s.slice(1);

  const handleSave = () => {
    setHasSaved(true);
    const editableData = filterPrivateKeys(formData);
    socket.emit("save_module_config", { id, config: editableData });
  };

  const handleReset = () => {
    socket.emit("reset_module_config", { module_id: module.id });
    setShowResetConfirm(false);
  };

  const handleReboot = () => {
    socket.emit("send_command", { module_id: module.id, type: "reboot", params: {} });
    setShowRebootConfirm(false);
  };

  const handleGetModes = () => {
    socket.emit("send_command", { module_id: module.id, type: "get_sensor_modes", params: {} });
  };

  return (
    <div className="config-card">
      <div className="card-header">
        <h3>{module.name} ({module.id})</h3>
        <div className="device-info">
          {typeof module.ip === "string" && module.ip && <span>IP: {module.ip}</span>}
          {typeof module.version === "string" && module.version && <span>SAVIOUR {module.version}</span>}
        </div>
      </div>

      <div className="config-card-body">
        <div className="config-form">
          {clipboard && (
            <div className="clipboard-bar">
              <span className="clipboard-label">Clipboard: {clipboard.label}</span>
              <button type="button" className="copy-btn" onClick={handlePaste}>Paste</button>
              <button type="button" className="copy-btn" onClick={() => onCopy(null)}>Clear</button>
            </div>
          )}
          <form>
            <ConfigFields data={formData} handleChange={handleChange} />
          </form>
          <div className="copy-bar">
            <span className="copy-bar-label">Copy:</span>
            {sections.map(key => (
              <button key={key} type="button" className="copy-btn"
                onClick={() => onCopy({ label: `${capitalize(key)} — ${module.name}`, data: { [key]: formData[key] } })}>
                {capitalize(key)}
              </button>
            ))}
            <button type="button" className="copy-btn"
              onClick={() => onCopy({ label: `All — ${module.name}`, data: filterPrivateKeys(formData) })}>
              All
            </button>
          </div>
          <div className="config-action-buttons">
            <button className="save-button" type="button" onClick={handleSave}>
              Save Config
            </button>
            <button className="reset-button" type="button" onClick={() => setShowResetConfirm(true)}>
              Reset to Default
            </button>
          </div>
          {hasSaved && module.config_sync_status === "PENDING" && (
            <span className="config-sync-badge config-sync-badge--pending">Saving...</span>
          )}
          {hasSaved && module.config_sync_status === "SYNCED" && (
            <span className="config-sync-badge config-sync-badge--synced">Saved</span>
          )}
          {hasSaved && module.config_sync_status === "FAILED" && (
            <span className="config-sync-badge config-sync-badge--failed">Save failed</span>
          )}

          {formData?.export !== undefined && (
            <div className="config-action-buttons">
              <button type="button" className="save-button"
                onClick={syncExport}
                disabled={syncStatus === "syncing"}>
                {syncStatus === "syncing" ? "Syncing…" : "Sync Export from Controller"}
              </button>
              {syncStatus && syncStatus !== "syncing" && (
                <span className={`config-sync-badge ${syncStatus.success ? "config-sync-badge--synced" : "config-sync-badge--failed"}`}>
                  {syncStatus.success ? "Export synced" : `Sync failed: ${syncStatus.error}`}
                </span>
              )}
            </div>
          )}
        </div>

        {module.type.includes("camera") && (
          <div className="livestream-wrapper">
            <LivestreamCard module={module} />
            <button type="button" onClick={handleGetModes}>Get Sensor Modes</button>
          </div>
        )}
      </div>

      <div className="update-button-wrapper">
        <button className="update-button" type="button" onClick={handleUpdate} disabled={updateStatus === "updating"}>
          {updateStatus === "updating" ? "Updating…" : "Update Saviour Version"}
        </button>
        {updateStatus && updateStatus !== "updating" && (
          <span className={`config-sync-badge ${updateStatus.success ? "config-sync-badge--synced" : "config-sync-badge--failed"}`}>
            {updateStatus.success ? `Updated: ${updateStatus.output}` : `Update failed: ${updateStatus.output}`}
          </span>
        )}
      </div>
      <div className="update-button-wrapper">
        <button className="update-button" type="button" onClick={() => setShowRebootConfirm(true)}>
          Reboot Module
        </button>
      </div>

      {showRebootConfirm && (
        <div className="modal-overlay" onClick={() => setShowRebootConfirm(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <p>Reboot <strong>{module.name}</strong>?</p>
            <p className="modal-subtext">The module will restart and reconnect automatically.</p>
            <div className="modal-buttons">
              <button className="reset-button" type="button" onClick={handleReboot}>Reboot</button>
              <button className="save-button" type="button" onClick={() => setShowRebootConfirm(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {showResetConfirm && (
        <div className="modal-overlay" onClick={() => setShowResetConfirm(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <p>Reset <strong>{module.name}</strong> to default settings?</p>
            <p className="modal-subtext">All unsaved changes and any custom configuration will be lost.</p>
            <div className="modal-buttons">
              <button className="reset-button" type="button" onClick={handleReset}>
                Reset
              </button>
              <button className="save-button" type="button" onClick={() => setShowResetConfirm(false)}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default GenericConfigCard;
