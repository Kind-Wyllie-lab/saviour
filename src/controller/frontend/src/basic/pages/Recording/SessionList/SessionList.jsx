import React from "react";
import socket from "/src/socket";

function SessionList({ sessionList }) {
  const handleStop = (sessionName) => {
    socket.emit("stop_session", { session_name: sessionName });
  };

  return (
    <div className="session-list card">
      <h2>Session List</h2>
      {Object.values(sessionList).length === 0 ? (
        <p>No sessions yet</p>
      ) : (
        Object.values(sessionList).map((session) => (
            session.active? (
            <div
                key={session.session_name}
                className={`session ${session.active ? "active" : ""}`}
              >
                <h2>{session.session_name}</h2>
                <p>Target: {session.target}</p>
                <p>Modules: {session.modules.join(", ")}</p>
                <p>Status: {session.active ? "Recording" : "Stopped"}</p>
                <p>Start: {session.start_time || "–"}</p>
                <p>End: {session.end_time || "–"}</p>
    
                {/* Show Stop button only if session is active */}
                {session.active && (
                  <button onClick={() => handleStop(session.session_name)}>
                    Stop Session
                  </button>
                )}
              </div>
            ) : (
            <div
                key={session.session_name}
                className={`session ${session.active ? "active" : ""}`}
            >
                <p>{session.session_name} - Stopped</p>
            </div>
            )

        ))
      )}
    </div>
  );
}

export default SessionList;
