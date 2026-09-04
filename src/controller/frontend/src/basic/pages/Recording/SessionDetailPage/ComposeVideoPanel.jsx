import React, { useState, useEffect, useMemo, useRef } from "react";
import socket from "/src/socket";
import usePersistedState from "/src/hooks/usePersistedState";
import { formatDuration } from "../sessionFormat";
import { SYNC_CLASS, SYNC_LABEL, worstSummary } from "../syncFormat";

const LAYOUTS = [
  { value: "auto", label: "Auto" },
  { value: "side", label: "Side by side" },
  { value: "stack", label: "Stacked" },
  { value: "grid", label: "Grid" },
  { value: "loom", label: "Loom (LoomCam / Home / Screen)" },
];

const AUDIO_MODES = [
  { value: "none", label: "No audio" },
  { value: "track", label: "Muxed audio track" },
  { value: "strip", label: "Spectrogram strip (over video)" },
  { value: "panel", label: "Spectrogram panel (below video)" },
];

const SPEC_COLORS = [
  "intensity", "rainbow", "magma", "viridis", "plasma", "cividis",
  "fire", "fiery", "fruit", "moreland", "nebulae", "terrain",
  "cool", "green", "channel",
];

// Committed swatch PNGs, one per colour map (tools/gen_spectrogram_swatches.py).
// { "intensity": "/assets/.../intensity.png", ... }
const SWATCHES = Object.fromEntries(
  Object.entries(
    import.meta.glob("/src/assets/spectrogram-swatches/*.png", {
      eager: true,
      query: "?url",
      import: "default",
    }),
  ).map(([path, url]) => [path.split("/").pop().replace(".png", ""), url]),
);

const PREVIEW_DEBOUNCE_MS = 650;

// Group the flat session file list into { dateDir: { cameras: [...], mics: [...] } }.
// A camera stream is a folder with a video (.ts/.mp4) + a *_timestamps.csv;
// a mic is a folder with a .flac/.wav + a *_timestamps.txt — the pairs
// compose.discover_streams() / find_microphone() look for.
function streamsByDate(files) {
  const has = { vid: new Set(), csv: new Set(), aud: new Set(), txt: new Set() };
  for (const f of files || []) {
    const parts = f.path.split("/");
    if (parts.length < 3) continue;
    const key = `${parts[0]}/${parts[1]}`;
    if (/\.(ts|mp4)$/i.test(f.name)) has.vid.add(key);
    if (/_timestamps\.csv$/i.test(f.name)) has.csv.add(key);
    if (/\.(flac|wav)$/i.test(f.name)) has.aud.add(key);
    if (/_timestamps\.txt$/i.test(f.name)) has.txt.add(key);
  }
  const byDate = {};
  const bucket = (d) => (byDate[d] ||= { cameras: [], mics: [] });
  for (const key of has.vid) {
    if (!has.csv.has(key)) continue;
    const [d, m] = key.split("/");
    bucket(d).cameras.push(m);
  }
  for (const key of has.aud) {
    if (!has.txt.has(key)) continue;
    const [d, m] = key.split("/");
    bucket(d).mics.push(m);
  }
  for (const d of Object.keys(byDate)) {
    byDate[d].cameras.sort();
    byDate[d].mics.sort();
  }
  return byDate;
}

function JobRow({ job, onCancel, onDownload }) {
  const pct = Math.round((job.progress || 0) * 100);
  const active = job.state === "queued" || job.state === "running";
  const elapsed =
    job.started_at && job.finished_at
      ? formatDuration((job.finished_at - job.started_at) / 60)
      : null;
  return (
    <div className="compose-job">
      <div className="compose-job__head">
        <span className={`compose-job__state compose-job__state--${job.state}`}>
          {job.stage || job.state}
        </span>
        {active && <span className="compose-job__pct">{pct}%</span>}
        {elapsed && <span className="compose-job__pct">{elapsed}</span>}
        {active && (
          <button className="btn btn-small" onClick={() => onCancel(job.id)}>
            Cancel
          </button>
        )}
        {job.state === "done" && job.output_rel && (
          <button className="btn btn-small" onClick={() => onDownload(job)}>
            Download
          </button>
        )}
      </div>
      {active && (
        <div className="compose-job__bar">
          <div className="compose-job__bar-fill" style={{ width: `${pct}%` }} />
        </div>
      )}
      {job.state === "error" && (
        <div className="compose-job__error">{job.error}</div>
      )}
    </div>
  );
}

