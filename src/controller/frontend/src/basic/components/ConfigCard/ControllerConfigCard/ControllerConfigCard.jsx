import { useEffect, useState } from "react";
import "./ControllerConfigCard.css";
import socket from "/src/socket";
import { useConfigForm } from "../useConfigForm";
import { filterPrivateKeys } from "../configUtils";
import ConfigFields from "../ConfigFields";

function ControllerConfigCard() {
  const { formData, setFormData, handleChange } = useConfigForm();
  const [showRebootConfirm, setShowRebootConfirm] = useState(false);

  useEffect(() => {
    socket.emit("get_controller_config");
    socket.on("controller_config_response", (data) => {
      setFormData(data.config || {});
    });
    return () => socket.off("controller_config_response");
  }, []);

  const handleSave = () => {
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
