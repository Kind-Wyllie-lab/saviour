import { useEffect, useState, useRef } from "react";
import socket from "/src/socket";
import { useConfigForm } from "../useConfigForm";
import { useHashTab } from "../useHashTab";
import ConfigFields from "../ConfigFields";
import FullscreenVideo from "/src/basic/components/FullscreenVideo/FullscreenVideo";
import SnapshotButton from "/src/basic/components/SnapshotButton/SnapshotButton";
import { videoFeedUrl } from "/src/basic/utils/streamUrls";
import ExportConfigSection from "../ExportConfigSection";
import ConfigCardShell from "../ConfigCardShell";
import { HIDDEN_CONFIG_SECTIONS } from "../configUtils";
import SyncTab from "./SyncTab";
import { calibrationStatus } from "./syncCalibration";
import CallBandsEditor from "/src/basic/components/CallBandsEditor/CallBandsEditor";
import { PALETTES, paletteByName } from "/src/basic/utils/spectrogramPalettes";
import { PaletteSwatch } from "/src/basic/components/SpectrogramSwatch/SpectrogramSwatch";

const STALL_MS     = 8000;
const RECONNECT_MS = 2500;
// Floor between actual reconnects, regardless of how many things ask for one.
// Each bump() fully unmounts/remounts the <img>, opening a fresh long-lived
// MJPEG connection — a burst of triggers in quick succession can otherwise
// open more concurrent connections to the same host than the browser
// allows, leaving the newest one stuck pending forever with neither onLoad
// nor onError ever firing to recover it.
const MIN_RECONNECT_MS = 3000;

function MicrophoneStream({ ip, port }) {
  const [bumpKey, setBumpKey] = useState(Date.now());
  const [fullscreen, setFullscreen]   = useState(false);
  const stallTimer     = useRef(null);
  const reconnectTimer = useRef(null);
  const lastBumpAt       = useRef(0);
  const pendingBumpTimer = useRef(null);

  const bump = () => {
    const now = Date.now();
    const elapsed = now - lastBumpAt.current;
    if (elapsed >= MIN_RECONNECT_MS) {
      lastBumpAt.current = now;
      setBumpKey(now);
    } else {
      clearTimeout(pendingBumpTimer.current);
      pendingBumpTimer.current = setTimeout(() => {
        lastBumpAt.current = Date.now();
        setBumpKey(Date.now());
      }, MIN_RECONNECT_MS - elapsed);
    }
  };

  const imgKey = `${bumpKey}`;

  useEffect(() => {
    stallTimer.current = setTimeout(bump, STALL_MS);
    return () => {
      clearTimeout(stallTimer.current);
      clearTimeout(reconnectTimer.current);
      clearTimeout(pendingBumpTimer.current);
    };
  }, [imgKey]);

  // Clear only, don't re-arm — Chrome fires onLoad once for a
  // multipart/x-mixed-replace (MJPEG) stream, on the first frame only, and
  // never again for subsequent frames. Rescheduling here would fire the
  // timer forever every STALL_MS regardless of stream health, forcing a
  // full reconnect (and killing the live video) on a healthy stream.
  // Recovery from a stream that actually dies is handled by onError.
  const resetStall = () => clearTimeout(stallTimer.current);

  const handleError = () => {
    clearTimeout(stallTimer.current);
    clearTimeout(reconnectTimer.current);
    reconnectTimer.current = setTimeout(bump, RECONNECT_MS);
  };

  const src = videoFeedUrl(ip, { port, key: bumpKey });

  return (
    <>
      <div
        className="snapshot-hover-parent"
        style={{ position: "relative", display: "block", lineHeight: 0 }}
      >
        <img
          key={imgKey}
          src={src}
          alt="Microphone monitor stream"
          style={{ width: "100%", maxHeight: "400px", objectFit: "contain", display: "block", borderRadius: "4px", cursor: "pointer" }}
          onLoad={resetStall}
          onError={handleError}
          onClick={() => setFullscreen(true)}
        />
        {ip && <SnapshotButton module={{ ip, name: "microphone" }} port={port} />}
      </div>
      {fullscreen && (
        <FullscreenVideo ip={ip} port={port} onClose={() => setFullscreen(false)} />
      )}
    </>
  );
}

// Selectable recorder block sizes (powers of two). 32768 is the default
// (settled 2026-09-07 for A/V-sync latency vs xrun headroom); smaller values
// cut first-read latency further but risk xruns at 192 kHz on a loaded Pi.
const MIC_BLOCK_SIZES = [8192, 16384, 32768, 65536, 131072, 262144];

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
  { key: "sync",      label: "Sync"      },
  { key: "export",    label: "Export"    },
];

