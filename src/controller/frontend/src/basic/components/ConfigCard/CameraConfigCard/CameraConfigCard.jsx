import { useEffect, useState } from "react";
import socket from "/src/socket";
import LivestreamCard from "/src/basic/components/LivestreamCard/LivestreamCard";
import { useConfigForm } from "../useConfigForm";
import { filterPrivateKeys } from "../configUtils";
import ConfigFields from "../ConfigFields";

const PRESETS = [
  { key: "1080p30",  label: "1080p",        sub: "30 fps",  width: 1920, height: 1080, fps: 30  },
  { key: "1080p60",  label: "1080p Fast",   sub: "60 fps",  width: 1920, height: 1080, fps: 60  },
  { key: "square25", label: "Square",       sub: "25 fps",  width: 1000, height: 1000, fps: 25  },
  { key: "fast100",  label: "High Speed",   sub: "100 fps", width: 1332, height: 990,  fps: 100 },
  { key: "4k10",     label: "4K",           sub: "10 fps",  width: 4056, height: 3040, fps: 10  },
  { key: "custom",   label: "Custom",       sub: null,      width: null, height: null, fps: null },
];

// Pick the smallest sensor mode that can output the requested size at the requested fps.
// "Smallest" means least sensor area — the most efficient mode that still covers the request.
function bestSensorMode(sensorModes, width, height, fps) {
  if (!sensorModes.length) return null;
  const candidates = sensorModes.filter(
    m => m.size[0] >= width && m.size[1] >= height && m.fps >= fps
  );
  if (!candidates.length) return null;
  return candidates.reduce((best, m) =>
    m.size[0] * m.size[1] < best.size[0] * best.size[1] ? m : best
  );
}

// If the current w/h/fps exactly matches a known preset, return that key; otherwise "custom".
function detectPreset(width, height, fps) {
  return PRESETS.find(p => p.width === width && p.height === height && p.fps === fps)?.key ?? "custom";
}

