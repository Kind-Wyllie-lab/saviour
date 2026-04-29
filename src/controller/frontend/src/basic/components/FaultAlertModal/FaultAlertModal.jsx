import "./FaultAlertModal.css";

function parseTimestamp(str) {
  if (!str) return null;
  const m = str.match(/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})$/);
  if (!m) return null;
  return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]);
}

function formatDateTime(date) {
  if (!date) return "—";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export default function FaultAlertModal({ faultedSessions, onAcknowledge }) {
  const hasActiveFault   = faultedSessions.some((s) => s.state === "error");
  const hasRecoveredFault = faultedSessions.some((s) => s.state !== "error");

  const title = faultedSessions.length === 1
    ? (faultedSessions[0].state === "error"
        ? "Recording Fault Detected"
        : "Recording Fault — Session Recovered")
    : "Recording Faults Detected";

  return (
    <div className="modal-overlay fault-alert-overlay">
      <div className="modal fault-alert-modal" onClick={(e) => e.stopPropagation()}>
        <div className="fault-alert-header">
          <span className="fault-alert-icon">⚠</span>
          <h2>{title}</h2>
        </div>

        <p className="fault-alert-intro">
          {faultedSessions.length === 1
            ? "A recording session encountered a fault."
            : `${faultedSessions.length} recording sessions encountered faults.`}
        </p>

        <div className="fault-alert-sessions">
          {faultedSessions.map((session) => {
            const faultDate = parseTimestamp(session.error_time);
            const isRecovered = session.state !== "error";
            return (
              <div
                key={session.session_name}
                className={`fault-alert-session ${isRecovered ? "fault-alert-session--recovered" : ""}`}
              >
                <div className="fault-alert-session-header">
                  <div className="fault-alert-session-name">{session.session_name}</div>
                  <span className={`fault-alert-session-badge ${isRecovered ? "fault-alert-session-badge--recovered" : "fault-alert-session-badge--error"}`}>
                    {isRecovered ? "Recovered" : "Fault active"}
                  </span>
                </div>
                {faultDate && (
                  <div className="fault-alert-time">
                    Fault detected at {formatDateTime(faultDate)}
                  </div>
                )}
                {session.error_message && (
                  <div className="fault-alert-message">{session.error_message}</div>
                )}
              </div>
            );
          })}
        </div>

        {hasActiveFault && (
          <p className="fault-alert-note">
            Recording has not stopped. Go to the Recording page to add a replacement module or end the session.
          </p>
        )}
        {hasRecoveredFault && !hasActiveFault && (
          <p className="fault-alert-note">
            Recording recovered and is continuing with the remaining modules. Check the Recording page for details.
          </p>
        )}
        {hasActiveFault && hasRecoveredFault && (
          <p className="fault-alert-note">
            Some sessions are still in a fault state. Go to the Recording page to review each session.
          </p>
        )}

        <div className="modal-buttons">
          <button className="save-button fault-alert-ack" onClick={onAcknowledge}>
            Acknowledge
          </button>
        </div>
      </div>
    </div>
  );
}
