import { useCallback, useEffect, useRef, useState } from "react";
import socket from "/src/socket";
import useIsLoggedIn from "/src/hooks/useIsLoggedIn";

// Readiness of a pending session's modules, checked for you: the module
// readiness check (validate_readiness: local write, disk space, PTP, not
// recording, export share mount + write) runs when the page opens, again
// before a module's READY lapses (Modules.READY_TIMEOUT_SECS = 120 s), and
// whenever one drops back to WAITING. Start Recording is enabled only while
// every module is READY (`onChange(allReady)`).

const RECHECK_MS = 90_000;  // under the 120 s READY timeout, so Start doesn't flicker off
const THROTTLE_MS = 15_000; // never re-check more often than this

const LABEL = {
  READY: "Ready",
  NOT_READY: "Not ready",
  RECORDING: "Recording",
  OFFLINE: "Offline",
  FAULT: "Fault",
};

export default function SessionReadiness({ moduleIds, modules, onChange }) {
  const loggedIn = useIsLoggedIn();
  const [pending, setPending] = useState(false); // a check was sent, answers may still be due
  const lastRun = useRef(0);
  const pendingTimer = useRef(null);
  useEffect(() => () => clearTimeout(pendingTimer.current), []);
  const ids = moduleIds.join(",");

  const run = useCallback((force = false) => {
    const now = Date.now();
    if (!loggedIn || !ids) return;
    if (!force && now - lastRun.current < THROTTLE_MS) return;
    if (typeof document !== "undefined" && document.visibilityState === "hidden" && !force) return;
    lastRun.current = now;
    setPending(true);
    clearTimeout(pendingTimer.current);
    pendingTimer.current = setTimeout(() => setPending(false), THROTTLE_MS + 5000);
    socket.emit("check_ready", { modules: ids.split(",") });
  }, [loggedIn, ids]);

  // On open, and on a timer while the page stays open.
  useEffect(() => {
    run(true);
    const t = setInterval(() => run(), RECHECK_MS);
    const onVisible = () => { if (document.visibilityState === "visible") run(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => { clearInterval(t); document.removeEventListener("visibilitychange", onVisible); };
  }, [run]);

  const rows = moduleIds.map((id) => {
    const m = modules.find((x) => x.id === id);
    return { id, name: m?.name || id, status: m ? (m.online === false ? "OFFLINE" : m.status) : "OFFLINE",
      message: m?.ready_message };
  });
  const allReady = rows.length > 0 && rows.every((r) => r.status === "READY");
  const anyWaiting = rows.some((r) => r.status === "WAITING");

  // A module fell back to WAITING (its READY lapsed, or it reconnected).
  const waitingKey = rows.filter((r) => r.status === "WAITING").map((r) => r.id).join(",");
  useEffect(() => { if (waitingKey) run(); }, [waitingKey, run]);

  useEffect(() => { onChange?.(allReady); }, [allReady, onChange]);

  const checking = anyWaiting && pending;

  return (
    <div className={`session-readiness${allReady ? " session-readiness--ok" : ""}`}>
      <div className="session-readiness__head">
        <strong>
          {allReady ? "✓ All modules ready" : checking ? "Checking modules…" : "Modules not ready yet"}
        </strong>
        <button type="button" className="session-readiness__recheck" onClick={() => run(true)}
          disabled={!loggedIn} title={loggedIn ? "Run the readiness check again now" : "Sign in to check"}>
          Check again
        </button>
      </div>
      {!loggedIn && <p className="session-readiness__hint">Sign in to check the modules and start recording.</p>}
      <ul className="session-readiness__list">
        {rows.map((r) => (
          <li key={r.id} className={`session-readiness__row session-readiness__row--${(r.status || "WAITING").toLowerCase()}`}>
            <span className="session-readiness__dot" aria-hidden="true" />
            <span className="session-readiness__name">{r.name}</span>
            <span className="session-readiness__state">
              {r.status === "WAITING" ? (checking ? "Checking…" : "Waiting") : (LABEL[r.status] ?? r.status)}
              {r.status === "NOT_READY" && r.message ? `: ${r.message}` : ""}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
