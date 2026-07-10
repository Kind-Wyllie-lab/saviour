import { useEffect, useState } from "react";
import socket from "/src/socket";
import LivestreamCard from "/src/basic/components/LivestreamCard/LivestreamCard";
import { useConfigForm } from "../useConfigForm";
import LoomRoiLineEditorModal from "/src/basic/components/LoomRoiLineEditorModal/LoomRoiLineEditorModal";
import ExportConfigSection from "../ExportConfigSection";
import ConfigFields from "../ConfigFields";
import ConfigCardShell from "../ConfigCardShell";

const HQ_PRESETS = [
  { key: "1080p30",  label: "1080p",       sub: "30 fps",  width: 1920, height: 1080, fps: 30  },
  { key: "1080p60",  label: "1080p Fast",  sub: "60 fps",  width: 1920, height: 1080, fps: 60  },
  { key: "square25", label: "Square",      sub: "25 fps",  width: 1000, height: 1000, fps: 25  },
  { key: "fast100",  label: "High Speed",  sub: "100 fps", width: 1332, height: 990,  fps: 100 },
  { key: "4k10",     label: "4K",          sub: "10 fps",  width: 4056, height: 3040, fps: 10  },
  { key: "custom",   label: "Custom",      sub: null,      width: null, height: null, fps: null },
];

const CM3_PRESETS = [
  { key: "cm3_2304p56",  label: "Full FoV",   sub: "56 fps",  width: 2304, height: 1296, fps: 56  },
  { key: "cm3_1536p120", label: "High Speed", sub: "120 fps", width: 1536, height: 864,  fps: 120 },
  { key: "1080p30",      label: "1080p",      sub: "30 fps",  width: 1920, height: 1080, fps: 30  },
  { key: "1080p60",      label: "1080p Fast", sub: "60 fps",  width: 1920, height: 1080, fps: 60  },
  { key: "square25",     label: "Square",     sub: "25 fps",  width: 1000, height: 1000, fps: 25  },
  { key: "custom",       label: "Custom",     sub: null,      width: null, height: null, fps: null },
];

const BASE_TABS = [
  { key: "basic",  label: "Basic"  },
  { key: "image",  label: "Image"  },
  { key: "record", label: "Record" },
];

const LOOM_TABS = [
  { key: "tracking", label: "Tracking" },
  { key: "stimulus", label: "Stimulus" },
];

const EXPORT_TAB = { key: "export", label: "Export" };

const TAB_COPY_SECTION = {
  basic:    { key: "module",        label: "Basic"    },
  image:    { key: "camera",        label: "Image"    },
  record:   { key: "camera",        label: "Record"   },
  tracking: { key: "loom_tracking", label: "Tracking" },
  stimulus: { key: "loom_stimulus", label: "Stimulus" },
  export:   { key: "export",        label: "Export"   },
};

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

