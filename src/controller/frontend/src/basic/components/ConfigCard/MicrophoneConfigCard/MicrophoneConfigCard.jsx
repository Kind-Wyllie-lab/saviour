import { useEffect, useState, useRef } from "react";
import socket from "/src/socket";
import { useConfigForm } from "../useConfigForm";
import ConfigFields from "../ConfigFields";
import FullscreenVideo from "/src/basic/components/FullscreenVideo/FullscreenVideo";
import ExportConfigSection from "../ExportConfigSection";
import ConfigCardShell from "../ConfigCardShell";

const STALL_MS     = 8000;
const RECONNECT_MS = 2500;

function MicrophoneStream({ ip, port, plotMode, freqRange, layout }) {
  const [bumpKey, setBumpKey] = useState(Date.now());
  const [fullscreen, setFullscreen]   = useState(false);
  const stallTimer     = useRef(null);
  const reconnectTimer = useRef(null);
  const bump = () => setBumpKey(Date.now());

  // Reconnect whenever display options change
  const imgKey = `${bumpKey}-${plotMode}-${freqRange}-${layout}`;

  useEffect(() => {
    stallTimer.current = setTimeout(bump, STALL_MS);
    return () => {
      clearTimeout(stallTimer.current);
      clearTimeout(reconnectTimer.current);
    };
  }, [imgKey]);

  const resetStall = () => clearTimeout(stallTimer.current);

  const handleError = () => {
    clearTimeout(stallTimer.current);
    clearTimeout(reconnectTimer.current);
    reconnectTimer.current = setTimeout(bump, RECONNECT_MS);
  };

  const src = `http://${ip}:${port}/video_feed?mode=${plotMode}&range=${freqRange}&layout=${layout}&t=${bumpKey}`;

  return (
    <>
      <img
        key={imgKey}
        src={src}
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

const TAB_COPY_SECTION = {
  basic:     { key: "module",     label: "Basic"     },
  recording: { key: "recording",  label: "Recording" },
  audiomoth: { key: "audiomoth",  label: "AudioMoth" },
  monitor:   { key: "monitoring", label: "Monitor"   },
  export:    { key: "export",     label: "Export"    },
  // labels: omitted — serial-specific, not meaningful to copy to other modules
};

const MIC_TABS = [
  { key: "basic",     label: "Basic"     },
  { key: "recording", label: "Recording" },
  { key: "audiomoth", label: "AudioMoth" },
  { key: "monitor",   label: "Monitor"   },
  { key: "labels",    label: "Labels"    },
  { key: "export",    label: "Export"    },
];

function MicrophoneConfigCard({ id, module, clipboard, onCopy }) {
  const { formData, setFormData, handleChange } = useConfigForm(module.config);
  const [activeTab, setActiveTab]               = useState("basic");
  const [discoveredSerials, setDiscoveredSerials] = useState([]);
  const [plotMode,  setPlotMode]  = useState("spectrogram");
  const [freqRange, setFreqRange] = useState("band");
  const [streamLayout, setStreamLayout] = useState("stacked");

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

  // Strip sections that are rendered in dedicated tabs
  const configFieldsData = (() => {
    if (!formData) return formData;
    const {
      monitoring: _m, audiomoth: _a, audiomoth_labels: _al,
      module: _mod, export: _exp, recording: _rec,
      ...rest
    } = formData;
    return rest;
  })();

  const GAIN_LABELS = ["Low", "Low-Medium", "Medium", "Medium-High", "High"];
  const AM_SAMPLE_RATES = [8000, 16000, 32000, 48000, 96000, 192000, 250000, 384000];


  useEffect(() => {
    socket.emit("get_module_config", { module_id: module.id });
    socket.emit("send_command", { module_id: module.id, type: "list_audiomoths", params: {} });

    const onAudiomothList = (data) => {
      if (data.module_id === module.id) {
        setDiscoveredSerials(Object.keys(data.audiomoths ?? {}));
      }
    };
    socket.on("audiomoth_list_response", onAudiomothList);
    return () => socket.off("audiomoth_list_response", onAudiomothList);
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

  const handleLabelChange = (serial, value) => {
    setFormData(prev => {
      const cloned = structuredClone(prev);
      if (!cloned.audiomoth_labels) cloned.audiomoth_labels = {};
      cloned.audiomoth_labels[serial] = value;
      return cloned;
    });
  };

  // Show discovered serials plus any already-labelled serials (for disconnected devices)
  const allLabelledSerials = Object.keys(formData?.audiomoth_labels ?? {});
  const allSerials = [...new Set([...discoveredSerials, ...allLabelledSerials])];

  // Compute tabBadges: warn on monitor tab if there are validation errors
  const tabBadges = (freqError || timeWindowError) ? { monitor: "⚠" } : {};

  return (
    <ConfigCardShell
      id={id}
      module={module}
      formData={formData}
      clipboard={clipboard}
      onCopy={onCopy}
      onPaste={handlePaste}
      tabs={MIC_TABS}
      activeTab={activeTab}
      onTabChange={setActiveTab}
      tabSectionMap={TAB_COPY_SECTION}
      saveDisabled={!!freqError || !!timeWindowError}
      tabBadges={tabBadges}
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

      {/* RECORDING tab */}
      {activeTab === "recording" && (
        <>
          <div className="filesize-preview">
            ~{estGbPerHour.toFixed(2)} GB / hr @ {(sampleRate / 1000).toFixed(0)}kHz
            {filetype === "flac" ? " (FLAC compressed)" : ` (${filetype.toUpperCase()} raw)`}
          </div>
          <div className="form-field">
            <label>Segment length (mins):</label>
            <input type="number" min="1" step="1"
              value={formData?.recording?.segment_length_mins ?? 60}
              onChange={e => handleChange(["recording", "segment_length_mins"], e)} />
          </div>
          <div className="config-section-divider" />
          <form>
            <ConfigFields data={configFieldsData} handleChange={handleChange} />
          </form>
        </>
      )}

      {/* AUDIOMOTH tab */}
      {activeTab === "audiomoth" && (
        formData?.audiomoth !== undefined ? (
          <>
            <div className="form-field">
              <label>Sample rate:</label>
              <select value={amRate} onChange={handleSampleRateChange}>
                {AM_SAMPLE_RATES.map(r => (
                  <option key={r} value={r}>{(r / 1000).toFixed(0)} kHz</option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Gain:</label>
              <select value={amGain} onChange={e => handleChange(["audiomoth", "gain"], e)}>
                {GAIN_LABELS.map((label, i) => (
                  <option key={i} value={i}>{i} — {label}</option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Filter type:</label>
              <select value={amFilter} onChange={e => handleChange(["audiomoth", "filter_type"], e)}>
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
            <div className="config-section-divider" />
            <div className="form-field">
              <label>Low gain range:</label>
              <input type="checkbox" checked={!!am.low_gain_range}
                onChange={e => handleChange(["audiomoth", "low_gain_range"], e)} />
            </div>
            <div className="form-field">
              <label>Energy saver mode:</label>
              <input type="checkbox" checked={!!am.energy_saver_mode}
                onChange={e => handleChange(["audiomoth", "energy_saver_mode"], e)} />
            </div>
            <div className="form-field">
              <label>Disable 48 Hz filter:</label>
              <input type="checkbox" checked={!!am.disable_48hz_filter}
                onChange={e => handleChange(["audiomoth", "disable_48hz_filter"], e)} />
            </div>
            <div className="form-field">
              <label>LED enabled:</label>
              <input type="checkbox" checked={!!am.led_enabled}
                onChange={e => handleChange(["audiomoth", "led_enabled"], e)} />
            </div>
          </>
        ) : (
          <div className="sensor-mode-info sensor-mode-info--muted">
            No AudioMoth configuration found for this module.
          </div>
        )
      )}

      {/* MONITOR tab */}
      {activeTab === "monitor" && (
        <>
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
            <label>Time window (s):</label>
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
          <div className="sensor-mode-info" style={{ marginTop: "8px" }}>
            Nyquist: {nyquistKhz.toFixed(1)} kHz @ {(sampleRate / 1000).toFixed(0)} kHz sample rate
          </div>
          <div className="config-section-divider" />

          {/* Stream display controls */}
          <div className="monitor-controls">
            <div className="monitor-controls__row">
              <span className="monitor-controls__label">Plot</span>
              {["spectrogram", "spectrum"].map(m => (
                <button key={m} type="button"
                  className={`monitor-toggle-btn${plotMode === m ? " monitor-toggle-btn--active" : ""}`}
                  onClick={() => setPlotMode(m)}>
                  {m === "spectrogram" ? "Spectrogram" : "Spectrum"}
                </button>
              ))}
            </div>
            <div className="monitor-controls__row">
              <span className="monitor-controls__label">Range</span>
              {[["band", "Band"], ["full", "Full"]].map(([val, lbl]) => (
                <button key={val} type="button"
                  className={`monitor-toggle-btn${freqRange === val ? " monitor-toggle-btn--active" : ""}`}
                  onClick={() => setFreqRange(val)}>
                  {lbl}
                </button>
              ))}
            </div>
            {discoveredSerials.length > 1 && (
              <div className="monitor-controls__row">
                <span className="monitor-controls__label">Layout</span>
                {[["stacked", "Stacked"], ["grid", "Grid"]].map(([val, lbl]) => (
                  <button key={val} type="button"
                    className={`monitor-toggle-btn${streamLayout === val ? " monitor-toggle-btn--active" : ""}`}
                    onClick={() => setStreamLayout(val)}>
                    {lbl}
                  </button>
                ))}
              </div>
            )}
          </div>

          <MicrophoneStream ip={module.ip} port={streamPort}
            plotMode={plotMode} freqRange={freqRange} layout={streamLayout} />
        </>
      )}

      {/* LABELS tab */}
      {activeTab === "labels" && (
        <>
          {allSerials.length === 0 ? (
            <div className="sensor-mode-info sensor-mode-info--muted">
              No AudioMoths discovered — connect devices and refresh
            </div>
          ) : allSerials.map(serial => (
            <div key={serial} className="form-field">
              <label title={serial}>
                {discoveredSerials.includes(serial) ? serial : `${serial} (disconnected)`}:
              </label>
              <input type="text"
                value={formData?.audiomoth_labels?.[serial] ?? ""}
                placeholder={serial}
                onChange={e => handleLabelChange(serial, e.target.value)} />
            </div>
          ))}
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
  );
}

export default MicrophoneConfigCard;
