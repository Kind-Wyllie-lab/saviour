import { useState } from "react";
import SoundView from "./SoundView";
import { TYPES } from "./soundTypes";

// "New sound": pick a type, set its parameters, Preview (the controller
// renders it and returns the same analysis as an upload), then Add to
// library. Defaults and limits mirror src/shared/sound_generator.py, which
// checks everything again and explains a refusal.

const MAX_HZ = 21600;

const COLOURS = [
  { key: "white", label: "White", help: "flat: equal energy per Hz" },
  { key: "pink", label: "Pink", help: "−3 dB/octave: equal energy per octave" },
  { key: "brown", label: "Brown", help: "−6 dB/octave: mostly low frequencies" },
  { key: "blue", label: "Blue", help: "+3 dB/octave: weighted to high frequencies" },
  { key: "violet", label: "Violet", help: "+6 dB/octave: strongly high-frequency" },
];

const NOISE = { colour: "white", low_hz: 1000, high_hz: 20000, notch_low_hz: "", notch_high_hz: "", seed: "" };
const DEFAULTS = {
  tone: { duration_s: 1, level_dbfs: -20, ramp_ms: 5, freq_hz: 5000 },
  noise: { duration_s: 0.04, level_dbfs: -20, ramp_ms: 2, ...NOISE },
  gap: { duration_s: 1, level_dbfs: -20, ramp_ms: 5, ...NOISE, gap_start_s: 0.5, gap_ms: 50, gap_ramp_ms: 1 },
  click_train: { duration_s: 1, level_dbfs: -20, ramp_ms: 0, rate_hz: 10, click_us: 100, polarity: "positive" },
  am_tone: { duration_s: 1, level_dbfs: -20, ramp_ms: 5, carrier_hz: 8000, mod_hz: 40, depth: 1 },
  fm_sweep: { duration_s: 1, level_dbfs: -20, ramp_ms: 5, start_hz: 2000, end_hz: 20000, sweep: "log" },
};

const RMS_TYPES = new Set(["noise", "gap"]);

function Num({ label, k, p, set, help, ...props }) {
  return (
    <div className="sound-fblock">
      <label>{label}</label>
      <input type="number" step="any" value={p[k] ?? ""} {...props}
        onChange={(e) => set(k, e.target.value === "" ? "" : Number(e.target.value))} />
      {help && <span className="sound-help">{help}</span>}
    </div>
  );
}

