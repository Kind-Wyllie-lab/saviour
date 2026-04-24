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
  const [value, setValue] = useState(() => toDatetimeLocal(new Date()));
  const [status, setStatus] = useState(null); // null | "saving" | "ok" | "error"
  const [error, setError] = useState("");

  useEffect(() => {
    const handler = (data) => {
      if (data.success) {
        setStatus("ok");
        setTimeout(onClose, 1500);
      } else {
        setStatus("error");
        setError(data.error || "Unknown error");
      }
    };
    socket.on("set_time_result", handler);
    return () => socket.off("set_time_result", handler);
  }, [onClose]);

  const handleSubmit = () => {
    setStatus("saving");
    socket.emit("set_controller_time", { iso: new Date(value).toISOString() });
  };

  const isDone = status === "saving" || status === "ok";

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal clock-modal" onClick={e => e.stopPropagation()}>
        <h2>Controller Clock</h2>

        {driftMs != null && Math.abs(driftMs) >= 2 * 60 * 1000 && (
          <p className="modal-subtext clock-modal__drift">
            Controller clock is <strong>{formatDrift(driftMs)}</strong> compared to this browser.
          </p>
        )}

        {controllerTime && (
          <div className="clock-modal__times">
            <span className="clock-modal__label">Controller</span>
            <span>{new Date(controllerTime).toUTCString().replace(/GMT$/, "UTC")}</span>
            <span className="clock-modal__label">Browser</span>
            <span>{new Date().toUTCString().replace(/GMT$/, "UTC")}</span>
          </div>
        )}

        <label className="clock-modal__label" htmlFor="clock-input">Set to (local time)</label>
        <input
          id="clock-input"
          type="datetime-local"
          className="clock-modal__input"
          value={value}
          onChange={e => setValue(e.target.value)}
          disabled={isDone}
        />

        {status === "ok"    && <p className="clock-modal__msg val--ok">Time set successfully.</p>}
        {status === "error" && <p className="clock-modal__msg val--danger">{error}</p>}

        <div className="modal-buttons">
          <button className="save-button" onClick={handleSubmit} disabled={isDone}>
            {status === "saving" ? "Setting…" : "Set Time"}
          </button>
          <button className="reset-button" onClick={onClose}>Skip</button>
        </div>
      </div>
    </div>
  );
}
