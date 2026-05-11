import { useEffect, useState, useRef } from "react";
import socket from "/src/socket";
import { useConfigForm } from "../useConfigForm";
import { filterPrivateKeys, checkClipboardCompatibility } from "../configUtils";
import ConfigFields from "../ConfigFields";
import FullscreenVideo from "/src/basic/components/FullscreenVideo/FullscreenVideo";
import { useModuleUpdate } from "/src/hooks/useModuleUpdate";
import ExportSyncButton from "../ExportSyncButton";

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
  const [applyAllConfirm, setApplyAllConfirm] = useState(null);
  const { updateStatus, handleUpdate } = useModuleUpdate(module.id);

  const streamPort = module.config?.monitoring?._port ?? 8081;

  // Audiomoth values come from formData so all derived quantities (Nyquist,
  // filesize estimate) stay live as the user edits, before they hit Save.
  const am           = formData?.audiomoth ?? {};
  const amGain       = Number(am.gain ?? 2);
  const amRate       = Number(am.sample_rate ?? 192000);
  const amFilter     = am.filter_type ?? "none";
  const amLo         = Number(am.filter_lo_hz ?? 20000);
  const amHi         = Number(am.filter_hi_hz ?? 90000);
  const amLoKhz      = amLo / 1000;
  const amHiKhz      = amHi / 1000;
  const amNyquistKhz = amRate / 2000;

  const sampleRate    = formData?.audiomoth !== undefined
    ? amRate
    : (module.config?.microphone?._sample_rate ?? 192000);
  const filetype      = module.config?.recording?._recording_filetype ?? "flac";
  const bytesPerSec   = sampleRate * 2; // 16-bit mono
  const rawGbPerHour  = (bytesPerSec * 3600) / 1e9;
  const estGbPerHour  = filetype === "flac" ? rawGbPerHour * 0.6 : rawGbPerHour;

  const nyquist       = sampleRate / 2;
  const nyquistKhz    = nyquist / 1000;
  const mon           = formData?.monitoring ?? {};
  const freqLo        = Number(mon.freq_lo_hz ?? 20000);
  const freqHi        = Number(mon.freq_hi_hz ?? 70000);
  const freqLoKhz     = freqLo / 1000;
  const freqHiKhz     = freqHi / 1000;
  const timeWindow    = Number(mon.time_window_s ?? 3.0);

  const freqError = freqLo >= freqHi
    ? `Low (${freqLoKhz} kHz) must be less than high (${freqHiKhz} kHz)`
    : freqHi > nyquist
    ? `High frequency exceeds Nyquist (${nyquistKhz.toFixed(1)} kHz)`
    : null;

  const timeWindowError = isNaN(timeWindow) || timeWindow < 0.5
    ? "Time window must be at least 0.5 s"
    : timeWindow > 60
    ? "Time window must be 60 s or less"
    : null;

  // Strip monitoring and audiomoth sections — rendered manually below
  const configFieldsData = (() => {
    if (!formData) return formData;
    const { monitoring: _m, audiomoth: _a, ...rest } = formData;
    return rest;
  })();

  const GAIN_LABELS = ["Low", "Low-Medium", "Medium", "Medium-High", "High"];
  const AM_SAMPLE_RATES = [8000, 16000, 32000, 48000, 96000, 192000, 250000, 384000];


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

  // kHz display ↔ Hz storage: inputs show kHz floats, config stores integer Hz
  const handleKhzChange = (path, khzValue) => {
    const hz = Math.round(Number(khzValue) * 1000);
    setFormData(prev => {
      const cloned = structuredClone(prev);
      let node = cloned;
      for (const key of path.slice(0, -1)) node = node[key];
      node[path[path.length - 1]] = hz;
      return cloned;
    });
  };

  // When sample rate drops, auto-clamp freq_hi to the new Nyquist so the form
  // stays valid and the user doesn't get locked out of Save.
  const handleSampleRateChange = (e) => {
    const newRate = Number(e.target.value);
    const newNyquist = newRate / 2;
    setFormData(prev => {
      const cloned = structuredClone(prev);
      cloned.audiomoth.sample_rate = newRate;
      if (cloned.monitoring && cloned.monitoring.freq_hi_hz > newNyquist) {
        cloned.monitoring.freq_hi_hz = newNyquist;
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

  const confirmApplyToAll = () => {
    if (!applyAllConfirm) return;
    const { section, moduleType } = applyAllConfirm;
    const filtered = filterPrivateKeys(formData);
    const data = filtered?.[section];
    if (data) {
      socket.emit("apply_section_to_type", { module_type: moduleType ?? null, section, data });
    }
    setApplyAllConfirm(null);
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

          <form>
            <ConfigFields data={configFieldsData} handleChange={handleChange}
              sectionExtras={{ export: <ExportSyncButton moduleId={module.id} /> }} />
          </form>

          {/* ── Monitoring display ── */}
          <fieldset className="nested-fieldset">
            <legend className="nested-fieldset-legend">monitoring</legend>
            <div className="nested">
              <div className="form-field">
                <label>Freq low (kHz):</label>
                <input type="number" min="0" max={nyquistKhz} step="0.5"
                  value={freqLoKhz}
                  onChange={e => handleKhzChange(["monitoring", "freq_lo_hz"], e.target.value)} />
              </div>
              <div className="form-field">
                <label>Freq high (kHz):</label>
                <input type="number" min="0" max={nyquistKhz} step="0.5"
                  value={freqHiKhz}
                  onChange={e => handleKhzChange(["monitoring", "freq_hi_hz"], e.target.value)} />
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

          {/* ── AudioMoth hardware config ── */}
          {formData?.audiomoth !== undefined && (
            <fieldset className="nested-fieldset">
              <legend className="nested-fieldset-legend">audiomoth</legend>
              <div className="nested">
                <div className="form-field">
                  <label>sample_rate:</label>
                  <select value={amRate}
                    onChange={handleSampleRateChange}>
                    {AM_SAMPLE_RATES.map(r => (
                      <option key={r} value={r}>{(r / 1000).toFixed(0)} kHz</option>
                    ))}
                  </select>
                </div>
                <div className="form-field">
                  <label>gain:</label>
                  <select value={amGain}
                    onChange={e => handleChange(["audiomoth", "gain"], e)}>
                    {GAIN_LABELS.map((label, i) => (
                      <option key={i} value={i}>{i} — {label}</option>
                    ))}
                  </select>
                </div>
                <div className="form-field">
                  <label>filter_type:</label>
                  <select value={amFilter}
                    onChange={e => handleChange(["audiomoth", "filter_type"], e)}>
                    <option value="none">None</option>
                    <option value="lpf">Low-pass (LPF)</option>
                    <option value="hpf">High-pass (HPF)</option>
                    <option value="bpf">Band-pass (BPF)</option>
                  </select>
                </div>
                {(amFilter === "hpf" || amFilter === "bpf") && (
                  <div className="form-field">
                    <label>{amFilter === "bpf" ? "Filter low (kHz):" : "Cutoff (kHz):"}</label>
                    <input type="number" min="0" max={amNyquistKhz} step="0.5"
                      value={amLoKhz}
                      onChange={e => handleKhzChange(["audiomoth", "filter_lo_hz"], e.target.value)} />
                  </div>
                )}
                {(amFilter === "lpf" || amFilter === "bpf") && (
                  <div className="form-field">
                    <label>{amFilter === "bpf" ? "Filter high (kHz):" : "Cutoff (kHz):"}</label>
                    <input type="number" min="0" max={amNyquistKhz} step="0.5"
                      value={amHiKhz}
                      onChange={e => handleKhzChange(["audiomoth", "filter_hi_hz"], e.target.value)} />
                  </div>
                )}
                <div className="form-field">
                  <label>low_gain_range:</label>
                  <input type="checkbox" checked={!!am.low_gain_range}
                    onChange={e => handleChange(["audiomoth", "low_gain_range"], e)} />
                </div>
                <div className="form-field">
                  <label>energy_saver_mode:</label>
                  <input type="checkbox" checked={!!am.energy_saver_mode}
                    onChange={e => handleChange(["audiomoth", "energy_saver_mode"], e)} />
                </div>
                <div className="form-field">
                  <label>disable_48hz_filter:</label>
                  <input type="checkbox" checked={!!am.disable_48hz_filter}
                    onChange={e => handleChange(["audiomoth", "disable_48hz_filter"], e)} />
                </div>
                <div className="form-field">
                  <label>led_enabled:</label>
                  <input type="checkbox" checked={!!am.led_enabled}
                    onChange={e => handleChange(["audiomoth", "led_enabled"], e)} />
                </div>
              </div>
            </fieldset>
          )}

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

          <div className="copy-bar">
            <span className="copy-bar-label">Apply to all {module.type}s:</span>
            {sections.map(key => (
              <button key={key} type="button" className="copy-btn"
                onClick={() => setApplyAllConfirm({ section: key, label: capitalize(key), moduleType: module.type })}>
                {capitalize(key)}
              </button>
            ))}
          </div>

          <div className="copy-bar">
            <span className="copy-bar-label">Apply to all modules:</span>
            {sections.map(key => (
              <button key={key} type="button" className="copy-btn"
                onClick={() => setApplyAllConfirm({ section: key, label: capitalize(key), moduleType: null })}>
                {capitalize(key)}
              </button>
            ))}
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

      {applyAllConfirm && (
        <div className="modal-overlay" onClick={() => setApplyAllConfirm(null)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <p>
              Apply <strong>{applyAllConfirm.label}</strong> settings from{" "}
              <strong>{module.name}</strong> to all connected{" "}
              {applyAllConfirm.moduleType ? `${applyAllConfirm.moduleType} ` : ""}modules?
            </p>
            <p className="modal-subtext">
              This will overwrite the {applyAllConfirm.label.toLowerCase()} config on every{" "}
              {applyAllConfirm.moduleType ?? "module"} and save immediately — unsaved changes on other modules will be lost.
            </p>
            <div className="modal-buttons">
              <button className="save-button" type="button" onClick={confirmApplyToAll}>Apply to All</button>
              <button className="reset-button" type="button" onClick={() => setApplyAllConfirm(null)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default MicrophoneConfigCard;
