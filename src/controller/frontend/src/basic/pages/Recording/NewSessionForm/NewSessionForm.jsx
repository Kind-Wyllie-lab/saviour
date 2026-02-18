import { useState } from "react";
import socket from "/src/socket";
import "./NewSessionForm.css";


import useExperimentTitle from "/src/hooks/useExperimentTitle";
import SessionName from "../SessionName/SessionName";


function NewSessionForm({ modules }) {
  const [target, setTarget] = useState("all");
  const { experimentName } = useExperimentTitle();
  
  const targetModules = target === "all" ? modules : modules.filter((m) => m.id === target); // TODO: Handle groups
  const allTargetReady = targetModules.length > 0 && targetModules.every((m) => m.status === "READY");
  const anyTargetRecording = targetModules.some((m) => m.status === "RECORDING");

  const canStart = experimentName && allTargetReady && !anyTargetRecording;


  const handleSubmit = (e) => {
    e.preventDefault();
    if (!experimentName) return; // safety check

    console.log("Creating session for " + experimentName + " with target " + target);

    socket.emit("create_session", { target, session_name: experimentName });
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
