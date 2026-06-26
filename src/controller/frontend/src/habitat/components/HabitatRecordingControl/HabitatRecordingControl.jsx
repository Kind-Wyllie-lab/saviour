import React, { useState, useEffect, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import socket from "/src/socket";
import "./HabitatRecordingControl.css";

function formatTime(t) {
  if (!t) return "—";
  // YYYYMMDD-HHMMSS → "25 Jun, 14:30"
  const m = t.match(/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/);
  if (!m) return t;
  const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  return `${parseInt(m[3])} ${months[parseInt(m[2]) - 1]}, ${m[4]}:${m[5]}`;
}

export default function HabitatRecordingControl({ sessionList = [], modules = {} }) {
  const navigate = useNavigate();
  const [habitatConfig, setHabitatConfig] = useState(null);
  const [startError, setStartError] = useState(null);

  useEffect(() => {
    socket.emit("get_habitat_config");
    const onConfig = (data) => setHabitatConfig(data);
    socket.on("habitat_config", onConfig);
    return () => socket.off("habitat_config", onConfig);
  }, []);

  useEffect(() => {
    const onError = (data) => {
      setStartError(data.error || "Failed to start recording");
      setBusy(false);
      setTimeout(() => setStartError(null), 10000);
    };
    socket.on("session_error", onError);
    return () => socket.off("session_error", onError);
  }, []);


  // ── Derive session state ─────────────────────────────────────────────────
  const cameraSession = useMemo(
    () => sessionList.find(s => s.target?.includes("camera") && s.state !== "stopped"),
    [sessionList]
  );
  const audioSession = useMemo(
    () => sessionList.find(s => s.target === "microphone" && s.state !== "stopped"),
    [sessionList]
  );

  const isRecording = cameraSession?.state === "active";
  const isStarting  = cameraSession?.state === "active" &&
    !Object.values(modules).some(m => m.type?.includes("camera") && m.status === "RECORDING");
  const hasFault    = cameraSession?.state === "error";

  // ── Module counts ────────────────────────────────────────────────────────
  const moduleList = useMemo(() => Object.values(modules), [modules]);

  const cameras         = moduleList.filter(m => m.type?.includes("camera"));
  const cameraOnline    = cameras.filter(m => m.online !== false).length;
  const cameraRecording = cameras.filter(m => m.status === "RECORDING").length;

  const mics            = moduleList.filter(m => m.type === "microphone");
  const micOnline       = mics.filter(m => m.online !== false).length;
  const micRecording    = mics.filter(m => m.status === "RECORDING").length;

  // ── Config values ────────────────────────────────────────────────────────
  const habitatName = habitatConfig?.name       ?? "Habitat";
  const audioStart  = habitatConfig?.audioStart ?? "—";
  const audioEnd    = habitatConfig?.audioEnd   ?? "—";

  // ── Audio status line ────────────────────────────────────────────────────
  let audioLine;
  if (!audioSession) {
    audioLine = `${micOnline} online · schedule ${audioStart}–${audioEnd}`;
  } else if (audioSession.state === "active") {
    audioLine = `${micRecording} / ${mics.length} recording`;
  } else if (audioSession.state === "scheduled") {
    audioLine = `${micOnline} online · scheduled ${audioStart}–${audioEnd} daily`;
  } else {
    audioLine = `${audioSession.state}`;
  }

  // ── State label ──────────────────────────────────────────────────────────
  let statusLabel, statusClass;
  if (isStarting) {
    statusLabel = "STARTING";
    statusClass = "hrc-badge--starting";
  } else if (isRecording) {
    statusLabel = "● RECORDING";
    statusClass = "hrc-badge--recording";
  } else if (hasFault) {
    statusLabel = "⚠ FAULT";
    statusClass = "hrc-badge--fault";
  } else {
    statusLabel = "READY";
    statusClass = "hrc-badge--ready";
  }

  return (
    <div
      className={`hrc card hrc--clickable ${isRecording || isStarting ? "hrc--active" : hasFault ? "hrc--fault" : ""}`}
      onClick={() => navigate("/recording")}
      title="Go to Recording"
    >
      <div className="hrc-header">
        <div className="hrc-identity">
          <span className="hrc-name">{habitatName}</span>
          <span className={`hrc-badge ${statusClass}`}>{statusLabel}</span>
        </div>
      </div>

      <div className="hrc-summary">
        <div className="hrc-row">
          <span className="hrc-row-label">Cameras</span>
          <span className="hrc-row-value">
            {isRecording || isStarting
              ? <><span className={`hrc-module-dot ${cameraRecording > 0 ? "hrc-module-dot--recording" : "hrc-module-dot--idle"}`} />{cameraRecording} / {cameras.length} recording</>
              : `${cameraOnline} online`
            }
          </span>
        </div>
        <div className="hrc-row">
          <span className="hrc-row-label">Audio</span>
          <span className="hrc-row-value">
            {micRecording > 0 && <span className="hrc-module-dot hrc-module-dot--recording" />}
            {audioLine}
          </span>
        </div>
      </div>

      {isRecording && cameraSession?.start_time && (
        <p className="hrc-since">Recording since {formatTime(cameraSession.start_time)}</p>
      )}
      {hasFault && cameraSession?.error_message && (
        <p className="hrc-fault-msg">{cameraSession.error_message}</p>
      )}
      {startError && (
        <p className="hrc-fault-msg">{startError}</p>
      )}
    </div>
  );
}
