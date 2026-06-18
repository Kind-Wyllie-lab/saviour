import { useEffect, useRef, useState } from "react";
import "./ControllerConfigCard.css";
import socket from "/src/socket";
import { useConfigForm } from "../useConfigForm";
import { filterPrivateKeys } from "../configUtils";
import ConfigFields from "../ConfigFields";
import ExportConfigSection from "./ExportConfigSection";

const TABS = [
  { key: "basic",    label: "Basic"    },
  { key: "settings", label: "Settings" },
  { key: "export",   label: "Export"   },
];

function ControllerConfigCard() {
  const { formData, setFormData, handleChange } = useConfigForm();
  const [showRebootConfirm, setShowRebootConfirm] = useState(false);
  const [showUpdateConfirm, setShowUpdateConfirm] = useState(false);
  const [updateStatus, setUpdateStatus] = useState(null);
  const [controllerInfo, setControllerInfo] = useState({ ip: null, version: null });
  const [saveStatus, setSaveStatus] = useState(null);
  const [activeTab, setActiveTab] = useState("basic");
  const saveTimerRef = useRef(null);

  useEffect(() => {
    socket.emit("get_controller_config");
    socket.emit("get_controller_info");

    socket.on("controller_config_response", (data) => {
      setFormData(data.config || {});
      setSaveStatus(prev => {
        if (prev === "saving") {
          clearTimeout(saveTimerRef.current);
          saveTimerRef.current = setTimeout(() => setSaveStatus(null), 3000);
          return "saved";
        }
        return prev;
      });
    });

    socket.on("controller_info_response", (data) => {
      setControllerInfo({ ip: data.ip, version: data.version });
    });

    socket.on("update_saviour_controller_result", (data) => {
      setUpdateStatus({ success: data.success, output: data.output });
    });

    return () => {
      socket.off("controller_config_response");
      socket.off("controller_info_response");
      socket.off("update_saviour_controller_result");
      clearTimeout(saveTimerRef.current);
    };
  }, []);

  const handleSave = () => {
    setSaveStatus("saving");
    socket.emit("save_controller_config", { config: filterPrivateKeys(formData) });
  };

  const handleRebootSaviour = () => {
    socket.emit("reboot_saviour");
    setShowRebootConfirm(false);
  };

  const handleUpdateSaviour = () => {
    setUpdateStatus("updating");
    setShowUpdateConfirm(false);
    socket.emit("update_saviour_controller");
  };

  // Settings tab: everything except controller.name and export
  const settingsData = (() => {
    if (!formData) return formData;
    const { export: _e, controller: ctrl, ...rest } = filterPrivateKeys(formData) ?? {};
    // Keep controller section only if it has fields beyond `name` (name goes in Basic)
    const { name: _n, ...ctrlRest } = ctrl ?? {};
    const result = { ...rest };
    if (Object.keys(ctrlRest).length > 0) result.controller = ctrlRest;
    return result;
  })();

  return (
    <div className="config-card controller-config-card">
      <div className="card-header">
        <h3>Controller Config</h3>
        <div className="device-info">
          {controllerInfo.ip && <span>IP: {controllerInfo.ip}</span>}
          {controllerInfo.version && <span>{controllerInfo.version}</span>}
        </div>
      </div>
      <div className="config-card-body">
        <div className="config-form">

          <div className="config-tabs">
            {TABS.map(t => (
              <button key={t.key} type="button"
                className={`config-tab-btn${activeTab === t.key ? " active" : ""}`}
                onClick={() => setActiveTab(t.key)}>
                {t.label}
              </button>
            ))}
          </div>

          <div className="config-tab-content">

            {/* BASIC */}
            {activeTab === "basic" && (
              <div className="form-field">
                <label>Name:</label>
                <input type="text"
                  value={formData?.controller?.name ?? ""}
                  onChange={e => handleChange(["controller", "name"], e)} />
              </div>
            )}

            {/* SETTINGS */}
            {activeTab === "settings" && (
              <form>
                <ConfigFields data={settingsData} handleChange={handleChange} />
              </form>
            )}

            {/* EXPORT */}
            {activeTab === "export" && (
              <ExportConfigSection
                exportConfig={formData?.export}
                handleChange={handleChange}
              />
            )}
          </div>

          <div className="config-section-divider" />

          <button className="save-button" type="button" onClick={handleSave}>
            Save Config
          </button>
          {saveStatus === "saving" && (
            <span className="config-sync-badge config-sync-badge--pending">Saving...</span>
          )}
          {saveStatus === "saved" && (
            <span className="config-sync-badge config-sync-badge--synced">&#10003; Saved</span>
          )}
        </div>
      </div>
      <div className="update-button-wrapper">
        <button className="update-button" type="button" onClick={() => { setShowUpdateConfirm(true); setUpdateStatus(null); }}>
          Update SAVIOUR
        </button>
        <button className="reset-button" type="button" onClick={() => setShowRebootConfirm(true)}>
          Reboot SAVIOUR
        </button>
      </div>

      {updateStatus === "updating" && (
        <p className="config-sync-badge config-sync-badge--pending" style={{ textAlign: "center" }}>Updating...</p>
      )}
      {updateStatus && updateStatus !== "updating" && (
        <div className={`config-sync-badge ${updateStatus.success ? "config-sync-badge--synced" : "config-sync-badge--failed"}`} style={{ textAlign: "center", padding: "6px" }}>
          {updateStatus.success ? "Updated: " : "Update failed: "}{updateStatus.output}
        </div>
      )}

      {showUpdateConfirm && (
        <div className="modal-overlay" onClick={() => setShowUpdateConfirm(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <p>Update SAVIOUR on the controller?</p>
            <p className="modal-subtext">This will run <code>git pull</code> on the controller. Restart the service afterwards to apply changes.</p>
            <div className="modal-buttons">
              <button className="save-button" type="button" onClick={handleUpdateSaviour}>Update</button>
              <button className="reset-button" type="button" onClick={() => setShowUpdateConfirm(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {showRebootConfirm && (
        <div className="modal-overlay" onClick={() => setShowRebootConfirm(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <p>Reboot all modules and the controller?</p>
            <p className="modal-subtext">All active recordings will be stopped. The system will be unavailable for a short time.</p>
            <div className="modal-buttons">
              <button className="reset-button" type="button" onClick={handleRebootSaviour}>Reboot</button>
              <button className="save-button" type="button" onClick={() => setShowRebootConfirm(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default ControllerConfigCard;