function Choice({ label, k, p, set, options, help }) {
  return (
    <div className="sound-fblock">
      <label>{label}</label>
      <select value={p[k]} onChange={(e) => set(k, e.target.value)}>
        {options.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
      </select>
      {help && <span className="sound-help">{help}</span>}
    </div>
  );
}

// Parameters for the server: blanks dropped (defaults/random seed apply).
function cleaned(type, p) {
  const out = { type };
  for (const [k, v] of Object.entries(p)) if (v !== "" && v != null) out[k] = v;
  return out;
}

export default function NewSoundModal({ lib, initial, onClose }) {
  const startType = initial?.type && DEFAULTS[initial.type] ? initial.type : "tone";
  const [type, setType] = useState(startType);
  const [params, setParams] = useState(() => {
    const base = Object.fromEntries(Object.keys(DEFAULTS).map((t) => [t, { ...DEFAULTS[t] }]));
    if (initial?.type && base[initial.type]) {
      for (const k of Object.keys(base[initial.type])) {
        if (initial[k] !== undefined) base[initial.type][k] = initial[k] ?? "";
      }
    }
    return base;
  });
  const [name, setName] = useState("");
  const p = params[type];
  const set = (k, v) => setParams((all) => ({ ...all, [type]: { ...all[type], [k]: v } }));
  const up = lib.upload;
  const reviewing = up?.stage === "analysed" && up.generator;
  const isNoise = type === "noise" || type === "gap";

  const preview = () => lib.generate(cleaned(type, p), name.trim());
  const close = () => { if (reviewing) lib.discard(up.name); onClose(); };

  return (
    <div className="modal-overlay" onClick={close}>
      <div className="modal sound-new-modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="New sound">
        <h3>New sound</h3>

        {!reviewing && (
          <>
            <div className="sound-type-tiles" role="radiogroup" aria-label="Sound type">
              {TYPES.map((t) => (
                <button key={t.key} type="button" role="radio" aria-checked={type === t.key}
                  className={`sound-type-tile${type === t.key ? " sound-type-tile--active" : ""}`}
                  onClick={() => setType(t.key)}>
                  <b>{t.label}</b><span>{t.help}</span>
                </button>
              ))}
            </div>

            <div className="sound-gen-grid">
              <Num label="Length (s)" k="duration_s" p={p} set={set} min="0.001" max="300" />
              <Num label={RMS_TYPES.has(type) ? "Level, RMS (dBFS)" : "Level, peak (dBFS)"} k="level_dbfs" p={p} set={set} max="0"
                help={RMS_TYPES.has(type) ? "Noise peaks ~12 dB above its RMS; a level that would clip is refused." : null} />
              <Num label="Onset/offset ramp (ms)" k="ramp_ms" p={p} set={set} min="0"
                help={type === "click_train" ? "Usually 0 for clicks: a ramp swallows the first ones." : "Raised cosine; stops the sound clicking on and off."} />

              {type === "tone" && <Num label="Frequency (Hz)" k="freq_hz" p={p} set={set} min="20" max={MAX_HZ} />}

              {isNoise && (
                <>
                  <Choice label="Colour" k="colour" p={p} set={set} options={COLOURS}
                    help={COLOURS.find((c) => c.key === p.colour)?.help} />
                  <Num label="Low cut (Hz)" k="low_hz" p={p} set={set} min="20" max={MAX_HZ} />
                  <Num label="High cut (Hz)" k="high_hz" p={p} set={set} min="20" max={MAX_HZ}
                    help={`At most ${MAX_HZ / 1000} kHz at the 48 kHz output. Rats hear best around 8-38 kHz.`} />
                  <Num label="Notch from (Hz)" k="notch_low_hz" p={p} set={set} min="0" max={MAX_HZ} placeholder="none" />
                  <Num label="Notch to (Hz)" k="notch_high_hz" p={p} set={set} min="0" max={MAX_HZ} placeholder="none" />
                  <div className="sound-fblock">
                    <label>Seed</label>
                    <div className="sound-row">
                      <input type="number" step="1" min="0" value={p.seed ?? ""} placeholder="random"
                        onChange={(e) => set("seed", e.target.value === "" ? "" : Number(e.target.value))} />
                      {p.seed !== "" && <button type="button" className="sound-link" onClick={() => set("seed", "")}>New noise</button>}
                    </div>
                    <span className="sound-help">The same seed makes the identical noise (frozen). Blank draws a new one, recorded with the sound.</span>
                  </div>
                </>
              )}

              {type === "gap" && (
                <>
                  <Num label="Gap starts at (s)" k="gap_start_s" p={p} set={set} min="0" />
                  <Num label="Gap length (ms)" k="gap_ms" p={p} set={set} min="0.1" />
                  <Num label="Gap ramps (ms)" k="gap_ramp_ms" p={p} set={set} min="0" help="Fall into and rise out of the gap." />
                </>
              )}

              {type === "click_train" && (
                <>
                  <Num label="Clicks per second" k="rate_hz" p={p} set={set} min="0.1" max="1000" />
                  <Num label="Click width (µs)" k="click_us" p={p} set={set} min="21" />
                  <Choice label="Polarity" k="polarity" p={p} set={set}
                    options={[{ key: "positive", label: "All positive" }, { key: "alternating", label: "Alternating" }]} />
                </>
              )}

              {type === "am_tone" && (
                <>
                  <Num label="Carrier (Hz)" k="carrier_hz" p={p} set={set} min="20" max={MAX_HZ} />
                  <Num label="Modulation rate (Hz)" k="mod_hz" p={p} set={set} min="0.1" max="5000" />
                  <Num label="Depth (0-1)" k="depth" p={p} set={set} min="0" max="1" help="1 = the level falls to silence each cycle." />
                </>
              )}

              {type === "fm_sweep" && (
                <>
                  <Num label="Start (Hz)" k="start_hz" p={p} set={set} min="20" max={MAX_HZ} />
                  <Num label="End (Hz)" k="end_hz" p={p} set={set} min="20" max={MAX_HZ} />
                  <Choice label="Sweep" k="sweep" p={p} set={set}
                    options={[{ key: "log", label: "Logarithmic (even per octave)" }, { key: "linear", label: "Linear (even per Hz)" }]} />
                </>
              )}
            </div>

            <div className="sound-fblock">
              <label>File name</label>
              <input type="text" value={name} placeholder="automatic (describes the sound)"
                onChange={(e) => setName(e.target.value)} />
            </div>

            {up?.error && <p className="sound-error">{up.error}</p>}
            <div className="modal-buttons">
              <button type="button" className="save-button" onClick={preview} disabled={up?.stage === "generating"}>
                {up?.stage === "generating" ? "Making…" : "Preview"}
              </button>
              <button type="button" className="reset-button" onClick={close}>Cancel</button>
            </div>
          </>
        )}

        {reviewing && (
          <>
            <h4 className="sound-gen-name">{up.name}{up.replaces ? " (replaces the library file of this name)" : ""}</h4>
            <SoundView entry={{ analysis: up.analysis, sha256: up.analysis?.sha256 }} />
            <div className="modal-buttons">
              <button type="button" className="save-button" onClick={() => { lib.add(up.name); onClose(up.name); }}>
                {up.replaces ? "Replace in library" : "Add to library"}
              </button>
              <button type="button" className="reset-button" onClick={() => {
                if (up.generator?.seed != null && isNoise) set("seed", up.generator.seed);
                lib.discard(up.name);
              }}>Back to edit</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