function CameraConfigCard({ id, module }) {
  const { formData, setFormData, handleChange } = useConfigForm(module.config);
  const [sensorModes, setSensorModes] = useState([]);
  const [activePreset, setActivePreset] = useState("custom");

  useEffect(() => {
    socket.emit("get_module_config", { module_id: module.id });
    socket.emit("send_command", { module_id: module.id, type: "get_sensor_modes", params: {} });

    const onSensorModes = (data) => {
      if (data.module_id === module.id) setSensorModes(data.sensor_modes);
    };
    socket.on("sensor_modes_response", onSensorModes);
    return () => socket.off("sensor_modes_response", onSensorModes);
  }, [module.id]);

  // When sensor modes first arrive, auto-select the best mode for current config.
  useEffect(() => {
    if (!sensorModes.length || !formData?.camera) return;
    const { width, height, fps } = formData.camera;
    if (!width || !height || !fps) return;
    const best = bestSensorMode(sensorModes, width, height, fps);
    if (best && best.index !== formData.camera.sensor_mode_index) {
      setFormData(prev => {
        const cloned = structuredClone(prev);
        cloned.camera.sensor_mode_index = best.index;
        return cloned;
      });
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sensorModes]);

  // Detect which preset matches the loaded config.
  useEffect(() => {
    if (formData?.camera) {
      const { width, height, fps } = formData.camera;
      setActivePreset(detectPreset(width, height, fps));
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [formData?.camera?.width, formData?.camera?.height, formData?.camera?.fps]);

  const handlePresetSelect = (preset) => {
    setActivePreset(preset.key);
    if (preset.key === "custom") return;
    const best = bestSensorMode(sensorModes, preset.width, preset.height, preset.fps);
    setFormData(prev => {
      const cloned = structuredClone(prev);
      cloned.camera.width = preset.width;
      cloned.camera.height = preset.height;
      cloned.camera.fps = preset.fps;
      if (best) cloned.camera.sensor_mode_index = best.index;
      return cloned;
    });
  };

  const handleCustomChange = (field, rawValue) => {
    const value = rawValue === "" ? "" : Number(rawValue);
    setFormData(prev => {
      const cloned = structuredClone(prev);
      cloned.camera[field] = value;
      // Auto-select best sensor mode whenever all three fields are filled.
      const w = field === "width"  ? value : cloned.camera.width;
      const h = field === "height" ? value : cloned.camera.height;
      const f = field === "fps"    ? value : cloned.camera.fps;
      if (w && h && f && sensorModes.length) {
        const best = bestSensorMode(sensorModes, w, h, f);
        if (best) cloned.camera.sensor_mode_index = best.index;
      }
      return cloned;
    });
  };

  const handleSave = () => {
    socket.emit("save_module_config", { id, config: filterPrivateKeys(formData) });
  };

  const currentWidth  = formData?.camera?.width;
  const currentHeight = formData?.camera?.height;
  const currentFps    = formData?.camera?.fps;
  const selectedModeIndex = formData?.camera?.sensor_mode_index ?? 0;
  const selectedMode  = sensorModes[selectedModeIndex];

  const maxFps    = selectedMode?.fps ?? null;
  const fpsOverMax = maxFps != null && currentFps != null && Number(currentFps) > maxFps;

  const maxWidth  = sensorModes.length ? Math.max(...sensorModes.map(m => m.size[0])) : 9999;
  const maxHeight = sensorModes.length ? Math.max(...sensorModes.map(m => m.size[1])) : 9999;
  const maxFpsAll = sensorModes.length ? Math.max(...sensorModes.map(m => m.fps)) : 999;

  const overlayTimestamp = formData?.camera?.overlay_timestamp ?? true;

  // Strip fields handled explicitly so ConfigFields doesn't render duplicates.
  const configFieldsData = (() => {
    if (!formData?.camera) return formData;
    const { sensor_mode_index, width, height, fps, overlay_timestamp, text_size, ...rest } = formData.camera;
    return { ...formData, camera: rest };
  })();

  return (
    <div className="config-card">
      <div className="card-header">
        <h3>{module.name} ({module.id})</h3>
      </div>

      <div className="config-card-body">
        <div className="config-form">

          {/* ── Presets ── */}
          <div className="form-field">
            <label>Mode:</label>
            <select
              value={activePreset}
              onChange={e => {
                const preset = PRESETS.find(p => p.key === e.target.value);
                if (preset) handlePresetSelect(preset);
              }}
            >
              {PRESETS.map(preset => (
                <option key={preset.key} value={preset.key}>
                  {preset.label}{preset.sub ? ` — ${preset.sub}` : ""}
                </option>
              ))}
            </select>
          </div>

          {/* ── Custom inputs ── */}
          {activePreset === "custom" ? (
            <>
              <div className="form-field">
                <label>Width (px):</label>
                <input
                  type="number" min="64" max={maxWidth} step="2"
                  value={currentWidth ?? ""}
                  onChange={e => handleCustomChange("width", e.target.value)}
                />
              </div>
              <div className="form-field">
                <label>Height (px):</label>
                <input
                  type="number" min="64" max={maxHeight} step="2"
                  value={currentHeight ?? ""}
                  onChange={e => handleCustomChange("height", e.target.value)}
                />
              </div>
              <div className="form-field">
                <label>FPS:</label>
                <input
                  type="number" min="1" max={maxFpsAll} step="1"
                  value={currentFps ?? ""}
                  onChange={e => handleCustomChange("fps", e.target.value)}
                />
              </div>
            </>
          ) : (
            currentWidth != null && (
              <div className="camera-output-summary">
                {currentWidth} × {currentHeight} &middot; {currentFps} fps
              </div>
            )
          )}

          {/* ── Auto-selected sensor mode ── */}
          {selectedMode ? (
            <div className="sensor-mode-info">
              Sensor mode: {selectedMode.label}
            </div>
          ) : (
            <div className="sensor-mode-info sensor-mode-info--muted">
              Sensor modes not yet loaded — click Refresh
            </div>
          )}

          {/* ── FPS warning ── */}
          {fpsOverMax && (
            <div className="fov-label fov-cropped">
              {currentFps} fps exceeds mode max ({maxFps} fps) — will be clamped on apply
            </div>
          )}

          {/* ── Timestamp overlay ── */}
          <div className="form-field">
            <label>Timestamp overlay:</label>
            <input
              type="checkbox"
              checked={overlayTimestamp}
              onChange={e => handleChange(["camera", "overlay_timestamp"], e)}
            />
          </div>
          {overlayTimestamp && (
            <div className="form-field">
              <label>Text size:</label>
              <select
                value={formData?.camera?.text_size ?? "medium"}
                onChange={e => handleChange(["camera", "text_size"], e)}
              >
                <option value="small">Small</option>
                <option value="medium">Medium</option>
                <option value="large">Large</option>
              </select>
            </div>
          )}

          {/* ── Remaining config fields ── */}
          <form>
            <ConfigFields data={configFieldsData} handleChange={handleChange} />
          </form>

          <button className="save-button" type="button" onClick={handleSave}>
            Save Config
          </button>
        </div>

        <div className="livestream-wrapper">
          <LivestreamCard module={module} />
          <button type="button"
            onClick={() => socket.emit("send_command", { module_id: module.id, type: "get_sensor_modes", params: {} })}>
            Refresh Sensor Modes
          </button>
        </div>
      </div>

      <div className="update-button-wrapper">
        <button className="update-button" type="button"
          onClick={() => socket.emit("send_command", { module_id: module.id, type: "update_saviour", params: {} })}>
          Update Saviour Version
        </button>
      </div>
      <div className="update-button-wrapper">
        <button className="update-button" type="button"
          onClick={() => socket.emit("send_command", { module_id: module.id, type: "reboot", params: {} })}>
          Reboot Module
        </button>
      </div>
    </div>
  );
}

export default CameraConfigCard;
