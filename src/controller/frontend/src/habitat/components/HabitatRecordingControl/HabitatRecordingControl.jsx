import React, { useState, useEffect, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import socket from "/src/socket";
import "./HabitatRecordingControl.css";

function formatTime(t) {
  if (!t) return "—";
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
      setTimeout(() => setStartError(null), 10000);
    };
    socket.on("session_error", onError);
    return () => socket.off("session_error", onError);
  }, []);

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

  const moduleList      = useMemo(() => Object.values(modules), [modules]);
  const cameras         = moduleList.filter(m => m.type?.includes("camera"));
  const cameraOnline    = cameras.filter(m => m.online !== false).length;
  const cameraRecording = cameras.filter(m => m.status === "RECORDING").length;
  const mics            = moduleList.filter(m => m.type === "microphone");
  const micOnline       = mics.filter(m => m.online !== false).length;
  const micRecording    = mics.filter(m => m.status === "RECORDING").length;

  const habitatName = habitatConfig?.name ?? "Habitat";

  let stateClass, stateLabel;
  if (isStarting) {
    stateClass = "hrc--starting"; stateLabel = "Starting";
  } else if (isRecording) {
    stateClass = "hrc--recording"; stateLabel = "Recording";
  } else if (hasFault) {
    stateClass = "hrc--fault"; stateLabel = "Fault";
  } else {
    stateClass = "hrc--ready"; stateLabel = "Ready";
  }

  const cameraStr = isRecording || isStarting
    ? `${cameraRecording}/${cameras.length} cameras`
    : `${cameraOnline} cameras`;

  let audioStr;
  if (!audioSession) {
    audioStr = `${micOnline} audio`;
  } else if (audioSession.state === "active") {
    audioStr = `${micRecording}/${mics.length} audio`;
  } else if (audioSession.state === "scheduled") {
    audioStr = `${micOnline} audio (scheduled)`;
  } else {
    audioStr = `${micOnline} audio`;
  }

  return (
    <div
      className={`hrc card hrc--clickable ${stateClass}`}
      onClick={() => navigate("/recording")}
      title="Go to Recording"
    >
      <div className="hrc-bar">
        <span className={`hrc-dot hrc-dot--${isStarting ? "starting" : isRecording ? "recording" : hasFault ? "fault" : "ready"}`} />
        <span className="hrc-name">{habitatName}</span>
        <span className={`hrc-state hrc-state--${isStarting ? "starting" : isRecording ? "recording" : hasFault ? "fault" : "ready"}`}>
          {stateLabel}
        </span>
        <span className="hrc-spacer" />
        <span className="hrc-stat">{cameraStr}</span>
        <span className="hrc-sep">·</span>
        <span className="hrc-stat">{audioStr}</span>
        {isRecording && cameraSession?.start_time && (
          <>
            <span className="hrc-sep">·</span>
            <span className="hrc-since">since {formatTime(cameraSession.start_time)}</span>
          </>
        )}
        {(hasFault || startError) && (
          <span className="hrc-fault-inline">{startError || cameraSession?.error_message}</span>
        )}
      </div>
    </div>
  );
}
