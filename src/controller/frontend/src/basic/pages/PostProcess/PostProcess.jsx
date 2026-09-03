import React, { useState, useEffect, useMemo, useRef } from "react";
import { useSearchParams } from "react-router";
import socket from "/src/socket";
import useSessions from "/src/hooks/useSessions";
import { triggerDownload, formatBytes } from "../Recording/sessionFormat";
import ComposeVideoPanel from "../Recording/SessionDetailPage/ComposeVideoPanel";
import EphysAlignPanel from "./EphysAlignPanel";
import "./PostProcess.css";

// Everything that turns a *recorded* session into a derived artefact —
// layout/compose video, aligned audio + spectrogram, and (once a dataset
// is uploaded) ephys alignment. Kept off the Recording page, which is for
// running sessions and browsing raw files.
export default function PostProcess() {
  const { sessions } = useSessions();
  const [params, setParams] = useSearchParams();
  const names = useMemo(
    () => Object.keys(sessions || {}).sort(),
    [sessions],
  );

  const [selected, setSelected] = useState(params.get("session") || "");
  const [fileInfo, setFileInfo] = useState(null);
  const seededRef = useRef(false);

  // Default to the session in the URL, else the most recent one.
  useEffect(() => {
    if (seededRef.current || !names.length) return;
    const want = names.includes(selected) ? selected : names[names.length - 1];
    seededRef.current = true;
    if (want !== selected) setSelected(want);
  }, [names]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!selected) return;
    setParams((p) => {
      p.set("session", selected);
      return p;
    });
    setFileInfo(null);
    const onInfo = (d) => {
      if (d.session_name === selected) setFileInfo(d);
    };
    socket.on("session_file_info_response", onInfo);
    socket.emit("get_session_file_info", { session_name: selected });
    return () => socket.off("session_file_info_response", onInfo);
  }, [selected]); // eslint-disable-line react-hooks/exhaustive-deps

  const requestDownload = (url, name) => triggerDownload(url, name);

  return (
    <div className="post-process">
      <h1>Post-Process</h1>
      <p className="post-process__intro">
        Build aligned videos, spectrograms and (with an uploaded recording)
        ephys alignment from a finished session. Jobs run in the background —
        you can leave this page.
      </p>

      <label className="post-process__pick">
        Session
        <select
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
        >
          <option value="">— choose a session —</option>
          {names.map((n) => (
            <option key={n} value={n}>
              {n}
              {sessions[n]?.state ? `  (${sessions[n].state})` : ""}
            </option>
          ))}
        </select>
        {fileInfo && fileInfo !== "loading" && (
          <span className="post-process__size">
            {fileInfo.files?.length || 0} files ·{" "}
            {formatBytes(fileInfo.total_bytes || 0)}
          </span>
        )}
      </label>

      {!selected && (
        <div className="post-process__empty">Pick a session to start.</div>
      )}

      {selected && fileInfo && fileInfo.files && fileInfo.files.length > 0 && (
        <>
          <ComposeVideoPanel
            sessionName={selected}
            files={fileInfo.files}
            onRequestDownload={requestDownload}
          />
          <EphysAlignPanel sessionName={selected} />
        </>
      )}

      {selected && fileInfo && fileInfo.files && fileInfo.files.length === 0 && (
        <div className="post-process__empty">
          This session has no exported files yet.
        </div>
      )}
    </div>
  );
}
