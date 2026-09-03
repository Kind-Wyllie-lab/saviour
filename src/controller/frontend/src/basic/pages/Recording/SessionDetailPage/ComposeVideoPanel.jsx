import React, { useState, useEffect, useMemo } from "react";
import socket from "/src/socket";
import { formatDuration } from "../sessionFormat";

const LAYOUTS = [
  { value: "auto", label: "Auto" },
  { value: "side", label: "Side by side" },
  { value: "stack", label: "Stacked" },
  { value: "grid", label: "Grid" },
  { value: "loom", label: "Loom (LoomCam / Home / Screen)" },
];

// Group the flat session file list into { dateDir: [cameraStreamName, ...] }.
// A camera stream is a module folder that holds both a video (.ts/.mp4) and
// a *_timestamps.csv — the pair compose.discover_streams() looks for.
function cameraStreamsByDate(files) {
  const videos = new Map(); // "date/module" -> true
  const csvs = new Map();
  for (const f of files || []) {
    const parts = f.path.split("/");
    if (parts.length < 3) continue;
    const key = `${parts[0]}/${parts[1]}`;
    if (/\.(ts|mp4)$/i.test(f.name)) videos.set(key, true);
    if (/_timestamps\.csv$/i.test(f.name)) csvs.set(key, true);
  }
  const byDate = {};
  for (const key of videos.keys()) {
    if (!csvs.has(key)) continue;
    const [date, mod] = key.split("/");
    (byDate[date] ||= []).push(mod);
  }
  for (const date of Object.keys(byDate)) byDate[date].sort();
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

export default function ComposeVideoPanel({ sessionName, files, onRequestDownload }) {
  const byDate = useMemo(() => cameraStreamsByDate(files), [files]);
  const dates = Object.keys(byDate).sort();

  const [dateDir, setDateDir] = useState("");
  const [selected, setSelected] = useState([]);
  const [layout, setLayout] = useState("auto");
  const [fps, setFps] = useState(15);
  const [fmt, setFmt] = useState("mp4");
  const [jobs, setJobs] = useState({});
  const [notice, setNotice] = useState("");

  // Default to the most recent date dir and select all its cameras.
  useEffect(() => {
    if (!dates.length) return;
    const d = dates.includes(dateDir) ? dateDir : dates[dates.length - 1];
    if (d !== dateDir) setDateDir(d);
    setSelected(byDate[d] || []);
  }, [dates.join("|")]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setSelected(byDate[dateDir] || []);
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
    socket.on("compose_jobs", onList);
    socket.on("compose_job_update", upsert);
    socket.on("compose_job_accepted", onAccepted);
    socket.on("compose_job_rejected", onRejected);
    socket.emit("get_compose_jobs");
    return () => {
      socket.off("compose_jobs", onList);
      socket.off("compose_job_update", upsert);
      socket.off("compose_job_accepted", onAccepted);
      socket.off("compose_job_rejected", onRejected);
    };
  }, []);

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
      fps: Number(fps) || 15,
      fmt,
    });
  };

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
          {(byDate[dateDir] || []).map((mod) => (
            <label key={mod} className="compose-panel__stream">
              <input
                type="checkbox"
                checked={selected.includes(mod)}
                onChange={() => toggle(mod)}
              />
              {mod}
            </label>
          ))}
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
          <label>Output FPS:</label>
          <input
            type="number"
            min="1"
            max="60"
            value={fps}
            onChange={(e) => setFps(e.target.value)}
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

      {notice && <div className="compose-job__error">{notice}</div>}

      <button
        className="btn"
        onClick={submit}
        disabled={!selected.length || anyActive}
      >
        {anyActive ? "Rendering…" : "Render video"}
      </button>

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
