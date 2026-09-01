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
//
// A session that hits a mid-recording fault (a module dropping out / a dead
// capture thread) flips to state "error" but is STILL recording on every
// other module — so the timer stays up, just with a fault treatment. Only a
// genuinely stopped/absent session blanks it. (RecordingStatusWidget's
// topbar already treats "active" and "error" the same way; this matches it.)
export default function LoomRecordingTimer() {
  const { sessionList } = useSessions();
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const liveSessions = useMemo(
    () => sessionList.filter((s) => s.state === "active" || s.state === "error"),
    [sessionList]
  );
  const extraCount = Math.max(0, liveSessions.length - 1);
  // Prefer a cleanly-active session for the headline slot; fall back to a
  // faulted one so a single-session rig still shows its timer during a fault.
  const session =
    liveSessions.find((s) => s.state === "active") ?? liveSessions[0] ?? null;

  if (!session) {
    return (
      <div className="loom-timer card loom-timer--idle">
        <span className="loom-timer-clock">--:--</span>
        <span className="loom-timer-label">Not recording</span>
      </div>
    );
  }

  const isFault = session.state === "error";

  const startDate = parseTimestamp(session.start_time);
  const elapsedSeconds = startDate ? Math.max(0, (nowMs - startDate.getTime()) / 1000) : 0;

  const isTimed = !!session.timed_stop_at && !!startDate;
  const totalSeconds = isTimed ? Math.max(1, session.timed_stop_at - startDate.getTime() / 1000) : null;
  const remainingSeconds = isTimed ? Math.max(0, session.timed_stop_at - nowMs / 1000) : null;
  const progressPct = isTimed ? Math.min(100, (elapsedSeconds / totalSeconds) * 100) : null;
  const nearEnd = isTimed && remainingSeconds <= 60;

  const stateClass = isFault
    ? " loom-timer--fault"
    : nearEnd
    ? " loom-timer--near-end"
    : "";

  return (
    <div className={`loom-timer card loom-timer--recording${stateClass}`}>
      <div className="loom-timer-top">
        <span className="loom-timer-dot" />
        <span className="loom-timer-session-name" title={session.session_name}>
          {session.session_name}
        </span>
        {extraCount > 0 && <span className="loom-timer-extra">+{extraCount} more</span>}
      </div>
      <span className="loom-timer-clock">{formatClock(elapsedSeconds)}</span>
      {isFault && (
        <span className="loom-timer-label loom-timer-label--fault" title={session.error_message || ""}>
          {session.error_message || "Module fault — recording continues on other modules"}
        </span>
      )}
      {isTimed ? (
        <>
          <div className="loom-timer-bar">
            <div className="loom-timer-bar-fill" style={{ width: `${progressPct}%` }} />
          </div>
          <span className="loom-timer-label">{formatClock(remainingSeconds)} remaining</span>
        </>
      ) : (
        !isFault && <span className="loom-timer-label">Elapsed - manual stop</span>
      )}
    </div>
  );
}
