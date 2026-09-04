import { useEffect, useState, useCallback } from "react";
import useSessions from "./useSessions";

// Acknowledgement is persisted in localStorage, not sessionStorage -- the
// latter is wiped when the tab/window closes, so a fault the operator had
// already acknowledged re-prompted every time they reopened the GUI. Keyed
// by session + error_time so a genuinely new fault on the same session
// (different error_time) still re-alerts.
function faultKey(session) {
  return `saviour_fault_ack::${session.session_name}::${session.error_time ?? "unknown"}`;
}

function isAcked(session) {
  try {
    return localStorage.getItem(faultKey(session)) === "1";
  } catch {
    return false;
  }
}

function markAcked(session) {
  try {
    localStorage.setItem(faultKey(session), "1");
  } catch {
    /* storage unavailable (private mode) -- the modal still closes for this
       browser session, it just won't be remembered next load */
  }
}

/**
 * Sessions that warrant a blocking fault modal: an active, unrecovered fault
 * (state === "error"), or a fault a still-running session recovered from
 * (error_time set, still active/paused). A *stopped* session's error is
 * history -- it's shown on that session's own detail page and doesn't need
 * re-acknowledging on every page load. Returns the unacknowledged ones plus
 * an `acknowledge()` that persists the dismissal.
 */
export default function useFaultAlerts() {
  const { sessionList } = useSessions();
  const [pendingFaults, setPendingFaults] = useState([]);

  useEffect(() => {
    setPendingFaults(
      sessionList.filter(
        (s) => s.error_time && s.state !== "stopped" && !isAcked(s)
      )
    );
  }, [sessionList]);

  const acknowledge = useCallback(() => {
    setPendingFaults((faults) => {
      faults.forEach(markAcked);
      return [];
    });
  }, []);

  return { pendingFaults, acknowledge };
}
