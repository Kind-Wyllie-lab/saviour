import React, { useState, useEffect } from "react";
import { useNavigate } from "react-router";
import socket from "/src/socket";
import { formatFaultTime, formatScheduledDays } from "../sessionFormat";
import { Countdown, Elapsed } from "../sessionFormatComponents";
import "./SessionList.css";

// Compact, clickable rows only -- everything else (files, share notice,
// events log, module-recording-state table, Delete/Retry Export/Add
// Module) lives in SessionDetailPage now, reached by clicking a row. See
// CLAUDE.md's "Recording page" entries for why: a session card that
// expanded in place got cramped once its own detail grew this rich, and
// didn't scale to a long list of habitat sessions either.
function SessionList({ sessionList, modules = [], onNewSession }) {
  const navigate = useNavigate();
  const [pendingClearAll, setPendingClearAll] = useState(false);
  const [clearAllWarning, setClearAllWarning] = useState(null); // { message, skippedSessions } | null
  const [forceStartErrors, setForceStartErrors] = useState({}); // session_name → error string

  useEffect(() => {
    const handler = ({ session_name, success, error }) => {
      if (!success && session_name && error) {
        setForceStartErrors(prev => ({ ...prev, [session_name]: error }));
        setTimeout(() => {
          setForceStartErrors(prev => {
            const next = { ...prev };
            delete next[session_name];
            return next;
          });
        }, 8000);
      }
    };
    socket.on("force_start_result", handler);
    return () => socket.off("force_start_result", handler);
  }, []);

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

  const handleStop = (sessionName) => {
    socket.emit("stop_session", { session_name: sessionName });
  };

  const handleForceStart = (sessionName) => {
    socket.emit("force_start_session", { session_name: sessionName });
  };

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
  const endedSessions = sessions.filter(s => s.state !== "active" && s.state !== "error" && s.state !== "scheduled");

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
        {endedSessions.length > 0 && !pendingClearAll && (
          <button
            type="button"
            className="session-list__clear-all-btn"
            onClick={() => setPendingClearAll(true)}
          >
            Clear all ended
          </button>
        )}
        {pendingClearAll && (
          <span className="session-list__clear-all-confirm">
            Clear {endedSessions.length} ended session{endedSessions.length !== 1 ? "s" : ""}?
            <button type="button" className="session-btn session-btn--delete-confirm" onClick={handleClearAllConfirm}>Yes</button>
            <button type="button" className="session-btn session-btn--cancel" onClick={() => setPendingClearAll(false)}>No</button>
          </span>
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
          const isActive    = state === "active";
          const isStopped   = state === "stopped";
          const isError     = state === "error";
          const isScheduled = state === "scheduled";

          // A session is "starting" when the controller has created it (active)
          // but no modules have confirmed recording yet.
          const isStarting = isActive && session.modules.length > 0 &&
            !session.modules.some(id => modules.find(m => m.id === id)?.status === "RECORDING");

          const totalComplete = session.total_exports_complete ?? 0;
          const totalFailed   = session.total_exports_failed ?? 0;

          let sessionClass = "session";
          if (isStarting)       sessionClass += " starting";
          else if (isActive)    sessionClass += " active";
          if (isStopped)        sessionClass += " stopped";
          if (isError)          sessionClass += " error";

          return (
            <div
              key={session.session_name}
              className={sessionClass}
              onClick={() => goToSession(session.session_name)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => { if (e.key === "Enter") goToSession(session.session_name); }}
            >
              <div className="session-row">
                <div className="session-row__left">
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

                  <div className="session-header__name">
                    <span className="session-name">{session.session_name}</span>
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
                </div>

                <div className="session-row__summary">
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
                  {forceStartErrors[session.session_name] && (
                    <span className="session-row__error-text">{forceStartErrors[session.session_name]}</span>
                  )}
                </div>

                <div className="session-row__actions" onClick={(e) => e.stopPropagation()}>
                  {(isActive || isStarting || isError) && (
                    <button
                      className="session-btn session-btn--stop"
                      onClick={() => handleStop(session.session_name)}
                    >
                      End Session
                    </button>
                  )}
                  {isScheduled && (
                    <>
                      <button
                        className="session-btn session-btn--start"
                        onClick={() => handleForceStart(session.session_name)}
                        title="Start recording now, bypassing the scheduled time window"
                      >
                        Start Now
                      </button>
                      <button
                        className="session-btn session-btn--stop"
                        onClick={() => handleStop(session.session_name)}
                      >
                        Cancel Schedule
                      </button>
                    </>
                  )}
                  {isError && session.scheduled && (
                    <button
                      className="session-btn session-btn--start"
                      onClick={() => handleForceStart(session.session_name)}
                      title="Retry this scheduled session now"
                    >
                      Retry Now
                    </button>
                  )}
                </div>
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}

export default SessionList;
