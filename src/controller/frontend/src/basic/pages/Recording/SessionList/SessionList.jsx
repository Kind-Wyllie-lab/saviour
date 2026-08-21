import React, { useState, useEffect } from "react";
import { useNavigate } from "react-router";
import socket from "/src/socket";
import { formatFaultTime, formatScheduledDays, formatTarget, formatRecordingMode } from "../sessionFormat";
import { Countdown, Elapsed } from "../sessionFormatComponents";
import "./SessionList.css";

// A narrow, always-visible selector rail (RecordingLayout renders it next
// to the routed detail pane) -- rows are compact on purpose, since this
// column stays this width regardless of viewport size. All actions
// (Stop/Start Now/Retry Now/Delete/Retry Export/Add Module) live on
// SessionDetailPage now, reached by clicking a row -- selecting a session
// is cheap here (no navigation away from the list), so duplicating quick
// actions onto the row as well would just be clutter in a column this
// narrow.
function SessionList({ sessionList, modules = [], onNewSession, selectedSessionName }) {
  const navigate = useNavigate();
  const [pendingClearAll, setPendingClearAll] = useState(false);
  const [clearAllWarning, setClearAllWarning] = useState(null); // { message, skippedSessions } | null

  useEffect(() => {
    const handler = (data) => {
      if (!data.export_warning || !data.skipped_sessions) return;
      // Bulk clear was partially refused — offer a force-clear follow-up.
      // (A single-session delete refusal is handled on SessionDetailPage now.)
      setClearAllWarning({ message: data.error, skippedSessions: data.skipped_sessions });
    };
    socket.on("session_error", handler);
    return () => socket.off("session_error", handler);
  }, []);

  const handleClearAllConfirm = () => {
    socket.emit("clear_ended_sessions", { delete_files: true });
    setPendingClearAll(false);
    setClearAllWarning(null);
  };

  const handleForceClearAllConfirm = () => {
    socket.emit("clear_ended_sessions", { delete_files: true, force: true });
    setClearAllWarning(null);
  };

  const sessions = Object.values(sessionList);
  // "Ended" = stopped, or errored-and-not-scheduled-to-retry -- explicitly
  // not pending/active/scheduled. A PENDING session hasn't ended, it just
  // hasn't started yet; "Clear all ended" must never sweep those up.
  const endedSessions = sessions.filter(
    s => s.state !== "pending" && s.state !== "active" && s.state !== "error" && s.state !== "scheduled"
  );

  const goToSession = (sessionName) => {
    navigate(`/recording/sessions/${encodeURIComponent(sessionName)}`);
  };

  return (
    <div className="session-list card">
      <div className="session-list__header">
        <h2>Sessions</h2>
        {sessions.length > 0 && (
          <span className="session-list__count">{sessions.length}</span>
        )}
        {onNewSession && (
          <button
            type="button"
            className="session-list__new-session-btn"
            onClick={onNewSession}
          >
            + New Session
          </button>
        )}
      </div>

      {clearAllWarning && (
        <div className="session-list__export-warning">
          <span>
            ⚠ {clearAllWarning.message}
          </span>
          <button
            type="button"
            className="session-btn session-btn--delete-confirm"
            onClick={handleForceClearAllConfirm}
          >
            Force clear {clearAllWarning.skippedSessions.length} anyway
          </button>
          <button
            type="button"
            className="session-btn session-btn--cancel"
            onClick={() => setClearAllWarning(null)}
          >
            Dismiss
          </button>
        </div>
      )}

      {sessions.length === 0 ? (
        <p className="session-list__empty">No sessions yet - create one to begin recording.</p>
      ) : (
        sessions.map((session) => {
          const state = session.state;
          const isPending   = state === "pending";
          const isActive    = state === "active";
          const isStopped   = state === "stopped";
          const isError     = state === "error";
          const isScheduled = state === "scheduled";
          const isSelected  = session.session_name === selectedSessionName;

          // A session is "starting" when the controller has created it (active)
          // but no modules have confirmed recording yet.
          const isStarting = isActive && session.modules.length > 0 &&
            !session.modules.some(id => modules.find(m => m.id === id)?.status === "RECORDING");

          const totalComplete = session.total_exports_complete ?? 0;
          const totalFailed   = session.total_exports_failed ?? 0;

          let sessionClass = "session";
          if (isStarting)       sessionClass += " starting";
          else if (isActive)    sessionClass += " active";
          if (isPending)        sessionClass += " pending";
          if (isStopped)        sessionClass += " stopped";
          if (isError)          sessionClass += " error";
          if (isSelected)       sessionClass += " session--selected";

          return (
            <div
              key={session.session_name}
              className={sessionClass}
              onClick={() => goToSession(session.session_name)}
              role="button"
              tabIndex={0}
              aria-current={isSelected ? "true" : undefined}
              onKeyDown={(e) => { if (e.key === "Enter") goToSession(session.session_name); }}
            >
              <div className="session-row">
                <span className="status-dot--wrap">
                  {isPending && (
                    <span className="status-dot status-dot--pending" title="Pending - not started yet" />
                  )}
                  {isStarting && (
                    <span className="status-dot status-dot--starting" title="Starting - waiting for modules" />
                  )}
                  {isActive && !isStarting && (
                    <span className="status-dot status-dot--recording" title="Recording" />
                  )}
                  {isError && (
                    <span className="status-dot status-dot--error" title={session.error_message} />
                  )}
                  {isScheduled && (
                    <span className="status-dot status-dot--scheduled" title="Scheduled" />
                  )}
                  {isStopped && (
                    <span className="status-dot status-dot--stopped" title="Stopped" />
                  )}
                </span>

                <div className="session-row__main">
                  <div className="session-header__name">
                    <span className="session-name">{session.session_name}</span>
                    {isPending   && <span className="session-state-label session-state-label--pending">Pending</span>}
                    {isStarting  && <span className="session-state-label session-state-label--starting">Starting…</span>}
                    {isActive && !isStarting && <span className="session-state-label session-state-label--recording">Recording</span>}
                    {isActive && !isStarting && session.error_time && (
                      <span className="session-state-label session-state-label--past-fault">
                        fault {formatFaultTime(session.error_time)}
                      </span>
                    )}
                    {isStopped   && <span className="session-state-label session-state-label--stopped">Stopped</span>}
                    {isScheduled && <span className="session-state-label session-state-label--scheduled">Scheduled</span>}
                    {isError     && <span className="session-state-label session-state-label--error">Error</span>}
                  </div>

                  <div className="session-row__type">
                    {formatTarget(session.target)} · {formatRecordingMode(session)}
                  </div>

                  <div className="session-row__summary">
                    {isPending && <span>Not started yet</span>}
                    {isActive && !isStarting && (
                      session.timed_stop_at
                        ? <span><Countdown timedStopAt={session.timed_stop_at} /> left</span>
                        : <Elapsed startTime={session.start_time} />
                    )}
                    {isError && session.error_message && (
                      <span className="session-row__error-text" title={session.error_message}>{session.error_message}</span>
                    )}
                    {isScheduled && session.scheduled_start_time && (
                      <span>{session.scheduled_start_time} – {session.scheduled_end_time}, {formatScheduledDays(session.scheduled_days)}</span>
                    )}
                    {isStopped && (totalComplete > 0 || totalFailed > 0) && (
                      <span>
                        {totalComplete} exported
                        {totalFailed > 0 && <span className="session-export-failed">, {totalFailed} failed</span>}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            </div>
          );
        })
      )}

      {endedSessions.length > 0 && (
        pendingClearAll ? (
          <span className="session-list__clear-all-confirm">
            Clear {endedSessions.length} ended session{endedSessions.length !== 1 ? "s" : ""}?
            <button type="button" className="session-btn session-btn--delete-confirm" onClick={handleClearAllConfirm}>Yes</button>
            <button type="button" className="session-btn session-btn--cancel" onClick={() => setPendingClearAll(false)}>No</button>
          </span>
        ) : (
          <button
            type="button"
            className="session-list__clear-all-btn"
            onClick={() => setPendingClearAll(true)}
          >
            Clear all ended
          </button>
        )
      )}
    </div>
  );
}

export default SessionList;
