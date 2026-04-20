import { useEffect, useState, useRef } from "react";
import socket from "/src/socket";
import { useConfigForm } from "../useConfigForm";
import { filterPrivateKeys } from "../configUtils";
import ConfigFields from "../ConfigFields";
import FullscreenVideo from "/src/basic/components/FullscreenVideo/FullscreenVideo";
import { useModuleUpdate } from "/src/hooks/useModuleUpdate";
import { useExportSync } from "/src/hooks/useExportSync";

const STALL_MS     = 8000;
const RECONNECT_MS = 2500;

function MicrophoneStream({ ip, port }) {
  const [streamKey, setStreamKey] = useState(Date.now());
  const [fullscreen, setFullscreen] = useState(false);
  const stallTimer     = useRef(null);
  const reconnectTimer = useRef(null);
  const bump = () => setStreamKey(Date.now());

  useEffect(() => {
    stallTimer.current = setTimeout(bump, STALL_MS);
    return () => {
      clearTimeout(stallTimer.current);
      clearTimeout(reconnectTimer.current);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streamKey]);

  const resetStall = () => {
    clearTimeout(stallTimer.current);
    stallTimer.current = setTimeout(bump, STALL_MS);
  };

  const handleError = () => {
    clearTimeout(stallTimer.current);
    clearTimeout(reconnectTimer.current);
    reconnectTimer.current = setTimeout(bump, RECONNECT_MS);
  };

  return (
    <>
      <img
        key={streamKey}
        src={`http://${ip}:${port}/video_feed`}
        alt="Microphone monitor stream"
        style={{ width: "100%", display: "block", borderRadius: "4px", cursor: "pointer" }}
        onLoad={resetStall}
        onError={handleError}
        onClick={() => setFullscreen(true)}
      />
      {fullscreen && (
        <FullscreenVideo ip={ip} port={port} onClose={() => setFullscreen(false)} />
      )}
    </>
  );
}

function MicrophoneConfigCard({ id, module, clipboard, onCopy }) {
  const { formData, setFormData, handleChange } = useConfigForm(module.config);
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [showRebootConfirm, setShowRebootConfirm] = useState(false);
  const [hasSaved, setHasSaved]                 = useState(false);
  const { updateStatus, handleUpdate } = useModuleUpdate(module.id);
  const { syncStatus, syncExport } = useExportSync(module.id);

  const streamPort = module.config?.monitoring?._port ?? 8081;

  const sampleRate    = module.config?.microphone?._sample_rate ?? 192000;
  const filetype      = module.config?.recording?._recording_filetype ?? "flac";
  const bytesPerSec   = sampleRate * 2; // 16-bit mono
  const rawGbPerHour  = (bytesPerSec * 3600) / 1e9;
  const estGbPerHour  = filetype === "flac" ? rawGbPerHour * 0.6 : rawGbPerHour;

  const nyquist    = sampleRate / 2;
  const mon        = formData?.monitoring ?? {};
  const freqLo     = Number(mon.freq_lo_hz ?? 20000);
  const freqHi     = Number(mon.freq_hi_hz ?? 70000);
  const timeWindow = Number(mon.time_window_s ?? 3.0);

  const freqError = freqLo >= freqHi
    ? `Low (${freqLo.toLocaleString()} Hz) must be less than high (${freqHi.toLocaleString()} Hz)`
    : freqHi > nyquist
    ? `High frequency exceeds Nyquist (${(nyquist / 1000).toFixed(0)} kHz)`
    : null;

  const timeWindowError = isNaN(timeWindow) || timeWindow < 0.5
    ? "Time window must be at least 0.5 s"
    : timeWindow > 60
    ? "Time window must be 60 s or less"
    : null;

  // Strip monitoring section from ConfigFields so we render it manually below
  const configFieldsData = (() => {
    if (!formData) return formData;
    const { monitoring: _omit, ...rest } = formData;
    return rest;
  })();

  useEffect(() => {
    socket.emit("get_module_config", { module_id: module.id });
  }, [module.id]);

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

  const sections   = Object.keys(filterPrivateKeys(formData) ?? {}).filter(
    k => formData[k] !== null && typeof formData[k] === "object"
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

  return (
    <div className="config-card">
      <div className="card-header">
        <h3>{module.name} ({module.id})</h3>
        <div className="device-info">
          {typeof module.ip      === "string" && module.ip      && <span>IP: {module.ip}</span>}
          {typeof module.version === "string" && module.version && <span>{module.version}</span>}
        </div>
      </div>

      <div className="config-card-body">
        <div className="config-form">
          {clipboard && (
            <div className="clipboard-bar">
              <span className="clipboard-label">Clipboard: {clipboard.label}</span>
              <button type="button" className="copy-btn" onClick={handlePaste}>Paste</button>
              <button type="button" className="copy-btn" onClick={() => onCopy(null)}>Clear</button>
            </div>
          )}

          <form>
            <ConfigFields data={configFieldsData} handleChange={handleChange} />
          </form>

          {/* ── Monitoring display ── */}
          <fieldset className="nested-fieldset">
            <legend className="nested-fieldset-legend">monitoring</legend>
            <div className="nested">
              <div className="form-field">
                <label>freq_lo_hz:</label>
                <input type="number" min="0" max={nyquist} step="1000"
                  value={freqLo}
                  onChange={e => handleChange(["monitoring", "freq_lo_hz"], e)} />
              </div>
              <div className="form-field">
                <label>freq_hi_hz:</label>
                <input type="number" min="0" max={nyquist} step="1000"
                  value={freqHi}
                  onChange={e => handleChange(["monitoring", "freq_hi_hz"], e)} />
              </div>
              {freqError && (
                <div className="form-field">
                  <label></label>
                  <span className="config-sync-badge config-sync-badge--failed">{freqError}</span>
                </div>
              )}
              <div className="form-field">
                <label>time_window_s:</label>
                <input type="number" min="0.5" max="60" step="0.5"
                  value={timeWindow}
                  onChange={e => handleChange(["monitoring", "time_window_s"], e)} />
              </div>
              {timeWindowError && (
                <div className="form-field">
                  <label></label>
                  <span className="config-sync-badge config-sync-badge--failed">{timeWindowError}</span>
                </div>
              )}
            </div>
          </fieldset>

          <div className="filesize-preview">
            ~{estGbPerHour.toFixed(2)} GB / hr @ {(sampleRate / 1000).toFixed(0)}kHz
            {filetype === "flac" ? " (FLAC compressed)" : ` (${filetype.toUpperCase()} raw)`}
          </div>

          <div className="copy-bar">
            <span className="copy-bar-label">Copy:</span>
            {sections.map(key => (
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

          <div className="config-action-buttons">
            <button className="save-button" type="button" onClick={handleSave} disabled={!!freqError || !!timeWindowError}>
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

          {formData?.export !== undefined && (
            <div className="config-action-buttons">
              <button type="button" className="save-button"
                onClick={syncExport}
                disabled={syncStatus === "syncing"}>
                {syncStatus === "syncing" ? "Syncing…" : "Sync Export from Controller"}
              </button>
              {syncStatus && syncStatus !== "syncing" && (
                <span className={`config-sync-badge ${syncStatus.success ? "config-sync-badge--synced" : "config-sync-badge--failed"}`}>
                  {syncStatus.success ? "Export synced" : `Sync failed: ${syncStatus.error}`}
                </span>
              )}
            </div>
          )}
        </div>

        <div className="livestream-wrapper">
          <MicrophoneStream ip={module.ip} port={streamPort} />
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

      {showResetConfirm && (
        <div className="modal-overlay" onClick={() => setShowResetConfirm(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <p>Reset <strong>{module.name}</strong> to default settings?</p>
            <p className="modal-subtext">All unsaved changes and any custom configuration will be lost.</p>
            <div className="modal-buttons">
              <button className="reset-button" type="button" onClick={handleReset}>Reset</button>
              <button className="save-button"  type="button" onClick={() => setShowResetConfirm(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default MicrophoneConfigCard;
