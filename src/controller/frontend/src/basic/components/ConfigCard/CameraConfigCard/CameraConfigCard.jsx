import { useCallback, useEffect, useRef, useState } from "react";
import socket from "/src/socket";
import useModules from "/src/hooks/useModules";
import LivestreamCard from "/src/basic/components/LivestreamCard/LivestreamCard";
import LivestreamSelector from "/src/basic/components/LivestreamSelector/LivestreamSelector";
import { useConfigForm } from "../useConfigForm";
import { useHashTab } from "../useHashTab";
import LoomRoiLineEditorModal from "/src/basic/components/LoomRoiLineEditorModal/LoomRoiLineEditorModal";
import CropEditorModal from "/src/basic/components/CropEditorModal/CropEditorModal";
import ExportConfigSection from "../ExportConfigSection";
import ConfigFields from "../ConfigFields";
import ConfigCardShell from "../ConfigCardShell";
import { filterPrivateKeys } from "../configUtils";

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
  { key: "basic",   label: "Basic"   },
  { key: "image",   label: "Image"   },
  { key: "record",  label: "Record"  },
  { key: "preview", label: "Preview" },
];

const LOOM_TABS = [
  { key: "tracking", label: "Tracking" },
  { key: "stimulus", label: "Stimulus" },
];

const HABITAT_TABS = [
  { key: "motion", label: "Motion" },
];

const HAILO_TABS = [
  { key: "hailo", label: "AI" },
];

// Shock Zone holds the zone geometry and the tracking that tests against it.
const APA_TABS = [
  { key: "mask",       label: "Mask"       },
  { key: "shock_zone", label: "Shock Zone" },
];

const HEF_CHUNK_SIZE = 256 * 1024;

const EXPORT_TAB = { key: "export", label: "Export" };

// Curated stock Hailo model-zoo nets - keep in sync with
// src/modules/hailo_infer.py CURATED_MODELS. Grouped by `category` in the
// dropdown. (A live list_hailo_models fetch would be nicer, but the demo just
// needs the picker.)
const HAILO_MODELS = [
  { key: "yolov8s",      label: "YOLOv8s - balanced (default)",         category: "Object detection",     task: "detection" },
  { key: "yolov6n",      label: "YOLOv6n - fastest",                    category: "Object detection",     task: "detection" },
  { key: "yolov8m",      label: "YOLOv8m - most accurate",              category: "Object detection",     task: "detection" },
  { key: "yolov11n",     label: "YOLOv11n - newest, fast",              category: "Object detection",     task: "detection" },
  { key: "topdown_rats_yolov8", label: "Rat detector (top-down, 1 class)", category: "Object detection",  task: "detection" },
  { key: "multiangle_rats_yolo11n", label: "Rat detector (multi-angle, 1 class)", category: "Object detection", task: "detection" },
  { key: "yolov8s_pose", label: "YOLOv8s-pose - body keypoints",        category: "Pose estimation",      task: "pose" },
  { key: "yolov8m_pose", label: "YOLOv8m-pose - keypoints, accurate",   category: "Pose estimation",      task: "pose" },
  { key: "yolov8s_seg",  label: "YOLOv8s-seg - instance masks",         category: "Instance segmentation", task: "segmentation" },
  { key: "yolov8m_seg",  label: "YOLOv8m-seg - masks, accurate",        category: "Instance segmentation", task: "segmentation" },
  { key: "scdepthv3",    label: "SC-Depth v3 - monocular depth",        category: "Depth estimation",     task: "depth" },
  { key: "fast_depth",   label: "FastDepth - monocular depth, fastest", category: "Depth estimation",     task: "depth" },
];

// Every tab key any camera-card variant can show - passed to useHashTab so a
// tab carried over from switching device (Settings.jsx) that isn't one of
// this card's falls back to the default instead of rendering blank.
const ALL_TAB_KEYS = [
  ...BASE_TABS, ...LOOM_TABS, ...HABITAT_TABS, ...HAILO_TABS, ...APA_TABS, EXPORT_TAB,
].map(t => t.key);

