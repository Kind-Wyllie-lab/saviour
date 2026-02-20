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
              className={`session ${session.active ? "active" : "stopped"}`}
            >
              {/* Header row */}
              <div
                className="session-header"
                onClick={() =>
                  !session.active &&
                  toggleExpand(session.session_name)
                }
              >
                <div>
                  <strong>{session.session_name}</strong>
                  {!session.active && " - Stopped"}
                </div>

                {session.active && (
                  session.error ? (
                    <span className="status-badge error">
                      ERROR
                    </span>
                  ) : (
                    <span className="status-badge recording">
                      Recording
                    </span>
                  )
                )}

                {!session.active && (
                  <span className="status-badge stopped">
                    {isExpanded ? "▲" : "▼"}
                  </span>
                )}
              </div>

              {/* Details */}
              {(session.active || isExpanded) && (
                <div className="session-details">
                  <p><strong>Target:</strong> {session.target}</p>
                  <p><strong>Modules:</strong> {session.modules.join(", ")}</p>
                  <p><strong>Start:</strong> {session.start_time || "–"}</p>
                  <p><strong>End:</strong> {session.end_time || "–"}</p>
                  {session.error && (
                    <p><strong>ERROR:</strong> {session.error_message}</p>
                  )}
                  {session.active && (
                    <button
                      className="stop-button"
                      onClick={() =>
                        handleStop(session.session_name)
                      }
                    >
                      Stop Session
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
