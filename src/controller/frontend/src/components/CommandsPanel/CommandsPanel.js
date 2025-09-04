import React from "react";
import socket from "../../socket";
import "./CommandsPanel.css";

function CommandsPanel({ modules, experimentName }) {
    const handleStartRecording = () => {
      socket.emit("send_command", {
        type: "start_recording",
        module_id: "all",
        params: { experiment_name: experimentName },
      });
    };
  
    const handleStopRecording = () => {
      socket.emit("send_command", { 
        type: "stop_recording",
        module_id: "all"
      }); 
    };
  
    const handleCheckReady = () => {
      socket.emit("send_command", { 
        type: "validate_readiness",
        module_id: "all"
      });
    };
  
    // Derived states from modules
    const allModulesReady = modules.every((m) => m.ready);
    const anyRecording = modules.some((m) => m.recording);
  
    return (
      <div className="commands-panel">
        <button onClick={handleStartRecording} disabled={!allModulesReady || anyRecording}>
          Start Recording
        </button>
        <button onClick={handleStopRecording} disabled={!anyRecording}>
          Stop Recording
        </button>
        <button onClick={handleCheckReady}>Check Ready</button>
      </div>
    );
  }
  

export default CommandsPanel;