export default function ComposeVideoPanel({ sessionName, session, files, onRequestDownload }) {
  const byDate = useMemo(() => streamsByDate(files), [files]);
  const dates = Object.keys(byDate).sort();

  const [dateDir, setDateDir] = useState("");
  const [selected, setSelected] = useState([]);
  // Persisted so the panel reopens (page reload, tab switch) with the same
  // choices the last preview was rendered with -- the colour picker in
  // particular used to snap back to "intensity" while the preview still
  // showed the last colour.
  const [layout, setLayout] = usePersistedState("compose_layout", "auto");
  const [fps, setFps] = useState("");
  const fpsTouched = useRef(false);
  const [fmt, setFmt] = usePersistedState("compose_fmt", "mp4");
  const [audioMode, setAudioMode] = usePersistedState("compose_audio_mode", "none");
  const [audioSource, setAudioSource] = useState("");
  const [spec, setSpec] = usePersistedState("compose_spec", {
    color: "intensity", fmin_hz: 0, fmax_hz: 96000, fscale: "lin",
    ascale: "log", gain: 2.5,
  });
  const [jobs, setJobs] = useState({});
  const [notice, setNotice] = useState("");
  const [preview, setPreview] = useState(null); // { image } | { error }
  const [previewing, setPreviewing] = useState(false);
  const [info, setInfo] = useState(null); // { suggested_fps, cameras, mics } | { error }
  const previewTimer = useRef(null);

  const cams = byDate[dateDir]?.cameras || [];
  const mics = byDate[dateDir]?.mics || [];
  const showSpec = audioMode === "strip" || audioMode === "panel";
  const selectedKey = selected.join("|");
  const specKey = showSpec ? JSON.stringify(spec) : "";

  // Default to the most recent date dir and select all its cameras.
  useEffect(() => {
    if (!dates.length) return;
    const d = dates.includes(dateDir) ? dateDir : dates[dates.length - 1];
    if (d !== dateDir) setDateDir(d);
    setSelected(byDate[d]?.cameras || []);
  }, [dates.join("|")]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setSelected(byDate[dateDir]?.cameras || []);
    setAudioSource((byDate[dateDir]?.mics || [])[0] || "");
    if (!(byDate[dateDir]?.mics || []).length) setAudioMode("none");
  }, [dateDir]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const upsert = (summary) =>
      setJobs((prev) => ({ ...prev, [summary.id]: summary }));
    const onList = (data) => {
      const map = {};
      for (const j of data.jobs || []) map[j.id] = j;
      setJobs(map);
    };
    const onAccepted = (j) => {
      setNotice("");
      upsert(j);
    };
    const onRejected = (d) => setNotice(d.error || "Compose request rejected");
    const onPreview = (d) => {
      setPreviewing(false);
      // Keep the last good image visible if this attempt only errored.
      setPreview((prev) => (d.error && prev?.image ? { ...prev, error: d.error } : d));
    };
    const onInfo = (d) => setInfo(d);
    socket.on("compose_jobs", onList);
    socket.on("compose_job_update", upsert);
    socket.on("compose_job_accepted", onAccepted);
    socket.on("compose_job_rejected", onRejected);
    socket.on("compose_preview_ready", onPreview);
    socket.on("compose_streams_info", onInfo);
    socket.emit("get_compose_jobs");
    return () => {
      socket.off("compose_jobs", onList);
      socket.off("compose_job_update", upsert);
      socket.off("compose_job_accepted", onAccepted);
      socket.off("compose_job_rejected", onRejected);
      socket.off("compose_preview_ready", onPreview);
      socket.off("compose_streams_info", onInfo);
    };
  }, []);

  // Ask the backend for the real per-camera capture rate / mic list for the
  // current selection, and adopt the suggested fps until the user overrides it.
  useEffect(() => {
    if (!dateDir || !selected.length) return;
    socket.emit("compose_streams_info", {
      session_name: sessionName,
      date_dir: dateDir,
      streams: selected,
    });
  }, [sessionName, dateDir, selectedKey]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!fpsTouched.current && info?.suggested_fps) {
      setFps(String(info.suggested_fps));
    }
  }, [info?.suggested_fps]);

  const buildAudio = () =>
    audioMode === "none"
      ? { mode: "none" }
      : {
          mode: audioMode,
          source: audioSource || undefined,
          spectrogram:
            audioMode === "track"
              ? {}
              : {
                  color: spec.color,
                  fmin_hz: Number(spec.fmin_hz) || 0,
                  fmax_hz: Number(spec.fmax_hz) || undefined,
                  fscale: spec.fscale,
                  ascale: spec.ascale,
                  gain: Number(spec.gain) || 1,
                },
        };

  const effectiveFps = Number(fps) || info?.suggested_fps || 15;

  const requestPreview = (opts = {}) => {
    if (!selected.length) return;
    setPreviewing(true);
    setPreview((prev) => (prev?.image ? { image: prev.image } : prev));
    socket.emit("compose_preview", {
      session_name: sessionName,
      date_dir: dateDir || undefined,
      streams: selected,
      layout,
      audio: buildAudio(),
      rebuild: opts.rebuild || undefined,
    });
  };

  // The preview is always on screen — re-render it (debounced) whenever an
  // input that changes the layout or the audio panel changes.
  useEffect(() => {
    if (!selected.length) return undefined;
    clearTimeout(previewTimer.current);
    previewTimer.current = setTimeout(() => requestPreview(), PREVIEW_DEBOUNCE_MS);
    return () => clearTimeout(previewTimer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dateDir, selectedKey, layout, audioMode, audioSource, specKey]);

  if (!dates.length) return null; // nothing composable in this session

  const toggle = (mod) =>
    setSelected((s) =>
      s.includes(mod) ? s.filter((m) => m !== mod) : [...s, mod],
    );

  const jobList = Object.values(jobs)
    .filter((j) => (j.spec?.session_name || sessionName) === sessionName)
    .sort((a, b) => b.created_at - a.created_at);
  const anyActive = jobList.some(
    (j) => j.state === "queued" || j.state === "running",
  );

  const submit = () => {
    setNotice("");
    socket.emit("compose_session_video", {
      session_name: sessionName,
      date_dir: dateDir || undefined,
      streams: selected,
      layout,
      fps: effectiveFps,
      fmt,
      audio: buildAudio(),
    });
  };

  const setSpecField = (k, v) => setSpec((s) => ({ ...s, [k]: v }));

  const download = (job) => {
    // output_rel is "<session>/<date>/<file>"; the download route wants the
    // path relative to the session dir.
    const rel = job.output_rel.split("/").slice(1);
    const url = `/api/sessions/${sessionName}/download/${rel
      .map(encodeURIComponent)
      .join("/")}`;
    const name = rel[rel.length - 1];
    if (onRequestDownload) onRequestDownload(url, name, 0);
  };

  return (
    <section className="card compose-panel">
      <h3>Compose aggregated video</h3>
      <p className="modal-subtext">
        Build one layout video from this session's cameras, aligned by each
        camera's real per-frame timestamp. Runs in the background — you can
        leave this page.
      </p>

      <div className="compose-panel__body">
       <div className="compose-panel__controls">

      {dates.length > 1 && (
        <div className="form-field">
          <label>Recording day:</label>
          <select value={dateDir} onChange={(e) => setDateDir(e.target.value)}>
            {dates.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </div>
      )}

      <div className="form-field">
        <label>Cameras:</label>
        <div className="compose-panel__streams">
          {cams.map((mod) => {
            const camFps = info?.cameras?.find((c) => c.name === mod)?.fps;
            return (
              <label key={mod} className="compose-panel__stream">
                <input
                  type="checkbox"
                  checked={selected.includes(mod)}
                  onChange={() => toggle(mod)}
                />
                {mod}
                {camFps ? (
                  <span className="compose-panel__stream-fps">
                    {" "}
                    {camFps} fps
                  </span>
                ) : null}
              </label>
            );
          })}
        </div>
      </div>

      <div className="compose-panel__row">
        <div className="form-field">
          <label>Layout:</label>
          <select value={layout} onChange={(e) => setLayout(e.target.value)}>
            {LAYOUTS.map((l) => (
              <option key={l.value} value={l.value}>
                {l.label}
              </option>
            ))}
          </select>
        </div>
        <div className="form-field">
          <label>
            Output FPS:
            {!fpsTouched.current && info?.suggested_fps ? (
              <span className="compose-panel__hint"> (camera rate)</span>
            ) : null}
          </label>
          <input
            type="number"
            min="1"
            max="60"
            value={fps}
            placeholder={info?.suggested_fps || ""}
            onChange={(e) => {
              fpsTouched.current = true;
              setFps(e.target.value);
            }}
          />
        </div>
        <div className="form-field">
          <label>Format:</label>
          <select value={fmt} onChange={(e) => setFmt(e.target.value)}>
            <option value="mp4">MP4</option>
            <option value="mkv">MKV</option>
          </select>
        </div>
      </div>

      <div className="compose-panel__row">
        <div className="form-field">
          <label>Audio:</label>
          <select
            value={audioMode}
            onChange={(e) => setAudioMode(e.target.value)}
            disabled={!mics.length}
            title={mics.length ? "" : "No microphone recording in this session"}
          >
            {AUDIO_MODES.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </div>
        {audioMode !== "none" && mics.length > 1 && (
          <div className="form-field">
            <label>Microphone:</label>
            <select
              value={audioSource}
              onChange={(e) => setAudioSource(e.target.value)}
            >
              {mics.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      {showSpec && (
        <>
          <div className="compose-panel__colour">
            <span className="compose-panel__colour-label">
              Colour: <strong>{spec.color}</strong>
            </span>
            <div className="compose-panel__swatches">
              {SPEC_COLORS.map((c) => (
                <button
                  type="button"
                  key={c}
                  className={`compose-panel__swatch${
                    spec.color === c ? " compose-panel__swatch--active" : ""
                  }`}
                  title={c}
                  aria-label={c}
                  onClick={() => setSpecField("color", c)}
                >
                  {SWATCHES[c] ? (
                    <img src={SWATCHES[c]} alt="" />
                  ) : (
                    <span className="compose-panel__swatch-fallback">{c}</span>
                  )}
                </button>
              ))}
            </div>
          </div>
          <div className="compose-panel__row compose-panel__spec">
            <div className="form-field">
              <label>Freq min (Hz):</label>
              <input
                type="number"
                min="0"
                step="1000"
                value={spec.fmin_hz}
                onChange={(e) => setSpecField("fmin_hz", e.target.value)}
              />
            </div>
            <div className="form-field">
              <label>Freq max (Hz):</label>
              <input
                type="number"
                min="1000"
                step="1000"
                value={spec.fmax_hz}
                onChange={(e) => setSpecField("fmax_hz", e.target.value)}
              />
            </div>
            <div className="form-field">
              <label>Freq scale:</label>
              <select
                value={spec.fscale}
                onChange={(e) => setSpecField("fscale", e.target.value)}
              >
                <option value="lin">Linear</option>
                <option value="log">Log</option>
              </select>
            </div>
            <div className="form-field">
              <label>Gain:</label>
              <input
                type="number"
                min="0.1"
                max="20"
                step="0.5"
                value={spec.gain}
                onChange={(e) => setSpecField("gain", e.target.value)}
              />
            </div>
          </div>
        </>
      )}

      {notice && <div className="compose-job__error">{notice}</div>}

       </div>

      <div className="compose-panel__preview-wrap">
        <div className="compose-panel__preview-head">
          <span>Preview</span>
          <button
            className="btn btn-small"
            onClick={() => requestPreview({ rebuild: true })}
            disabled={previewing || !selected.length}
            title="Re-decode each camera's thumbnail and rebuild the preview"
          >
            {previewing ? "Rendering…" : "Rebuild"}
          </button>
        </div>
        <div className="compose-panel__preview-box">
          {preview?.image ? (
            <img
              className="compose-panel__preview"
              src={preview.image}
              alt="Layout preview (mid-session frame)"
            />
          ) : (
            <div className="compose-panel__preview-empty">
              {selected.length ? "Preview will appear here" : "Select a camera"}
            </div>
          )}
          {previewing && (
            <div className="compose-panel__preview-spinner">Rendering…</div>
          )}
        </div>
        {preview?.error && (
          <div className="compose-job__error">{preview.error}</div>
        )}
      </div>
      </div>

      {(() => {
        const verdict =
          session?.day_verdicts?.[dateDir] ||
          (dates.length === 1 ? session?.framesync_verdict : null);
        if (verdict?.status) {
          return (
            <div className={`compose-panel__sync compose-panel__sync--${SYNC_CLASS[verdict.status]}`}>
              Sync quality {dateDir ? `(${dateDir})` : ""}:{" "}
              <strong>{SYNC_LABEL[verdict.status]}</strong>
              {worstSummary(verdict) ? ` — ${worstSummary(verdict)}` : ""}
              <span className="compose-panel__sync-note">
                {" "}validated automatically; not recomputed here
              </span>
            </div>
          );
        }
        return (
          <div className="compose-panel__sync compose-panel__sync--muted">
            Sync quality not yet validated for this day.
          </div>
        );
      })()}

      <div className="compose-panel__actions">
        <button
          className="btn"
          onClick={submit}
          disabled={!selected.length || anyActive}
        >
          {anyActive ? "Rendering…" : "Render video"}
        </button>
      </div>

      {jobList.length > 0 && (
        <div className="compose-panel__jobs">
          {jobList.map((job) => (
            <JobRow
              key={job.id}
              job={job}
              onCancel={(id) => socket.emit("cancel_compose_job", { job_id: id })}
              onDownload={download}
            />
          ))}
        </div>
      )}
    </section>
  );
}
