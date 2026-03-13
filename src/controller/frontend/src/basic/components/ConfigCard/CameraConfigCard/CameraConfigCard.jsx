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

function bestSensorMode(sensorModes, width, height, fps) {
  if (!sensorModes.length) return null;
  if (!width) return null;
  if (!height) return null;
  if (!fps) return null;
  const candidates = sensorModes.filter(
    m => m.size[0] >= width && m.size[1] >= height && m.fps >= fps
  );
  if (!candidates.length) return null;
  // Prefer the largest sensor area — more area means more FOV.
  // The pipeline downsamples to the requested resolution regardless.
  return candidates.reduce((best, m) =>
    m.size[0] * m.size[1] > best.size[0] * best.size[1] ? m : best
  );
}

function detectPreset(width, height, fps) {
  return PRESETS.find(p => p.width === width && p.height === height && p.fps === fps)?.key ?? "custom";
}

function CameraConfigCard({ id, module, clipboard, onCopy }) {
  const { formData, setFormData, handleChange } = useConfigForm(module.config);
  const [sensorModes, setSensorModes] = useState([]);
  const [activePreset, setActivePreset] = useState("custom");
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [applyAllConfirm, setApplyAllConfirm] = useState(null); // { section, label }
  const [hasSaved, setHasSaved] = useState(false);

  useEffect(() => {
    socket.emit("get_module_config", { module_id: module.id });
    socket.emit("send_command", { module_id: module.id, type: "get_sensor_modes", params: {} });

    const onSensorModes = (data) => {
      if (data.module_id === module.id) setSensorModes(data.sensor_modes);
    };
    socket.on("sensor_modes_response", onSensorModes);
    return () => socket.off("sensor_modes_response", onSensorModes);
  }, [module.id]);

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

  const otherSections = Object.keys(filterPrivateKeys(formData) ?? {}).filter(
    k => k !== "camera" && formData[k] !== null && typeof formData[k] === "object"
  );

  const capitalize = s => s.charAt(0).toUpperCase() + s.slice(1);

  const handleSave = () => {
    setHasSaved(true);
    socket.emit("save_module_config", { id, config: filterPrivateKeys(formData) });
  };

  const handleReset = () => {
    socket.emit("reset_module_config", { module_id: module.id });
    setShowResetConfirm(false);
  };

  const confirmApplyToAll = () => {
    if (!applyAllConfirm) return;
    const { section } = applyAllConfirm;
    const filtered = filterPrivateKeys(formData);
    const data = filtered?.[section];
    if (data) {
      socket.emit("apply_section_to_cameras", { section, data });
    }
    setApplyAllConfirm(null);
  };

  const cam = formData?.camera ?? {};
  const currentWidth  = cam.width;
  const currentHeight = cam.height;
  const currentFps    = cam.fps;
  const selectedMode  = sensorModes[cam.sensor_mode_index ?? 0];

  const maxFps    = selectedMode?.fps ?? null;
  const fpsOverMax = maxFps != null && currentFps != null && Number(currentFps) > maxFps;

  const maxWidth  = sensorModes.length ? Math.max(...sensorModes.map(m => m.size[0])) : 9999;
  const maxHeight = sensorModes.length ? Math.max(...sensorModes.map(m => m.size[1])) : 9999;
  const maxFpsAll = sensorModes.length ? Math.max(...sensorModes.map(m => m.fps)) : 999;

  const overlayTimestamp = cam.overlay_timestamp ?? true;
  const brightness       = cam.brightness ?? 0;
  const bitrateMb        = cam.bitrate_mb ?? 0;
  const gbPerHour        = (bitrateMb * 3600 / 8 / 1000).toFixed(2);

  // Strip all explicitly-rendered camera fields so ConfigFields doesn't duplicate them.
  const configFieldsData = (() => {
    if (!formData?.camera) return formData;
    const {
      sensor_mode_index, width, height, fps,
      overlay_timestamp, text_size,
      monochrome, brightness, gain, exposure_time, overlay_framerate_on_preview,
      bitrate_mb,
      ...rest
    } = formData.camera;
    return { ...formData, camera: rest };
  })();

  return (
    <div className="config-card">
      <div className="card-header">
        <h3>{module.name} ({module.id})</h3>
        <div className="device-info">
          {typeof module.ip === "string" && module.ip && <span>IP: {module.ip}</span>}
          {typeof module.version === "string" && module.version && <span>v{module.version}</span>}
        </div>
      </div>

      <div className="config-card-body">
        <div className="config-form">

          {/* ── Clipboard paste bar ── */}
          {clipboard && (
            <div className="clipboard-bar">
              <span className="clipboard-label">Clipboard: {clipboard.label}</span>
              <button type="button" className="copy-btn" onClick={handlePaste}>Paste</button>
              <button type="button" className="copy-btn" onClick={() => onCopy(null)}>Clear</button>
            </div>
          )}

          {/* ── Resolution / mode ── */}
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

          {activePreset === "custom" ? (
            <>
              <div className="form-field">
                <label>Width (px):</label>
                <input type="number" min="64" max={maxWidth} step="2"
                  value={currentWidth ?? ""}
                  onChange={e => handleCustomChange("width", e.target.value)} />
              </div>
              <div className="form-field">
                <label>Height (px):</label>
                <input type="number" min="64" max={maxHeight} step="2"
                  value={currentHeight ?? ""}
                  onChange={e => handleCustomChange("height", e.target.value)} />
              </div>
              <div className="form-field">
                <label>FPS:</label>
                <input type="number" min="1" max={maxFpsAll} step="1"
                  value={currentFps ?? ""}
                  onChange={e => handleCustomChange("fps", e.target.value)} />
              </div>
            </>
          ) : (
            currentWidth != null && (
              <div className="camera-output-summary">
                {currentWidth} × {currentHeight} &middot; {currentFps} fps
              </div>
            )
          )}

          {selectedMode ? (
            <div className="sensor-mode-info">
              Sensor mode: {selectedMode.label}
            </div>
          ) : (
            <div className="sensor-mode-info sensor-mode-info--muted">
              Sensor modes not yet loaded — click Refresh
            </div>
          )}

          {fpsOverMax && (
            <div className="fov-label fov-cropped">
              {currentFps} fps exceeds mode max ({maxFps} fps) — will be clamped on apply
            </div>
          )}

          {/* ── Image ── */}
          <div className="form-field">
            <label>Monochrome:</label>
            <input type="checkbox"
              checked={cam.monochrome ?? false}
              onChange={e => handleChange(["camera", "monochrome"], e)} />
          </div>
          <div className="form-field">
            <label>Brightness: {Number(brightness).toFixed(2)}</label>
            <input type="range" min="-1" max="1" step="0.05"
              value={brightness}
              className="brightness-slider"
              onChange={e => handleChange(["camera", "brightness"], e)} />
          </div>
          <div className="form-field">
            <label>Gain:</label>
            <input type="number" min="1" step="1"
              value={cam.gain ?? ""}
              onChange={e => handleChange(["camera", "gain"], e)} />
          </div>
          <div className="form-field">
            <label>Exposure time (µs):</label>
            <input type="number" min="1" step="100"
              value={cam.exposure_time ?? ""}
              onChange={e => handleChange(["camera", "exposure_time"], e)} />
          </div>

          {/* ── Recording ── */}
          <div className="form-field">
            <label>Bitrate (Mbps):</label>
            <input type="number" min="1" max="50" step="1"
              value={bitrateMb}
              onChange={e => handleChange(["camera", "bitrate_mb"], e)} />
          </div>
          <div className="filesize-preview">
            ~{gbPerHour} GB / hr at {bitrateMb} Mbps
          </div>

          {/* ── Overlays ── */}
          <div className="form-field">
            <label>Timestamp overlay:</label>
            <input type="checkbox"
              checked={overlayTimestamp}
              onChange={e => handleChange(["camera", "overlay_timestamp"], e)} />
          </div>
          {overlayTimestamp && (
            <div className="form-field">
              <label>Text size:</label>
              <select
                value={cam.text_size ?? "medium"}
                onChange={e => handleChange(["camera", "text_size"], e)}
              >
                <option value="small">Small</option>
                <option value="medium">Medium</option>
                <option value="large">Large</option>
              </select>
            </div>
          )}
          <div className="form-field">
            <label>Overlay framerate (preview):</label>
            <input type="checkbox"
              checked={cam.overlay_framerate_on_preview ?? false}
              onChange={e => handleChange(["camera", "overlay_framerate_on_preview"], e)} />
          </div>

          {/* ── Remaining config fields (non-camera sections) ── */}
          <form>
            <ConfigFields data={configFieldsData} handleChange={handleChange} />
          </form>

          {/* ── Copy section buttons ── */}
          <div className="copy-bar">
            <span className="copy-bar-label">Copy:</span>
            <button type="button" className="copy-btn"
              onClick={() => onCopy({ label: `Camera — ${module.name}`, data: { camera: formData.camera } })}>
              Camera
            </button>
            {otherSections.map(key => (
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

          {/* ── Apply to all cameras ── */}
          <div className="copy-bar">
            <span className="copy-bar-label">Apply to all cameras:</span>
            <button type="button" className="copy-btn"
              onClick={() => setApplyAllConfirm({ section: "camera", label: "Camera" })}>
              Camera
            </button>
            {otherSections.map(key => (
              <button key={key} type="button" className="copy-btn"
                onClick={() => setApplyAllConfirm({ section: key, label: capitalize(key) })}>
                {capitalize(key)}
              </button>
            ))}
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

      {/* ── Apply-to-all confirmation modal ── */}
      {applyAllConfirm && (
        <div className="modal-overlay" onClick={() => setApplyAllConfirm(null)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <p>
              Apply <strong>{applyAllConfirm.label}</strong> settings from{" "}
              <strong>{module.name}</strong> to all connected cameras?
            </p>
            <p className="modal-subtext">
              This will overwrite the {applyAllConfirm.label.toLowerCase()} config on every
              camera module and save immediately — unsaved changes on other cameras will be lost.
            </p>
            <div className="modal-buttons">
              <button className="save-button" type="button" onClick={confirmApplyToAll}>
                Apply to All
              </button>
              <button className="reset-button" type="button" onClick={() => setApplyAllConfirm(null)}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Reset confirmation modal ── */}
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

export default CameraConfigCard;