function CameraConfigCard({ id, module, clipboard, onCopy, syncServerModule }) {
  const { formData, setFormData, handleChange } = useConfigForm(module.config);
  const [sensorModes, setSensorModes] = useState([]);
  const [sensorModel, setSensorModel] = useState("");
  const [hasAutofocus, setHasAutofocus] = useState(false);
  const [activePreset, setActivePreset] = useState("custom");
  const [activeTab, setActiveTab] = useState("basic");
  const [showLoomRoiEditor, setShowLoomRoiEditor] = useState(false);

  const presets = hasAutofocus ? CM3_PRESETS : HQ_PRESETS;

  useEffect(() => {
    socket.emit("get_module_config", { module_id: module.id });
    socket.emit("send_command", { module_id: module.id, type: "get_sensor_modes", params: {} });
    const onSensorModes = (data) => {
      if (data.module_id === module.id) {
        setSensorModes(data.sensor_modes);
        if (data.sensor_model !== undefined) setSensorModel(data.sensor_model);
        if (data.has_autofocus !== undefined) setHasAutofocus(data.has_autofocus);
      }
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
      setActivePreset(detectPreset(presets, width, height, fps));
    }
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

  const handleTriggerAutofocus = () => {
    socket.emit("send_command", { module_id: module.id, type: "trigger_autofocus", params: {} });
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

  const cam              = formData?.camera ?? {};
  const currentWidth     = cam.width;
  const currentHeight    = cam.height;
  const currentFps       = cam.fps;
  const selectedMode     = sensorModes[cam.sensor_mode_index ?? 0];
  const maxFps           = selectedMode?.fps ?? null;
  const fpsOverMax       = maxFps != null && currentFps != null && Number(currentFps) > maxFps;
  const maxWidth         = sensorModes.length ? Math.max(...sensorModes.map(m => m.size[0])) : 9999;
  const maxHeight        = sensorModes.length ? Math.max(...sensorModes.map(m => m.size[1])) : 9999;
  const maxFpsAll        = sensorModes.length ? Math.max(...sensorModes.map(m => m.fps)) : 999;
  const overlayTimestamp = cam.overlay_timestamp ?? true;
  const brightness       = cam.brightness ?? 0;
  const bitrateMb        = cam.bitrate_mb ?? 0;
  const gbPerHour        = (bitrateMb * 3600 / 8 / 1000).toFixed(2);

  const isThisServer     = syncServerModule?.id === module.id;
  const otherIsServer    = syncServerModule != null && !isThisServer;
  const currentSyncMode  = cam.sync_mode ?? "none";
  const syncExposureLocked = currentSyncMode === "client" && cam.sync_lock_exposure;
  const aeEnabled        = (cam.ae_enable ?? false) && !syncExposureLocked;
  const serverCam        = syncServerModule?.config?.camera ?? {};
  const serverFps        = serverCam.fps != null ? Number(serverCam.fps) : null;
  const serverExposureUs = serverCam.manual_exposure
    ? Number(serverCam.exposure_time)
    : (serverFps ? Math.round(1_000_000 / serverFps) : null);
  const clientExposureUs = cam.manual_exposure
    ? Number(cam.exposure_time)
    : (cam.fps ? Math.round(1_000_000 / cam.fps) : null);
  const fpsMismatch = currentSyncMode === "client" && syncServerModule && serverFps != null && Number(cam.fps) !== serverFps;
  const exposureMismatch = currentSyncMode === "client" && syncServerModule && serverExposureUs != null && clientExposureUs != null && clientExposureUs !== serverExposureUs;
  const hasSyncWarning   = fpsMismatch || exposureMismatch || (currentSyncMode === "client" && !syncServerModule);

  const tabs = [...BASE_TABS, ...(module.type === "loom_camera" ? LOOM_TABS : []), EXPORT_TAB];

  return (
    <>
      <ConfigCardShell
        id={id}
        module={module}
        formData={formData}
        clipboard={clipboard}
        onCopy={onCopy}
        onPaste={handlePaste}
        tabs={tabs}
        activeTab={activeTab}
        onTabChange={setActiveTab}
        tabSectionMap={TAB_COPY_SECTION}
        deviceInfoExtras={[sensorModel]}
        tabBadges={hasSyncWarning ? { record: "⚠" } : {}}
        sidebar={
          <>
            <LivestreamCard module={module} />
            {module.type === "loom_camera" && (
              <div style={{ display: "flex", justifyContent: "center", marginTop: "8px" }}>
                <button type="button" className="copy-btn" onClick={() => setShowLoomRoiEditor(true)}>
                  Set ROI / Line
                </button>
              </div>
            )}
          </>
        }
      >
        {/* BASIC */}
        {activeTab === "basic" && (
          <>
            <div className="form-field">
              <label>Name:</label>
              <input type="text"
                value={formData?.module?.name ?? ""}
                onChange={e => handleChange(["module", "name"], e)} />
            </div>
            <div className="form-field">
              <label>Group:</label>
              <input type="text"
                value={formData?.module?.group ?? ""}
                onChange={e => handleChange(["module", "group"], e)} />
            </div>
          </>
        )}

        {/* IMAGE */}
        {activeTab === "image" && (
          <>
            {currentSyncMode !== "client" && (
              <div className="form-field">
                <label>Mode:</label>
                <select value={activePreset}
                  onChange={e => {
                    const preset = presets.find(p => p.key === e.target.value);
                    if (preset) handlePresetSelect(preset);
                  }}>
                  {presets.map(p => (
                    <option key={p.key} value={p.key}>
                      {p.label}{p.sub ? ` — ${p.sub}` : ""}
                    </option>
                  ))}
                </select>
              </div>
            )}
            {(activePreset === "custom" || currentSyncMode === "client") ? (
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
                    disabled={currentSyncMode === "client"}
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
            {currentSyncMode === "client" && serverFps != null && (
              <div className="sensor-mode-info">
                FPS locked to {serverFps} fps via frame sync — resolution can be changed freely.
              </div>
            )}
            {selectedMode && currentSyncMode !== "client" && (
              <div className="sensor-mode-info">Sensor mode: {selectedMode.label}</div>
            )}
            {!selectedMode && currentSyncMode !== "client" && (
              <div className="sensor-mode-info sensor-mode-info--muted">
                Sensor modes not yet loaded
              </div>
            )}
            {fpsOverMax && (
              <div className="fov-label fov-cropped">
                {currentFps} fps exceeds mode max ({maxFps} fps) — will be clamped on apply
              </div>
            )}

            <div className="config-section-divider" />
            <div className="form-field">
              <label>Monochrome:</label>
              <input type="checkbox"
                checked={cam.monochrome ?? false}
                onChange={e => handleChange(["camera", "monochrome"], e)} />
            </div>
            <div className="form-field">
              <label>Flip horizontal:</label>
              <input type="checkbox"
                checked={cam.hflip ?? false}
                onChange={e => handleChange(["camera", "hflip"], e)} />
            </div>
            <div className="form-field">
              <label>Flip vertical:</label>
              <input type="checkbox"
                checked={cam.vflip ?? false}
                onChange={e => handleChange(["camera", "vflip"], e)} />
            </div>
            <div className="form-field">
              <label>Brightness: {Number(brightness).toFixed(2)}</label>
              <input type="range" min="-1" max="1" step="0.05"
                value={brightness} className="brightness-slider"
                onChange={e => handleChange(["camera", "brightness"], e)} />
            </div>
            <div className="form-field">
              <label>Auto gain/exposure:</label>
              <input type="checkbox"
                checked={cam.ae_enable ?? false}
                disabled={syncExposureLocked}
                onChange={e => handleChange(["camera", "ae_enable"], e)} />
            </div>
            {syncExposureLocked && (cam.ae_enable ?? false) && (
              <div className="sensor-mode-info">
                Overridden by "Lock exposure" (Record tab) while frame-synced.
              </div>
            )}
            <div className="form-field">
              <label>Gain:</label>
              <input type="number" min="1" step="1"
                disabled={aeEnabled}
                value={cam.gain ?? ""}
                onChange={e => handleChange(["camera", "gain"], e)} />
            </div>
            <div className="form-field">
              <label>Exposure time (µs):</label>
              <div className="exposure-control">
                <input type="number" min="1" step="100"
                  disabled={aeEnabled || !cam.manual_exposure || (currentSyncMode === "client" && cam.sync_lock_exposure)}
                  value={
                    currentSyncMode === "client" && cam.sync_lock_exposure && serverExposureUs != null
                      ? serverExposureUs
                      : cam.manual_exposure
                        ? (cam.exposure_time ?? "")
                        : (cam.fps ? Math.round(1_000_000 / cam.fps) : "")
                  }
                  onChange={e => handleChange(["camera", "exposure_time"], e)} />
                <label className="exposure-manual-label">
                  <input type="checkbox"
                    checked={cam.manual_exposure ?? false}
                    disabled={aeEnabled || (currentSyncMode === "client" && cam.sync_lock_exposure)}
                    onChange={e => handleChange(["camera", "manual_exposure"], e)} />
                  Manual
                </label>
              </div>
            </div>
            {currentSyncMode === "client" && cam.sync_lock_exposure && serverExposureUs != null && (
              <div className="sensor-mode-info">
                Exposure locked to server ({serverExposureUs} µs).
              </div>
            )}

            {hasAutofocus && (
              <>
                <div className="config-section-divider" />
                <div className="form-field">
                  <label>Autofocus mode:</label>
                  <select value={cam.autofocus_mode ?? "manual"}
                    onChange={e => handleChange(["camera", "autofocus_mode"], e)}>
                    <option value="manual">Manual</option>
                    <option value="auto">Auto (one-shot)</option>
                    <option value="continuous">Continuous</option>
                  </select>
                </div>
                {(cam.autofocus_mode ?? "manual") === "manual" && (
                  <div className="form-field">
                    <label>Lens position: {Number(cam.lens_position ?? 0).toFixed(1)}</label>
                    <input type="range" min="0" max="10" step="0.1"
                      value={cam.lens_position ?? 0} className="brightness-slider"
                      onChange={e => handleChange(["camera", "lens_position"], e)} />
                  </div>
                )}
                {(cam.autofocus_mode ?? "manual") === "auto" && (
                  <div className="form-field">
                    <label></label>
                    <button type="button" className="copy-btn" onClick={handleTriggerAutofocus}>
                      Trigger Autofocus
                    </button>
                  </div>
                )}
              </>
            )}
          </>
        )}

        {/* RECORD */}
        {activeTab === "record" && (
          <>
            <div className="form-field">
              <label>Frame sync:</label>
              <select value={currentSyncMode}
                onChange={e => handleChange(["camera", "sync_mode"], e)}>
                <option value="none">None</option>
                <option value="server" disabled={otherIsServer}>
                  Server (broadcasts timing){otherIsServer ? ` — ${syncServerModule.name} is already server` : ""}
                </option>
                <option value="client" disabled={!otherIsServer}>
                  Client (follows server){!otherIsServer ? " — set another camera to Server first" : ""}
                </option>
              </select>
            </div>
            {currentSyncMode === "server" && (
              <div className="sensor-mode-info">
                This camera broadcasts sync timing. Start client cameras first, then this one.
              </div>
            )}
            {currentSyncMode === "client" && !syncServerModule && (
              <div className="fov-label fov-cropped">
                No server camera configured — set another camera to Server first.
              </div>
            )}
            {currentSyncMode === "client" && syncServerModule && (
              <div className="sensor-mode-info">
                Syncing to {syncServerModule.name} ({syncServerModule.id}).
                {serverFps != null && <> FPS locked to {serverFps} fps.</>}
              </div>
            )}
            {fpsMismatch && (
              <div className="fov-label fov-cropped">
                FPS mismatch: this camera is {cam.fps} fps but server {syncServerModule.name} is {serverFps} fps — frame sync will not be 1:1.
              </div>
            )}
            {exposureMismatch && !fpsMismatch && (
              <div className="fov-label fov-cropped" style={{ opacity: 0.8 }}>
                Exposure mismatch: {clientExposureUs}µs here vs {serverExposureUs}µs on server — brightness will differ.
              </div>
            )}
            <div className="form-field">
              <label>Lock exposure:</label>
              <input type="checkbox"
                checked={cam.sync_lock_exposure ?? false}
                onChange={e => handleChange(["camera", "sync_lock_exposure"], e)} />
            </div>
            <div className="form-field">
              <label>Lock white balance:</label>
              <input type="checkbox"
                checked={cam.sync_lock_awb ?? false}
                onChange={e => handleChange(["camera", "sync_lock_awb"], e)} />
            </div>

            <div className="config-section-divider" />
            <div className="form-field">
              <label>Bitrate (Mbps):</label>
              <input type="number" min="1" max="50" step="1"
                value={bitrateMb}
                onChange={e => handleChange(["camera", "bitrate_mb"], e)} />
            </div>
            <div className="filesize-preview">~{gbPerHour} GB / hr at {bitrateMb} Mbps</div>
            <div className="form-field">
              <label>Timestamp overlay:</label>
              <input type="checkbox"
                checked={overlayTimestamp}
                onChange={e => handleChange(["camera", "overlay_timestamp"], e)} />
            </div>
            {overlayTimestamp && (
              <div className="form-field">
                <label>Text size:</label>
                <select value={cam.text_size ?? "medium"}
                  onChange={e => handleChange(["camera", "text_size"], e)}>
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
            <div className="form-field">
              <label>Livestream quality:</label>
              <select value={cam.livestream_quality ?? "normal"}
                onChange={e => handleChange(["camera", "livestream_quality"], e)}>
                <option value="normal">Normal (low-res)</option>
                <option value="high">High (recording resolution)</option>
              </select>
            </div>
            <div className="config-section-divider" />
            <div className="form-field">
              <label>Segment length (mins):</label>
              <input type="number" min="1" step="1"
                value={formData?.recording?.segment_length_mins ?? 60}
                onChange={e => handleChange(["recording", "segment_length_mins"], e)} />
            </div>
          </>
        )}

        {/* LOOM TRACKING */}
        {activeTab === "tracking" && (
          <ConfigFields
            data={formData?.loom_tracking}
            handleChange={(path, e) => handleChange(["loom_tracking", ...path], e)}
          />
        )}

        {/* LOOM STIMULUS */}
        {activeTab === "stimulus" && (
          <>
            <div className="stimulus-test-row">
              <button type="button" className="copy-btn"
                onClick={() => socket.emit("send_command", { module_id: id, type: "loom_stimulus_start", params: {} })}>
                Fire stimulus
              </button>
              <button type="button" className="copy-btn"
                onClick={() => socket.emit("send_command", { module_id: id, type: "loom_stimulus_stop", params: {} })}>
                Stop stimulus
              </button>
            </div>
            <div className="config-section-divider" />
            <ConfigFields
              data={formData?.loom_stimulus}
              handleChange={(path, e) => handleChange(["loom_stimulus", ...path], e)}
            />
          </>
        )}

        {/* EXPORT */}
        {activeTab === "export" && (
          <ExportConfigSection
            exportConfig={formData?.export}
            handleChange={handleChange}
            moduleId={id}
          />
        )}
      </ConfigCardShell>

      <LoomRoiLineEditorModal
        moduleIp={module.ip}
        moduleId={module.id}
        open={showLoomRoiEditor}
        onClose={() => setShowLoomRoiEditor(false)}
      />
    </>
  );
}

export default CameraConfigCard;
