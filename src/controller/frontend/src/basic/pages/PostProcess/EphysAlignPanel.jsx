import React, { useState, useEffect, useRef } from "react";
import socket from "/src/socket";
import { formatBytes } from "../Recording/sessionFormat";

// Short-lived token for the plain-HTTP upload (same mechanism the file
// downloads use — the endpoint has no Socket.IO session of its own).
function getUploadToken() {
  return new Promise((resolve, reject) => {
    const done = (fn) => (d) => {
      socket.off("download_token", ok);
      socket.off("auth_required", no);
      clearTimeout(t);
      fn(d);
    };
    const ok = done((d) => resolve(d.token));
    const no = done(() => reject(new Error("Login required to upload")));
    const t = setTimeout(
      () => done(() => reject(new Error("Timed out requesting upload token")))(),
      8000,
    );
    socket.on("download_token", ok);
    socket.on("auth_required", no);
    socket.emit("request_download_token");
  });
}

export default function EphysAlignPanel({ sessionName }) {
  const [dragOver, setDragOver] = useState(false);
  const [files, setFiles] = useState([]); // File[]
  const [progress, setProgress] = useState(null); // 0..1 | null
  const [uploaded, setUploaded] = useState(null); // { count, bytes } | null
  const [error, setError] = useState("");
  const [job, setJob] = useState(null);
  const inputRef = useRef(null);

  useEffect(() => {
    setUploaded(null);
    setJob(null);
    setError("");
  }, [sessionName]);

  useEffect(() => {
    const onJob = (d) => {
      if (d.session_name === sessionName) setJob(d);
    };
    socket.on("ephys_align_update", onJob);
    return () => socket.off("ephys_align_update", onJob);
  }, [sessionName]);

  const pick = (list) => setFiles(Array.from(list || []));

  const totalBytes = files.reduce((a, f) => a + f.size, 0);

  const upload = async () => {
    setError("");
    if (!files.length) return;
    let token;
    try {
      token = await getUploadToken();
    } catch (e) {
      return setError(e.message);
    }
    const form = new FormData();
    for (const f of files) {
      // keep the relative path when a folder was dropped
      form.append("files", f, f.webkitRelativePath || f.name);
    }
    const xhr = new XMLHttpRequest();
    xhr.open(
      "POST",
      `/api/ephys/upload?session=${encodeURIComponent(sessionName)}` +
        `&token=${encodeURIComponent(token)}`,
    );
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) setProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      setProgress(null);
      if (xhr.status >= 200 && xhr.status < 300) {
        let r = {};
        try {
          r = JSON.parse(xhr.responseText);
        } catch { /* ignore */ }
        setUploaded({ count: r.files ?? files.length, bytes: r.bytes ?? totalBytes });
        setFiles([]);
      } else {
        setError(`Upload failed (${xhr.status}): ${xhr.responseText}`);
      }
    };
    xhr.onerror = () => {
      setProgress(null);
      setError("Upload failed (network error)");
    };
    setProgress(0);
    xhr.send(form);
  };

  const runAlign = () =>
    socket.emit("run_ephys_align", { session_name: sessionName });

  return (
    <section className="card ephys-panel">
      <h3>Ephys alignment</h3>
      <p className="modal-subtext">
        Upload the Open Ephys recording (or just its <code>events</code> /
        TTL files) for this session. The shared pseudorandom pulse train is
        matched against this session's TTL module log to fit a clock model
        between ephys time and SAVIOUR/PTP time.
      </p>

      <div
        className={"ephys-drop" + (dragOver ? " ephys-drop--over" : "")}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          pick(e.dataTransfer.files);
        }}
        onClick={() => inputRef.current?.click()}
      >
        {files.length
          ? `${files.length} file${files.length !== 1 ? "s" : ""} · ${formatBytes(totalBytes)}`
          : "Drop the Open Ephys folder / files here, or click to choose"}
        <input
          ref={inputRef}
          type="file"
          multiple
          webkitdirectory=""
          directory=""
          hidden
          onChange={(e) => pick(e.target.files)}
        />
      </div>

      {progress != null && (
        <div className="compose-job__bar">
          <div
            className="compose-job__bar-fill"
            style={{ width: `${Math.round(progress * 100)}%` }}
          />
        </div>
      )}

      {error && <div className="compose-job__error">{error}</div>}

      <div className="ephys-panel__actions">
        <button
          className="btn btn-secondary"
          onClick={upload}
          disabled={!files.length || progress != null}
        >
          {progress != null ? "Uploading…" : "Upload"}
        </button>
        {uploaded && (
          <span className="ephys-panel__uploaded">
            {uploaded.count} files ({formatBytes(uploaded.bytes)}) stored
          </span>
        )}
      </div>

      {uploaded && (
        <div className="ephys-panel__run">
          <button
            className="btn"
            onClick={runAlign}
            disabled={job?.state === "running" || job?.state === "queued"}
          >
            {job?.state === "running" || job?.state === "queued"
              ? "Aligning…"
              : "Run alignment"}
          </button>
          {job && (
            <span className={`compose-job__state compose-job__state--${job.state}`}>
              {job.stage || job.state}
            </span>
          )}
          {job?.error && <div className="compose-job__error">{job.error}</div>}
        </div>
      )}

      <p className="ephys-panel__note">
        Experimental — the alignment tool (<code>tools/ephys/</code>, folded
        in from <code>saviour-ephys-analysis</code>) has not yet been
        validated running on a controller. Uploading and storage work now;
        treat the fitted model as needing a manual sanity check.
      </p>
    </section>
  );
}
