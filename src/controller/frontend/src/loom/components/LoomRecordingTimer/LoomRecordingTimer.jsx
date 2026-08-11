import React, { useEffect, useMemo, useState } from "react";
import useSessions from "/src/hooks/useSessions";
import "./LoomRecordingTimer.css";

function parseTimestamp(str) {
  if (!str) return null;
  const m = str.match(/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})$/);
  if (!m) return null;
  return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]);
}

function formatClock(totalSeconds) {
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(sec).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

// Big, at-a-glance elapsed-time readout for whichever session is currently
// recording — meant to be readable from across the room, unlike the small
// inline text in RecordingStatusWidget's topbar. Shows a fill bar toward
// timed_stop_at when the session is a timed recording.
export default function LoomRecordingTimer() {
  const { sessionList } = useSessions();
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const activeSessions = useMemo(
    () => sessionList.filter((s) => s.state === "active"),
    [sessionList]
  );
  const extraCount = Math.max(0, activeSessions.length - 1);
  const session = activeSessions[0] ?? null;

  if (!session) {
    return (
      <div className="loom-timer card loom-timer--idle">
        <span className="loom-timer-clock">--:--</span>
        <span className="loom-timer-label">Not recording</span>
      </div>
    );
  }

  const startDate = parseTimestamp(session.start_time);
  const elapsedSeconds = startDate ? Math.max(0, (nowMs - startDate.getTime()) / 1000) : 0;

  const isTimed = !!session.timed_stop_at && !!startDate;
  const totalSeconds = isTimed ? Math.max(1, session.timed_stop_at - startDate.getTime() / 1000) : null;
  const remainingSeconds = isTimed ? Math.max(0, session.timed_stop_at - nowMs / 1000) : null;
  const progressPct = isTimed ? Math.min(100, (elapsedSeconds / totalSeconds) * 100) : null;
  const nearEnd = isTimed && remainingSeconds <= 60;

  return (
    <div className={`loom-timer card loom-timer--recording${nearEnd ? " loom-timer--near-end" : ""}`}>
      <div className="loom-timer-top">
        <span className="loom-timer-dot" />
        <span className="loom-timer-session-name" title={session.session_name}>
          {session.session_name}
        </span>
        {extraCount > 0 && <span className="loom-timer-extra">+{extraCount} more</span>}
      </div>
      <span className="loom-timer-clock">{formatClock(elapsedSeconds)}</span>
      {isTimed ? (
        <>
          <div className="loom-timer-bar">
            <div className="loom-timer-bar-fill" style={{ width: `${progressPct}%` }} />
          </div>
          <span className="loom-timer-label">{formatClock(remainingSeconds)} remaining</span>
        </>
      ) : (
        <span className="loom-timer-label">Elapsed - manual stop</span>
      )}
    </div>
  );
}
