import React, { useState } from "react";
import socket from "../../socket";
import "./CommandsPanel.css";

function CommandsPanel({ modules, experimentName }) {
  const [showDurationModal, setShowDurationModal] = useState(false);
  const [duration, setDuration] = useState(0);
  const [targetModule, setTargetModule] = useState("all"); // default target is "All"
  

  const handleStartRecording = () => {
    setShowDurationModal(true); // open modal first
  };

  const confirmStartRecording = () => {
    socket.emit("send_command", {
      type: "start_recording",
      module_id: targetModule,
      params: { experiment_name: experimentName, duration: Number(duration) },
    });
    setShowDurationModal(false);
  };

  // const handleStartRecording = () => {
  //   socket.emit("send_command", {
  //     type: "start_recording",
  //     module_id: "all",
  //     params: { experiment_name: experimentName, duration: Number(duration) },
  //   });
  // };

  const handleStopRecording = () => {
    socket.emit("send_command", { 
      type: "stop_recording",
      module_id: targetModule
    }); 
  };

  const handleCheckReady = () => {
    socket.emit("send_command", { 
      type: "validate_readiness",
      module_id: targetModule
    });
  };

  // Derived states from modules
  const allModulesReady = modules.every((m) => m.status === "READY");
  const anyRecording = modules.some((m) => m.status === "RECORDING");

  return (
    <>
      <h2>Commands</h2>
      <div className="commands-panel">
        <div className="target-selector">
          <label>Target:</label>
          <select value={targetModule} onChange={(e) => setTargetModule(e.target.value)}>
            <option value="all">All</option>
            {modules.map((m) => (
              <option key={m.id} value={m.id}>{m.id}</option>
            ))}
          </select>
        </div>
        <button className="start-button" onClick={handleStartRecording} disabled={!allModulesReady || anyRecording}>
          Start Recording
        </button>
        <button className="stop-button" onClick={handleStopRecording} disabled={!anyRecording}>
          Stop Recording
        </button>
        <button className="ready-button" onClick={handleCheckReady} disabled={allModulesReady || anyRecording}>Check Ready</button>
        {showDurationModal && (
          <div className="modal-backdrop">
            <div className="modal">
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
