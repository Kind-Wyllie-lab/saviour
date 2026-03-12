import { useEffect, useRef, useState } from "react";
import "./ControllerConfigCard.css";
import socket from "/src/socket";
import { useConfigForm } from "../useConfigForm";
import { filterPrivateKeys } from "../configUtils";
import ConfigFields from "../ConfigFields";

function ControllerConfigCard() {
  const { formData, setFormData, handleChange } = useConfigForm();
  const [showRebootConfirm, setShowRebootConfirm] = useState(false);
  const [saveStatus, setSaveStatus] = useState(null); // null | "saving" | "saved"
  const saveTimerRef = useRef(null);

  useEffect(() => {
    socket.emit("get_controller_config");
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
    return () => {
      socket.off("controller_config_response");
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

  return (
    <div className="config-card">
      <div className="card-header">
        <h3>Controller Config</h3>
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
        <button className="reset-button" type="button" onClick={() => setShowRebootConfirm(true)}>
          Reboot SAVIOUR
        </button>
      </div>

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
