import { useEffect, useState } from "react";
import socket from "/src/socket";
import LivestreamCard from "/src/basic/components/LivestreamCard/LivestreamCard";
import { useConfigForm } from "../useConfigForm";
import { filterPrivateKeys } from "../configUtils";
import ConfigFields from "../ConfigFields";

function GenericConfigCard({ id, module }) {
  const { formData, handleChange } = useConfigForm(module.config);
  const [showResetConfirm, setShowResetConfirm] = useState(false);

  // Request fresh config from the module on mount.
  useEffect(() => {
    socket.emit("get_module_config", { module_id: module.id });
  }, [module.id]);

  const handleSave = () => {
    const editableData = filterPrivateKeys(formData);
    socket.emit("save_module_config", { id, config: editableData });
  };

  const handleReset = () => {
    socket.emit("reset_module_config", { module_id: module.id });
    setShowResetConfirm(false);
  };

  const handleUpdate = () => {
    socket.emit("send_command", { module_id: module.id, type: "update_saviour", params: {} });
  };

  const handleReboot = () => {
    socket.emit("send_command", { module_id: module.id, type: "reboot", params: {} });
  };

  const handleGetModes = () => {
    socket.emit("send_command", { module_id: module.id, type: "get_sensor_modes", params: {} });
  };

  return (
    <div className="config-card">
      <div className="card-header">
        <h3>{module.name} ({module.id})</h3>
      </div>

      <div className="config-card-body">
        <div className="config-form">
          <form>
            <ConfigFields data={formData} handleChange={handleChange} />
          </form>
          <div className="config-action-buttons">
            <button className="save-button" type="button" onClick={handleSave}>
              Save Config
            </button>
            <button className="reset-button" type="button" onClick={() => setShowResetConfirm(true)}>
              Reset to Default
            </button>
          </div>
        </div>

        {module.type.includes("camera") && (
          <div className="livestream-wrapper">
            <LivestreamCard module={module} />
            <button type="button" onClick={handleGetModes}>Get Sensor Modes</button>
          </div>
        )}
      </div>

      <div className="update-button-wrapper">
        <button className="update-button" type="button" onClick={handleUpdate}>
          Update Saviour Version
        </button>
      </div>
      <div className="update-button-wrapper">
        <button className="update-button" type="button" onClick={handleReboot}>
          Reboot Module
        </button>
      </div>

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
