import React, { useState } from "react";
import socket from "../../socket";
import "./CommandsPanel.css";

function CommandsPanel({ modules, experimentName }) {
  const [showDurationModal, setShowDurationModal] = useState(false);
  const [duration, setDuration] = useState(0);
  const [targetModule, setTargetModule] = useState("all"); // selected target

  // Compute derived states based on selected target
  const targetModules = targetModule === "all"
    ? modules
    : modules.filter((m) => m.id === targetModule);

  const allTargetReady = targetModules.every((m) => m.status === "READY");
  const anyTargetRecording = targetModules.some((m) => m.status === "RECORDING");

  const handleStartRecording = () => {
    setShowDurationModal(true);
  };

  const confirmStartRecording = () => {
    socket.emit("send_command", {
      type: "start_recording",
      module_id: targetModule,
      params: { experiment_name: experimentName, duration: Number(duration) },
    });
    setShowDurationModal(false);
  };

  const handleStopRecording = () => {
    socket.emit("send_command", {
      type: "stop_recording",
      module_id: targetModule,
    });
  };

  const handleCheckReady = () => {
    socket.emit("send_command", {
      type: "validate_readiness",
      module_id: targetModule,
    });
  };

  return (
    <>
      <h2>Commands</h2>
      <div className="commands-panel">
        {/* Target selection dropdown */}
        <div className="target-select">
          <label>Target Module(s):</label>
          <select
            value={targetModule}
            onChange={(e) => setTargetModule(e.target.value)}
          >
            <option value="all">All</option>
            {modules.map((m) => (
              <option key={m.id} value={m.id}>{m.id}</option>
            ))}
          </select>
        </div>

        <button
          className="start-button"
          onClick={handleStartRecording}
          disabled={!allTargetReady || anyTargetRecording}
        >
          Start Recording
        </button>

        <button
          className="stop-button"
          onClick={handleStopRecording}
          disabled={!anyTargetRecording}
        >
          Stop Recording
        </button>

        <button
          className="ready-button"
          onClick={handleCheckReady}
          disabled={allTargetReady || anyTargetRecording}
        >
          Check Ready
        </button>

        {/* Duration modal */}
        {showDurationModal && (
          <div className="modal-backdrop" onClick={() => setShowDurationModal(false)}>
            <div className="modal" onClick={(e) => e.stopPropagation()}>
              <h3>Experiment Duration (seconds)</h3>
              <input
                type="number"
                value={duration}
                onChange={(e) => setDuration(e.target.value)}
                min={0}
              />
              <div className="modal-buttons">
                <button onClick={confirmStartRecording}>Start</button>
                <button onClick={() => setShowDurationModal(false)}>Cancel</button>
              </div>
              <p>0 = infinite duration</p>
            </div>
          </div>
        )}
      </div>
    </>
  );
}

export default CommandsPanel;
