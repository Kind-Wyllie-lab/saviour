import { useEffect, useRef, useState } from "react";
import socket from "/src/socket";
import useIsLoggedIn from "/src/hooks/useIsLoggedIn";
import { useConfigForm } from "../useConfigForm";
import { useHashTab } from "../useHashTab";
import ConfigCardShell from "../ConfigCardShell";
import ExportConfigSection from "../ExportConfigSection";
import LibraryTab from "./LibraryTab";
import "./SoundConfigCard.css";

// Sound module (sound HAT: HiFiBerry DAC8x = 4 stereo outputs, Pi DAC Pro = 1).
// Outputs tab: what the module detected and a Test button per output; Library:
// the controller's sound library (plans/sound-library.md); Playback: the
// defaults a sound plays with and the welfare limits. The detected hardware (`sound_hardware`) is
// read-only runtime state, shown from module.config rather than the form.
// Saves send the whole form like every card (set_all prunes omitted sections).

const TABS = [
  { key: "outputs", label: "Outputs" },
  { key: "library", label: "Library" },
  { key: "playback", label: "Playback" },
  { key: "basic", label: "Basic" },
  { key: "export", label: "Export" },
];

const TAB_COPY_SECTION = {
  basic: { key: "module", label: "Basic" },
  playback: { key: "sound", label: "Playback" },
  export: { key: "export", label: "Export" },
};

// A test that never answers (module busy or gone) stops showing "Playing…".
const TEST_TIMEOUT_MS = 20000;

function setIn(obj, path, value) {
  const out = structuredClone(obj ?? {});
  let p = out;
  for (let i = 0; i < path.length - 1; i++) {
    if (p[path[i]] == null || typeof p[path[i]] !== "object") p[path[i]] = {};
    p = p[path[i]];
  }
  p[path[path.length - 1]] = value;
  return out;
}

// A gain factor as dB, e.g. 0.5 -> "−6.0 dB".
const volumeDb = (v) => {
  const n = Number(v);
  return n > 0 ? `${(20 * Math.log10(n)).toFixed(1).replace("-", "−")} dB` : "silent";
};

const fmtLen = (s) => (typeof s === "number" ? `${s < 10 ? s.toFixed(1) : Math.round(s)} s` : "");

