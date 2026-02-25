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
  const [startHour, setStartHour] = useState("19");
  const [startMinute, setStartMinute] = useState("00");
  const [endHour, setEndHour] = useState("23");
  const [endMinute, setEndMinute] = useState("00");

  const targetModules = target === "all" ? modules : modules.filter((m) => m.id === target); // TODO: Handle groups
  const allTargetReady = targetModules.length > 0 && targetModules.every((m) => m.status === "READY");
  const anyTargetRecording = targetModules.some((m) => m.status === "RECORDING");

  const canStart = experimentName && allTargetReady && !anyTargetRecording;

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!experimentName) return; // safety check

    console.log("Creating session for " + experimentName + " with target " + target);


    if (isScheduled) {
      socket.emit("create_scheduled_session", { 
        target, 
        session_name: experimentName, 
        start_time: `${startHour}:${startMinute}`, 
        end_time: `${endHour}:${endMinute}`
      });
    } else {
      socket.emit("create_session", { 
        target, 
        session_name: experimentName 
      });
    }

    setTarget("all");
  };


  const checkReady = (e) => {
    e.preventDefault();
    if (!experimentName) return;
    console.log("Checking modules are ready");

    socket.emit("check_ready", {target})
  }


  return (
    <div className="new-session-form card">
      <h2>New Recording Session</h2>

      {/* Experiment metadata */}
      <SessionName experimentName={experimentName} />

      {/* Target selection */}
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
              <option key={m.id} value={m.id}>
                {m.name || m.id}
              </option>
            ))}
          </select>
        </div>

        {/* Scheduled Session */}
        <div className="form-row">
          <label>
            Scheduled Session
            <input
              type="checkbox"
              checked={isScheduled}
              onChange={() => setIsScheduled(!isScheduled)}
            />

          </label>
        </div>
        {isScheduled && (
          <>
            <TimeSelect label="Start Time" hour={startHour} setHour={setStartHour} minute={startMinute} setMinute={setStartMinute} />
            <TimeSelect label="End Time" hour={endHour} setHour={setEndHour} minute={endMinute} setMinute={setEndMinute} />
          </>
        )}

        <div className="session-description">
          <h2>Session Overview</h2>
          <p>Will create a session named {experimentName}-(TIMESTAMP) with target {target}.</p>
          <p>The session name will be used as the prefix for all files as well as the name of the folder recordings may be found in.</p>
          {/* <p>(e.g. {experimentName}-20260223-133847/20260223/TopCamera/{experimentName}-20260223-133847_TopCamera_(0_20260223-133847).mp4)</p> */}
          {isScheduled? (
            <p>After pressing "Create Session", session will automatically record between {startHour}:{startMinute} and {endHour}:{endMinute} each day.</p>
          ) : (
            <p>Session will start recording immediately after pressing "Create session".</p>
          )}
        </div>

        <div className="button-row">
          <button
            type="button"
            className="secondary-button"
            onClick={checkReady}
          >
            Check Ready
          </button>

          <button
            type="submit"
            className="primary-button"
            disabled={!canStart}
          >
            Start Session
          </button>
        </div>
      </form>
    </div>
  );
}

export default NewSessionForm;
