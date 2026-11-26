import { useEffect, useMemo, useRef, useState } from "react";
import socket from "/src/socket";
import SoundView from "./SoundView";
import { TYPES } from "./soundTypes";

// Sound builder: pick a type and set its parameters; the controller renders a
// preview after each change (waveform, spectrogram, levels, and audio to
// play here) and Add to library renders the full sound and adds it. The
// library holds single sounds: repeating them is the protocol's job.
// Defaults and limits mirror src/shared/sound_generator.py, which checks
// everything again and explains a refusal.

const MAX_HZ = 21600;
const PREVIEW_DEBOUNCE_MS = 300;

const COLOURS = [
  { key: "white", label: "White", help: "flat: equal energy per Hz" },
  { key: "pink", label: "Pink", help: "−3 dB/octave: equal energy per octave" },
  { key: "brown", label: "Brown", help: "−6 dB/octave: mostly low frequencies" },
  { key: "blue", label: "Blue", help: "+3 dB/octave: weighted to high frequencies" },
  { key: "violet", label: "Violet", help: "+6 dB/octave: strongly high-frequency" },
];

const NOISE = { colour: "white", low_hz: 1000, high_hz: 20000, notch_low_hz: "", notch_high_hz: "", seed: "" };
const COMMON = { duration_s: 1, level_dbfs: -20, ramp_ms: 5 };
const DEFAULTS = {
  tone: { ...COMMON, freq_hz: 5000 },
  noise: { ...COMMON, ...NOISE },
  click: { level_dbfs: -20, click_us: 100, polarity: "positive" },
  am_tone: { ...COMMON, carrier_hz: 8000, mod_hz: 40, depth: 1 },
  fm_sweep: { ...COMMON, start_hz: 2000, end_hz: 20000, sweep: "log" },
};

