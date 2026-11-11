import { useEffect, useRef, useState } from "react";
import "./FontImportModal.css";
import {
  cleanFontName, facesFromFiles, fetchGoogleFonts, googleFontsUrl, uploadFont,
} from "/src/basic/utils/fonts";
import { formatBytes } from "/src/basic/pages/Recording/sessionFormat";

const SAMPLE = "Session 1TS-1627 · camera_d074 · 30 fps · PTP offset 12 µs";

let previewSeq = 0;

// Registers each candidate's faces with the browser under a private family
// name so the modal can show them before anything is uploaded.
function usePreviewFaces(candidates) {
  useEffect(() => {
    const added = [];
    for (const c of candidates) {
      for (const face of c.faces) {
        try {
          const ff = new FontFace(c.previewFamily, face.data, {
            weight: face.weight, style: face.style,
            ...(face.unicodeRange ? { unicodeRange: face.unicodeRange } : {}),
          });
          document.fonts.add(ff);
          ff.load().catch(() => {});
          added.push(ff);
        } catch {
          // An unreadable face just doesn't preview; the controller checks it on upload.
        }
      }
    }
    return () => added.forEach((ff) => document.fonts.delete(ff));
  }, [candidates]);
}

const nameKey = (name) => cleanFontName(name).toLowerCase();

function makeCandidate(family, faces, source, taken) {
  previewSeq += 1;
  const name = cleanFontName(family);
  const already = taken.has(nameKey(name));
  return {
    key: `${family}-${previewSeq}`,
    name,
    already,
    // Google's embed code lists every family in the operator's selection on
    // fonts.google.com, so families added before start unticked.
    include: !already,
    faces,
    source,
    previewFamily: `saviour-preview-${previewSeq}`,
  };
}

function describeFaces(faces) {
  const weights = [...new Set(faces.map((f) => f.weight))];
  const italic = faces.some((f) => f.style !== "normal");
  const w = weights.length === 1 && weights[0].includes(" ")
    ? `variable ${weights[0].replace(" ", "-")}`
    : `weight ${weights.sort((a, b) => parseInt(a, 10) - parseInt(b, 10)).join(", ")}`;
  return `${w}${italic ? ", with italics" : ""}`;
}

/**
 * Add a font to the controller: from a Google Fonts link, fetched by this
 * browser, or from font files dropped in. Either way the files are uploaded
 * to the controller, which serves them to every browser on the rig.
 *
 * `existingFonts` (frontend.custom_fonts) lets the modal skip families that
 * are already on the controller; the controller refuses a duplicate name too.
 * `onSaved(font)` is called with the first stored font, so the caller can
 * select it.
 */
