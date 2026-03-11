import { useEffect } from "react";
import "./ControllerConfigCard.css";
import socket from "/src/socket";
import { useConfigForm } from "../useConfigForm";
import { filterPrivateKeys } from "../configUtils";
import ConfigFields from "../ConfigFields";

function ControllerConfigCard() {
  const { formData, setFormData, handleChange } = useConfigForm({});

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
    </div>
  );
}

export default ControllerConfigCard;