function MicrophoneConfigCard({ id, module, clipboard, onCopy }) {
  const { formData, setFormData, handleChange, markSaved, isDirty } = useConfigForm(module.config);
  const [activeTab, setActiveTab]               = useHashTab("basic", MIC_TABS.map(t => t.key));
  const [discoveredSerials, setDiscoveredSerials] = useState([]);
  // serial -> { external, active } from the module's MICROPHONE query.
  const [mothInputs, setMothInputs] = useState({});
  const [streamEnabled, setStreamEnabled] = useState(true);

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
  // Colour mode: one palette over the whole spectrogram, or call bands (each
  // band in its own palette, grey elsewhere). Unset = bands if any are on,
  // matching src/shared/spectrogram_palette.resolve_colouring.
  const bandsOn       = (mon.call_bands ?? []).some(b => b.enabled !== false);
  const colourMode    = mon.colour_mode === "palette" || mon.colour_mode === "bands"
    ? mon.colour_mode
    : (bandsOn ? "bands" : "palette");

  // Stream display prefs — persisted in monitoring config, not local state
  const plotMode     = mon.plot_mode  ?? "spectrogram";
  const freqRange    = mon.freq_range ?? "band";
  const streamLayout = mon.layout     ?? "stacked";
  // Which settings each plot type uses (microphone_module._render_cell).
  const isSpectrogram = plotMode === "spectrogram" || plotMode === "spectrogram_compact";
  const usesRange     = isSpectrogram || plotMode === "spectrum";
  const usesBand      = (usesRange && freqRange === "band") || plotMode === "band_power";
  const usesPeakHold  = plotMode === "spectrogram" || plotMode === "spectrum" || plotMode === "peaks";

  const setMonitorPref = (key, value) => {
    setFormData(prev => {
      const cloned = structuredClone(prev);
      if (!cloned.monitoring) cloned.monitoring = {};
      cloned.monitoring[key] = value;
      return cloned;
    });
  };

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

  // Strip sections that are rendered in dedicated tabs, plus transport/infra
  // sections that are never surfaced as editable fields (see HIDDEN_CONFIG_SECTIONS).
  const configFieldsData = (() => {
    if (!formData) return formData;
    const {
      monitoring: _m, audiomoth: _a, audiomoth_labels: _al,
      module: _mod, export: _exp, recording: _rec, microphone: _mic,
      sync_pulse: _sp, audiomoth_sync_offsets_ms: _so,
      ...rest
    } = formData;
    for (const k of HIDDEN_CONFIG_SECTIONS) delete rest[k];
    return rest;
  })();

  const GAIN_LABELS = ["Low", "Low-Medium", "Medium", "Medium-High", "High"];
  const AM_SAMPLE_RATES = [8000, 16000, 32000, 48000, 96000, 192000, 250000, 384000];

  // Recording block size (samples per recorder.record() call). One control
  // drives both microphone.block_size and microphone.frame_num — they are
  // kept equal; decouple via the config file if ever needed. Applies to the
  // next recording, not live.
  const micCfg     = formData?.microphone ?? {};
  const blockSize  = Number(micCfg.block_size ?? 32768);
  const blockMs    = (blockSize / amRate) * 1000;
  const monEnabled = mon.enabled !== false;

  const setBlockSize = (n) => {
    setFormData(prev => {
      const cloned = structuredClone(prev);
      if (!cloned.microphone) cloned.microphone = {};
      cloned.microphone.block_size = n;
      cloned.microphone.frame_num  = n;
      return cloned;
    });
  };


  const refreshAudiomoths = () =>
    socket.emit("send_command", { module_id: module.id, type: "list_audiomoths", params: {} });

  useEffect(() => {
    socket.emit("get_module_config", { module_id: module.id });
    socket.emit("send_command", { module_id: module.id, type: "list_audiomoths", params: {} });

    const onAudiomothList = (data) => {
      if (data.module_id === module.id) {
        setDiscoveredSerials(Object.keys(data.audiomoths ?? {}));
        setMothInputs(data.inputs ?? {});
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

  // Per-AudioMoth settings (audiomoth_settings: serial -> { gain }). The
  // module-wide audiomoth.gain isn't shown any more: it's only the starting
  // gain for an AudioMoth that has never been set.
  const amSettings = formData?.audiomoth_settings ?? {};
  const handleDeviceGainChange = (serial, value) => {
    setFormData(prev => {
      const cloned = structuredClone(prev);
      const all = { ...(cloned.audiomoth_settings ?? {}) };
      const entry = { ...(all[serial] ?? {}) };
      entry.gain = Number(value);
      if (Object.keys(entry).length) all[serial] = entry;
      else delete all[serial];
      cloned.audiomoth_settings = all;
      return cloned;
    });
  };

  // Show discovered serials plus any already-labelled or overridden serials
  // (for disconnected devices)
  const allLabelledSerials = Object.keys(formData?.audiomoth_labels ?? {});
  const allSerials = [...new Set([
    ...discoveredSerials, ...allLabelledSerials, ...Object.keys(amSettings),
  ])];

  // Compute tabBadges: warn on monitor tab if there are validation errors,
  // and on Sync if a connected AudioMoth's calibration is missing or stale.
  const tabBadges = (freqError || timeWindowError) ? { monitor: "⚠" } : {};
  const calCurrent = {
    sampleRate: Number(module.config?.audiomoth?.sample_rate ?? 0) || null,
    blockSize: Number(module.config?.microphone?.block_size ?? 0) || null,
  };
  if (discoveredSerials.some(s => calibrationStatus(
      module.config?.audiomoth_sync_offsets_ms?.[s], calCurrent).level !== "ok")) {
    tabBadges.sync = "⚠";
  }

  // Right side of the card is the live plot, like every other config card's
  // MJPEG stream, with a Pause/Resume button for this browser's view only.
  // The plot settings (Plot / Range / Layout / colouring) live on the
  // Monitor tab and drive what renders here.
  const sidebar = !monEnabled ? (
    <div className="monitor-stream-paused">
      Live monitor is off on this module (Monitor tab)
    </div>
  ) : (
    <div>
      {streamEnabled ? (
        <MicrophoneStream ip={module.ip} port={streamPort}
          plotMode={plotMode} freqRange={freqRange} layout={streamLayout} />
      ) : (
        <div className="monitor-stream-paused">View paused</div>
      )}
      <button type="button" className="mic-stream-pause"
        title="Only stops the picture in this browser tab; the module keeps running the live monitor."
        onClick={() => setStreamEnabled(v => !v)}>
        {streamEnabled ? "Pause view" : "Resume view"}
      </button>
    </div>
  );

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
      markSaved={markSaved}
      isDirty={isDirty}
      saveDisabled={!!freqError || !!timeWindowError}
      tabBadges={tabBadges}
      sidebar={sidebar}
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
          <div className="form-field">
            <label title="Samples per recorder read. Smaller blocks cut the first-read priming latency but raise the risk of dropped audio (xruns) at high sample rates. Takes effect on the next recording.">
              Block size:
            </label>
            <select value={blockSize}
              onChange={e => setBlockSize(Number(e.target.value))}>
              {MIC_BLOCK_SIZES.map(n => (
                <option key={n} value={n}>
                  {n.toLocaleString()} samples (~{((n / amRate) * 1000).toFixed(0)} ms)
                </option>
              ))}
            </select>
          </div>
          {blockSize < 32768 && (
            <div className="sensor-mode-info sensor-mode-info--muted">
              Small blocks (~{blockMs.toFixed(0)} ms) can drop audio at{" "}
              {(amRate / 1000).toFixed(0)} kHz on a loaded Pi — bench testing only.
            </div>
          )}
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
            {/* Each AudioMoth: connected?, its name (used everywhere: file
                names, live monitor, Sync tab, habitat dashboard) and gain. */}
            <div className="mic-moths">
              <div className="mic-moths__head">
                <span className="mic-moths__title">AudioMoths</span>
                <button type="button" className="mic-moths__refresh" onClick={refreshAudiomoths}
                  title="Scan the module for connected AudioMoths">Refresh</button>
              </div>
              {allSerials.length === 0 ? (
                <div className="sensor-mode-info sensor-mode-info--muted">
                  No AudioMoths found. Connect one to the module and press Refresh.
                </div>
              ) : (
                <>
                  <div className="mic-moths__row mic-moths__row--header">
                    <span />
                    <span>Name</span>
                    <span>Gain</span>
                  </div>
                  {allSerials.map(serial => {
                    const connected = discoveredSerials.includes(serial);
                    const gain = amSettings[serial]?.gain ?? amGain;
                    return (
                      <div key={serial} className="mic-moths__row">
                        <span className={`mic-moths__dot${connected ? " mic-moths__dot--on" : ""}`}
                          title={connected ? "Connected" : "Not connected"} />
                        <span className="mic-moths__name">
                          <input type="text" maxLength={32}
                            value={formData?.audiomoth_labels?.[serial] ?? ""}
                            placeholder="Name, e.g. A1"
                            aria-label={`Name for AudioMoth ${serial}`}
                            onChange={e => handleLabelChange(serial, e.target.value)} />
                          <span className="mic-moths__serial">
                            {serial}{connected ? "" : " · not connected"}
                            {connected && mothInputs[serial] && (
                              <span title={mothInputs[serial].external
                                ? "Has an external microphone: records through it (the sync self-test uses the internal one)"
                                : "Onboard microphone (no external input on this unit)"}>
                                {" · "}{mothInputs[serial].active === "external" ? "external mic" : "internal mic"}
                              </span>
                            )}
                          </span>
                        </span>
                        <select value={gain} aria-label={`Gain for AudioMoth ${serial}`}
                          onChange={e => handleDeviceGainChange(serial, e.target.value)}>
                          {GAIN_LABELS.map((label, i) => (
                            <option key={i} value={i}>{i} - {label}</option>
                          ))}
                        </select>
                      </div>
                    );
                  })}
                  <div className="sensor-mode-info sensor-mode-info--muted">
                    Names are used in recording file names, the live monitor, the Sync tab and
                    the habitat dashboard. Changing a gain doesn&apos;t interrupt the live monitor.
                  </div>
                </>
              )}
            </div>
            <div className="mic-moths__title mic-moths__title--section">All AudioMoths</div>
            <div className="form-field">
              <label>Sample rate:</label>
              <select value={amRate} onChange={handleSampleRateChange}>
                {AM_SAMPLE_RATES.map(r => (
                  <option key={r} value={r}>{(r / 1000).toFixed(0)} kHz</option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label title="Rolling % of samples clipping that lights the CLIP indicator on the monitor and logs a warning per segment. Advisory only.">
                Clipping warn threshold (%):
              </label>
              <input type="number" min="0" max="100" step="0.1"
                value={am.clip_warn_pct ?? 0.5}
                onChange={e => handleChange(["audiomoth", "clip_warn_pct"], e)} />
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
          {/* 1. Module setting: whether the live monitor runs at all. */}
          <div className="mic-monitor-section">
            <div className="mic-monitor-section__title">Live monitor</div>
            <div className="form-field">
              <label>Run on this module:</label>
              <input type="checkbox" checked={monEnabled}
                onChange={e => handleChange(["monitoring", "enabled"], e)} />
            </div>
            <div className="sensor-mode-info sensor-mode-info--muted">
              {monEnabled
                ? "Keeps a second recorder open on every AudioMoth for the live plot, the CLIP indicator and the dashboard level meters. Recording is unaffected. Turn off to save CPU, or to isolate first-read latency in A/V-sync tests."
                : "Off: no live plot, CLIP indicator, rolling clip % or dashboard level meters. Recording is unaffected."}
            </div>
          </div>

          {monEnabled && (
            <>
              {/* 2. What the plot shows. Each setting appears only for the
                  plot types that use it. */}
              <div className="mic-monitor-section">
                <div className="mic-monitor-section__title">Plot</div>
                <div className="form-field">
                  <label>Type:</label>
                  <select value={plotMode}
                    onChange={e => setMonitorPref("plot_mode", e.target.value)}>
                    <option value="spectrogram">Spectrogram</option>
                    <option value="spectrogram_compact">Spectrogram (no meter)</option>
                    <option value="spectrum">Spectrum</option>
                    <option value="peaks">Peaks</option>
                    <option value="waveform">Waveform</option>
                    <option value="history">History</option>
                    <option value="band_power">Band Power</option>
                  </select>
                </div>
                {discoveredSerials.length > 1 && (
                  <div className="form-field">
                    <label>Layout:</label>
                    <select value={streamLayout}
                      onChange={e => setMonitorPref("layout", e.target.value)}>
                      <option value="stacked">Stacked</option>
                      <option value="grid">Grid</option>
                    </select>
                  </div>
                )}
                {usesRange && (
                  <div className="form-field">
                    <label>Frequency range:</label>
                    <select value={freqRange}
                      onChange={e => setMonitorPref("freq_range", e.target.value)}>
                      <option value="band">Band (set below)</option>
                      <option value="full">Full (0–{nyquistKhz.toFixed(0)} kHz)</option>
                    </select>
                  </div>
                )}
                {usesBand && (
                  <>
                    <div className="form-field">
                      <label>{plotMode === "band_power" ? "Target band low (kHz):" : "Band low (kHz):"}</label>
                      <input type="number" min="0" max={nyquistKhz} step="0.5"
                        value={freqLoKhz}
                        onChange={e => handleKhzChange(["monitoring", "freq_lo_hz"], e.target.value)} />
                    </div>
                    <div className="form-field">
                      <label>{plotMode === "band_power" ? "Target band high (kHz):" : "Band high (kHz):"}</label>
                      <input type="number" min="0" max={nyquistKhz} step="0.5"
                        value={freqHiKhz}
                        onChange={e => handleKhzChange(["monitoring", "freq_hi_hz"], e.target.value)} />
                    </div>
                    <div className="sensor-mode-info sensor-mode-info--muted">
                      Nyquist: {nyquistKhz.toFixed(1)} kHz @ {(sampleRate / 1000).toFixed(0)} kHz sample rate
                    </div>
                  </>
                )}
                {freqError && (
                  <div className="form-field">
                    <label></label>
                    <span className="config-sync-badge config-sync-badge--failed">{freqError}</span>
                  </div>
                )}
                {isSpectrogram && (
                  <div className="form-field">
                    <label>Time window (s):</label>
                    <input type="number" min="0.5" max="60" step="0.5"
                      value={timeWindow}
                      onChange={e => handleChange(["monitoring", "time_window_s"], e)} />
                  </div>
                )}
                {timeWindowError && (
                  <div className="form-field">
                    <label></label>
                    <span className="config-sync-badge config-sync-badge--failed">{timeWindowError}</span>
                  </div>
                )}
                {usesPeakHold && (
                  <div className="form-field">
                    <label>Peak hold (s):</label>
                    <input type="number" min="0.1" max="30" step="0.1"
                      value={mon.peak_hold_s ?? 2.0}
                      onChange={e => handleChange(["monitoring", "peak_hold_s"], e)} />
                  </div>
                )}
              </div>

              {/* 3. Spectrogram colouring: only spectrogram plots use it. */}
              {isSpectrogram && (
                <div className="mic-monitor-section">
                  <div className="mic-monitor-section__title">Spectrogram colouring</div>
                  <div className="mic-colour-mode" role="radiogroup" aria-label="Spectrogram colouring">
                    {[
                      ["palette", "One palette", "The whole spectrogram in one colour palette"],
                      ["bands", "Frequency bands", "Each frequency band in its own colours, grey everywhere else"],
                    ].map(([value, label, title]) => (
                      <button type="button" key={value} role="radio" title={title}
                        aria-checked={colourMode === value}
                        className={`mic-colour-mode__option${colourMode === value ? " mic-colour-mode__option--active" : ""}`}
                        onClick={() => setMonitorPref("colour_mode", value)}>
                        {label}
                      </button>
                    ))}
                  </div>
                  {colourMode === "palette" ? (
                    <div className="mic-colormap-picker">
                      <span className="mic-colormap-picker__label">
                        Palette: <strong>{paletteByName(mon.colormap).label}</strong>
                      </span>
                      <div className="mic-colormap-picker__swatches">
                        {PALETTES.map((p) => (
                          <button
                            type="button"
                            key={p.name}
                            className={`mic-colormap-picker__swatch${
                              paletteByName(mon.colormap).name === p.name
                                ? " mic-colormap-picker__swatch--active"
                                : ""
                            }`}
                            title={p.label}
                            aria-label={p.label}
                            onClick={() => {
                              setMonitorPref("colormap", p.name);
                              setMonitorPref("colormap_stops", []);
                            }}
                          >
                            <PaletteSwatch stops={p.stops} width={100} height={28} />
                          </button>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <CallBandsEditor
                      bands={mon.call_bands ?? []}
                      onChange={(bands) => setMonitorPref("call_bands", bands)}
                      nyquistHz={nyquist}
                      plotLoHz={freqRange === "full" ? 0 : freqLo}
                      plotHiHz={freqRange === "full" ? nyquist : freqHi}
                    />
                  )}
                </div>
              )}
            </>
          )}
        </>
      )}

      {/* SYNC tab */}
      {activeTab === "sync" && (
        <SyncTab
          module={module}
          formData={formData}
          setFormData={setFormData}
          serials={allSerials}
          discovered={discoveredSerials}
          labelFor={serial => formData?.audiomoth_labels?.[serial] || serial}
        />
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
