import React, { useState, useEffect, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import useSessions from "/src/hooks/useSessions";
import useModules from "/src/hooks/useModules";
import useHealth from "/src/hooks/useHealth";
import "./RecordingStatusWidget.css";

function parseTimestamp(str) {
  if (!str) return null;
  const m = str.match(/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})$/);
  if (!m) return null;
  return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]);
}

function formatElapsed(totalSeconds) {
  const h = Math.floor(totalSeconds / 3600);
  const min = Math.floor((totalSeconds % 3600) / 60);
  const sec = totalSeconds % 60;
  const mm = String(min).padStart(2, "0");
  const ss = String(sec).padStart(2, "0");
  return h > 0 ? `${h}h ${mm}m ${ss}s` : `${mm}m ${ss}s`;
}

function formatTime(date) {
  if (!date) return "—";
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

function Countdown({ timedStopAt }) {
  const [remaining, setRemaining] = useState(() => Math.max(0, Math.floor(timedStopAt - Date.now() / 1000)));
  useEffect(() => {
    const id = setInterval(() => setRemaining(Math.max(0, Math.floor(timedStopAt - Date.now() / 1000))), 1000);
    return () => clearInterval(id);
  }, [timedStopAt]);
  if (remaining <= 0) return <span className="rsw-countdown"> · ending…</span>;
  const h = Math.floor(remaining / 3600);
  const m = Math.floor((remaining % 3600) / 60);
  const s = remaining % 60;
  return <span className="rsw-countdown"> · {h > 0 ? `${h}h ${String(m).padStart(2,"0")}m ${String(s).padStart(2,"0")}s` : `${String(m).padStart(2,"0")}m ${String(s).padStart(2,"0")}s`} left</span>;
}

function SessionEntry({ session, modules, moduleHealth }) {
  const [elapsed, setElapsed] = useState(0);
  const isError = session.state === "error";
  const hasPastFault = !isError && !!session.error_time;

  useEffect(() => {
    const startDate = parseTimestamp(session.start_time);
    const tick = () => setElapsed(!startDate ? 0 : Math.max(0, Math.floor((Date.now() - startDate.getTime()) / 1000)));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [session]);

  const moduleStatuses = (session.modules ?? []).map((id) => {
    const mod = modules[id];
    const health = moduleHealth[id];
    const isRecording = mod?.status === "RECORDING";
    const isOffline = (!mod && !health) || health?.status === "offline" || health?.status === "suspected";
    let dotClass;
    if (isError && (isOffline || !isRecording)) dotClass = "rsw-dot--fault";
    else if (isOffline) dotClass = "rsw-dot--offline";
    else if (isRecording) dotClass = "rsw-dot--recording";
    else dotClass = "rsw-dot--ready";
    return { id, name: mod?.name ?? id, dotClass };
  });

  const faultDate = parseTimestamp(session.error_time);

  return (
    <span className={`rsw-session ${isError ? "rsw-session--error" : hasPastFault ? "rsw-session--recovered" : ""}`}>
      <span className={`rsw-dot ${isError ? "rsw-dot--fault rsw-dot--pulse" : "rsw-dot--recording rsw-dot--pulse"}`} />
      <span className="rsw-session-name">{session.session_name}</span>
      {isError ? (
        <span className="rsw-fault-label">Fault at {formatTime(faultDate)}</span>
      ) : (
        <span className="rsw-elapsed" title={hasPastFault ? `Fault at ${formatTime(faultDate)}` : undefined}>
          {hasPastFault ? "Recording" : "Without issue"}: {formatElapsed(elapsed)}
          {hasPastFault && <span className="rsw-past-fault"> · fault at {formatTime(faultDate)}</span>}
          {session.timed_stop_at && <Countdown timedStopAt={session.timed_stop_at} />}
        </span>
      )}
      <span className="rsw-modules">
        {moduleStatuses.map(({ id, name, dotClass }) => (
          <span key={id} className="rsw-module-status" title={id}>
            <span className={`rsw-dot ${dotClass}`} />
            <span className="rsw-module-name">{name}</span>
          </span>
        ))}
      </span>
    </span>
  );
}

export default function RecordingStatusWidget() {
  const navigate = useNavigate();
  const { sessionList } = useSessions();
  const { modules } = useModules();
  const { moduleHealth } = useHealth();

  const moduleList = useMemo(() => Object.values(modules), [modules]);
  const cameras    = moduleList.filter(m => m.type?.includes("camera"));
  const mics       = moduleList.filter(m => m.type === "microphone");

  const visibleSessions = useMemo(
    () => sessionList.filter(s => s.state === "active" || s.state === "error"),
    [sessionList]
  );

  const hasError   = visibleSessions.some(s => s.state === "error");
  const hasActive  = visibleSessions.some(s => s.state === "active");
  const hasPartial = !hasError && visibleSessions.some(s => s.state !== "error" && s.error_time);

  let stateClass, stateLabel, dotVariant;
  if (hasError) {
    stateClass = "hrc--fault";    stateLabel = "Fault";     dotVariant = "fault";
  } else if (hasPartial) {
    stateClass = "hrc--starting"; stateLabel = "Recording"; dotVariant = "starting";
  } else if (hasActive) {
    stateClass = "hrc--recording"; stateLabel = "Recording"; dotVariant = "recording";
  } else {
    stateClass = "hrc--ready";   stateLabel = "Ready";     dotVariant = "ready";
  }

  const cameraRecording = cameras.filter(m => m.status === "RECORDING").length;
  const micRecording    = mics.filter(m => m.status === "RECORDING").length;
  const cameraOnline    = cameras.filter(m => m.online !== false).length;
  const micOnline       = mics.filter(m => m.online !== false).length;

  const cameraStr = hasActive
    ? `${cameraRecording}/${cameras.length} cameras`
    : `${cameraOnline} cameras`;
  const audioStr = hasActive
    ? `${micRecording}/${mics.length} audio`
    : `${micOnline} audio`;

  return (
    <div
      className={`recording-status-widget card hrc ${stateClass}`}
      onClick={() => navigate("/recording")}
    >
      <div className="hrc-bar">
        <span className={`hrc-dot hrc-dot--${dotVariant}`} />
        <span className="hrc-name">{document.title}</span>
        <span className={`hrc-state hrc-state--${dotVariant}`}>{stateLabel}</span>

        {visibleSessions.length > 0 && (
          <>
            <span className="hrc-sep">·</span>
            <span className="rsw-sessions-inline">
              {visibleSessions.map((session, i) => (
                <React.Fragment key={session.session_name}>
                  {i > 0 && <span className="rsw-divider" />}
                  <SessionEntry session={session} modules={modules} moduleHealth={moduleHealth} />
                </React.Fragment>
              ))}
            </span>
          </>
        )}

        <span className="hrc-spacer" />
        <span className="hrc-stat">{cameraStr}</span>
        <span className="hrc-sep">·</span>
        <span className="hrc-stat">{audioStr}</span>
      </div>
    </div>
  );
}
