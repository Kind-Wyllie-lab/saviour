import React, { useState } from "react";
import socket from "/src/socket";
import "./SessionList.css";

function SessionList({ sessionList }) {
  const [expandedSessions, setExpandedSessions] = useState({});

  const handleStop = (sessionName) => {
    socket.emit("stop_session", { session_name: sessionName });
  };

  const toggleExpand = (sessionName) => {
    setExpandedSessions((prev) => ({
      ...prev,
      [sessionName]: !prev[sessionName],
    }));
  };

  const sessions = Object.values(sessionList);

  return (
    <div className="session-list card">
      <h2>Session List</h2>

      {sessions.length === 0 ? (
        <p>No sessions yet</p>
      ) : (
        sessions.map((session) => {
          const state = session.state;           // "active" | "stopped" | "error" | "scheduled"
          const isActive    = state === "active";
          const isStopped   = state === "stopped";
          const isError     = state === "error";
          const isScheduled = state === "scheduled";
          const isExpanded  = expandedSessions[session.session_name];

          // Per-module export summary
          const exportStates = session.module_export_states || {};
          const exportEntries = Object.entries(exportStates);
          const pendingExports  = exportEntries.filter(([, s]) => s === "pending").length;
          const failedExports   = exportEntries.filter(([, s]) => s === "failed").length;
          const completeExports = exportEntries.filter(([, s]) => s === "complete").length;

          // Per-module stop summary for the stopping phase
          const stopStates = session.module_stop_states || {};
          const stillStopping = Object.values(stopStates).filter(s => s === "stopping").length;

          return (
            <div
              key={session.session_name}
              className={`session ${isStopped ? "stopped" : ""} ${isError ? "error" : ""}`}
            >
              {/* Header row */}
              <div
                className="session-header"
                onClick={() => isStopped && toggleExpand(session.session_name)}
              >
                <div>
                  <strong>{session.session_name}</strong>
                  {isStopped   && " — Stopped"}
                  {isScheduled && " — Scheduled"}
                </div>

                {isActive && (
                  <span className="status-icon recording" title="Recording" />
                )}
                {isError && (
                  <span className="status-icon error" title={session.error_message} />
                )}
                {isScheduled && (
                  <span className="status-icon scheduled" title="Waiting for scheduled start" />
                )}
                {isStopped && (
                  <span>{isExpanded ? "▲" : "▼"}</span>
                )}
              </div>

              {/* Details */}
              {(!isStopped || isExpanded) && (
                <div className="session-details">
                  <p><strong>Target:</strong> {session.target}</p>
                  <p><strong>Modules:</strong> {session.modules.join(", ")}</p>
                  <p><strong>Start:</strong> {session.start_time || "-"}</p>
                  <p><strong>End:</strong>   {session.end_time   || "-"}</p>

                  {isError && (
                    <p className="error-message"><strong>Error:</strong> {session.error_message}</p>
                  )}

                  {isStopped && (
                    <p><strong>Status:</strong> Session stopped</p>
                  )}

                  {/* Stop progress — visible while modules are confirming */}
                  {stillStopping > 0 && (
                    <p><strong>Stopping:</strong> waiting for {stillStopping} module(s) to confirm</p>
                  )}

                  {/* Export progress */}
                  {exportEntries.length > 0 && (
                    <p>
                      <strong>Exports:</strong>{" "}
                      {completeExports}/{exportEntries.length} complete
                      {pendingExports > 0 && `, ${pendingExports} pending`}
                      {failedExports  > 0 && `, ${failedExports} failed`}
                    </p>
                  )}

                  {isScheduled && session.scheduled_start_time && (
                    <p>
                      <strong>Schedule:</strong>{" "}
                      {session.scheduled_start_time} – {session.scheduled_end_time}
                    </p>
                  )}

                  {!isStopped && !isScheduled && (
                    <button
                      className="stop-button"
                      onClick={() => handleStop(session.session_name)}
                    >
                      End Session
                    </button>
                  )}
                </div>
              )}
            </div>
          );
        })
      )}
    </div>
  );
}

export default SessionList;
