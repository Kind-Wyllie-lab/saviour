import React, { useState, useEffect, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import useSessions from "/src/hooks/useSessions";
import useModules from "/src/hooks/useModules";
import useHealth from "/src/hooks/useHealth";
import "./RecordingStatusWidget.css";

// "20250811-143215" -> Date object (local time)
function parseStartTime(str) {
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

function SessionEntry({ session, modules, moduleHealth }) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const startDate = parseStartTime(session.start_time);
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
    const isOffline = health?.status === "offline" || health?.status === "suspected";
    const dotClass = isOffline
      ? "rsw-dot--offline"
      : isRecording
      ? "rsw-dot--recording"
      : "rsw-dot--ready";
    return { id, name: mod?.name ?? id, dotClass };
  });

  const allRecording = moduleStatuses.every((m) => m.dotClass === "rsw-dot--recording");

  return (
    <span className={`rsw-session ${allRecording ? "" : "rsw-session--partial"}`}>
      <span className="rsw-dot rsw-dot--recording rsw-dot--pulse" />
      <span className="rsw-session-name">{session.session_name}</span>
      <span className="rsw-elapsed">{formatElapsed(elapsed)}</span>
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

  const activeSessions = useMemo(
    () => sessionList.filter((s) => s.state === "active"),
    [sessionList]
  );

  if (activeSessions.length === 0) {
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
      className="recording-status-widget recording-status-widget--active"
      onClick={() => navigate("/recording")}
      style={{ cursor: "pointer" }}
    >
      {activeSessions.map((session, i) => (
        <React.Fragment key={session.session_name}>
          {i > 0 && <span className="rsw-divider" />}
          <SessionEntry session={session} modules={modules} moduleHealth={moduleHealth} />
        </React.Fragment>
      ))}
    </div>
  );
}
