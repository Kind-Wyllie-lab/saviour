import { useEffect, useRef, useState } from "react";
import useIsLoggedIn from "/src/hooks/useIsLoggedIn";
import useSoundLibrary from "./useSoundLibrary";
import SoundView from "./SoundView";

// The controller's sound library, from a sound module's card: every file
// (built-ins + uploads) with its analysis, upload (analysed before it's
// added) and delete. `moduleFiles` is what this module has (sound_hardware
// .files), so a file still being copied to it shows as such.

const fmtLen = (s) => (s == null ? "–" : s < 1 ? `${Math.round(s * 1000)} ms` : `${s.toFixed(s < 10 ? 1 : 0)} s`);
const fmtRate = (hz) => (hz ? `${hz % 1000 ? (hz / 1000).toFixed(1) : hz / 1000} kHz` : "–");
const fmtDb = (db) => (db == null ? "–" : db.toFixed(1));

export default function LibraryTab({ moduleFiles }) {
  const loggedIn = useIsLoggedIn();
  const lib = useSoundLibrary();
  const [selected, setSelected] = useState(null);
  const [confirmDelete, setConfirmDelete] = useState(null);
  const fileInput = useRef(null);
  const { open } = lib;

  useEffect(() => { open(selected); }, [selected, open]);
  useEffect(() => {
    if (selected && lib.items && !lib.items.some((e) => e.name === selected)) setSelected(null);
  }, [lib.items, selected]);

  const up = lib.upload;
  const onModule = (name) => Object.prototype.hasOwnProperty.call(moduleFiles ?? {}, name);

  return (
    <div className="sound-library">
      <div className="sound-row">
        <input ref={fileInput} type="file" accept=".wav,audio/wav,audio/x-wav" hidden
          onChange={(e) => { lib.start(e.target.files?.[0]); e.target.value = ""; }} />
        <button type="button" className="sound-test-btn" disabled={!loggedIn || up?.stage === "uploading"}
          title={loggedIn ? "Upload a WAV file; it's analysed before you add it" : "Sign in as admin to upload"}
          onClick={() => fileInput.current?.click()}>
          Upload sound…
        </button>
        <span className="sound-muted">WAV (PCM or float). Shared by every sound module on this rig.</span>
      </div>

      {up?.stage === "uploading" && (
        <p className="sound-muted">Uploading {up.filename}: {up.received ?? 0} / {up.total} chunks…</p>
      )}
      {up?.error && (
        <p className="sound-error">{up.error} <button type="button" className="sound-link" onClick={() => lib.discard()}>Dismiss</button></p>
      )}
      {up?.stage === "analysed" && (
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

      {lib.error && !up?.error && (
        <p className="sound-error">{lib.error} <button type="button" className="sound-link" onClick={lib.clearError}>Dismiss</button></p>
      )}

      {lib.items == null ? (
        <p className="sound-muted">Loading library…</p>
      ) : (
        <table className="sound-outputs sound-library__table">
          <thead>
            <tr><th>Sound</th><th>Rate</th><th>Length</th><th>Peak dBFS</th><th>RMS dBFS</th><th>Checks</th><th>This module</th><th /></tr>
          </thead>
          <tbody>
            {lib.items.map((e) => {
              const a = e.analysis ?? {};
              const warn = a.warnings?.length ?? 0;
              return (
                <tr key={e.name} className={selected === e.name ? "sound-library__row--selected" : ""}
                  onClick={() => setSelected(selected === e.name ? null : e.name)}>
                  <td>
                    <button type="button" className="sound-link" aria-expanded={selected === e.name}>{e.name}</button>
                    {e.builtin && <span className="sound-tag">built-in</span>}
                  </td>
                  <td>{fmtRate(a.sample_rate)}</td>
                  <td>{fmtLen(a.duration_s)}</td>
                  <td>{fmtDb(a.peak_dbfs)}</td>
                  <td>{fmtDb(a.rms_dbfs)}</td>
                  <td>{a.error ? <span className="sound-error">unreadable</span>
                    : warn ? <span className="sound-warn" title={a.warnings.join("\n")}>{warn} warning{warn > 1 ? "s" : ""}</span>
                    : <span className="sound-ok">OK</span>}</td>
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
      )}

      {selected && (
        lib.detail?.name === selected
          ? <div className="sound-library__detail"><h4>{selected}</h4><SoundView entry={lib.detail} /></div>
          : <p className="sound-muted">Loading {selected}…</p>
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
