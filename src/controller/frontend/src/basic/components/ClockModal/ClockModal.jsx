import { useState, useEffect } from "react";
import socket from "/src/socket";
import "./ClockModal.css";

function toDatetimeLocal(date) {
  const pad = n => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function formatDrift(ms) {
  const abs = Math.abs(ms);
  const sign = ms < 0 ? "behind" : "ahead";
  if (abs >= 3600000) return `${Math.round(abs / 3600000)}h ${sign}`;
  if (abs >= 60000)   return `${Math.round(abs / 60000)}m ${sign}`;
  return `${Math.round(abs / 1000)}s ${sign}`;
}

export default function ClockModal({ driftMs, controllerTime, onClose }) {
  const [manualValue, setManualValue] = useState(() => toDatetimeLocal(new Date()));
  const [status, setStatus] = useState(null); // null | "saving" | "ok" | "error"
  const [error, setError] = useState("");
  const [browserNow, setBrowserNow] = useState(() => new Date());

  // Keep browser time live in the modal
  useEffect(() => {
    const id = setInterval(() => setBrowserNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    const handler = (data) => {
      if (data.success) {
        setStatus("ok");
        setTimeout(onClose, 1200);
      } else {
        setStatus("error");
        setError(data.error || "Unknown error");
      }
    };
    socket.on("set_time_result", handler);
    return () => socket.off("set_time_result", handler);
  }, [onClose]);

  const send = (iso) => {
    setStatus("saving");
    setError("");
    socket.emit("set_controller_time", { iso });
  };

  const isDone = status === "saving" || status === "ok";

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal clock-modal" onClick={e => e.stopPropagation()}>
        <h2>Set Controller Time</h2>

        {driftMs != null && Math.abs(driftMs) >= 2000 && (
          <p className="modal-subtext clock-modal__drift">
            Controller is <strong>{formatDrift(driftMs)}</strong> compared to this browser.
          </p>
        )}

        {controllerTime && (
          <div className="clock-modal__times">
            <span className="clock-modal__label">Controller</span>
            <span>{new Date(controllerTime).toUTCString().replace(/GMT$/, "UTC")}</span>
            <span className="clock-modal__label">Browser</span>
            <span>{browserNow.toUTCString().replace(/GMT$/, "UTC")}</span>
          </div>
        )}

        <div className="clock-modal__section">
          <p className="clock-modal__section-label">Sync to browser</p>
          <p className="clock-modal__section-hint">Time is captured at the moment you click.</p>
          <button
            className="save-button"
            onClick={() => send(new Date().toISOString())}
            disabled={isDone}
          >
            {status === "saving" ? "Setting…" : status === "ok" ? "Done" : `Sync to ${browserNow.toUTCString().replace(/GMT$/, "UTC")}`}
          </button>
        </div>

        <div className="clock-modal__divider">or set manually</div>

        <div className="clock-modal__section">
          <label className="clock-modal__label" htmlFor="clock-input">Date &amp; time (local)</label>
          <input
            id="clock-input"
            type="datetime-local"
            className="clock-modal__input"
            value={manualValue}
            onChange={e => setManualValue(e.target.value)}
            disabled={isDone}
          />
          <button
            className="reset-button"
            onClick={() => send(new Date(manualValue).toISOString())}
            disabled={isDone}
          >
            Set Manually
          </button>
        </div>

        {status === "error" && <p className="clock-modal__msg val--danger">{error}</p>}

        <div className="modal-buttons" style={{marginTop: "16px"}}>
          <button className="save-button" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}
