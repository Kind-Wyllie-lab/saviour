import { useEffect, useState } from "react";
import socket from "/src/socket";
import LivestreamCard from "/src/basic/components/LivestreamCard/LivestreamCard";
import { useConfigForm } from "../useConfigForm";
import { filterPrivateKeys } from "../configUtils";
import ConfigFields from "../ConfigFields";

function CameraConfigCard({ id, module }) {
  const { formData, handleChange } = useConfigForm(module.config);
  const [sensorModes, setSensorModes] = useState([]);

  useEffect(() => {
    socket.emit("get_module_config", { module_id: module.id });
    socket.emit("send_command", { module_id: module.id, type: "get_sensor_modes", params: {} });

    const onSensorModes = (data) => {
      if (data.module_id === module.id) {
        setSensorModes(data.sensor_modes);
      }
    };
    socket.on("sensor_modes_response", onSensorModes);
    return () => socket.off("sensor_modes_response", onSensorModes);
  }, [module.id]);

  const handleSave = () => {
    const editableData = filterPrivateKeys(formData);
    socket.emit("save_module_config", { id, config: editableData });
  };

  const handleUpdate = () => {
    socket.emit("send_command", { module_id: module.id, type: "update_saviour", params: {} });
  };

  const handleReboot = () => {
    socket.emit("send_command", { module_id: module.id, type: "reboot", params: {} });
  };

  const handleRefreshModes = () => {
    socket.emit("send_command", { module_id: module.id, type: "get_sensor_modes", params: {} });
  };

  // FoV indicator: compare current output size against the selected mode's max
  const selectedModeIndex = formData?.camera?.sensor_mode_index ?? 0;
  const selectedMode = sensorModes[selectedModeIndex];
  const currentWidth = formData?.camera?.width;
  const currentHeight = formData?.camera?.height;

  let fovLabel = null;
  if (selectedMode && currentWidth != null && currentHeight != null) {
    const [maxW, maxH] = selectedMode.size;
    if (currentWidth === maxW && currentHeight === maxH) {
      fovLabel = { text: `Full sensor output (${maxW}×${maxH})`, full: true };
    } else {
      const pct = Math.round((currentWidth * currentHeight * 100) / (maxW * maxH));
      fovLabel = {
        text: `Cropped: ${currentWidth}×${currentHeight} (${pct}% of ${maxW}×${maxH})`,
        full: false,
      };
    }
  }

  // Strip sensor_mode_index from the data passed to ConfigFields — it has its own dropdown.
  const configFieldsData = (() => {
    if (!formData?.camera) return formData;
    const { sensor_mode_index, ...rest } = formData.camera;
    return { ...formData, camera: rest };
  })();

  return (
    <div className="config-card">
      <div className="card-header">
        <h3>{module.name} ({module.id})</h3>
      </div>

      <div className="config-card-body">
        <div className="config-form">
          <div className="form-field">
            <label>Sensor Mode:</label>
            <select
              value={selectedModeIndex}
              onChange={(e) => handleChange(["camera", "sensor_mode_index"], e)}
            >
              {sensorModes.length > 0
                ? sensorModes.map((m) => (
                    <option key={m.index} value={m.index}>{m.label}</option>
                  ))
                : <option value={selectedModeIndex}>Mode {selectedModeIndex} (click Refresh)</option>
              }
            </select>
          </div>

          {fovLabel && (
            <div className={`fov-label ${fovLabel.full ? "fov-full" : "fov-cropped"}`}>
              {fovLabel.text}
            </div>
          )}

          <form>
            <ConfigFields data={configFieldsData} handleChange={handleChange} />
          </form>

          <button className="save-button" type="button" onClick={handleSave}>
            Save Config
          </button>
        </div>

        <div className="livestream-wrapper">
          <LivestreamCard module={module} />
          <button type="button" onClick={handleRefreshModes}>
            Refresh Sensor Modes
          </button>
        </div>
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
    </div>
  );
}

export default CameraConfigCard;