const fmtDb = (v) => (v == null ? "–" : `${v.toFixed(1)}`);

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
  const [preview, setPreview] = useState(null);   // {params, analysis, levels, preview_s, name} | {error}
  const [loading, setLoading] = useState(false);
  const [adding, setAdding] = useState(null);     // name being added
  const [addError, setAddError] = useState(null);
  const [audioUrl, setAudioUrl] = useState(null);
  const seq = useRef(0);
  const p = params[type];
  const set = (k, v) => setParams((all) => ({ ...all, [type]: { ...all[type], [k]: v } }));
  const isNoise = type === "noise";
  const request = useMemo(() => cleaned(type, p), [type, p]);
  const requestKey = JSON.stringify(request);

  // Preview after each change (debounced); stale replies are ignored.
  useEffect(() => {
    const t = setTimeout(() => {
      seq.current += 1;
      setLoading(true);
      socket.emit("preview_sound", { seq: seq.current, params: request });
    }, PREVIEW_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [requestKey]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onPreview = (d) => {
      if (d?.seq !== seq.current) return;
      setLoading(false);
      setPreview(d);
      // Keep the noise the seed made while the other fields change.
      if (!d.error && d.params?.seed != null) {
        setParams((all) => {
          const cur = all[d.params.type];
          return cur && cur.seed === "" ? { ...all, [d.params.type]: { ...cur, seed: d.params.seed } } : all;
        });
      }
    };
    const onList = (d) => {
      if (adding && d?.added === adding) onClose(adding);
    };
    const onError = (d) => { if (adding) { setAdding(null); setAddError(d?.error ?? "Couldn't add the sound"); } };
    socket.on("sound_preview", onPreview);
    socket.on("sound_library", onList);
    socket.on("sound_library_error", onError);
    return () => {
      socket.off("sound_preview", onPreview);
      socket.off("sound_library", onList);
      socket.off("sound_library_error", onError);
    };
  }, [adding, onClose]);

  // Audio for the play button.
  useEffect(() => {
    if (!preview?.wav) { setAudioUrl(null); return undefined; }
    const url = URL.createObjectURL(new Blob([preview.wav], { type: "audio/wav" }));
    setAudioUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [preview]);

  const finalName = (name.trim() || preview?.name || "").replace(/(\.wav)?$/i, ".wav");
  const exists = finalName && (lib.items ?? []).some((e) => e.name === finalName);
  const ok = preview && !preview.error;
  const lv = ok ? preview.levels : null;
  const truncated = ok && preview.params.duration_s > preview.preview_s + 1e-9;

  const add = () => {
    setAddError(null);
    setAdding(finalName);
    // The seed the preview used, so the library gets the noise you heard.
    socket.emit("generate_sound", { params: { ...request, ...(isNoise && preview?.params ? { seed: preview.params.seed } : {}) },
      name: finalName, add: true });
  };

  return (
    <div className="modal-overlay" onClick={() => onClose()}>
      <div className="modal sound-new-modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Sound builder">
        <h3>Sound builder</h3>

        <div className="sound-type-tiles" role="radiogroup" aria-label="Sound type">
          {TYPES.map((t) => (
            <button key={t.key} type="button" role="radio" aria-checked={type === t.key}
              className={`sound-type-tile${type === t.key ? " sound-type-tile--active" : ""}`}
              onClick={() => setType(t.key)}>
              <b>{t.label}</b><span>{t.help}</span>
            </button>
          ))}
        </div>

        <div className="sound-new-body">
          <div className="sound-new-form">
            <div className="sound-gen-grid">
              {type !== "click" && <Num label="Length (s)" k="duration_s" p={p} set={set} min="0.001" max="300" />}
              <Num label={type === "click" ? "Level, peak (dBFS)" : "Level, RMS (dBFS)"} k="level_dbfs" p={p} set={set} max="0"
                help={type === "click" ? "A click is described by its peak." : "Measured over the plateau, without the ramps."} />
              {type !== "click" && (
                <Num label="Onset/offset ramp (ms)" k="ramp_ms" p={p} set={set} min="0" help="Raised cosine; stops the sound clicking on and off." />
              )}

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
                      <button type="button" className="sound-link" onClick={() => set("seed", "")}>New noise</button>
                    </div>
                    <span className="sound-help">The same seed makes the identical noise. Stored with the sound.</span>
                  </div>
                </>
              )}

              {type === "click" && (
                <>
                  <Num label="Width (µs)" k="click_us" p={p} set={set} min="21" help="Rounded to whole samples (20.8 µs each at 48 kHz)." />
                  <Choice label="Polarity" k="polarity" p={p} set={set}
                    options={[{ key: "positive", label: "Positive (condensation)" }, { key: "negative", label: "Negative (rarefaction)" }]} />
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
              <input type="text" value={name} placeholder={preview?.name ?? "automatic (describes the sound)"}
                onChange={(e) => setName(e.target.value)} />
              {exists && <span className="sound-warn">Replaces the library file of this name.</span>}
            </div>
          </div>

          <div className="sound-new-preview" aria-live="polite">
            <div className="sound-row sound-new-preview__head">
              <b>Preview</b>
              {loading && <span className="sound-muted">updating…</span>}
              {audioUrl && <audio controls src={audioUrl} className="sound-new-audio" />}
            </div>
            {preview?.error && <p className="sound-error">{preview.error}</p>}
            {ok && (
              <>
                <p className="sound-levels">
                  Plateau RMS <b>{fmtDb(lv.plateau_rms_dbfs)} dBFS</b> · peak {fmtDb(lv.peak_dbfs)} dBFS · headroom {fmtDb(lv.headroom_db)} dB
                </p>
                {truncated && <p className="sound-muted">Showing the first {preview.preview_s} s of {preview.params.duration_s} s; Add renders it all.</p>}
                {preview.listen_note && <p className="sound-muted">{preview.listen_note}</p>}
                <p className="sound-muted">Played here through your computer: for the shape and pitch, not the level. Use an output's Test button to hear it on the rig.</p>
                <SoundView entry={{ analysis: preview.analysis }} />
              </>
            )}
          </div>
        </div>

        {addError && <p className="sound-error">{addError}</p>}
        <div className="modal-buttons">
          <button type="button" className="save-button" onClick={add} disabled={!ok || !!adding || loading}>
            {adding ? "Adding…" : exists ? "Replace in library" : "Add to library"}
          </button>
          <button type="button" className="reset-button" onClick={() => onClose()}>Cancel</button>
        </div>
      </div>
    </div>
  );
}
