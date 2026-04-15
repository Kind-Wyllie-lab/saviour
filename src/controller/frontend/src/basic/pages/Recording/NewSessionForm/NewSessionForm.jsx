import { useState } from "react";
import socket from "/src/socket";
import "./NewSessionForm.css";

import useExperimentTitle from "/src/hooks/useExperimentTitle";
import SessionName from "../SessionName/SessionName";
import TimeSelect from "./TimeSelect/TimeSelect";


function NewSessionForm({ modules }) {
  const [target, setTarget] = useState("all");
  const { experimentName } = useExperimentTitle();

  const [isScheduled, setIsScheduled] = useState(false);
  const [startHour, setStartHour]     = useState("19");
  const [startMinute, setStartMinute] = useState("00");
  const [endHour, setEndHour]         = useState("23");
  const [endMinute, setEndMinute]     = useState("00");

  const targetModules      = target === "all" ? modules : modules.filter((m) => m.id === target);
  const allTargetReady     = targetModules.length > 0 && targetModules.every((m) => m.status === "READY");
  const anyTargetRecording = targetModules.some((m) => m.status === "RECORDING");
  const canStart           = experimentName && allTargetReady && !anyTargetRecording;

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!experimentName) return;

    if (isScheduled) {
      socket.emit("create_scheduled_session", {
        target,
        session_name: experimentName,
        start_time: `${startHour}:${startMinute}`,
        end_time:   `${endHour}:${endMinute}`,
      });
    } else {
      socket.emit("create_session", { target, session_name: experimentName });
    }

    setTarget("all");
  };

  const checkReady = (e) => {
    e.preventDefault();
    if (!experimentName) return;
    socket.emit("check_ready", { target });
  };

  return (
    <div className="new-session-form card">
      <h2>New Session</h2>

      <SessionName experimentName={experimentName} />

      <form onSubmit={handleSubmit} className="session-form">
        <div className="form-row">
          <label htmlFor="target-select">Target</label>
          <select
            id="target-select"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
          >
            <option value="all">All Modules</option>
            {modules.map((m) => (
              <option key={m.id} value={m.id}>{m.name || m.id}</option>
            ))}
          </select>
        </div>

        <div className="form-row form-row--checkbox">
          <label>
            <input
              type="checkbox"
              checked={isScheduled}
              onChange={() => setIsScheduled(!isScheduled)}
            />
            Scheduled recording
          </label>
        </div>

        {isScheduled && (
          <>
            <TimeSelect label="Start" hour={startHour} setHour={setStartHour} minute={startMinute} setMinute={setStartMinute} />
            <TimeSelect label="End"   hour={endHour}   setHour={setEndHour}   minute={endMinute}   setMinute={setEndMinute} />
          </>
        )}

        <div className="session-description">
          {isScheduled ? (
            <p>Session will record daily between {startHour}:{startMinute} and {endHour}:{endMinute}.</p>
          ) : (
            <p>Recording starts on all {target === "all" ? "available" : "selected"} modules in ~3 seconds.</p>
          )}
          <div className="session-name-preview-block">
            Session name <strong>{experimentName ? `${experimentName}-(TIMESTAMP)` : "—"}</strong>
          </div>
        </div>

        <div className="button-row">
          <button type="button" className="secondary-button" onClick={checkReady}>
            Check Ready
          </button>
          <button type="submit" className="primary-button" disabled={!canStart}>
            {isScheduled ? "Schedule Session" : "Start Recording"}
          </button>
        </div>
      </form>
    </div>
  );
}

export default NewSessionForm;
