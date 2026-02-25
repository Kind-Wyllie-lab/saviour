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
          const isExpanded = expandedSessions[session.session_name];

          return (
            <div
              key={session.session_name}
              className={`session ${session.stopped? "stopped" : ""}`}
            >
              {/* Header row */}
              <div
                className="session-header"
                onClick={() =>
                  session.stopped &&
                  toggleExpand(session.session_name)
                }
              >
                <div>
                  <strong>{session.session_name}</strong>
                  {session.stopped && " - Stopped"}
                </div>

                {session.active && (
                  session.error ? (
                    <span className="status-icon error">
                    </span>
                  ) : (
                    <span className="status-icon recording">
                    </span>
                  )
                )}

                {session.stopped && (
                  <span>
                    {isExpanded ? "▲" : "▼"}
                  </span>
                )}
              </div>

              {/* Details */}
              {(!session.stopped || isExpanded) && (
                <div className="session-details">
                  <p><strong>Target:</strong> {session.target}</p>
                  <p><strong>Modules:</strong> {session.modules.join(", ")}</p>
                  <p><strong>Start:</strong> {session.start_time || "-"}</p>
                  <p><strong>End:</strong> {session.end_time || "-"}</p>
                  {session.error && (
                    <p><strong>ERROR:</strong> {session.error_message}</p>
                  )}
                  {session.stopped && (
                    <p><strong>Status:</strong> Session is stopped</p>
                  )}
                  {session.scheduled && <p><strong>Scheduled Time:</strong> {session.scheduled_start_time} - {session.scheduled_end_time}</p>}
                  {!session.stopped && (
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
