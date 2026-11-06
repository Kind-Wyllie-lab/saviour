import { useEffect, useRef, useState } from "react";
import socket from "/src/socket";
import { isLoggedIn, onAuthChange } from "/src/auth";
import "./SyncTab.css";
import { calibrationStatus, khz } from "./syncCalibration";

// A/V sync for a microphone module (docs/AV_SYNC_TEST.md): each AudioMoth's
// calibration (audiomoth_sync_offsets_ms, written by the buzzer self-test),
// a button to run that self-test, and the buzzer/LED rig settings.

const SELFTEST = { pulses: 20, interval_s: 1.2 };
// Recording lead + pulses + analysis, with room to spare.
const SELFTEST_TIMEOUT_MS = (4 + SELFTEST.pulses * SELFTEST.interval_s * 1.3 + 30) * 1000;

function Pin({ label, value, onChange, disabled }) {
  return (
    <div className="form-field">
      <label>{label}:</label>
      <input type="number" min="0" max="27" placeholder="not fitted"
        value={value ?? ""} disabled={disabled}
        onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} />
    </div>
  );
}

export default function SyncTab({ module, formData, setFormData, serials, labelFor,
                                  discovered }) {
  const isRecording = module.status === "RECORDING";
  const online = module.online !== false;
  const sp = formData?.sync_pulse ?? {};
  const savedSp = module.config?.sync_pulse ?? {};
  const offsets = module.config?.audiomoth_sync_offsets_ms ?? {};
  const current = {
    sampleRate: Number(module.config?.audiomoth?.sample_rate ?? 0) || null,
    blockSize: Number(module.config?.microphone?.block_size ?? 0) || null,
  };

  const [run, setRun] = useState(null);   // {state: running|done|error, ...}
  const [loggedIn, setLoggedIn] = useState(() => isLoggedIn());
  useEffect(() => onAuthChange(() => setLoggedIn(isLoggedIn())), []);
  const timer = useRef(null);

  useEffect(() => {
    const onState = (d) => {
      if (d.module_id !== module.id) return;
      if (d.result === "error") {
        clearTimeout(timer.current);
        setRun({ state: "error", message: d.message || "The module refused the self-test." });
      }
    };
    const onResult = (d) => {
      if (d.module_id !== module.id) return;
      clearTimeout(timer.current);
      if (d.status !== "ok") {
        setRun({ state: "error", message: d.message || "The self-test failed." });
        return;
      }
      setRun({ state: "done", result: d });
      socket.emit("get_module_config", { module_id: module.id });
    };
    socket.on("sync_selftest_state", onState);
    socket.on("sync_selftest_result", onResult);
    return () => {
      socket.off("sync_selftest_state", onState);
      socket.off("sync_selftest_result", onResult);
      clearTimeout(timer.current);
    };
  }, [module.id]);

  const setSp = (key, value) => setFormData((prev) => {
    const cloned = structuredClone(prev ?? {});
    cloned.sync_pulse = { ...(cloned.sync_pulse ?? {}), [key]: value };
    return cloned;
  });

  const startSelftest = () => {
    setRun({ state: "running", startedAt: Date.now() });
    socket.emit("send_command", { module_id: module.id, type: "sync_selftest", params: SELFTEST });
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setRun({
      state: "error",
      message: "No result came back. Check the module is online and on the latest version.",
    }), SELFTEST_TIMEOUT_MS);
  };

  const noBuzzer = savedSp.buzzer_pin === null || savedSp.buzzer_pin === undefined;
  const blocked = !loggedIn ? "Sign in to run the self-test."
    : isRecording ? "Not while recording."
    : !online ? "The module is offline."
    : noBuzzer ? "Set and save a buzzer pin first."
    : null;
  const running = run?.state === "running";
  const seconds = Math.round(4 + SELFTEST.pulses * SELFTEST.interval_s * 1.15);

  return (
    <div className="sync-tab">
      <p className="sync-tab-intro">
        Each AudioMoth's recordings are lined up with video using an offset that the
        buzzer self-test measures. Calibrate each AudioMoth once with the buzzer a few cm
        away, and again after changing its sample rate, block size or firmware.
      </p>

      <div className="sync-cal-list">
        {serials.length === 0 && (
          <div className="sensor-mode-info sensor-mode-info--muted">No AudioMoths discovered.</div>
        )}
        {serials.map((serial) => {
          const st = calibrationStatus(offsets[serial], current);
          const measured = run?.state === "done"
            ? Object.values(run.result.microphones ?? {}).find((m) => m.serial === serial)
            : null;
          return (
            <div key={serial} className={`sync-cal-row sync-cal-row--${st.level}`}>
              <div className="sync-cal-head">
                <span className="sync-cal-name" title={serial}>
                  {labelFor(serial)}
                  {!discovered.includes(serial) && <span className="sync-cal-dim"> (disconnected)</span>}
                </span>
                <span className={`sync-cal-badge sync-cal-badge--${st.level}`}>{st.title}</span>
              </div>
              {st.notes.map((n) => <div key={n} className="sync-cal-note">{n}</div>)}
              {measured && (
                <div className="sync-cal-result">
                  Self-test: {measured.detected ?? 0}/{measured.n ?? 0} buzzes found,
                  error {measured.median_ms ?? "–"} ms (sd {measured.std_ms ?? "–"}).
                  {measured.calibration_skipped
                    ? ` Not stored: ${measured.calibration_skipped}.`
                    : run.result.calibrated && Object.keys(run.result.calibrated).length
                      ? " Offset stored." : ""}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="sync-run">
        <button type="button" className="save-button"
          disabled={running || !!blocked} title={blocked ?? ""} onClick={startSelftest}>
          {running ? "Running self-test…" : "Run self-test"}
        </button>
        <span className="sync-run-note">
          {blocked ?? (running
            ? `About ${seconds} s. Keep the room quiet.`
            : `${SELFTEST.pulses} buzzes over about ${seconds} s, using the saved settings below.`)}
        </span>
      </div>
      {run?.state === "error" && <div className="sync-run-error">{run.message}</div>}
      {Number(savedSp.tone_hz) > 16000 && (
        <div className="sensor-mode-info sensor-mode-info--muted">
          The {khz(savedSp.tone_hz)} buzz is silent to people but loud to rats and mice:
          keep animals away while it runs.
        </div>
      )}

      <div className="config-section-divider" />
      <h4 className="sync-tab-h">Buzzer and LED</h4>
      <Pin label="Buzzer GPIO" value={sp.buzzer_pin} onChange={(v) => setSp("buzzer_pin", v)} />
      <Pin label="LED GPIO" value={sp.led_pin} onChange={(v) => setSp("led_pin", v)} />
      <div className="form-field">
        <label>Buzzer type:</label>
        <select value={sp.drive ?? "dc"} onChange={(e) => setSp("drive", e.target.value)}>
          <option value="pwm">Passive piezo (driven with a tone)</option>
          <option value="dc">Active buzzer (own oscillator)</option>
        </select>
      </div>
      {(sp.drive ?? "dc") === "pwm" && (
        <div className="form-field">
          <label>Tone (Hz):</label>
          <input type="number" min="100" max="90000" step="100" value={sp.tone_hz ?? 4000}
            onChange={(e) => setSp("tone_hz", Number(e.target.value))} />
        </div>
      )}
      {(sp.drive ?? "dc") === "pwm" && Number(sp.tone_hz) > 10000 && (
        <div className="sensor-mode-info sensor-mode-info--muted">
          Above 10 kHz the buzzer pin needs dtoverlay=pwm-pio,gpio={sp.buzzer_pin ?? "N"} in
          the Pi's /boot/firmware/config.txt (docs/AV_SYNC_TEST.md).
        </div>
      )}
      <div className="form-field">
        <label>Pulse length (ms):</label>
        <input type="number" min="1" max="500" value={sp.pulse_ms ?? 50}
          onChange={(e) => setSp("pulse_ms", Number(e.target.value))} />
      </div>
      <div className="form-field">
        <label>Buzzer to AudioMoth (m):</label>
        <input type="number" min="0" step="0.01" value={sp.mic_distance_m ?? 0}
          onChange={(e) => setSp("mic_distance_m", Number(e.target.value))} />
      </div>
    </div>
  );
}
