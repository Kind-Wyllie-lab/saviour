import { useEffect, useRef, useState } from "react";
import "./ControllerConfigCard.css";
import socket from "/src/socket";
import { useConfigForm } from "../useConfigForm";
import { filterPrivateKeys } from "../configUtils";
import ConfigFields from "../ConfigFields";

function ControllerConfigCard() {
  const { formData, setFormData, handleChange } = useConfigForm();
  const [showRebootConfirm, setShowRebootConfirm] = useState(false);
  const [showUpdateConfirm, setShowUpdateConfirm] = useState(false);
  const [updateStatus, setUpdateStatus] = useState(null); // null | "updating" | { success, output }
  const [controllerInfo, setControllerInfo] = useState({ ip: null, version: null });
  const [saveStatus, setSaveStatus] = useState(null); // null | "saving" | "saved"
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
    const editableData = filterPrivateKeys(formData);
    socket.emit("save_controller_config", { config: editableData });
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

  return (
    <div className="config-card">
      <div className="card-header">
        <h3>Controller Config</h3>
        <div className="device-info">
          {controllerInfo.ip && <span>IP: {controllerInfo.ip}</span>}
          {controllerInfo.version && <span>v{controllerInfo.version}</span>}
        </div>
      </div>
      <div className="config-card-body">
        <div className="config-form">
          <form>
            <ConfigFields data={formData} handleChange={handleChange} />
          </form>
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
              <button className="save-button" type="button" onClick={handleUpdateSaviour}>
                Update
              </button>
              <button className="reset-button" type="button" onClick={() => setShowUpdateConfirm(false)}>
                Cancel
              </button>
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
              <button className="reset-button" type="button" onClick={handleRebootSaviour}>
                Reboot
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

export default ControllerConfigCard;