function SoundConfigCard({ id, module, clipboard, onCopy }) {
  const loggedIn = useIsLoggedIn();
  const { formData, setFormData, handleChange, markSaved, isDirty } = useConfigForm(module.config);
  const [activeTab, setActiveTab] = useHashTab("outputs", TABS.map((t) => t.key));
  const [testFile, setTestFile] = useState("");
  const [test, setTest] = useState(null);   // { output, state: "playing"|"ok"|"error", message }
  const timer = useRef(null);

  useEffect(() => {
    socket.emit("get_module_config", { module_id: module.id });
  }, [module.id]);

  // Detected hardware comes from the module, not the form.
  const hw = module.config?.sound_hardware ?? {};
  const files = hw.files ?? {};
  const fileNames = Object.keys(files).sort();
  const outputs = Number(hw.outputs) || 0;
  const sound = formData?.sound ?? {};
  const isRecording = module.status === "RECORDING";

  useEffect(() => {
    if (fileNames.length && !fileNames.includes(testFile)) setTestFile(fileNames[0]);
  }, [fileNames.join("|")]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onResult = (msg) => {
      if (msg?.module_id !== module.id) return;
      clearTimeout(timer.current);
      setTest((t) => t && {
        ...t,
        state: msg.result === "success" ? "ok" : "error",
        message: msg.result === "success" ? "" : (msg.message || String(msg.result || "failed")),
      });
    };
    socket.on("sound_test_result", onResult);
    return () => { socket.off("sound_test_result", onResult); clearTimeout(timer.current); };
  }, [module.id]);

  const set = (path, value) => setFormData((prev) => setIn(prev, path, value));

  const runTest = (output) => {
    const params = { filename: testFile, output };
    // Play with what's in the Playback fields now, so a level can be found
    // by ear before saving.
    if (Number.isFinite(Number(sound.volume)) && sound.volume !== "") params.volume = Number(sound.volume);
    if (Number(sound.duration) > 0) params.duration_s = Number(sound.duration);
    setTest({ output, state: "playing" });
    socket.emit("send_command", { module_id: id, type: "play_sound", params });
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setTest((t) => (
      t?.output === output && t.state === "playing"
        ? { output, state: "error", message: "No reply from the module" } : t)), TEST_TIMEOUT_MS);
  };

  const handlePaste = () => {
    if (!clipboard) return;
    setFormData((prev) => {
      const cloned = structuredClone(prev);
      for (const [key, value] of Object.entries(clipboard.data)) cloned[key] = structuredClone(value);
      return cloned;
    });
  };

  const testBlocked =
    !loggedIn ? "Sign in as admin to play a test sound"
    : isRecording ? "Not while recording: a test sound would land in the session"
    : !hw.present ? "No sound card detected"
    : !testFile ? "No sound files on the module"
    : null;
  const busy = test?.state === "playing";

  return (
    <ConfigCardShell
      id={id}
      module={module}
      formData={formData}
      clipboard={clipboard}
      onCopy={onCopy}
      onPaste={handlePaste}
      tabs={TABS}
      activeTab={activeTab}
      onTabChange={setActiveTab}
      tabSectionMap={TAB_COPY_SECTION}
      markSaved={markSaved}
      isDirty={isDirty}
      deviceInfoExtras={[hw.board ? `${hw.board}, ${outputs} output${outputs === 1 ? "" : "s"}` : null]}
    >
      <div className="sound-card">
        {activeTab === "outputs" && (
          <>
            {hw.present ? (
              <p className="sound-muted">
                <b>{hw.board ?? "Sound card"}</b>: {outputs} stereo output{outputs === 1 ? "" : "s"}
                {hw.channels ? ` (${hw.channels} channels)` : ""}.
                {hw.presence_verified === false && (
                  <span className="sound-help">
                    The driver can't confirm the board is fitted (the card appears even without it), so a silent test can mean no board.
                  </span>
                )}
              </p>
            ) : (
              <p className="sound-error">{hw.detail || "No sound card detected on this module."}</p>
            )}

            <div className="sound-fblock">
              <label htmlFor={`sound-test-file-${id}`}>Test sound</label>
              <select id={`sound-test-file-${id}`} value={testFile} onChange={(e) => setTestFile(e.target.value)}
                disabled={!fileNames.length}>
                {fileNames.length === 0 && <option value="">No sound files</option>}
                {fileNames.map((f) => <option key={f} value={f}>{f} ({fmtLen(files[f])})</option>)}
              </select>
              <span className="sound-help">
                Plays at volume {sound.volume ?? 1}, cut to {sound.duration ?? 1} s (Playback tab; unsaved values are used).
              </span>
            </div>

            <table className="sound-outputs">
              <thead><tr><th>Output</th><th>Channels</th><th /><th /></tr></thead>
              <tbody>
                {Array.from({ length: outputs }, (_, i) => i + 1).map((n) => {
                  const mine = test?.output === n ? test : null;
                  return (
                    <tr key={n}>
                      <td>Output {n}</td>
                      <td className="sound-muted">{2 * n - 1}-{2 * n}</td>
                      <td>
                        <button type="button"
                          className={`sound-test-btn${mine?.state === "playing" ? " sound-test-btn--active" : ""}`}
                          disabled={!!testBlocked || busy}
                          title={testBlocked ?? `Play ${testFile} on output ${n}`}
                          onClick={() => runTest(n)}>
                          {mine?.state === "playing" ? "Playing…" : "Test"}
                        </button>
                      </td>
                      <td>
                        {mine?.state === "ok" && <span className="sound-ok">Played</span>}
                        {mine?.state === "error" && <span className="sound-error">{mine.message}</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {testBlocked && hw.present && <p className="sound-muted">{testBlocked}.</p>}
          </>
        )}

        {activeTab === "library" && <LibraryTab moduleFiles={files} />}

        {activeTab === "playback" && (
          <>
            <div className="sound-fblock">
              <label htmlFor={`sound-volume-${id}`}>Volume</label>
              <div className="sound-slider">
                <input id={`sound-volume-${id}`} type="range" min="0" max="1" step="0.05"
                  value={Math.min(1, Math.max(0, Number(sound.volume ?? 1)))}
                  onChange={(e) => set(["sound", "volume"], Number(e.target.value))} />
                <output htmlFor={`sound-volume-${id}`} className="sound-slider__value">
                  {Number(sound.volume ?? 1).toFixed(2)}
                  <span className="sound-muted"> ({volumeDb(sound.volume ?? 1)})</span>
                </output>
              </div>
              <span className="sound-help">Gain on the file: 1 plays it as stored, 0.5 is half the amplitude (−6 dB). A protocol step can set its own.</span>
              {Number(sound.volume) > 1 && <span className="sound-warn">Saved value {sound.volume} is above 1 (it may clip); moving the slider sets it back in range.</span>}
            </div>
            <div className="sound-fblock">
              <label>Length (s)</label>
              <input type="number" min="0.01" step="0.1" value={sound.duration ?? ""}
                onChange={(e) => set(["sound", "duration"], e.target.value === "" ? "" : Number(e.target.value))} />
              <span className="sound-help">Sounds longer than this are cut (with a short fade). A protocol step can set its own.</span>
            </div>
            <div className="config-section-divider" />
            <h4 className="sound-subhead">Limits</h4>
            <p className="sound-muted">The module refuses a sound (test, protocol or manual) that would go over these. 0 = no limit. Levels are digital (dBFS) until the outputs are calibrated in dB SPL.</p>
            <div className="sound-fblock">
              <label>Max peak level (dBFS)</label>
              <input type="number" max="0" step="1" value={sound.max_peak_dbfs ?? 0}
                onChange={(e) => set(["sound", "max_peak_dbfs"], e.target.value === "" ? 0 : Math.min(0, Number(e.target.value)))} />
              <span className="sound-help">The file's peak plus the volume, e.g. a -6 dBFS file at volume 0.5 peaks at -12 dBFS.</span>
            </div>
            <div className="sound-fblock">
              <label>Max length (s)</label>
              <input type="number" min="0" step="1" value={sound.max_length_s ?? 0}
                onChange={(e) => set(["sound", "max_length_s"], e.target.value === "" ? 0 : Math.max(0, Number(e.target.value)))} />
              <span className="sound-help">Longest a single sound may play (after any cut).</span>
            </div>
            <details className="sound-advanced">
              <summary>Calibration</summary>
              <div className="sound-fblock">
                <label>Output latency offset (ms)</label>
                <input type="number" step="0.1" value={sound.output_latency_offset_ms ?? 0}
                  onChange={(e) => set(["sound", "output_latency_offset_ms"], e.target.value === "" ? 0 : Number(e.target.value))} />
                <span className="sound-help">Added to the card's reported latency when timing protocol sounds. Leave at 0 until measured.</span>
              </div>
            </details>
          </>
        )}

        {activeTab === "basic" && (
          <>
            <div className="form-field">
              <label>Name:</label>
              <input type="text" value={formData?.module?.name ?? ""}
                onChange={(e) => handleChange(["module", "name"], e)} />
            </div>
            <div className="form-field">
              <label>Group:</label>
              <input type="text" value={formData?.module?.group ?? ""}
                onChange={(e) => handleChange(["module", "group"], e)} />
            </div>
          </>
        )}

        {activeTab === "export" && (
          <ExportConfigSection
            exportConfig={formData?.export}
            handleChange={handleChange}
            moduleId={module.id}
          />
        )}
      </div>
    </ConfigCardShell>
  );
}

export default SoundConfigCard;
