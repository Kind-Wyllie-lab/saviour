import { useEffect, useRef, useState } from "react";
import useIsLoggedIn from "/src/hooks/useIsLoggedIn";
import useSoundLibrary from "./useSoundLibrary";
import SoundView from "./SoundView";
import NewSoundModal from "./NewSoundModal";
import { TYPES, levelKind } from "./soundTypes";

// The controller's sound library, from a sound module's card: every file
// (built-ins + uploads) with its analysis, upload (analysed before it's
// added) and delete. `moduleFiles` is what this module has (sound_hardware
// .files), so a file still being copied to it shows as such.

const fmtLen = (s) => (s == null ? "–" : s < 1 ? `${Math.round(s * 1000)} ms` : `${s.toFixed(s < 10 ? 1 : 0)} s`);
const fmtRate = (hz) => (hz ? `${hz % 1000 ? (hz / 1000).toFixed(1) : hz / 1000} kHz` : "–");
const fmtDb = (db) => (db == null ? "–" : db.toFixed(1));

// The controller serves library files (and built-ins) inline for the browser.
const playUrl = (name) => `/sound_library/${encodeURIComponent(name)}?play=1`;

// Type and main frequency/colour, for the table.
function recipeSummary(g) {
  if (!g) return "–";
  const kind = TYPES.find((t) => t.key === g.type)?.label ?? g.type.replace("_", " ");
  const hz = (v) => (v >= 1000 ? `${+(v / 1000).toFixed(2)} kHz` : `${v} Hz`);
  switch (g.type) {
    case "tone": return `${kind}, ${hz(g.freq_hz)}`;
    case "noise": case "gap": return `${g.colour} ${g.type === "gap" ? "gap noise" : "noise"}, ${hz(g.low_hz)}-${hz(g.high_hz)}`;
    case "click": return `${kind}, ${g.click_us} µs`;
    case "am_tone": return `${kind}, ${hz(g.carrier_hz)} at ${g.mod_hz} Hz`;
    case "fm_sweep": return `${kind}, ${hz(g.start_hz)} → ${hz(g.end_hz)}`;
    default: return kind;
  }
}

// One line saying how a generated sound was made.
function describe(g) {
  const kind = TYPES.find((t) => t.key === g.type)?.label ?? g.type;
  const hz = (v) => (v >= 1000 ? `${+(v / 1000).toFixed(2)} kHz` : `${v} Hz`);
  const bits = [kind];
  if (g.type === "tone") bits.push(hz(g.freq_hz));
  if (g.type === "noise" || g.type === "gap") {
    bits.push(`${g.colour}, ${hz(g.low_hz)}-${hz(g.high_hz)}`);
    if (g.notch_low_hz != null) bits.push(`notch ${hz(g.notch_low_hz)}-${hz(g.notch_high_hz)}`);
    if (g.type === "gap") bits.push(`${g.gap_ms} ms gap at ${g.gap_start_s} s`);
    bits.push(`seed ${g.seed}`);
  }
  if (g.type === "click") bits.push(`${g.click_us} µs, ${g.polarity}`);
  if (g.type === "click_train") bits.push(`click train ${g.rate_hz}/s, ${g.click_us} µs, ${g.polarity}`);
  if (g.type === "am_tone") bits.push(`${hz(g.carrier_hz)} at ${g.mod_hz} Hz, depth ${g.depth}`);
  if (g.type === "fm_sweep") bits.push(`${hz(g.start_hz)} → ${hz(g.end_hz)}, ${g.sweep}`);
  bits.push(`${g.level_dbfs} dBFS ${levelKind(g)}`,
    `${g.ramp_ms} ms ramps`, `generator v${g.generator_version}`);
  return bits.join(" · ");
}

