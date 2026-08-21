import React, { useState, useEffect, useMemo } from "react";
import { useNavigate } from "react-router";
import socket from "/src/socket";
import "./HabitatRecordingControl.css";

function formatTime(t) {
  if (!t) return "-";
  const m = t.match(/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/);
  if (!m) return t;
  const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  return `${parseInt(m[3])} ${months[parseInt(m[2]) - 1]}, ${m[4]}:${m[5]}`;
}

// Below this many seconds remaining, the countdown switches to its "soon"
// styling (see .hrc-countdown--soon) so an imminent stop is visually
// distinct from routine "time left" text, not just a smaller number in the
// same style.
const COUNTDOWN_SOON_SECS = 60;

// Live-ticking "time left" for a timed (duration-limited) session, so it's
// clear from the top banner alone when a recording is about to stop rather
// than only via the elapsed "since" time. Mirrors RecordingStatusWidget's
// own Countdown (basic variant) -- kept as a separate small copy rather
// than a shared import since the two widgets have unrelated layouts and
// this is a handful of lines.
function Countdown({ timedStopAt }) {
  const [remaining, setRemaining] = useState(() => Math.max(0, Math.floor(timedStopAt - Date.now() / 1000)));
  useEffect(() => {
    const id = setInterval(() => setRemaining(Math.max(0, Math.floor(timedStopAt - Date.now() / 1000))), 1000);
    return () => clearInterval(id);
  }, [timedStopAt]);
  if (remaining <= 0) {
    return <span className="hrc-countdown hrc-countdown--soon"> · ending…</span>;
  }
  const h = Math.floor(remaining / 3600);
  const m = Math.floor((remaining % 3600) / 60);
  const s = remaining % 60;
  const label = h > 0
    ? `${h}h ${String(m).padStart(2, "0")}m ${String(s).padStart(2, "0")}s`
    : `${String(m).padStart(2, "0")}m ${String(s).padStart(2, "0")}s`;
  return (
    <span className={`hrc-countdown ${remaining <= COUNTDOWN_SOON_SECS ? "hrc-countdown--soon" : ""}`}>
      {" "}· {label} left
    </span>
  );
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
    // "Idle", not "Ready" — module.status === "READY" is a separate, per-module
    // concept (passed pre-flight checks); this just means no session is
    // active/starting/faulted right now.
    stateClass = "hrc--ready"; stateLabel = "Idle";
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
            <span className="hrc-since">
              since {formatTime(cameraSession.start_time)}
              {cameraSession.timed_stop_at && <Countdown timedStopAt={cameraSession.timed_stop_at} />}
            </span>
          </>
        )}
        {audioSession?.state === "active" && audioSession.timed_stop_at && (
          <>
            <span className="hrc-sep">·</span>
            <span className="hrc-since">audio<Countdown timedStopAt={audioSession.timed_stop_at} /></span>
          </>
        )}
        {(hasFault || startError) && (
          <span className="hrc-fault-inline">{startError || cameraSession?.error_message}</span>
        )}
      </div>
    </div>
  );
}
