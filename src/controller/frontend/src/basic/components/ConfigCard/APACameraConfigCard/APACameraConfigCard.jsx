import { useEffect, useState } from "react";
import socket from "/src/socket";
import LivestreamCard from "/src/basic/components/LivestreamCard/LivestreamCard";
import { useConfigForm } from "../useConfigForm";
import { filterPrivateKeys, checkClipboardCompatibility } from "../configUtils";
import { useModuleUpdate } from "/src/hooks/useModuleUpdate";

const HQ_PRESETS = [
  { key: "1080p30",  label: "1080p",        sub: "30 fps",  width: 1920, height: 1080, fps: 30  },
  { key: "1080p60",  label: "1080p Fast",   sub: "60 fps",  width: 1920, height: 1080, fps: 60  },
  { key: "square25", label: "Square",       sub: "25 fps",  width: 1000, height: 1000, fps: 25  },
  { key: "fast100",  label: "High Speed",   sub: "100 fps", width: 1332, height: 990,  fps: 100 },
  { key: "4k10",     label: "4K",           sub: "10 fps",  width: 4056, height: 3040, fps: 10  },
  { key: "custom",   label: "Custom",       sub: null,      width: null, height: null, fps: null },
];

const CM3_PRESETS = [
  { key: "cm3_2304p56",  label: "Full FoV",   sub: "56 fps",  width: 2304, height: 1296, fps: 56  },
  { key: "cm3_1536p120", label: "High Speed", sub: "120 fps", width: 1536, height: 864,  fps: 120 },
  { key: "1080p30",      label: "1080p",      sub: "30 fps",  width: 1920, height: 1080, fps: 30  },
  { key: "1080p60",      label: "1080p Fast", sub: "60 fps",  width: 1920, height: 1080, fps: 60  },
  { key: "square25",     label: "Square",     sub: "25 fps",  width: 1000, height: 1000, fps: 25  },
  { key: "custom",       label: "Custom",     sub: null,      width: null, height: null, fps: null },
];

function bestSensorMode(sensorModes, width, height, fps) {
  if (!sensorModes.length || !width || !height || !fps) return null;
  const candidates = sensorModes.filter(
    m => m.size[0] >= width && m.size[1] >= height && m.fps >= fps
  );
  if (!candidates.length) return null;
  return candidates.reduce((best, m) =>
    m.size[0] * m.size[1] > best.size[0] * best.size[1] ? m : best
  );
}

function detectPreset(presetList, width, height, fps) {
  return presetList.find(p => p.width === width && p.height === height && p.fps === fps)?.key ?? "custom";
}