export default function FontImportModal({ existingFonts = [], onClose, onSaved }) {
  const [link, setLink] = useState("");
  const [candidates, setCandidates] = useState([]);
  const [status, setStatus] = useState("idle"); // idle | fetching | saving | error
  const [error, setError] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const fileInput = useRef(null);

  usePreviewFaces(candidates);

  const taken = new Set(existingFonts.map((f) => nameKey(f.name || "")));

  const fail = (e) => { setStatus("error"); setError(e.message || String(e)); };

  const fetchLink = async () => {
    setError("");
    let url;
    try { url = googleFontsUrl(link); } catch (e) { fail(e); return; }
    setStatus("fetching");
    try {
      const families = await fetchGoogleFonts(url);
      setCandidates(families.map((f) => makeCandidate(f.family, f.faces, { kind: "google", url }, taken)));
      setStatus("idle");
    } catch (e) { fail(e); }
  };

  const takeFiles = async (files) => {
    setError("");
    setStatus("fetching");
    try {
      const { family, faces } = await facesFromFiles(files);
      setCandidates([makeCandidate(family, faces, { kind: "files" }, taken)]);
      setStatus("idle");
    } catch (e) { fail(e); }
  };

  const update = (key, patch) =>
    setCandidates((cs) => cs.map((c) => (c.key === key ? { ...c, ...patch } : c)));

  const chosen = candidates.filter((c) => c.include);
  const clashes = (c) => taken.has(nameKey(c.name))
    || chosen.some((o) => o !== c && nameKey(o.name) === nameKey(c.name));
  const clashing = chosen.filter((c) => cleanFontName(c.name) && clashes(c));
  const allAdded = candidates.length > 0 && candidates.every((c) => c.already);
  const canSave = chosen.length > 0 && chosen.every((c) => cleanFontName(c.name))
    && clashing.length === 0 && status !== "saving" && status !== "fetching";

  const save = async () => {
    if (!canSave) return;
    setStatus("saving");
    setError("");
    try {
      let first = null;
      for (const c of chosen) {
        const font = await uploadFont({ name: cleanFontName(c.name), source: c.source, faces: c.faces });
        first ||= font;
      }
      onSaved?.(first);
      onClose();
    } catch (e) { fail(e); }
  };

  return (
    <div className="modal-overlay font-import-overlay" onClick={onClose}>
      <div className="modal font-import-modal" onClick={(e) => e.stopPropagation()}>
        <h2>Add a font</h2>
        <p className="modal-subtext">
          This browser downloads the font and sends it to the controller, which
          serves it to every browser on the rig. The controller doesn't need
          internet access, and neither do the other browsers.
        </p>

        <label className="font-import-modal__label" htmlFor="font-link">Google Fonts page or link</label>
        <div className="font-import-modal__row">
          <input
            id="font-link"
            className="font-import-modal__input"
            type="text"
            autoFocus
            placeholder="https://fonts.google.com/specimen/Inter"
            value={link}
            onChange={(e) => setLink(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") fetchLink(); }}
          />
          <button
            type="button"
            className="teams-test-btn"
            onClick={fetchLink}
            disabled={!link.trim() || status === "fetching"}
          >
            {status === "fetching" ? "Fetching…" : "Fetch"}
          </button>
        </div>
        <p className="font-import-modal__hint">
          On <a href="https://fonts.google.com" target="_blank" rel="noreferrer">fonts.google.com</a>,
          open a family and paste its page address; every weight and italic it has is fetched.
          An embed code (<b>Get font</b> → <b>Get embed code</b>) works too, but it lists every
          family you have picked there, so ones already on the controller start unticked. Only
          Latin character sets are kept, to stay small.
        </p>

        <div
          className={`font-import-modal__drop${dragOver ? " is-over" : ""}`}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => { e.preventDefault(); setDragOver(false); takeFiles(e.dataTransfer.files); }}
          onClick={() => fileInput.current?.click()}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") fileInput.current?.click(); }}
        >
          Or drop font files here (.woff2, .woff, .ttf, .otf), or click to choose them.
          <span>One family at a time. Weights are read from the file names.</span>
          <input
            ref={fileInput}
            type="file"
            multiple
            accept=".woff2,.woff,.ttf,.otf"
            hidden
            onChange={(e) => { takeFiles(e.target.files); e.target.value = ""; }}
          />
        </div>

        {candidates.length > 0 && (
          <div className="font-import-modal__found">
            {candidates.map((c) => (
              <div key={c.key} className="font-import-modal__family">
                <div className="font-import-modal__family-head">
                  {(candidates.length > 1 || c.already) && (
                    <input
                      type="checkbox"
                      checked={c.include}
                      aria-label={`Add ${c.name}`}
                      onChange={(e) => update(c.key, { include: e.target.checked })}
                    />
                  )}
                  <input
                    className="font-import-modal__input font-import-modal__name"
                    type="text"
                    maxLength={60}
                    aria-label="Font name"
                    value={c.name}
                    onChange={(e) => update(c.key, { name: e.target.value })}
                  />
                  {c.already && <span className="font-import-modal__tag">Already added</span>}
                  <span className="font-import-modal__meta">
                    {c.faces.length} file{c.faces.length === 1 ? "" : "s"},{" "}
                    {formatBytes(c.faces.reduce((a, f) => a + f.bytes, 0))}
                  </span>
                </div>
                <div className="font-import-modal__sample" style={{ fontFamily: `"${c.previewFamily}", sans-serif` }}>
                  <span className="font-import-modal__sample-head">Recording sessions</span>
                  <span>{SAMPLE}</span>
                </div>
                <span className="font-import-modal__meta">{describeFaces(c.faces)}</span>
              </div>
            ))}
          </div>
        )}

        {allAdded && (
          <p className="font-import-modal__msg">
            {candidates.length === 1 ? "This font is" : "All of these fonts are"} already on the
            controller. To replace one, delete it in the font list first.
          </p>
        )}
        {clashing.length > 0 && !allAdded && (
          <p className="font-import-modal__msg val--warn">
            {clashing.map((c) => `"${cleanFontName(c.name)}"`).join(", ")}{" "}
            {clashing.length === 1 ? "is" : "are"} already in use: rename or untick{" "}
            {clashing.length === 1 ? "it" : "them"}.
          </p>
        )}
        {status === "error" && <p className="font-import-modal__msg val--danger">{error}</p>}

        <div className="modal-buttons">
          <button className="reset-button" onClick={onClose}>Cancel</button>
          <button className="save-button" onClick={save} disabled={!canSave}>
            {status === "saving" ? "Uploading…" : chosen.length > 1 ? `Add ${chosen.length} fonts` : "Add font"}
          </button>
        </div>
      </div>
    </div>
  );
}