export default function LibraryTab({ moduleFiles }) {
  const loggedIn = useIsLoggedIn();
  const lib = useSoundLibrary();
  const [selected, setSelected] = useState(null);
  const [confirmDelete, setConfirmDelete] = useState(null);
  const [making, setMaking] = useState(null);       // null | { initial }
  const fileInput = useRef(null);
  const { open } = lib;

  useEffect(() => { open(selected); }, [selected, open]);
  useEffect(() => {
    if (selected && lib.items && !lib.items.some((e) => e.name === selected)) setSelected(null);
  }, [lib.items, selected]);

  // While the New sound window is open, the upload/generate state is its own.
  const up = making ? null : lib.upload;
  const onModule = (name) => Object.prototype.hasOwnProperty.call(moduleFiles ?? {}, name);

  // One sound plays in the browser at a time (row ▶ / ■).
  const [playing, setPlaying] = useState(null);
  const player = useRef(null);
  useEffect(() => () => player.current?.pause(), []);
  const togglePlay = (name) => {
    player.current?.pause();
    if (playing === name) { setPlaying(null); return; }
    const a = new Audio(playUrl(name));
    a.onended = () => setPlaying((p) => (p === name ? null : p));
    a.onerror = () => setPlaying((p) => (p === name ? null : p));
    player.current = a;
    a.play().catch(() => setPlaying(null));
    setPlaying(name);
  };
  const playButton = (name) => (
    <button type="button" className="sound-play-btn" aria-label={`${playing === name ? "Stop" : "Play"} ${name} here`}
      title={playing === name ? "Stop" : "Play in this browser"} onClick={() => togglePlay(name)}>
      {playing === name ? "■" : "▶"}
    </button>
  );

  const all = lib.items ?? [];
  const recordings = all.filter((e) => e.kind !== "recipe");
  const recipes = all.filter((e) => e.kind === "recipe");

  const checks = (a) => {
    const warn = a.warnings?.length ?? 0;
    return a.error ? <span className="sound-error">unreadable</span>
      : warn ? <span className="sound-warn" title={a.warnings.join("\n")}>{warn} warning{warn > 1 ? "s" : ""}</span>
      : <span className="sound-ok">OK</span>;
  };

  const renderTable = (items, kind, empty) => {
    if (lib.items == null) return <p className="sound-muted">Loading library…</p>;
    if (!items.length) return <p className="sound-muted">{empty}</p>;
    return (
      <table className="sound-outputs sound-library__table">
        <thead>
          {kind === "recipe"
            ? <tr><th /><th>Sound</th><th>Recipe</th><th>Level</th><th>Checks</th><th>This module</th><th /></tr>
            : <tr><th /><th>Sound</th><th>Rate</th><th>Length</th><th>Peak dBFS</th><th>RMS dBFS</th><th>Checks</th><th>This module</th><th /></tr>}
        </thead>
        <tbody>
          {items.map((e) => {
            const a = e.analysis ?? {};
            const g = e.generator;
            return (
              <tr key={e.name} className={selected === e.name ? "sound-library__row--selected" : ""}
                onClick={() => setSelected(selected === e.name ? null : e.name)}>
                <td onClick={(ev) => ev.stopPropagation()}>{playButton(e.name)}</td>
                <td>
                  <button type="button" className="sound-link" aria-expanded={selected === e.name}>{e.name}</button>
                  {e.builtin && <span className="sound-tag">built-in</span>}
                </td>
                {kind === "recipe" ? (
                  <>
                    <td className="sound-muted">{recipeSummary(g)}</td>
                    <td>{g ? `${g.level_dbfs} dBFS ${levelKind(g)}` : "–"}</td>
                  </>
                ) : (
                  <>
                    <td>{fmtRate(a.sample_rate)}</td>
                    <td>{fmtLen(a.duration_s)}</td>
                    <td>{fmtDb(a.peak_dbfs)}</td>
                    <td>{fmtDb(a.rms_dbfs)}</td>
                  </>
                )}
                <td>{checks(a)}</td>
                <td>{onModule(e.name) ? <span className="sound-ok">✓</span> : <span className="sound-muted">copying…</span>}</td>
                <td onClick={(ev) => ev.stopPropagation()}>
                  {!e.builtin && (
                    <button type="button" className="sound-link" disabled={!loggedIn}
                      title={loggedIn ? `Delete ${e.name}` : "Sign in as admin to delete"}
                      onClick={() => setConfirmDelete(e.name)}>Delete</button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    );
  };

  return (
    <div className="sound-library">
      <input ref={fileInput} type="file" accept=".wav,audio/wav,audio/x-wav" hidden
        onChange={(e) => { lib.start(e.target.files?.[0]); e.target.value = ""; }} />
      <p className="sound-muted">Shared by every sound module on this rig. A protocol plays these and repeats them (e.g. a sound for 1 s every 10 s).</p>

      {lib.error && !up?.error && (
        <p className="sound-error">{lib.error} <button type="button" className="sound-link" onClick={lib.clearError}>Dismiss</button></p>
      )}

      <section className="sound-library__section">
        <div className="sound-row sound-library__head">
          <h4>Recordings</h4>
          <span className="sound-muted">Uploaded WAV files: USVs, predator sounds, anything recorded. Played as they are.</span>
          <button type="button" className="sound-test-btn" disabled={!loggedIn || up?.stage === "uploading"}
            title={loggedIn ? "Upload a WAV file (PCM or float); it's analysed before you add it" : "Sign in as admin to upload"}
            onClick={() => fileInput.current?.click()}>
            Upload recording…
          </button>
        </div>

        {up?.stage === "uploading" && (
          <p className="sound-muted">Uploading {up.filename}: {up.received ?? 0} / {up.total} chunks…</p>
        )}
        {up?.error && (
          <p className="sound-error">{up.error} <button type="button" className="sound-link" onClick={() => lib.discard()}>Dismiss</button></p>
        )}
        {up?.stage === "analysed" && !up.generator && (
          <div className="sound-upload-review">
            <h4>{up.name}{up.replaces ? " (replaces the library file of this name)" : ""}</h4>
            <SoundView entry={{ analysis: up.analysis, sha256: up.analysis?.sha256 }} />
            <div className="sound-row">
              <button type="button" className="sound-test-btn" onClick={() => { lib.add(up.name); setSelected(up.name); }}>
                {up.replaces ? "Replace in library" : "Add to library"}
              </button>
              <button type="button" className="sound-test-btn" onClick={() => lib.discard(up.name)}>Cancel</button>
            </div>
          </div>
        )}

        {renderTable(recordings, "recording", "No recordings yet.")}
      </section>

      <section className="sound-library__section">
        <div className="sound-row sound-library__head">
          <h4>Built sounds</h4>
          <span className="sound-muted">Tones, noise, clicks, AM tones and sweeps made in the sound builder, kept with their recipe.</span>
          <button type="button" className="sound-test-btn" disabled={!loggedIn}
            title={loggedIn ? "Build a tone, noise, click, AM tone or sweep" : "Sign in as admin to build sounds"}
            onClick={() => { lib.discard(); setMaking({ initial: null }); }}>
            Build sound…
          </button>
        </div>
        {renderTable(recipes, "recipe", "No built sounds yet.")}
      </section>

      {selected && (
        lib.detail?.name === selected
          ? (
            <div className="sound-library__detail">
              <h4>{selected}</h4>
              <div className="sound-row">
                <audio controls preload="none" src={playUrl(selected)} className="sound-new-audio" />
                <span className="sound-muted">
                  Plays through your computer: for checking what it is, not the level.
                  {lib.detail.analysis?.sample_rate > 48000 && " Anything above ~20 kHz (USVs) is inaudible here; the spectrogram shows it."}
                </span>
              </div>
              {lib.detail.generator && (
                <div className="sound-row sound-gen-summary">
                  <span className="sound-muted">{describe(lib.detail.generator)}</span>
                  <button type="button" className="sound-link" disabled={!loggedIn}
                    onClick={() => { lib.discard(); setMaking({ initial: lib.detail.generator }); }}>
                    Edit a copy
                  </button>
                </div>
              )}
              <SoundView entry={lib.detail} />
            </div>
          )
          : <p className="sound-muted">Loading {selected}…</p>
      )}

      {making && (
        <NewSoundModal lib={lib} initial={making.initial}
          onClose={(added) => { setMaking(null); if (added) setSelected(added); }} />
      )}

      {confirmDelete && (
        <div className="modal-overlay" onClick={() => setConfirmDelete(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <p>Delete <strong>{confirmDelete}</strong> from the sound library?</p>
            <p className="modal-subtext">It's removed from every sound module. Refused while a saved protocol plays it.</p>
            <div className="modal-buttons">
              <button className="reset-button" type="button" onClick={() => { lib.remove(confirmDelete); setConfirmDelete(null); }}>Delete</button>
              <button className="save-button" type="button" onClick={() => setConfirmDelete(null)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
