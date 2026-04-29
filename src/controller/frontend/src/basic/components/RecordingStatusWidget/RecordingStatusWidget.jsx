import React, { useState, useEffect, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import useSessions from "/src/hooks/useSessions";
import useModules from "/src/hooks/useModules";
import useHealth from "/src/hooks/useHealth";
import "./RecordingStatusWidget.css";

// "20250811-143215" -> Date object (local time)
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
  const hh = String(date.getHours()).padStart(2, "0");
  const mm = String(date.getMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}

function SessionEntry({ session, modules, moduleHealth }) {
  const [elapsed, setElapsed] = useState(0);

  const isError = session.state === "error";
  const hasPastFault = !isError && !!session.error_time;

  useEffect(() => {
    const startDate = parseTimestamp(session.start_time);
    const tick = () => {
      if (!startDate) { setElapsed(0); return; }
      setElapsed(Math.max(0, Math.floor((Date.now() - startDate.getTime()) / 1000)));
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [session]);

  const moduleStatuses = (session.modules ?? []).map((id) => {
    const mod = modules[id];
    const health = moduleHealth[id];
    const isRecording = mod?.status === "RECORDING";
    const isOffline = !mod && !health  // not in discovery data at all
      || health?.status === "offline"
      || health?.status === "suspected";
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
        <span className="rsw-fault-label">
          Fault at {formatTime(faultDate)}
        </span>
      ) : (
        <span className="rsw-elapsed" title={hasPastFault ? `Fault at ${formatTime(faultDate)}` : undefined}>
          {hasPastFault ? "Recording" : "Without issue"}: {formatElapsed(elapsed)}
          {hasPastFault && (
            <span className="rsw-past-fault"> · fault at {formatTime(faultDate)}</span>
          )}
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

  const visibleSessions = useMemo(
    () => sessionList.filter((s) => s.state === "active" || s.state === "error"),
    [sessionList]
  );

  const hasError   = visibleSessions.some((s) => s.state === "error");
  const hasPartial = !hasError && visibleSessions.some((s) => s.state !== "error" && s.error_time);

  if (visibleSessions.length === 0) {
    return (
      <div
        className="recording-status-widget recording-status-widget--idle"
        onClick={() => navigate("/recording")}
        style={{ cursor: "pointer" }}
      >
        <span className="rsw-dot rsw-dot--idle" />
        <span className="rsw-idle-text">No active session</span>
      </div>
    );
  }

  return (
    <div
      className={`recording-status-widget ${hasError ? "recording-status-widget--error" : hasPartial ? "recording-status-widget--partial" : "recording-status-widget--active"}`}
      onClick={() => navigate("/recording")}
      style={{ cursor: "pointer" }}
    >
      {visibleSessions.map((session, i) => (
        <React.Fragment key={session.session_name}>
          {i > 0 && <span className="rsw-divider" />}
          <SessionEntry session={session} modules={modules} moduleHealth={moduleHealth} />
        </React.Fragment>
      ))}
    </div>
  );
}
