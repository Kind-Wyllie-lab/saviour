import React, { useState, useEffect, useRef, useCallback } from "react";
import socket from "/src/socket";
import { formatBytes } from "../Recording/sessionFormat";
import AccelChart from "./AccelChart";
// Short-lived token for the plain-HTTP upload / CSV download.
import { getUploadToken as getToken } from "/src/basic/utils/uploadToken";

const busy = (job) => job?.state === "running" || job?.state === "queued";

function shortRel(rel) {
  // "<date>/Record Node 101/experiment2/recording1" -> "experiment2 / recording1"
  const parts = (rel || "").split("/");
  return parts.slice(-2).join(" / ") || rel;
}

function fmtStart(unixS) {
  if (unixS == null) return "start unknown";
  return new Date(unixS * 1000).toLocaleString([], {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    second: "2-digit", hour12: false,
  });
}

function JobState({ job }) {
  if (!job) return null;
  return (
    <>
      <span className={`ephys-panel__stage ephys-panel__stage--${job.state}`}>
        {job.stage || job.state}
      </span>
      {job.error && <div className="compose-job__error">{job.error}</div>}
      {job.warnings?.length > 0 && (
        <ul className="ephys-panel__warnings">
          {job.warnings.map((w) => <li key={w}>{w}</li>)}
        </ul>
      )}
    </>
  );
}