const TAB_COPY_SECTION = {
  basic:    { key: "module",         label: "Basic"    },
  image:    { key: "camera",         label: "Image"    },
  record:   { key: "camera",         label: "Record"   },
  preview:  { key: "camera",         label: "Preview"  },
  tracking: { key: "loom_tracking",  label: "Tracking" },
  stimulus: { key: "loom_stimulus",  label: "Stimulus" },
  motion:   { key: "habitat_motion", label: "Motion"   },
  hailo:    { key: "hailo",          label: "AI"       },
  mask:       { key: "mask",           label: "Mask"       },
  // shock_zone: omitted — spans shock_zone + object_detection + blob_tracker,
  // so only "Copy all" is offered
  export:   { key: "export",         label: "Export"   },
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

// APA shock-zone colour is stored as [r, g, b] (base config) or {r, g, b}
// (after a save); the colour input wants hex.
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

// APA save: send shock_zone_color as an object and keep
// object_detection.labels, an array filterPrivateKeys would otherwise strip.
function apaSaveTransform(fd) {
  const filtered = filterPrivateKeys(fd) ?? {};
  const rawColor = fd?.shock_zone?.shock_zone_color;
  if (rawColor) {
    filtered.shock_zone = filtered.shock_zone ?? {};
    filtered.shock_zone.shock_zone_color = Array.isArray(rawColor)
      ? { r: rawColor[0], g: rawColor[1], b: rawColor[2] }
      : rawColor;
  }
  const rawLabels = fd?.object_detection?.labels;
  if (Array.isArray(rawLabels)) {
    filtered.object_detection = filtered.object_detection ?? {};
    filtered.object_detection.labels = rawLabels;
  }
  return filtered;
}

function CameraConfigCard({ id, module, clipboard, onCopy, syncServerModule }) {
  const { formData, setFormData, handleChange, markSaved, isDirty } = useConfigForm(module.config);
  const { moduleList } = useModules();
  const [sensorModes, setSensorModes] = useState([]);
  const [sensorModel, setSensorModel] = useState("");
  const [hasAutofocus, setHasAutofocus] = useState(false);
  const [activePreset, setActivePreset] = useState("custom");
  const [activeTab, setActiveTab] = useHashTab("basic", ALL_TAB_KEYS);
  const [showLoomRoiEditor, setShowLoomRoiEditor] = useState(false);
  const [roiInfo, setRoiInfo] = useState(null);
  const [showCropEditor, setShowCropEditor] = useState(false);
  const closeCropEditor = useCallback(() => setShowCropEditor(false), []);
  // APA tracking model picker: list_hefs result + upload progress.
  const [hefInfo, setHefInfo] = useState(null); // { hailo_present, hefs, current, error }
  const [hefUpload, setHefUpload] = useState(null); // null | { received, total } | { error } | { done }
  const hefInputRef = useRef(null);

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

  useEffect(() => {
    if (module.type !== "apa_camera") return;
    socket.emit("send_command", { module_id: module.id, type: "list_hefs", params: {} });
    const mine = (h) => (data) => { if (data.module_id === module.id) h(data); };
    const onList = mine((data) => {
      setHefInfo(data);
      if (data.error) setHefUpload({ error: data.error });
      else if (data.fetched) {
        setHefUpload({ done: true });
        // Select the new model; it takes effect when the card is saved.
        setFormData(prev => {
          const cloned = structuredClone(prev);
          cloned.object_detection = { ...(cloned.object_detection ?? {}), model_path: data.fetched };
          return cloned;
        });
      }
    });
    const onProgress = mine(({ received, total }) => setHefUpload({ received, total }));
    const onComplete = mine(() => setHefUpload({ received: 1, total: 1, installing: true }));
    const onError = mine(({ error }) => setHefUpload({ error }));
    socket.on("hef_list_response", onList);
    socket.on("hef_upload_progress", onProgress);
    socket.on("hef_upload_complete", onComplete);
    socket.on("hef_upload_error", onError);
    return () => {
      socket.off("hef_list_response", onList);
      socket.off("hef_upload_progress", onProgress);
      socket.off("hef_upload_complete", onComplete);
      socket.off("hef_upload_error", onError);
    };
  }, [module.id, module.type, setFormData]);

  // Track what resolution the saved ROI (arena polygon) was drawn at, so a
  // later resolution change can be flagged -- the module rescales the ROI
  // proportionally on load (see loom_load_roi_and_line), but that assumes a
  // uniform resize; a sensor-mode change can shift the actual cropped field
  // of view, silently making a "successfully rescaled" ROI wrong rather than
  // erroring. Re-fetched when the editor modal closes, since that's when a
  // save (and a fresh image_size) can happen.
  useEffect(() => {
    if (module.type !== "loom_camera" || !module.ip || showLoomRoiEditor) return;
    let cancelled = false;
    fetch(`http://${module.ip}:8080/roi`)
      .then(r => (r.ok ? r.json() : null))
      .then(data => {
        if (cancelled) return;
        if (!data) { setRoiInfo(null); return; }
        const poly = data.arena_polygon ?? data.points;
        setRoiInfo({
          width: data.image_size?.width,
          height: data.image_size?.height,
          hasPolygon: Array.isArray(poly) && poly.length >= 3,
        });
      })
      .catch(() => { if (!cancelled) setRoiInfo(null); });
    return () => { cancelled = true; };
  }, [module.type, module.ip, showLoomRoiEditor]);

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

  const cropRect         = cam.crop_rect ?? null;
  // Pre-2026-10 crops were pixel rectangles on the preview; the module still
  // applies them (trimmed to the output's shape), but re-saving converts them.
  const cropLegacy       = cropRect != null && cropRect.preview_width != null;

  const currentSyncMode  = cam.sync_mode ?? "none";
  const framesyncEnabled = cam.framesync_enabled ?? true;
  const isRecording      = module.status === "RECORDING";
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

  const isApa            = module.type === "apa_camera";
  const mask             = formData?.mask ?? {};
  const shockZone        = formData?.shock_zone ?? {};
  const detection        = formData?.object_detection ?? {};
  const blobTracker      = formData?.blob_tracker ?? {};

  const trackingMethod = !(detection.enabled ?? false) ? "off"
    : (detection.backend ?? "blob") === "blob" ? "blob" : "hailo";

  const handleTrackingMethod = (method) => {
    setFormData(prev => {
      const cloned = structuredClone(prev);
      const od = cloned.object_detection = cloned.object_detection ?? {};
      od.enabled = method !== "off";
      if (method === "blob") od.backend = "blob";
      // Keep hailo_raw if that's what was set; otherwise the on-chip-NMS default.
      if (method === "hailo" && od.backend !== "hailo_raw") od.backend = "hailo";
      return cloned;
    });
  };

  const handleHefFile = (file) => {
    if (!file) return;
    if (!file.name.endsWith(".hef")) {
      setHefUpload({ error: "Please choose a .hef file." });
      return;
    }
    const totalChunks = Math.ceil(file.size / HEF_CHUNK_SIZE);
    setHefUpload({ received: 0, total: totalChunks });
    socket.emit("upload_hef_start", {
      module_id: module.id, filename: file.name,
      total_chunks: totalChunks, total_bytes: file.size,
    });
    (async () => {
      for (let i = 0; i < totalChunks; i++) {
        const bytes = await file.slice(i * HEF_CHUNK_SIZE, (i + 1) * HEF_CHUNK_SIZE).arrayBuffer();
        socket.emit("upload_hef_chunk", { index: i, data: bytes });
        await new Promise(r => setTimeout(r, 0));
      }
    })();
  };

  const handleShockZoneColor = (hex) => {
    setFormData(prev => {
      const cloned = structuredClone(prev);
      cloned.shock_zone.shock_zone_color = hexToRgbObj(hex);
      return cloned;
    });
  };

  const tabs = [
    ...BASE_TABS,
    ...(module.type === "loom_camera" ? LOOM_TABS : []),
    ...(module.type === "habitat_camera" ? HABITAT_TABS : []),
    ...(module.type === "hailo_camera" ? HAILO_TABS : []),
    ...(isApa ? APA_TABS : []),
    EXPORT_TAB,
  ];

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
        saveTransform={isApa ? apaSaveTransform : undefined}
        markSaved={markSaved}
        isDirty={isDirty}
        deviceInfoExtras={[sensorModel]}
        tabBadges={hasSyncWarning ? { record: "⚠" } : {}}
        sidebar={
          <>
            <LivestreamCard module={module} />
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "4px", marginTop: "8px" }}>
              <button type="button" className="copy-btn" onClick={() => setShowCropEditor(true)}
                disabled={isRecording} title={isRecording ? "Stop recording to change the crop" : ""}>
                {cropRect ? "Edit Crop" : "Set Crop / Zoom"}
              </button>
              {cropRect && (
                <div className="fov-label fov-cropped">
                  {cropLegacy
                    ? "Crop saved in the old format: open the editor and save it again so the recording isn't trimmed to fit."
                    : `Cropped: output follows the crop${cropRect.base_width ? ` (uncropped ${cropRect.base_width}×${cropRect.base_height})` : ""}.`}
                </div>
              )}
            </div>
            {module.type === "loom_camera" && (
              <div style={{ display: "flex", justifyContent: "center", marginTop: "8px" }}>
                <button type="button" className="copy-btn" onClick={() => setShowLoomRoiEditor(true)}>
                  Set ROI / Line
                </button>
              </div>
            )}
            {module.type === "loom_camera" && (
              <>
                <div className="config-section-divider" />
                <LivestreamSelector modules={moduleList.filter(m => m.id !== module.id)} heading="Other Camera" />
              </>
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
                      {p.label}{p.sub ? ` - ${p.sub}` : ""}
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
                FPS locked to {serverFps} fps via frame sync - resolution can be changed freely.
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
                {currentFps} fps exceeds mode max ({maxFps} fps) - will be clamped on apply
              </div>
            )}
            {module.type === "loom_camera" && roiInfo?.hasPolygon && roiInfo.width && roiInfo.height &&
              (Number(roiInfo.width) !== Number(currentWidth) || Number(roiInfo.height) !== Number(currentHeight)) && (
              <div className="fov-label fov-cropped">
                ROI was drawn at {roiInfo.width}×{roiInfo.height}, current resolution is {currentWidth}×{currentHeight} -
                it will be rescaled proportionally but may no longer line up with the physical arena,
                especially if this changed sensor mode rather than just resolution.
                Re-open "Set ROI / Line" to redraw it.
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
              <label>Rotation:</label>
              <select value={cam.rotation ?? 0}
                onChange={e => handleChange(["camera", "rotation"], e)}>
                <option value={0}>0°</option>
                <option value={90}>90°</option>
                <option value={180}>180°</option>
                <option value={270}>270°</option>
              </select>
            </div>
            <div className="form-field">
              <label>Brightness: {Number(brightness).toFixed(2)}</label>
              <input type="range" min="-1" max="1" step="0.05"
                value={brightness} className="brightness-slider"
                onChange={e => handleChange(["camera", "brightness"], e)} />
            </div>
            <div className="form-field">
              <label>Contrast: {Number(cam.contrast ?? 1.0).toFixed(2)}</label>
              <input type="range" min="0" max="4" step="0.05"
                value={cam.contrast ?? 1.0} className="brightness-slider"
                onChange={e => handleChange(["camera", "contrast"], e)} />
            </div>
            <div className="form-field">
              <label>Sharpness: {Number(cam.sharpness ?? 1.0).toFixed(2)}</label>
              <input type="range" min="0" max="8" step="0.1"
                value={cam.sharpness ?? 1.0} className="brightness-slider"
                onChange={e => handleChange(["camera", "sharpness"], e)} />
            </div>
            <div className="form-field">
              <label>Noise reduction:</label>
              <select value={cam.noise_reduction_mode ?? "fast"}
                onChange={e => handleChange(["camera", "noise_reduction_mode"], e)}>
                <option value="off">Off (max detail, more visible noise)</option>
                <option value="fast">Fast (default)</option>
                <option value="high_quality">High quality (smoother, may cost framerate)</option>
              </select>
            </div>
            <div className="form-field">
              <label title="Rolling % of the frame clipped white or crushed black that shows the OVER/UNDEREXPOSED preview badge and logs a warning. Advisory only.">
                Exposure warning threshold (%):
              </label>
              <input type="number" min="0" max="100" step="0.5"
                value={cam.exposure_warn_pct ?? 5.0}
                onChange={e => handleChange(["camera", "exposure_warn_pct"], e)} />
            </div>
            <div className="form-field">
              <label>Exposure warning overlay:</label>
              <input type="checkbox"
                checked={cam.exposure_overlay ?? true}
                onChange={e => handleChange(["camera", "exposure_overlay"], e)} />
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

            <div className="config-section-divider" />
            <div className="sensor-mode-info">
              Auto-exposure tuning — applies while "Auto gain/exposure" is on.
            </div>
            <div className="form-field">
              <label>Exposure bias (EV): {Number(cam.exposure_value ?? 0).toFixed(1)}</label>
              <input type="range" min="-8" max="8" step="0.5"
                disabled={!aeEnabled}
                value={cam.exposure_value ?? 0} className="brightness-slider"
                onChange={e => handleChange(["camera", "exposure_value"], e)} />
            </div>
            <div className="form-field">
              <label>Metering:</label>
              <select value={cam.ae_metering_mode ?? "centre_weighted"}
                disabled={!aeEnabled}
                onChange={e => handleChange(["camera", "ae_metering_mode"], e)}>
                <option value="centre_weighted">Centre-weighted (default)</option>
                <option value="spot">Spot (centre only — ignores bright surroundings)</option>
                <option value="matrix">Matrix (whole frame)</option>
              </select>
            </div>
            <div className="form-field">
              <label>Constraint:</label>
              <select value={cam.ae_constraint_mode ?? "normal"}
                disabled={!aeEnabled}
                onChange={e => handleChange(["camera", "ae_constraint_mode"], e)}>
                <option value="normal">Normal (default)</option>
                <option value="highlight">Highlight (protect bright areas from clipping)</option>
                <option value="shadows">Shadows (expose for the darker subject)</option>
              </select>
            </div>
            <div className="form-field">
              <label>Exposure mode:</label>
              <select value={cam.ae_exposure_mode ?? "normal"}
                disabled={!aeEnabled}
                onChange={e => handleChange(["camera", "ae_exposure_mode"], e)}>
                <option value="normal">Normal (default)</option>
                <option value="short">Short (prefer shorter shutter)</option>
                <option value="long">Long (prefer longer shutter — less gain, less noise)</option>
              </select>
            </div>

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

            <div className="config-section-divider" />
            <div className="form-field">
              <label>Timestamp overlay:</label>
              <input type="checkbox"
                checked={overlayTimestamp}
                onChange={e => handleChange(["camera", "overlay_timestamp"], e)} />
            </div>
            <div className="sensor-mode-info sensor-mode-info--muted">
              This will be on your saved videos.
            </div>
            {overlayTimestamp && (
              <>
                <div className="form-field">
                  <label>Timestamp size:</label>
                  <select value={cam.text_size ?? "medium"}
                    onChange={e => handleChange(["camera", "text_size"], e)}>
                    <option value="small">Small</option>
                    <option value="medium">Medium</option>
                    <option value="large">Large</option>
                  </select>
                </div>
                <div className="form-field">
                  <label>Timestamp position:</label>
                  <select value={cam.timestamp_position ?? "top"}
                    onChange={e => handleChange(["camera", "timestamp_position"], e)}>
                    <option value="top">Top</option>
                    <option value="bottom">Bottom</option>
                  </select>
                </div>
              </>
            )}
          </>
        )}

        {/* RECORD */}
        {activeTab === "record" && (
          <>
            <div className="form-field">
              <label>Enable FrameSync:</label>
              <input type="checkbox"
                checked={framesyncEnabled}
                disabled={isRecording}
                onChange={e => handleChange(["camera", "framesync_enabled"], e)} />
            </div>
            {isRecording && (
              <div className="sensor-mode-info sensor-mode-info--muted">
                Stop recording to change FrameSync.
              </div>
            )}
            {framesyncEnabled && currentSyncMode === "server" && (
              <div className="sensor-mode-info">
                This camera is the FrameSync transmitter - it broadcasts timing to every
                other FrameSync-enabled camera. The controller assigns this automatically;
                start client cameras first, then this one.
              </div>
            )}
            {framesyncEnabled && currentSyncMode === "client" && syncServerModule && (
              <div className="sensor-mode-info">
                Syncing to {syncServerModule.name} ({syncServerModule.id}).
                {serverFps != null && <> FPS locked to {serverFps} fps.</>}
              </div>
            )}
            {framesyncEnabled && currentSyncMode === "none" && (
              <div className="sensor-mode-info sensor-mode-info--muted">
                Waiting for the controller to assign a FrameSync role - this camera
                may be offline, or has just been enabled and hasn't been reconciled yet.
              </div>
            )}
            {fpsMismatch && (
              <div className="fov-label fov-cropped">
                FPS mismatch: this camera is {cam.fps} fps but server {syncServerModule.name} is {serverFps} fps - frame sync will not be 1:1.
              </div>
            )}
            {exposureMismatch && !fpsMismatch && (
              <div className="fov-label fov-cropped" style={{ opacity: 0.8 }}>
                Exposure mismatch: {clientExposureUs}µs here vs {serverExposureUs}µs on server - brightness will differ.
              </div>
            )}
            <div className="form-field">
              <label>Lock exposure:</label>
              <input type="checkbox"
                checked={cam.sync_lock_exposure ?? false}
                onChange={e => handleChange(["camera", "sync_lock_exposure"], e)} />
            </div>
            <div className="sensor-mode-info sensor-mode-info--muted">
              While frame-synced: fixes exposure/gain so brightness matches across cameras -
              overrides Image tab's "Auto gain/exposure".
            </div>
            <div className="form-field">
              <label>Lock white balance:</label>
              <input type="checkbox"
                checked={cam.sync_lock_awb ?? false}
                onChange={e => handleChange(["camera", "sync_lock_awb"], e)} />
            </div>
            <div className="sensor-mode-info sensor-mode-info--muted">
              While frame-synced: fixes white balance so colour matches across cameras
              (no manual control for this elsewhere).
            </div>

            <div className="config-section-divider" />
            <div className="form-field">
              <label>Bitrate (Mbps):</label>
              <input type="number" min="1" max="50" step="1"
                value={bitrateMb}
                onChange={e => handleChange(["camera", "bitrate_mb"], e)} />
            </div>
            <div className="filesize-preview">~{gbPerHour} GB / hr at {bitrateMb} Mbps</div>
            <div className="config-section-divider" />
            <div className="form-field">
              <label>Segment length (mins):</label>
              <input type="number" min="1" step="1"
                value={formData?.recording?.segment_length_mins ?? 60}
                onChange={e => handleChange(["recording", "segment_length_mins"], e)} />
            </div>
            <div className="form-field">
              <label title="Re-mux each .ts through ffmpeg at stop to reset container positioning timestamps. Under investigation as a cause of a few dropped frames on framesync-client cameras — turn off to compare. Downstream tools read the per-frame CSV, not container timestamps.">
                Fix .ts positioning timestamps:
              </label>
              <input type="checkbox"
                checked={formData?.recording?.fix_positioning_timestamps !== false}
                onChange={e => handleChange(["recording", "fix_positioning_timestamps"], e)} />
            </div>
          </>
        )}

        {/* PREVIEW */}
        {activeTab === "preview" && (
          <>
            <div className="form-field">
              <label>Overlay framerate (preview):</label>
              <input type="checkbox"
                checked={cam.overlay_framerate_on_preview ?? false}
                onChange={e => handleChange(["camera", "overlay_framerate_on_preview"], e)} />
            </div>
            <div className="sensor-mode-info sensor-mode-info--muted">
              Live monitoring stream only - never on saved videos.
            </div>
            <div className="form-field">
              <label>Livestream quality:</label>
              <select value={cam.livestream_quality ?? "normal"}
                onChange={e => handleChange(["camera", "livestream_quality"], e)}>
                <option value="normal">Normal (low-res)</option>
                <option value="high">High (recording resolution)</option>
              </select>
            </div>
          </>
        )}

        {/* LOOM TRACKING */}
        {activeTab === "tracking" && (
          <>
            <ConfigFields
              data={formData?.loom_tracking}
              handleChange={(path, e) => handleChange(["loom_tracking", ...path], e)}
            />
            <div className="config-section-divider" />
            <div className="sensor-mode-info sensor-mode-info--muted">Zone label position &amp; size</div>
            <div className="form-field">
              <label>X position (0–1):</label>
              <input type="number" min="0" max="1" step="0.01"
                value={formData?.loom_tracking?.overlay?.zone_label_x_frac ?? 0.01}
                onChange={e => handleChange(["loom_tracking", "overlay", "zone_label_x_frac"], e)} />
            </div>
            <div className="form-field">
              <label>Y position (0–1):</label>
              <input type="number" min="0" max="1" step="0.01"
                value={formData?.loom_tracking?.overlay?.zone_label_y_frac ?? 0.95}
                onChange={e => handleChange(["loom_tracking", "overlay", "zone_label_y_frac"], e)} />
            </div>
            <div className="form-field">
              <label>Font scale:</label>
              <input type="number" min="0.1" max="5" step="0.1"
                value={formData?.loom_tracking?.overlay?.zone_label_font_scale ?? 1.0}
                onChange={e => handleChange(["loom_tracking", "overlay", "zone_label_font_scale"], e)} />
            </div>
          </>
        )}

        {/* LOOM STIMULUS */}
        {activeTab === "stimulus" && (
          <>
            <div className="form-field">
              <label>Armed (auto-fire on tracking):</label>
              <input type="checkbox"
                checked={formData?.loom_stimulus?.armed ?? false}
                onChange={e => handleChange(["loom_stimulus", "armed"], e)} />
            </div>
            <div className="stimulus-test-row">
              <button type="button" className="copy-btn"
                onClick={() => socket.emit("send_command", { module_id: id, type: "loom_stimulus_start", params: {} })}>
                Fire
              </button>
              <button type="button" className="copy-btn"
                onClick={() => socket.emit("send_command", { module_id: id, type: "loom_stimulus_stop", params: {} })}>
                Stop
              </button>
            </div>
            <div className="config-section-divider" />
            <div className="sensor-mode-info sensor-mode-info--muted">Display</div>
            <div className="form-field">
              <label>Stimulus monitor:</label>
              <select value={formData?.loom_stimulus?.stimulus_monitor_index ?? 0}
                onChange={e => handleChange(["loom_stimulus", "stimulus_monitor_index"], e)}>
                <option value={0}>Monitor 0</option>
                <option value={1}>Monitor 1</option>
              </select>
            </div>
            <div className="sensor-mode-info sensor-mode-info--muted">
              Opens a window on each detected physical monitor, picked by GLFW's
              raw detection order. There's no way to know in advance which is
              Monitor 0 vs 1 - use the "Test screens" flash button below to
              check, and switch this if it's backwards. If a second monitor is
              detected it automatically gets the near/keepalive-only display.
            </div>
            <div className="form-field">
              <label>Background brightness: {Number(formData?.loom_stimulus?.background_rgba?.[0] ?? 0.5).toFixed(2)}</label>
              <input type="range" min="0" max="1" step="0.01"
                className="brightness-slider"
                value={formData?.loom_stimulus?.background_rgba?.[0] ?? 0.5}
                onChange={e => {
                  const v = parseFloat(e.target.value);
                  setFormData(prev => {
                    const cloned = structuredClone(prev);
                    cloned.loom_stimulus.background_rgba = [v, v, v, 1.0];
                    return cloned;
                  });
                }} />
            </div>
            <div className="config-section-divider" />
            <div className="sensor-mode-info sensor-mode-info--muted">Timing</div>
            <div className="form-field">
              <label>Travel time (s):</label>
              <input type="number" min="0.1" max="60" step="0.1"
                value={formData?.loom_stimulus?.travel_time_s ?? 5.0}
                onChange={e => handleChange(["loom_stimulus", "travel_time_s"], e)} />
            </div>
            <div className="form-field">
              <label>Return time (s):</label>
              <input type="number" min="0.05" max="10" step="0.05"
                value={formData?.loom_stimulus?.loom_wait_time_s ?? 0.5}
                onChange={e => handleChange(["loom_stimulus", "loom_wait_time_s"], e)} />
            </div>
            <div className="form-field">
              <label>Looms per round:</label>
              <input type="number" min="1" max="100" step="1"
                value={formData?.loom_stimulus?.round_size ?? 5}
                onChange={e => handleChange(["loom_stimulus", "round_size"], e)} />
            </div>
            <div className="config-section-divider" />
            <div className="sensor-mode-info sensor-mode-info--muted">Size</div>
            <div className="form-field">
              <label>Initial size (cm):</label>
              <input type="number" min="0" max="100" step="0.5"
                value={formData?.loom_stimulus?.initial_size_cm ?? 6.0}
                onChange={e => handleChange(["loom_stimulus", "initial_size_cm"], e)} />
            </div>
            <div className="form-field">
              <label>Final size (cm):</label>
              <input type="number" min="0" max="200" step="0.5"
                value={formData?.loom_stimulus?.final_size_cm ?? 40.0}
                onChange={e => handleChange(["loom_stimulus", "final_size_cm"], e)} />
            </div>
            <div className="form-field">
              <label>Image angle (°):</label>
              <input type="number" min="0" max="360" step="1"
                value={formData?.loom_stimulus?.image_angle_deg ?? 90}
                onChange={e => handleChange(["loom_stimulus", "image_angle_deg"], e)} />
            </div>
            <div className="config-section-divider" />
            <div className="sensor-mode-info sensor-mode-info--muted">Position</div>
            <div className="form-field">
              <label>X offset: {Number(formData?.loom_stimulus?.x_offset_ndc ?? 0.0).toFixed(2)} (0 = centre)</label>
              <input type="range" min="-0.5" max="0.5" step="0.01"
                className="brightness-slider"
                value={formData?.loom_stimulus?.x_offset_ndc ?? 0.0}
                onChange={e => handleChange(["loom_stimulus", "x_offset_ndc"], e)} />
            </div>
            <div className="form-field">
              <label>Initial Y: {Number(formData?.loom_stimulus?.initial_pos_ndc?.[1] ?? 0.0).toFixed(2)} (0 = centre)</label>
              <input type="range" min="-1" max="1" step="0.01"
                className="brightness-slider"
                value={formData?.loom_stimulus?.initial_pos_ndc?.[1] ?? 0.0}
                onChange={e => {
                  const v = parseFloat(e.target.value);
                  setFormData(prev => {
                    const cloned = structuredClone(prev);
                    cloned.loom_stimulus.initial_pos_ndc = [cloned.loom_stimulus.initial_pos_ndc?.[0] ?? 0.5, v];
                    return cloned;
                  });
                }} />
            </div>
            <div className="form-field">
              <label>Final Y: {Number(formData?.loom_stimulus?.final_pos_ndc?.[1] ?? 0.0).toFixed(2)} (0 = centre)</label>
              <input type="range" min="-1" max="1" step="0.01"
                className="brightness-slider"
                value={formData?.loom_stimulus?.final_pos_ndc?.[1] ?? 0.0}
                onChange={e => {
                  const v = parseFloat(e.target.value);
                  setFormData(prev => {
                    const cloned = structuredClone(prev);
                    cloned.loom_stimulus.final_pos_ndc = [cloned.loom_stimulus.final_pos_ndc?.[0] ?? 0.5, v];
                    return cloned;
                  });
                }} />
            </div>
            <div className="config-section-divider" />
            <div className="sensor-mode-info sensor-mode-info--muted">Calibration</div>
            <div className="form-field">
              <label>Screen width (cm):</label>
              <input type="number" min="10" max="500" step="0.1"
                value={formData?.loom_stimulus?.screen_width_cm ?? 105.41}
                onChange={e => handleChange(["loom_stimulus", "screen_width_cm"], e)} />
            </div>
            <div className="form-field">
              <label>Screen height (cm):</label>
              <input type="number" min="10" max="500" step="0.1"
                value={formData?.loom_stimulus?.screen_height_cm ?? 59.29}
                onChange={e => handleChange(["loom_stimulus", "screen_height_cm"], e)} />
            </div>
            <div className="form-field">
              <label>Size correction:</label>
              <input type="number" min="0.1" max="3" step="0.01"
                value={formData?.loom_stimulus?.size_correction ?? 1.125}
                onChange={e => handleChange(["loom_stimulus", "size_correction"], e)} />
            </div>
            <div className="config-section-divider" />
            <div className="sensor-mode-info sensor-mode-info--muted">Photodiode</div>
            <div className="form-field">
              <label>Box size (px):</label>
              <input type="number" min="10" max="500" step="5"
                value={formData?.loom_stimulus?.photodiode_box_px ?? 80}
                onChange={e => handleChange(["loom_stimulus", "photodiode_box_px"], e)} />
            </div>
            <div className="form-field">
              <label>Y position (−1 bottom, +1 top):</label>
              <input type="number" min="-1" max="1" step="0.05"
                value={formData?.loom_stimulus?.photodiode_y_ndc ?? 0.0}
                onChange={e => handleChange(["loom_stimulus", "photodiode_y_ndc"], e)} />
            </div>
            <div className="config-section-divider" />
            <div className="sensor-mode-info sensor-mode-info--muted">Keep-alive (both screens)</div>
            <div className="form-field">
              <label>Keep-alive mode:</label>
              <select value={formData?.loom_stimulus?.keepalive_mode ?? "corner"}
                onChange={e => handleChange(["loom_stimulus", "keepalive_mode"], e)}>
                <option value="corner">Corner patch (least intrusive)</option>
                <option value="distributed">Distributed patches (5 positions)</option>
                <option value="pulse">Full-screen pulse (most effective)</option>
              </select>
            </div>
            <div className="form-field">
              <label>Keep-alive interval (s, 0 = off):</label>
              <input type="number" min="0" max="120" step="1"
                value={formData?.loom_stimulus?.keepalive_interval_s ?? 10}
                onChange={e => handleChange(["loom_stimulus", "keepalive_interval_s"], e)} />
            </div>
            <div className="sensor-mode-info sensor-mode-info--muted">
              Applies to the near screen at all times, and to the stimulus screen
              only between trials - a tick never fires mid-loom, so it can't
              blank the animation or the photodiode marker.
            </div>
            <div className="form-field">
              <label>Test screens:</label>
              <button className="btn btn--small"
                onClick={() => socket.emit("send_command", { module_id: id, type: "loom_stimulus_test_screens", params: { duration_s: 2.0 } })}>
                Test (2 s flash)
              </button>
            </div>
          </>
        )}

        {/* HABITAT MOTION */}
        {activeTab === "motion" && (
          <>
            <div className="sensor-mode-info sensor-mode-info--muted">
              While recording (armed), a clip is only written while the
              activity score below stays above threshold - the score itself
              is always computed every frame and logged to the timestamp CSV
              (and shown live on the preview, including while idle) so you
              can tune these values against real activity. A score spike
              right after the camera's auto-exposure adjusts (e.g. as ambient
              light changes) is ignored for the settle time below, rather
              than treated as motion - the preview shows "AE settling" during
              that window.
            </div>
            <div className="form-field">
              <label>Motion algorithm:</label>
              <select value={formData?.habitat_motion?.algorithm ?? "frame_diff"}
                onChange={e => handleChange(["habitat_motion", "algorithm"], e)}>
                <option value="frame_diff">Frame differencing (fast, simple)</option>
                <option value="mog2">Background subtraction - MOG2 (more robust to lighting drift)</option>
              </select>
            </div>
            <div className="form-field">
              <label>Activity threshold (0–1, fraction of frame changed):</label>
              <input type="number" min="0" max="1" step="0.001"
                value={formData?.habitat_motion?.activity_threshold ?? 0.02}
                onChange={e => handleChange(["habitat_motion", "activity_threshold"], e)} />
            </div>
            <div className="form-field">
              <label>Min activity duration before triggering (s):</label>
              <input type="number" min="0" step="0.1"
                value={formData?.habitat_motion?.activity_min_duration_s ?? 1.0}
                onChange={e => handleChange(["habitat_motion", "activity_min_duration_s"], e)} />
            </div>
            <div className="form-field">
              <label>Min inactivity duration before clearing (s):</label>
              <input type="number" min="0" step="1"
                value={formData?.habitat_motion?.inactivity_min_duration_s ?? 300}
                onChange={e => handleChange(["habitat_motion", "inactivity_min_duration_s"], e)} />
            </div>
            <div className="form-field">
              <label>Pre-roll buffer before trigger (s):</label>
              <input type="number" min="0" step="0.5"
                value={formData?.habitat_motion?.pre_roll_secs ?? 3.0}
                onChange={e => handleChange(["habitat_motion", "pre_roll_secs"], e)} />
            </div>
            <div className="form-field">
              <label>Auto-exposure settle time before trusting a score (s):</label>
              <input type="number" min="0" step="0.05"
                value={formData?.habitat_motion?.ae_settle_s ?? 0.75}
                onChange={e => handleChange(["habitat_motion", "ae_settle_s"], e)} />
            </div>
            <div className="form-field">
              <label>Processing width (px, downscaled before detection):</label>
              <input type="number" min="32" max="1280" step="1"
                value={formData?.habitat_motion?.process_width ?? 256}
                onChange={e => handleChange(["habitat_motion", "process_width"], e)} />
            </div>

            <div className="config-section-divider" />
            <div className="sensor-mode-info sensor-mode-info--muted">
              Occupancy trigger — a slow CPU rat-detector run every few
              seconds, OR-fused with the motion trigger above: recording
              keeps going while a rat is in frame even when it stops moving.
              Watch the "[subject NN]" / "RAT NN" readout on the preview to
              pick a threshold. Needs onnxruntime installed on the module.
            </div>
            <div className="form-field">
              <label>Enable occupancy trigger:</label>
              <input type="checkbox"
                checked={formData?.occupancy?.enabled ?? false}
                onChange={e => handleChange(["occupancy", "enabled"], e)} />
            </div>
            <div className="form-field">
              <label>Detection confidence threshold (0–1):</label>
              <input type="number" min="0" max="1" step="0.05"
                value={formData?.occupancy?.threshold ?? 0.35}
                onChange={e => handleChange(["occupancy", "threshold"], e)} />
            </div>
            <div className="form-field">
              <label>Check interval (s):</label>
              <input type="number" min="0.2" step="0.5"
                value={formData?.occupancy?.interval_s ?? 2.0}
                onChange={e => handleChange(["occupancy", "interval_s"], e)} />
            </div>
            <div className="form-field">
              <label>Consecutive detections to trigger:</label>
              <input type="number" min="1" step="1"
                value={formData?.occupancy?.confirm_samples ?? 2}
                onChange={e => handleChange(["occupancy", "confirm_samples"], e)} />
            </div>
            <div className="form-field">
              <label>Clear after this long with no detection (s):</label>
              <input type="number" min="0" step="5"
                value={formData?.occupancy?.clear_secs ?? 30.0}
                onChange={e => handleChange(["occupancy", "clear_secs"], e)} />
            </div>
            <div className="form-field">
              <label title="ONNX model file, relative to the habitat_camera variant folder unless absolute.">
                Model path:
              </label>
              <input type="text"
                value={formData?.occupancy?.model_path ?? "models/rats_yolov8n_416.onnx"}
                onChange={e => handleChange(["occupancy", "model_path"], e)} />
            </div>

            <div className="config-action-buttons">
              <button type="button" className="save-button"
                onClick={() => socket.emit("send_command", { module_id: id, type: "reset_motion_trigger", params: {} })}>
                Reset trigger to idle
              </button>
            </div>
            <div className="sensor-mode-info sensor-mode-info--muted">
              A waiting/triggered state otherwise only clears once the score
              has stayed below threshold for the full inactivity duration
              above (120s by default) - the same wait a real clip close-out
              would take. Use this to reset instantly while tuning.
            </div>
          </>
        )}

        {/* AI (hailo_camera) */}
        {activeTab === "hailo" && (() => {
          const hailoTask = (HAILO_MODELS.find(
            m => m.key === (formData?.hailo?.model ?? "yolov8s")
          )?.task) ?? "detection";
          return (
          <>
            <div className="form-field">
              <label>Model:</label>
              <select
                value={formData?.hailo?.model ?? "yolov8s"}
                onChange={e => handleChange(["hailo", "model"], e)}>
                {[...new Set(HAILO_MODELS.map(m => m.category))].map(cat => (
                  <optgroup key={cat} label={cat}>
                    {HAILO_MODELS.filter(m => m.category === cat).map(m => (
                      <option key={m.key} value={m.key}>{m.label}</option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </div>
            {hailoTask !== "depth" && (
              <div className="form-field">
                <label>Confidence threshold:</label>
                <input type="number" min="0.05" max="0.95" step="0.05"
                  value={formData?.hailo?.threshold ?? 0.4}
                  onChange={e => handleChange(["hailo", "threshold"], e)} />
              </div>
            )}
            {(hailoTask === "detection" || hailoTask === "pose") && (
              <div className="form-field">
                <label>Max overlays drawn per frame:</label>
                <input type="number" min="1" max="100" step="1"
                  value={formData?.hailo?.max_labels ?? 40}
                  onChange={e => handleChange(["hailo", "max_labels"], e)} />
              </div>
            )}
            {hailoTask === "segmentation" && (
              <div className="form-field">
                <label>Max instances:</label>
                <input type="number" min="1" max="50" step="1"
                  value={formData?.hailo?.max_detections ?? 12}
                  onChange={e => handleChange(["hailo", "max_detections"], e)} />
              </div>
            )}
            <div className="form-field">
              <label>Run inference every N frames:</label>
              <input type="number" min="1" max="10" step="1"
                value={formData?.hailo?.infer_every_n ?? 2}
                onChange={e => handleChange(["hailo", "infer_every_n"], e)} />
            </div>
            <div className="sensor-mode-info sensor-mode-info--muted">
              Live inference overlay on the preview stream only - the recorded
              video is unaffected. HEFs (Hailo-8 vs 8L auto-detected) are fetched
              at module setup; switching model reloads on the device and takes a
              few seconds. No Hailo device / missing HEF → still records, overlay
              shows why.
              {hailoTask === "depth" && (
                <> Depth nets output one relative-depth map - there are no
                boxes, instances or confidence, so only the frame-skip applies.</>
              )}
            </div>
          </>
          );
        })()}

        {/* APA MASK */}
        {activeTab === "mask" && (
          <>
            <div className="sensor-mode-info sensor-mode-info--muted">
              The arena circle. Tracking only looks inside it, and the shock
              zone is drawn relative to it.
            </div>
            <div className="form-field">
              <label>Radius: {Number(mask.mask_radius ?? 0.65).toFixed(2)}</label>
              <input type="range" min="0.1" max="1" step="0.01"
                value={mask.mask_radius ?? 0.65} className="brightness-slider"
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
            <div className="config-section-divider" />
            <div className="form-field">
              <label>Show on livestream:</label>
              <input type="checkbox"
                checked={mask.mask_display ?? true}
                onChange={e => handleChange(["mask", "mask_display"], e)} />
            </div>
            <div className="form-field">
              <label>Save to recording:</label>
              <input type="checkbox"
                checked={mask.mask_record ?? true}
                onChange={e => handleChange(["mask", "mask_record"], e)} />
            </div>
            <div className="sensor-mode-info sensor-mode-info--muted">
              Blacks out everything outside the circle.
            </div>
          </>
        )}

        {/* APA SHOCK ZONE + TRACKING */}
        {activeTab === "shock_zone" && (
          <>
            <div className="sensor-mode-info sensor-mode-info--muted">
              Shocks are delivered from the APA page. The zone and tracking mark
              when the animal is in the zone: the in_zone column of the
              per-frame CSV, and zone entered/exited events.
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
                value={shockZone.shock_zone_inner_offset ?? 0.3} className="brightness-slider"
                onChange={e => handleChange(["shock_zone", "shock_zone_inner_offset"], e)} />
            </div>
            <div className="form-field">
              <label>Colour:</label>
              <input type="color" value={rgbToHex(shockZone.shock_zone_color)}
                onChange={e => handleShockZoneColor(e.target.value)} />
            </div>
            <div className="form-field">
              <label>Line thickness (px):</label>
              <input type="number" min="1" max="20" step="1"
                value={shockZone.shock_zone_line_thickness ?? 3}
                onChange={e => handleChange(["shock_zone", "shock_zone_line_thickness"], e)} />
            </div>
            <div className="form-field">
              <label>Show on livestream:</label>
              <input type="checkbox"
                checked={shockZone.shock_zone_display ?? true}
                onChange={e => handleChange(["shock_zone", "shock_zone_display"], e)} />
            </div>
            <div className="form-field">
              <label>Save to recording:</label>
              <input type="checkbox"
                checked={shockZone.shock_zone_record ?? false}
                onChange={e => handleChange(["shock_zone", "shock_zone_record"], e)} />
            </div>

            <div className="config-section-divider" />
            <div className="sensor-mode-info sensor-mode-info--muted">Tracking</div>
            <div className="form-field">
              <label>Method:</label>
              <select value={trackingMethod} onChange={e => handleTrackingMethod(e.target.value)}>
                <option value="off">Off</option>
                <option value="blob">Blob tracker (motion, no model)</option>
                <option value="hailo" disabled={hefInfo != null && !hefInfo.hailo_present && trackingMethod !== "hailo"}>
                  Hailo model{hefInfo != null && !hefInfo.hailo_present ? " (no Hailo hardware detected)" : ""}
                </option>
              </select>
            </div>
            {trackingMethod === "hailo" && hefInfo != null && !hefInfo.hailo_present && (
              <div className="fov-label fov-cropped">
                No Hailo accelerator found on this module: tracking won't run.
              </div>
            )}

            {trackingMethod === "blob" && (
              <>
                <div className="form-field">
                  <label>Process width (px):</label>
                  <input type="number" min="64" max="1920" step="16"
                    value={blobTracker.process_width ?? 256}
                    onChange={e => handleChange(["blob_tracker", "process_width"], e)} />
                </div>
                <div className="form-field">
                  <label>Diff threshold: {Number(blobTracker.thr_hi ?? 5).toFixed(1)}</label>
                  <input type="range" min="1" max="50" step="0.5"
                    value={blobTracker.thr_hi ?? 5} className="brightness-slider"
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
                    value={blobTracker.smoothing_alpha ?? 0.3} className="brightness-slider"
                    onChange={e => handleChange(["blob_tracker", "smoothing_alpha"], e)} />
                </div>
                <div className="form-field">
                  <label>Track box size (px):</label>
                  <input type="number" min="20" max="600" step="10"
                    value={blobTracker.track_square_size ?? 150}
                    onChange={e => handleChange(["blob_tracker", "track_square_size"], e)} />
                </div>
              </>
            )}

            {trackingMethod === "hailo" && (() => {
              const hefs = hefInfo?.hefs ?? [];
              const current = detection.model_path ?? "";
              const known = hefs.some(h => h.path === current);
              const uploading = hefUpload && hefUpload.total != null && !hefUpload.error;
              return (
              <>
                <div className="form-field">
                  <label>Model:</label>
                  <select value={current}
                    onChange={e => handleChange(["object_detection", "model_path"], e)}>
                    {!known && (
                      <option value={current}>
                        {current ? `${current.split("/").pop()} (not on this module)` : "Choose a model…"}
                      </option>
                    )}
                    {["uploaded", "bundled"].map(src => {
                      const group = hefs.filter(h => h.source === src);
                      return group.length > 0 && (
                        <optgroup key={src} label={src === "uploaded" ? "Uploaded" : "On device"}>
                          {group.map(h => (
                            <option key={h.path} value={h.path}>
                              {h.name} ({(h.size_bytes / 1048576).toFixed(1)} MB)
                            </option>
                          ))}
                        </optgroup>
                      );
                    })}
                  </select>
                </div>
                <div className="form-field">
                  <label></label>
                  <div>
                    <button type="button" className="copy-btn" disabled={!!uploading && !hefUpload.done}
                      onClick={() => hefInputRef.current?.click()}>
                      Upload .hef…
                    </button>
                    <input ref={hefInputRef} type="file" accept=".hef" style={{ display: "none" }}
                      onChange={e => { handleHefFile(e.target.files[0]); e.target.value = ""; }} />
                  </div>
                </div>
                {hefUpload && (
                  <div className={hefUpload.error ? "fov-label fov-cropped" : "sensor-mode-info"}>
                    {hefUpload.error
                      ? `Upload failed: ${hefUpload.error}`
                      : hefUpload.done
                        ? "Uploaded and selected. Save to start using it."
                        : hefUpload.installing
                          ? "Copying to the module…"
                          : `Uploading… ${hefUpload.received} / ${hefUpload.total} chunks`}
                  </div>
                )}
                <div className="form-field">
                  <label title="Most HEFs (including ones built with tools/convert_to_hailo.py) have NMS compiled in. Pick raw only for a model that outputs raw YOLOv8 tensors.">
                    Model output:
                  </label>
                  <select value={detection.backend === "hailo_raw" ? "hailo_raw" : "hailo"}
                    onChange={e => handleChange(["object_detection", "backend"], e)}>
                    <option value="hailo">Detections (NMS in the model, usual)</option>
                    <option value="hailo_raw">Raw YOLOv8 tensors</option>
                  </select>
                </div>
                <div className="form-field">
                  <label>Confidence threshold: {Number(detection.threshold ?? 0.55).toFixed(2)}</label>
                  <input type="range" min="0.05" max="1" step="0.01"
                    value={detection.threshold ?? 0.55} className="brightness-slider"
                    onChange={e => handleChange(["object_detection", "threshold"], e)} />
                </div>
              </>
              );
            })()}

            {trackingMethod !== "off" && (
              <>
                <div className="config-section-divider" />
                <div className="sensor-mode-info sensor-mode-info--muted">
                  While recording, each frame's row in the timestamps CSV gets the
                  tracked centroid (det_cx, det_cy, in pixels), det_conf (blank when
                  the position is held over from an earlier frame) and in_zone.
                  Tracking is independent of shocking.
                </div>
                <div className="sensor-mode-info sensor-mode-info--muted">
                  Position filtering: the zone test, CSV and overlay all use the
                  filtered position.
                </div>
                <div className="form-field">
                  <label title="Weight on the previous position each frame: 0 follows every detection, higher is steadier but lags more.">
                    Smoothing: {Number(detection.smoothing ?? 0.5).toFixed(2)}
                  </label>
                  <input type="range" min="0" max="0.95" step="0.05"
                    value={detection.smoothing ?? 0.5} className="brightness-slider"
                    onChange={e => handleChange(["object_detection", "smoothing"], e)} />
                </div>
                <div className="form-field">
                  <label title="A detection further than this (fraction of the frame's short side) from the tracked position is treated as a jump.">
                    Max jump per frame: {Number(detection.max_jump_frac ?? 0.15).toFixed(2)}
                  </label>
                  <input type="range" min="0.02" max="1" step="0.01"
                    value={detection.max_jump_frac ?? 0.15} className="brightness-slider"
                    onChange={e => handleChange(["object_detection", "max_jump_frac"], e)} />
                </div>
                <div className="form-field">
                  <label title="A jump is only followed once the far detection has been seen this many frames in a row; shorter false positives are ignored.">
                    Frames to confirm a jump:
                  </label>
                  <input type="number" min="1" max="30" step="1"
                    value={detection.jump_confirm_frames ?? 3}
                    onChange={e => handleChange(["object_detection", "jump_confirm_frames"], e)} />
                </div>
                <div className="form-field">
                  <label title="With no accepted detection for this long the position is cleared: the overlay disappears and the CSV position/in_zone go blank.">
                    Lost after (s):
                  </label>
                  <input type="number" min="0.05" max="10" step="0.05"
                    value={detection.lost_timeout_s ?? 0.5}
                    onChange={e => handleChange(["object_detection", "lost_timeout_s"], e)} />
                </div>
                <div className="form-field">
                  <label>Show tracking on livestream:</label>
                  <input type="checkbox"
                    checked={detection.overlay_display ?? true}
                    onChange={e => handleChange(["object_detection", "overlay_display"], e)} />
                </div>
                <div className="form-field">
                  <label>Save tracking to recording:</label>
                  <input type="checkbox"
                    checked={detection.overlay_record ?? true}
                    onChange={e => handleChange(["object_detection", "overlay_record"], e)} />
                </div>
                <div className="sensor-mode-info sensor-mode-info--muted">
                  The position dot, label and "IN SHOCK ZONE" text.
                </div>
              </>
            )}
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

      <CropEditorModal
        moduleIp={module.ip}
        moduleId={module.id}
        open={showCropEditor}
        onClose={closeCropEditor}
      />
    </>
  );
}

export default CameraConfigCard;
