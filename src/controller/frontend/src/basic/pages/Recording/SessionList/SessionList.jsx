import React, { useState, useEffect, useRef } from "react";
import socket from "/src/socket";
import "./SessionList.css";

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function Countdown({ timedStopAt }) {
  const [remaining, setRemaining] = useState(() => Math.max(0, Math.floor(timedStopAt - Date.now() / 1000)));
  useEffect(() => {
    const tick = () => setRemaining(Math.max(0, Math.floor(timedStopAt - Date.now() / 1000)));
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [timedStopAt]);
  if (remaining <= 0) return <span>ending…</span>;
  const h = Math.floor(remaining / 3600);
  const m = Math.floor((remaining % 3600) / 60);
  const s = remaining % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return <span>{h > 0 ? `${h}h ${mm}m ${ss}s` : `${mm}m ${ss}s`}</span>;
}

function formatDuration(minutes) {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h > 0 && m > 0) return `${h}h ${m}m`;
  if (h > 0) return `${h}h`;
  return `${m}m`;
}

function formatScheduledDays(days) {
  if (!days || days.length === 0 || days.length === 7) return "every day";
  return [...days].sort((a, b) => a - b).map(d => DAY_NAMES[d]).join(", ");
}

function AddModuleModal({ sessionName, candidates, onConfirm, onClose }) {
  const [selectedId, setSelectedId] = useState("");
  const selectRef = useRef(null);

  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    selectRef.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const handleConfirm = () => {
    if (!selectedId) return;
    onConfirm(sessionName, selectedId);
    onClose();
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <p>Add a module to <strong>{sessionName}</strong></p>
        <p className="modal-subtext">
          The selected module will begin recording immediately and join the existing session.
        </p>
        <div className="form-field" style={{ marginBottom: "1rem" }}>
          <label>Module:</label>
          <select
            ref={selectRef}
            value={selectedId}
            onChange={e => setSelectedId(e.target.value)}
          >
            <option value="">— select a module —</option>
            {candidates.map(m => (
              <option key={m.id} value={m.id}>{m.name || m.id}</option>
            ))}
          </select>
        </div>
        <div className="modal-buttons">
          <button
            className="save-button"
            type="button"
            disabled={!selectedId}
            onClick={handleConfirm}
          >
            Add to Session
          </button>
          <button className="reset-button" type="button" onClick={onClose}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

function SessionList({ sessionList, modules = [] }) {
  const [expandedSessions, setExpandedSessions] = useState({});
  const [pendingDelete, setPendingDelete] = useState(null);
  const [addModuleTarget, setAddModuleTarget] = useState(null); // session_name | null

  const handleStop = (sessionName) => {
    socket.emit("stop_session", { session_name: sessionName });
  };

  const handleDeleteConfirm = (sessionName) => {
    socket.emit("delete_session", { session_name: sessionName, delete_files: true });
    setPendingDelete(null);
  };

  const handleAddModuleConfirm = (sessionName, moduleId) => {
    socket.emit("add_module_to_session", { session_name: sessionName, module_id: moduleId });
  };

  const toggleExpand = (sessionName) => {
    setExpandedSessions((prev) => ({
      ...prev,
      [sessionName]: !prev[sessionName],
    }));
  };

  const sessions = Object.values(sessionList);

  // Module IDs already committed to an active/error session — not available as candidates.
  const allBusyModuleIds = new Set(
    sessions
      .filter(s => s.state === "active" || s.state === "error")
      .flatMap(s => s.modules)
  );

  const candidates = modules.filter(m => !allBusyModuleIds.has(m.id));

  return (
    <div className="session-list card">
      <div className="session-list__header">
        <h2>Sessions</h2>
        {sessions.length > 0 && (
          <span className="session-list__count">{sessions.length}</span>
        )}
      </div>

      {sessions.length === 0 ? (
        <p className="session-list__empty">No sessions yet — create one to begin recording.</p>
      ) : (
        sessions.map((session) => {
          const state = session.state;
          const isActive    = state === "active";
          const isStopped   = state === "stopped";
          const isError     = state === "error";
          const isScheduled = state === "scheduled";
          const isExpanded  = expandedSessions[session.session_name];

          // A session is "starting" when the controller has created it (active)
          // but no modules have confirmed recording yet.
          const isStarting = isActive && session.modules.length > 0 &&
            !session.modules.some(id => modules.find(m => m.id === id)?.status === "RECORDING");

          // Export summary
          const exportStates   = session.module_export_states || {};
          const exportEntries  = Object.entries(exportStates);
          const pendingExports  = exportEntries.filter(([, s]) => s === "pending").length;
          const completeExports = exportEntries.filter(([, s]) => s === "complete").length;
          const totalComplete   = session.total_exports_complete ?? 0;
          const totalFailed     = session.total_exports_failed ?? 0;
          const activeSegment   = pendingExports > 0 || completeExports > 0;

          // Stop progress
          const stopStates    = session.module_stop_states || {};
          const stillStopping = Object.values(stopStates).filter(s => s === "stopping").length;

          let sessionClass = "session";
          if (isStarting)       sessionClass += " starting";
          else if (isActive)    sessionClass += " active";
          if (isStopped)        sessionClass += " stopped";
          if (isError)          sessionClass += " error";

          return (
            <div key={session.session_name} className={sessionClass}>
              {/* Header row */}
              <div
                className="session-header"
                onClick={() => isStopped && toggleExpand(session.session_name)}
                style={{ cursor: isStopped ? "pointer" : "default" }}
              >
                <div className="session-header__left">
                  {isStarting && (
                    <span className="status-dot status-dot--starting" title="Starting — waiting for modules" />
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
                      <span className="session-state-label session-state-label--past-fault">fault recorded</span>
                    )}
                    {isStopped   && <span className="session-state-label session-state-label--stopped">Stopped</span>}
                    {isScheduled && <span className="session-state-label session-state-label--scheduled">Scheduled</span>}
                    {isError     && <span className="session-state-label session-state-label--error">Error</span>}
                  </div>
                </div>

                <div className="session-header__right">
                  {isStopped && (
                    <span className="session-expand-toggle">{isExpanded ? "▲" : "▼"}</span>
                  )}
                </div>
              </div>

              {/* Details */}
              {(!isStopped || isExpanded) && (
                <div className="session-details">
                  <div className="session-meta-grid">
                    <span className="session-meta-label">Target</span>
                    <span>{session.target}</span>

                    <span className="session-meta-label">Modules</span>
                    <span>{session.modules.join(", ")}</span>

                    <span className="session-meta-label">Start</span>
                    <span>{session.start_time || "—"}</span>

                    {session.end_time && (
                      <>
                        <span className="session-meta-label">End</span>
                        <span>{session.end_time}</span>
                      </>
                    )}

                    {session.timed_stop_at && session.duration_minutes && (
                      <>
                        <span className="session-meta-label">Duration</span>
                        <span>{formatDuration(session.duration_minutes)}</span>
                        {(isActive || isError) && (
                          <>
                            <span className="session-meta-label">Remaining</span>
                            <span><Countdown timedStopAt={session.timed_stop_at} /></span>
                          </>
                        )}
                      </>
                    )}

                    {isScheduled && session.scheduled_start_time && (
                      <>
                        <span className="session-meta-label">Schedule</span>
                        <span>
                          {session.scheduled_start_time} – {session.scheduled_end_time}
                          {", "}
                          {formatScheduledDays(session.scheduled_days)}
                        </span>
                      </>
                    )}
                  </div>

                  {isError && (
                    <p className="session-error-message">{session.error_message}</p>
                  )}

                  {isActive && session.error_time && (
                    <p className="session-past-fault-message">
                      Fault recorded at {session.error_time.replace(
                        /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})$/,
                        "$4:$5"
                      )}{session.error_message ? ` — ${session.error_message}` : ""}
                    </p>
                  )}

                  {stillStopping > 0 && (
                    <p className="session-info-text">
                      Waiting for {stillStopping} module{stillStopping !== 1 ? "s" : ""} to stop…
                    </p>
                  )}

                  {(exportEntries.length > 0 || totalComplete > 0) && (
                    <p className="session-info-text">
                      <strong>Exports:</strong>{" "}
                      {totalComplete} file{totalComplete !== 1 ? "s" : ""} exported
                      {totalFailed > 0 && <span className="session-export-failed">, {totalFailed} failed</span>}
                      {activeSegment && (
                        <span className="session-export-progress">
                          {" "}· {completeExports}/{exportEntries.length} this segment
                          {pendingExports > 0 && `, ${pendingExports} pending`}
                        </span>
                      )}
                    </p>
                  )}

                  <div className="session-actions">
                    {(isActive || isStarting || isError) && (
                      <button
                        className="session-btn session-btn--stop"
                        onClick={() => handleStop(session.session_name)}
                      >
                        End Session
                      </button>
                    )}
                    {isScheduled && (
                      <button
                        className="session-btn session-btn--stop"
                        onClick={() => handleStop(session.session_name)}
                      >
                        Cancel Schedule
                      </button>
                    )}
                    {(isActive || isStarting || isError) && candidates.length > 0 && (
                      <button
                        className="session-btn session-btn--join"
                        onClick={() => setAddModuleTarget(session.session_name)}
                      >
                        Add Module to Session
                      </button>
                    )}
                    {isStopped && (
                      pendingDelete === session.session_name ? (
                        <div className="delete-confirm">
                          <span>Delete session and all files?</span>
                          <button className="session-btn session-btn--delete-confirm" onClick={() => handleDeleteConfirm(session.session_name)}>
                            Yes, delete
                          </button>
                          <button className="session-btn session-btn--cancel" onClick={() => setPendingDelete(null)}>
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <button
                          className="session-btn session-btn--delete"
                          onClick={() => setPendingDelete(session.session_name)}
                        >
                          Delete
                        </button>
                      )
                    )}
                  </div>
                </div>
              )}
            </div>
          );
        })
      )}

      {addModuleTarget && (
        <AddModuleModal
          sessionName={addModuleTarget}
          candidates={candidates}
          onConfirm={handleAddModuleConfirm}
          onClose={() => setAddModuleTarget(null)}
        />
      )}
    </div>
  );
}

export default SessionList;
