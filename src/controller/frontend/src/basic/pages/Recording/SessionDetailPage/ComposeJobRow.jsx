import React, { useEffect, useRef, useState } from "react";
import socket from "/src/socket";
import useIsLoggedIn from "/src/hooks/useIsLoggedIn";
import {
  formatBytes, formatDuration, formatEta, composeJobDownload, composeJobSummary,
  triggerDownload,
} from "../sessionFormat";

/** Seconds left for a running render, from the progress this browser has
 *  seen it make (not the controller's clock, which may differ from ours).
 *  Null until there's enough to go on: 10 s and 1 % of movement. */
function useRenderEta(job) {
  const first = useRef(null);
  const running = job.state === "running";
  const p = job.progress || 0;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return undefined;
    const id = setInterval(() => setNow(Date.now()), 5000);
    return () => clearInterval(id);
  }, [running]);
  useEffect(() => {
    if (!running) { first.current = null; return; }
    if (first.current === null || p < first.current.p) {
      first.current = { t: Date.now(), p };
    }
  }, [running, p]);
  const f = first.current;
  if (!running || !f) return null;
  const dt = (now - f.t) / 1000;
  const dp = p - f.p;
  if (dt < 10 || dp < 0.01) return null;
  return (dt / dp) * (1 - p);
}

/**
 * One render job: state, progress with a time estimate, and its actions:
 * Cancel while it runs; Download when done; Re-run (same settings) when it
 * failed, was cancelled or was cut off by a controller restart; Delete
 * (asks whether to remove the video from the share too) once it's finished.
 * `showSession` adds the session name, for lists spanning sessions.
 */
export default function ComposeJobRow({ job, showSession = false, onShowSession }) {
  const loggedIn = useIsLoggedIn();
  const [confirming, setConfirming] = useState(false);
  const [deleteFile, setDeleteFile] = useState(false);
  const pct = Math.round((job.progress || 0) * 100);
  const active = job.state === "queued" || job.state === "running";
  const etaText = formatEta(useRenderEta(job));
  const took =
    job.started_at && job.finished_at && job.state === "done"
      ? formatDuration((job.finished_at - job.started_at) / 60)
      : null;
  const when = job.created_at
    ? new Date(job.created_at * 1000).toLocaleString([], {
      day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })
    : "";
  const session = job.spec?.session_name;

  const remove = () => {
    socket.emit("delete_compose_job", { job_id: job.id, delete_file: deleteFile });
    setConfirming(false);
  };

  return (
    <div className={`compose-job compose-job--${job.state}`}>
      <div className="compose-job__head">
        <span className={`compose-job__state compose-job__state--${job.state}`}>
          {job.stage || job.state}
        </span>
        {active && <span className="compose-job__pct">{pct}%</span>}
        {active && job.state === "running" && (
          <span className="compose-job__pct">{etaText || "estimating time left…"}</span>
        )}
        {took && <span className="compose-job__pct">took {took}</span>}
        <span className="compose-job__actions">
          {active && (
            <button className="btn btn-small" onClick={() => socket.emit("cancel_compose_job", { job_id: job.id })}>
              Cancel
            </button>
          )}
          {job.state === "done" && job.output_rel && (
            <button
              className="btn btn-small"
              onClick={() => {
                const { url, name } = composeJobDownload(job);
                triggerDownload(url, name);
              }}
            >
              Download
            </button>
          )}
          {["error", "cancelled", "interrupted"].includes(job.state) && (
            <button className="btn btn-small" onClick={() => socket.emit("rerun_compose_job", { job_id: job.id })}>
              Re-run
            </button>
          )}
          {!active && !confirming && (
            <button
              className="btn btn-small"
              onClick={() => { setDeleteFile(false); setConfirming(true); }}
              disabled={!loggedIn}
              title={loggedIn ? "Remove this render from the list" : "Login required for this action"}
            >
              Delete
            </button>
          )}
        </span>
      </div>
      <div className="compose-job__meta">
        {showSession && session && (
          onShowSession
            ? <button className="compose-job__session" onClick={() => onShowSession(session)}>{session}</button>
            : <span className="compose-job__session">{session}</span>
        )}
        <span>{composeJobSummary(job)}</span>
        {when && <span>· {when}</span>}
        {job.output_bytes != null && <span>· {formatBytes(job.output_bytes)}</span>}
      </div>
      {active && (
        <div className="compose-job__bar">
          <div className="compose-job__bar-fill" style={{ width: `${pct}%` }} />
        </div>
      )}
      {(job.state === "error" || job.state === "interrupted") && job.error && (
        <div className="compose-job__error">{job.error}</div>
      )}
      {Array.isArray(job.warnings) && job.warnings.length > 0 && (
        <ul className="compose-job__warnings">
          {job.warnings.map((w, i) => (
            <li key={i} className="compose-job__warning">⚠ {w}</li>
          ))}
        </ul>
      )}
      {confirming && (
        <div className="compose-job__confirm">
          <span>Delete this render from the list?</span>
          {job.output_rel && (
            <label>
              <input
                type="checkbox"
                checked={deleteFile}
                onChange={(e) => setDeleteFile(e.target.checked)}
              />{" "}
              Also delete the video from the share
              {job.output_bytes != null ? ` (${formatBytes(job.output_bytes)})` : ""}
            </label>
          )}
          <span className="compose-job__confirm-btns">
            <button className="btn btn-small btn-danger" onClick={remove}>Delete</button>
            <button className="btn btn-small" onClick={() => setConfirming(false)}>Keep</button>
          </span>
        </div>
      )}
    </div>
  );
}