function UploadBox({ sessionName, onUploaded }) {
  const [dragOver, setDragOver] = useState(false);
  const [files, setFiles] = useState([]); // File[]
  const [progress, setProgress] = useState(null); // 0..1 | null
  const [error, setError] = useState("");
  const inputRef = useRef(null);
  const totalBytes = files.reduce((a, f) => a + f.size, 0);

  useEffect(() => {
    setFiles([]);
    setError("");
  }, [sessionName]);

  const upload = async () => {
    setError("");
    if (!files.length) return;
    let token;
    try {
      token = await getToken();
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
        setFiles([]);
        onUploaded();
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

  return (
    <>
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
          setFiles(Array.from(e.dataTransfer.files || []));
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
          onChange={(e) => setFiles(Array.from(e.target.files || []))}
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
      </div>
    </>
  );
}

function AccelSection({ sessionName, info, recording, onDone }) {
  const rec = info.recordings.find((r) => r.rel === recording);
  const ports = rec?.aux_ports || [];
  const [port, setPort] = useState("");
  const [job, setJob] = useState(null);
  const [plot, setPlot] = useState(null);
  const [plotError, setPlotError] = useState("");
  const result = info.accel?.[port];

  useEffect(() => {
    if (!ports.includes(port)) setPort(ports[0] || "");
  }, [ports.join(","), port]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setJob(null);
  }, [sessionName]);

  useEffect(() => {
    const onJob = (d) => {
      if (d.session_name !== sessionName) return;
      setJob(d);
      if (d.state === "done") onDone();
    };
    socket.on("ephys_accel_update", onJob);
    return () => socket.off("ephys_accel_update", onJob);
  }, [sessionName, onDone]);

  // Load the chart whenever a result for this port exists (or changes).
  useEffect(() => {
    setPlot(null);
    setPlotError("");
    if (!result || !port) return undefined;
    const onPlot = (d) => {
      if (d.session_name !== sessionName || d.port !== port) return;
      if (d.error) setPlotError(d.error);
      else setPlot(d.plot);
    };
    socket.on("ephys_accel_plot", onPlot);
    socket.emit("get_ephys_accel_plot", { session_name: sessionName, port });
    return () => socket.off("ephys_accel_plot", onPlot);
  }, [sessionName, port, result?.n, result?.recording, result?.aligned]); // eslint-disable-line react-hooks/exhaustive-deps

  const run = () => socket.emit("run_ephys_accel", {
    session_name: sessionName, recording, port,
  });

  const download = async () => {
    try {
      const token = await getToken();
      window.location.href =
        `/api/sessions/${encodeURIComponent(sessionName)}/download/` +
        `_ephys/accel/${encodeURIComponent(port)}/accel.csv` +
        `?token=${encodeURIComponent(token)}`;
    } catch (e) {
      setPlotError(e.message);
    }
  };

  if (!rec) return null;
  if (!ports.length) {
    return (
      <div className="ephys-accel">
        <h4>Accelerometer</h4>
        <p className="modal-subtext">
          This recording has no headstage AUX (accelerometer) channels. They
          are recorded only when AUX is switched on in the acquisition board
          plugin.
        </p>
      </div>
    );
  }

  const cal = result?.calibration;
  const stale = result && result.recording !== recording;

  return (
    <div className="ephys-accel">
      <h4>Accelerometer</h4>
      <p className="modal-subtext">
        The headstage's 3-axis accelerometer (<code>{port}_AUX1-3</code>):
        acceleration, head pitch and roll from the direction of gravity, and
        movement intensity. Heading can't be measured from an accelerometer.
        {info.model ? " Times use the clock model above." : " Run alignment first to put it on session time."}
      </p>
      <div className="ephys-panel__run">
        {ports.length > 1 && (
          <label className="ephys-panel__field">
            Headstage
            <select value={port} onChange={(e) => setPort(e.target.value)}>
              {ports.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
        )}
        <button className="btn" onClick={run} disabled={busy(job)}>
          {busy(job) ? "Processing…" : result ? "Re-process" : "Process accelerometer"}
        </button>
        <JobState job={job} />
      </div>

      {result && (
        <div className="ephys-accel__result">
          {stale && (
            <div className="compose-job__error">
              These results are from {shortRel(result.recording)}, not the
              recording selected above. Re-process to update them.
            </div>
          )}
          <dl className="ephys-accel__facts">
            <div>
              <dt>Calibration</dt>
              <dd>
                {cal.method === "auto" ? "fitted from still periods" :
                  cal.method === "default" ? "default (fit not reliable)" : "given"}
                {" · "}zero-g {cal.offset_v.map((v) => v.toFixed(3)).join(" / ")} V
                {" · "}{cal.v_per_g.toFixed(3)} V/g
              </dd>
            </div>
            <div>
              <dt>Bad samples</dt>
              <dd>
                {result.invalid_fraction.map((f) => `${(f * 100).toFixed(2)}%`).join(" / ")}
                {" "}(X / Y / Z, clipped above ~{Math.min(...result.clip_ceiling_g).toFixed(1)} g
                or below 0 V; filled in)
              </dd>
            </div>
            <div>
              <dt>Time base</dt>
              <dd>
                {result.aligned ? "session clock (aligned)" : "ephys clock only"}
                {" · "}{Math.round(result.duration_s)} s at {result.rate_hz} Hz
              </dd>
            </div>
            <div>
              <dt>Axes</dt>
              <dd>{result.frame}</dd>
            </div>
          </dl>
          {result.warnings?.length > 0 && (
            <ul className="ephys-panel__warnings">
              {result.warnings.map((w) => <li key={w}>{w}</li>)}
            </ul>
          )}
          {plotError && <div className="compose-job__error">{plotError}</div>}
          {plot ? <AccelChart plot={plot} /> : !plotError && (
            <div className="ephys-panel__note">Loading chart…</div>
          )}
          <div className="ephys-panel__actions">
            <button className="btn btn-secondary" onClick={download}>
              Download CSV (100 Hz)
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function EphysAlignPanel({ sessionName }) {
  const [info, setInfo] = useState(null);
  const [recording, setRecording] = useState("");
  const [alignJob, setAlignJob] = useState(null);
  const [showUpload, setShowUpload] = useState(false);

  const refresh = useCallback(
    () => socket.emit("get_ephys_info", { session_name: sessionName }),
    [sessionName],
  );

  useEffect(() => {
    setInfo(null);
    setAlignJob(null);
    setRecording("");
    setShowUpload(false);
    const onInfo = (d) => {
      if (d.session_name === sessionName) setInfo(d);
    };
    socket.on("ephys_info", onInfo);
    refresh();
    return () => socket.off("ephys_info", onInfo);
  }, [sessionName, refresh]);

  useEffect(() => {
    const onJob = (d) => {
      if (d.session_name !== sessionName) return;
      setAlignJob(d);
      if (d.state === "done") refresh();
    };
    socket.on("ephys_align_update", onJob);
    return () => socket.off("ephys_align_update", onJob);
  }, [sessionName, refresh]);

  // Default recording: the one the clock model used, else the one whose
  // time span matches the session.
  useEffect(() => {
    if (!info?.recordings?.length) return;
    const rels = info.recordings.map((r) => r.rel);
    if (rels.includes(recording)) return;
    const want = [info.model?.oe_recording, info.matched?.rel, rels[0]]
      .find((r) => r && rels.includes(r));
    setRecording(want);
  }, [info]); // eslint-disable-line react-hooks/exhaustive-deps

  const recs = info?.recordings || [];
  const uploaded = recs.length > 0;
  const model = info?.model;
  const modelOnOther = model?.oe_recording && model.oe_recording !== recording;

  const runAlign = () => socket.emit("run_ephys_align", {
    session_name: sessionName,
    ...(recording && recording !== info?.matched?.rel ? { recording } : {}),
  });

  return (
    <section className="card ephys-panel">
      <h3>Ephys</h3>
      <p className="modal-subtext">
        Upload the Open Ephys recording for this session. The shared
        pseudorandom pulse train is matched against this session's TTL log to
        fit a clock model between ephys time and SAVIOUR/PTP time; the
        headstage accelerometer can then be processed onto session time.
      </p>

      {info?.error && <div className="compose-job__error">{info.error}</div>}

      {(!uploaded || showUpload) && (
        <UploadBox
          sessionName={sessionName}
          onUploaded={() => {
            setShowUpload(false);
            refresh();
          }}
        />
      )}
      {info?.uploaded && !uploaded && (
        <div className="compose-job__error">
          Files were uploaded, but no Open Ephys recording (a folder with
          <code> structure.oebin</code>) was found in them.
        </div>
      )}

      {uploaded && (
        <>
          <div className="ephys-panel__run">
            <label className="ephys-panel__field">
              Recording
              <select value={recording} onChange={(e) => setRecording(e.target.value)}>
                {recs.map((r) => (
                  <option key={r.rel} value={r.rel}>
                    {shortRel(r.rel)} · {fmtStart(r.start_unix_s)} ·{" "}
                    {Math.round(r.duration_s)} s
                    {r.rel === info.matched?.rel ? " (matches session)" : ""}
                  </option>
                ))}
              </select>
            </label>
            {!showUpload && (
              <button className="btn btn-link" onClick={() => setShowUpload(true)}>
                Upload more
              </button>
            )}
          </div>
          <div className="ephys-panel__note">
            {info.matched?.rel
              ? `Matched: ${info.matched.reason}. Start times use the ephys PC's clock.`
              : info.matched?.reason}
          </div>

          <div className="ephys-panel__run">
            <button className="btn" onClick={runAlign} disabled={busy(alignJob)}>
              {busy(alignJob) ? "Aligning…" : model ? "Re-run alignment" : "Run alignment"}
            </button>
            <JobState job={alignJob} />
          </div>
          {model && !busy(alignJob) && (
            <div className={modelOnOther ? "compose-job__error" : "ephys-panel__model"}>
              {modelOnOther
                ? `The clock model was fitted on ${shortRel(model.oe_recording)}, not this recording.`
                : <>
                  Clock model: R² {model.r2 == null ? "–" : model.r2 >= 0.9999999
                    ? `1 − ${(1 - model.r2).toExponential(1)}` : model.r2.toFixed(7)}, worst residual{" "}
                  {model.residual_max_ms} ms over {model.edges_used} edges
                  {model.pc_clock_offset_s != null &&
                    `; the ephys PC's clock was ${model.pc_clock_offset_s > 0 ? "ahead" : "behind"} by ${Math.abs(model.pc_clock_offset_s).toFixed(1)} s`}
                  {model.oe_recording ? "" : " (fitted before recording selection; re-run to confirm)"}
                </>}
            </div>
          )}

          <AccelSection
            sessionName={sessionName}
            info={info}
            recording={recording}
            onDone={refresh}
          />
        </>
      )}

      <p className="ephys-panel__note">
        Experimental: the ephys tools (<code>tools/ephys/</code>,{" "}
        <code>ephys_accel.py</code>) haven't been validated running on a
        controller yet; sanity-check the clock model before relying on it.
      </p>
    </section>
  );
}