function rgbToHex(color) {
  let r, g, b;
  if (Array.isArray(color))                      [r, g, b] = color;
  else if (color && typeof color === "object")   ({ r, g, b } = color);
  else                                           return "#00ff00";
  const toHex = v => Math.max(0, Math.min(255, v ?? 0)).toString(16).padStart(2, "0");
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

function hexToRgbObj(hex) {
  return {
    r: parseInt(hex.slice(1, 3), 16),
    g: parseInt(hex.slice(3, 5), 16),
    b: parseInt(hex.slice(5, 7), 16),
  };
}

function Section({ title, open, onToggle, children }) {
  return (
    <div className="nested-fieldset">
      <div className="nested-fieldset-legend" onClick={onToggle}>
        <span className="nested-fieldset-arrow">{open ? "▼" : "▶"}</span>
        {title}
      </div>
      {open && children}
    </div>
  );
}

function APACameraConfigCard({ id, module, clipboard, onCopy }) {
  const { formData, setFormData, handleChange } = useConfigForm(module.config);
  const [sensorModes, setSensorModes]     = useState([]);
  const [sensorModel, setSensorModel]     = useState("");
  const [hasAutofocus, setHasAutofocus]   = useState(false);
  const [activePreset, setActivePreset]   = useState("custom");
  const [showResetConfirm, setShowResetConfirm]   = useState(false);
  const [showRebootConfirm, setShowRebootConfirm] = useState(false);
  const [applyAllConfirm, setApplyAllConfirm]     = useState(null);
  const [hasSaved, setHasSaved]           = useState(false);
  const [maskOpen, setMaskOpen]           = useState(true);
  const [shockZoneOpen, setShockZoneOpen] = useState(true);
  const [detectionOpen, setDetectionOpen] = useState(false);
  const [blobOpen, setBlobOpen]           = useState(true);
  const { updateStatus, handleUpdate }    = useModuleUpdate(module.id);

  const presets = hasAutofocus ? CM3_PRESETS : HQ_PRESETS;

  useEffect(() => {
    socket.emit("get_module_config", { module_id: module.id });
    socket.emit("send_command", { module_id: module.id, type: "get_sensor_modes", params: {} });
    const onSensorModes = (data) => {
      if (data.module_id !== module.id) return;
      setSensorModes(data.sensor_modes);
      if (data.sensor_model !== undefined) setSensorModel(data.sensor_model);
      if (data.has_autofocus !== undefined) setHasAutofocus(data.has_autofocus);
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
    if (!formData?.camera) return;
    const { width, height, fps } = formData.camera;
    setActivePreset(detectPreset(presets, width, height, fps));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [formData?.camera?.width, formData?.camera?.height, formData?.camera?.fps, presets]);

  const handlePresetSelect = (preset) => {
    setActivePreset(preset.key);
    if (preset.key === "custom") return;
    const best = bestSensorMode(sensorModes, preset.width, preset.height, preset.fps);
    setFormData(prev => {
      const cloned = structuredClone(prev);
      cloned.camera.width  = preset.width;
      cloned.camera.height = preset.height;
      cloned.camera.fps    = preset.fps;
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

  const handleColorChange = (hex) => {
    setFormData(prev => {
      const cloned = structuredClone(prev);
      cloned.shock_zone.shock_zone_color = hexToRgbObj(hex);
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

  const handleSave = () => {
    setHasSaved(true);
    const filtered = filterPrivateKeys(formData) ?? {};

    // filterPrivateKeys drops arrays — restore shock_zone_color and labels
    const rawColor = formData?.shock_zone?.shock_zone_color;
    if (rawColor) {
      filtered.shock_zone = filtered.shock_zone ?? {};
      filtered.shock_zone.shock_zone_color = Array.isArray(rawColor)
        ? { r: rawColor[0], g: rawColor[1], b: rawColor[2] }
        : rawColor;
    }
    const rawLabels = formData?.object_detection?.labels;
    if (Array.isArray(rawLabels)) {
      filtered.object_detection = filtered.object_detection ?? {};
      filtered.object_detection.labels = rawLabels;
    }

    socket.emit("save_module_config", { id, config: filtered });
  };

  const handleReset = () => {
    socket.emit("reset_module_config", { module_id: module.id });
    setShowResetConfirm(false);
  };

  const confirmApplyToAll = () => {
    if (!applyAllConfirm) return;
    const { section, moduleType } = applyAllConfirm;
    const filtered = filterPrivateKeys(formData);
    const data = filtered?.[section];
    if (data) socket.emit("apply_section_to_type", { module_type: moduleType ?? null, section, data });
    setApplyAllConfirm(null);
  };

  const cam         = formData?.camera ?? {};
  const mask        = formData?.mask ?? {};
  const shockZone   = formData?.shock_zone ?? {};
  const detection   = formData?.object_detection ?? {};
  const blobTracker = formData?.blob_tracker ?? {};

  const selectedMode = sensorModes[cam.sensor_mode_index ?? 0];
  const maxFps    = selectedMode?.fps ?? null;
  const fpsOverMax = maxFps != null && cam.fps != null && Number(cam.fps) > maxFps;
  const maxWidth  = sensorModes.length ? Math.max(...sensorModes.map(m => m.size[0])) : 9999;
  const maxHeight = sensorModes.length ? Math.max(...sensorModes.map(m => m.size[1])) : 9999;
  const maxFpsAll = sensorModes.length ? Math.max(...sensorModes.map(m => m.fps)) : 999;
  const bitrateMb  = cam.bitrate_mb ?? 0;
  const gbPerHour  = (bitrateMb * 3600 / 8 / 1000).toFixed(2);
  const colorHex   = rgbToHex(shockZone.shock_zone_color);

  const COPY_SECTIONS = ["camera", "mask", "shock_zone", "object_detection", "blob_tracker"];
  const sectionLabel = key => key.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());

  return (
    <div className="config-card">
      <div className="card-header">
        <h3>{module.name} ({module.id})</h3>
        <div className="device-info">
          {typeof module.ip === "string" && module.ip && <span>IP: {module.ip}</span>}
          {typeof module.version === "string" && module.version && <span>{module.version}</span>}
        </div>
      </div>

      <div className="config-card-body">
        <div className="config-form">

          {/* ── Clipboard paste bar ── */}
          {clipboard && (() => {
            const pasteError = checkClipboardCompatibility(clipboard.data, formData);
            return (
              <div className="clipboard-bar">
                <span className="clipboard-label">Clipboard: {clipboard.label}</span>
                <button type="button" className="copy-btn" onClick={handlePaste} disabled={!!pasteError}>Paste</button>
                <button type="button" className="copy-btn" onClick={() => onCopy(null)}>Clear</button>
                {pasteError && <span className="config-sync-badge config-sync-badge--failed">{pasteError}</span>}
              </div>
            );
          })()}

          {/* ── Resolution / mode ── */}
          <div className="form-field">
            <label>Mode:</label>
            <select
              value={activePreset}
              onChange={e => {
                const preset = presets.find(p => p.key === e.target.value);
                if (preset) handlePresetSelect(preset);
              }}
            >
              {presets.map(p => (
                <option key={p.key} value={p.key}>
                  {p.label}{p.sub ? ` — ${p.sub}` : ""}
                </option>
              ))}
            </select>
          </div>

          {activePreset === "custom" ? (
            <>
              <div className="form-field">
                <label>Width (px):</label>
                <input type="number" min="64" max={maxWidth} step="2"
                  value={cam.width ?? ""}
                  onChange={e => handleCustomChange("width", e.target.value)} />
              </div>
              <div className="form-field">
                <label>Height (px):</label>
                <input type="number" min="64" max={maxHeight} step="2"
                  value={cam.height ?? ""}
                  onChange={e => handleCustomChange("height", e.target.value)} />
              </div>
              <div className="form-field">
                <label>FPS:</label>
                <input type="number" min="1" max={maxFpsAll} step="1"
                  value={cam.fps ?? ""}
                  onChange={e => handleCustomChange("fps", e.target.value)} />
              </div>
            </>
          ) : (
            cam.width != null && (
              <div className="camera-output-summary">
                {cam.width} × {cam.height} &middot; {cam.fps} fps
              </div>
            )
          )}

          {selectedMode ? (
            <div className="sensor-mode-info">Sensor mode: {selectedMode.label}</div>
          ) : (
            <div className="sensor-mode-info sensor-mode-info--muted">
              Sensor modes not yet loaded — click Refresh
            </div>
          )}

          {fpsOverMax && (
            <div className="fov-label fov-cropped">
              {cam.fps} fps exceeds mode max ({maxFps} fps) — will be clamped on apply
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
            <label>Brightness: {Number(cam.brightness ?? 0).toFixed(2)}</label>
            <input type="range" min="-1" max="1" step="0.05"
              value={cam.brightness ?? 0}
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
            <div className="exposure-control">
              <input type="number" min="1" step="100"
                disabled={!cam.manual_exposure}
                value={cam.manual_exposure
                  ? (cam.exposure_time ?? "")
                  : (cam.fps ? Math.round(1_000_000 / cam.fps) : "")}
                onChange={e => handleChange(["camera", "exposure_time"], e)} />
              <label className="exposure-manual-label">
                <input type="checkbox"
                  checked={cam.manual_exposure ?? false}
                  onChange={e => handleChange(["camera", "manual_exposure"], e)} />
                Manual
              </label>
            </div>
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

          {/* ── Autofocus (IMX708 only) ── */}
          {hasAutofocus && (
            <>
              <div className="form-field">
                <label>Autofocus mode:</label>
                <select
                  value={cam.autofocus_mode ?? "manual"}
                  onChange={e => handleChange(["camera", "autofocus_mode"], e)}
                >
                  <option value="manual">Manual</option>
                  <option value="auto">Auto (one-shot)</option>
                  <option value="continuous">Continuous</option>
                </select>
              </div>
              {(cam.autofocus_mode ?? "manual") === "manual" && (
                <div className="form-field">
                  <label>Lens position: {Number(cam.lens_position ?? 0).toFixed(1)}</label>
                  <input type="range" min="0" max="10" step="0.1"
                    value={cam.lens_position ?? 0}
                    className="brightness-slider"
                    onChange={e => handleChange(["camera", "lens_position"], e)} />
                </div>
              )}
            </>
          )}

          {/* ── Overlays ── */}
          <div className="form-field">
            <label>Timestamp overlay:</label>
            <input type="checkbox"
              checked={cam.overlay_timestamp ?? true}
              onChange={e => handleChange(["camera", "overlay_timestamp"], e)} />
          </div>
          {(cam.overlay_timestamp ?? true) && (
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

          {/* ── Mask ── */}
          <Section title="Mask" open={maskOpen} onToggle={() => setMaskOpen(p => !p)}>
            <div className="form-field">
              <label>Enabled:</label>
              <input type="checkbox"
                checked={mask.mask_enabled ?? true}
                onChange={e => handleChange(["mask", "mask_enabled"], e)} />
            </div>
            <div className="form-field">
              <label>Radius: {Number(mask.mask_radius ?? 0.65).toFixed(2)}</label>
              <input type="range" min="0.1" max="1" step="0.01"
                value={mask.mask_radius ?? 0.65}
                className="brightness-slider"
                onChange={e => handleChange(["mask", "mask_radius"], e)} />
            </div>
            <div className="form-field">
              <label>Center X offset (px):</label>
              <input type="number" step="1"
                value={mask.mask_center_x_offset ?? 0}
                onChange={e => handleChange(["mask", "mask_center_x_offset"], e)} />
            </div>
            <div className="form-field">
              <label>Center Y offset (px):</label>
              <input type="number" step="1"
                value={mask.mask_center_y_offset ?? 0}
                onChange={e => handleChange(["mask", "mask_center_y_offset"], e)} />
            </div>
          </Section>

          {/* ── Shock Zone ── */}
          <Section title="Shock Zone" open={shockZoneOpen} onToggle={() => setShockZoneOpen(p => !p)}>
            <div className="form-field">
              <label>Enabled:</label>
              <input type="checkbox"
                checked={shockZone.shock_zone_enabled ?? true}
                onChange={e => handleChange(["shock_zone", "shock_zone_enabled"], e)} />
            </div>
            <div className="form-field">
              <label>Display overlay:</label>
              <input type="checkbox"
                checked={shockZone.shock_zone_display ?? true}
                onChange={e => handleChange(["shock_zone", "shock_zone_display"], e)} />
            </div>
            <div className="form-field">
              <label>Start angle (°):</label>
              <input type="number" min="0" max="360" step="1"
                value={shockZone.shock_zone_start_angle_deg ?? 180}
                onChange={e => handleChange(["shock_zone", "shock_zone_start_angle_deg"], e)} />
            </div>
            <div className="form-field">
              <label>Span (°):</label>
              <input type="number" min="1" max="360" step="1"
                value={shockZone.shock_zone_angle_span_deg ?? 60}
                onChange={e => handleChange(["shock_zone", "shock_zone_angle_span_deg"], e)} />
            </div>
            <div className="form-field">
              <label>Inner offset: {Number(shockZone.shock_zone_inner_offset ?? 0.3).toFixed(2)}</label>
              <input type="range" min="0" max="0.99" step="0.01"
                value={shockZone.shock_zone_inner_offset ?? 0.3}
                className="brightness-slider"
                onChange={e => handleChange(["shock_zone", "shock_zone_inner_offset"], e)} />
            </div>
            <div className="form-field">
              <label>Colour:</label>
              <input type="color"
                value={colorHex}
                onChange={e => handleColorChange(e.target.value)} />
            </div>
            <div className="form-field">
              <label>Line thickness (px):</label>
              <input type="number" min="1" max="20" step="1"
                value={shockZone.shock_zone_line_thickness ?? 3}
                onChange={e => handleChange(["shock_zone", "shock_zone_line_thickness"], e)} />
            </div>
          </Section>

          {/* ── Object Detection ── */}
          <Section title="Object Detection" open={detectionOpen} onToggle={() => setDetectionOpen(p => !p)}>
            <div className="form-field">
              <label>Enabled:</label>
              <input type="checkbox"
                checked={detection.enabled ?? false}
                onChange={e => handleChange(["object_detection", "enabled"], e)} />
            </div>
            {(detection.enabled ?? false) && (
              <>
                <div className="form-field">
                  <label>Backend:</label>
                  <select
                    value={detection.backend ?? "blob"}
                    onChange={e => handleChange(["object_detection", "backend"], e)}
                  >
                    <option value="blob">Blob tracker (no model required)</option>
                    <option value="hailo">Hailo .hef model</option>
                  </select>
                </div>

                {(detection.backend ?? "blob") === "hailo" ? (
                  <>
                    <div className="form-field">
                      <label>Model path:</label>
                      <input type="text"
                        value={detection.model_path ?? ""}
                        onChange={e => handleChange(["object_detection", "model_path"], e)} />
                    </div>
                    <div className="form-field">
                      <label>Threshold: {Number(detection.threshold ?? 0.55).toFixed(2)}</label>
                      <input type="range" min="0.05" max="1" step="0.01"
                        value={detection.threshold ?? 0.55}
                        className="brightness-slider"
                        onChange={e => handleChange(["object_detection", "threshold"], e)} />
                    </div>
                    <div className="form-field">
                      <label>Max detections:</label>
                      <input type="number" min="1" max="10" step="1"
                        value={detection.max_detections ?? 2}
                        onChange={e => handleChange(["object_detection", "max_detections"], e)} />
                    </div>
                    <div className="form-field">
                      <label>Coordinate smoothing:</label>
                      <input type="checkbox"
                        checked={detection.coordinate_smoothing ?? false}
                        onChange={e => handleChange(["object_detection", "coordinate_smoothing"], e)} />
                    </div>
                  </>
                ) : (
                  <Section title="Blob tracker settings" open={blobOpen} onToggle={() => setBlobOpen(p => !p)}>
                    <div className="form-field">
                      <label>Process width (px):</label>
                      <input type="number" min="64" max="1920" step="16"
                        value={blobTracker.process_width ?? 256}
                        onChange={e => handleChange(["blob_tracker", "process_width"], e)} />
                    </div>
                    <div className="form-field">
                      <label>Diff threshold: {Number(blobTracker.thr_hi ?? 5).toFixed(1)}</label>
                      <input type="range" min="1" max="50" step="0.5"
                        value={blobTracker.thr_hi ?? 5}
                        className="brightness-slider"
                        onChange={e => handleChange(["blob_tracker", "thr_hi"], e)} />
                    </div>
                    <div className="form-field">
                      <label>H gap fill (px):</label>
                      <input type="number" min="0" max="100" step="1"
                        value={blobTracker.gap_h_px ?? 15}
                        onChange={e => handleChange(["blob_tracker", "gap_h_px"], e)} />
                    </div>
                    <div className="form-field">
                      <label>V gap fill (px):</label>
                      <input type="number" min="0" max="100" step="1"
                        value={blobTracker.gap_v_px ?? 15}
                        onChange={e => handleChange(["blob_tracker", "gap_v_px"], e)} />
                    </div>
                    <div className="form-field">
                      <label>Close kernel (px):</label>
                      <input type="number" min="0" max="50" step="1"
                        value={blobTracker.close_px ?? 7}
                        onChange={e => handleChange(["blob_tracker", "close_px"], e)} />
                    </div>
                    <div className="form-field">
                      <label>Open kernel (px):</label>
                      <input type="number" min="0" max="50" step="1"
                        value={blobTracker.open_px ?? 5}
                        onChange={e => handleChange(["blob_tracker", "open_px"], e)} />
                    </div>
                    <div className="form-field">
                      <label>Min blob area (px²):</label>
                      <input type="number" min="1" max="10000" step="10"
                        value={blobTracker.min_area_px ?? 50}
                        onChange={e => handleChange(["blob_tracker", "min_area_px"], e)} />
                    </div>
                    <div className="form-field">
                      <label>Patience (frames):</label>
                      <input type="number" min="0" max="120" step="1"
                        value={blobTracker.patience_frames ?? 10}
                        onChange={e => handleChange(["blob_tracker", "patience_frames"], e)} />
                    </div>
                    <div className="form-field">
                      <label>Smoothing: {Number(blobTracker.smoothing_alpha ?? 0.3).toFixed(2)}</label>
                      <input type="range" min="0" max="1" step="0.05"
                        value={blobTracker.smoothing_alpha ?? 0.3}
                        className="brightness-slider"
                        onChange={e => handleChange(["blob_tracker", "smoothing_alpha"], e)} />
                    </div>
                    <div className="form-field">
                      <label>Track box size (px):</label>
                      <input type="number" min="20" max="600" step="10"
                        value={blobTracker.track_square_size ?? 150}
                        onChange={e => handleChange(["blob_tracker", "track_square_size"], e)} />
                    </div>
                  </Section>
                )}
              </>
            )}
          </Section>

          {/* ── Copy section buttons ── */}
          <div className="copy-bar">
            <span className="copy-bar-label">Copy:</span>
            {COPY_SECTIONS.filter(k => formData?.[k]).map(key => (
              <button key={key} type="button" className="copy-btn"
                onClick={() => onCopy({ label: `${sectionLabel(key)} — ${module.name}`, data: { [key]: formData[key] } })}>
                {sectionLabel(key)}
              </button>
            ))}
            <button type="button" className="copy-btn"
              onClick={() => onCopy({ label: `All — ${module.name}`, data: filterPrivateKeys(formData) })}>
              All
            </button>
          </div>

          {/* ── Apply to all APA cameras ── */}
          <div className="copy-bar">
            <span className="copy-bar-label">Apply to all APA cameras:</span>
            {COPY_SECTIONS.filter(k => formData?.[k]).map(key => (
              <button key={key} type="button" className="copy-btn"
                onClick={() => setApplyAllConfirm({ section: key, label: sectionLabel(key), moduleType: module.type })}>
                {sectionLabel(key)}
              </button>
            ))}
          </div>

          <div className="config-action-buttons">
            <button className="save-button" type="button" onClick={handleSave}>Save Config</button>
            <button className="reset-button" type="button" onClick={() => setShowResetConfirm(true)}>Reset to Default</button>
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
              <button className="reset-button" type="button" onClick={() => {
                socket.emit("send_command", { module_id: module.id, type: "reboot", params: {} });
                setShowRebootConfirm(false);
              }}>Reboot</button>
              <button className="save-button" type="button" onClick={() => setShowRebootConfirm(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {applyAllConfirm && (
        <div className="modal-overlay" onClick={() => setApplyAllConfirm(null)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <p>
              Apply <strong>{applyAllConfirm.label}</strong> settings from{" "}
              <strong>{module.name}</strong> to all connected APA cameras?
            </p>
            <p className="modal-subtext">
              This will overwrite the {applyAllConfirm.label.toLowerCase()} config on every APA camera
              and save immediately — unsaved changes on other modules will be lost.
            </p>
            <div className="modal-buttons">
              <button className="save-button" type="button" onClick={confirmApplyToAll}>Apply to All</button>
              <button className="reset-button" type="button" onClick={() => setApplyAllConfirm(null)}>Cancel</button>
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
              <button className="reset-button" type="button" onClick={handleReset}>Reset</button>
              <button className="save-button" type="button" onClick={() => setShowResetConfirm(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default APACameraConfigCard;
