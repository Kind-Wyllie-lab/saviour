// React imports
import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom";
import socket from "../../../socket";


// Style imports
import './ModuleCard.css';

function ModuleCard({ module }) {
    const [showOptionsModal, setShowOptionsModal] = useState(false); // A modal for optional commands to do with module


    // Determine border colour based on status
    let borderClass = "";
    if (!module.online) borderClass = "offline";
    if (module.status === "READY") borderClass = "ready";
    if (module.status === "RECORDING") borderClass = "recording";
    // if (module.status === "NOT_READY") {
    //     alert("Module NOT_READY: " + module.ready_message);
    // }


    const removeModule = () => {
        console.log("Emitting remove-module");
        socket.emit("remove_module", module);
        setShowOptionsModal(false);
    };

    const handleCheckReady = () => {
        console.log("Sending check_ready command")
        socket.emit("send_command", { 
          type: "validate_readiness",
          module_id: module.id
        });
    };

    const handleStopRecording = () => {
        console.log("Sending stop_recording command")
        socket.emit("send_command", { 
          type: "stop_recording",
          module_id: "all"
        }); 
    };

    const handleStartRecording = () => {
        // setShowDurationModal(true); // open modal first
        console.log("No implementation for start recording here");
    };
    

    return (
        <div className="module-container">
            <div className={`module-card ${borderClass}`} onClick={() => {if (!showOptionsModal) setShowOptionsModal(true)}}>
                <h3>{module.id}</h3>
                <p>IP: {module.ip}</p>
                <p>Type: {module.type}</p>
                {module.status === "NOT_READY" ? (
                    <p>Status: {module.status} : {module.ready_message}</p>
                ) : (
                    <p>Status: {module.status}</p>
                )}

                {module.online ? (
                    module.status === "RECORDING" && (
                        <div className="recording-indicator" title="Recording"></div>
                    )
                ) : (
                    <div className="offline-indicator">OFFLINE</div>
                )}
            </div>
            {showOptionsModal && (
                    <div className="modal-backdrop" onClick={() => setShowOptionsModal(false)}>
                        <div className="modal" onClick={(e) => e.stopPropagation()}> 
                            <h3>Options - {module.id} ({module.status})</h3>
                            <div className="modal-buttons">
                                <button onClick={removeModule}>Remove Module</button>
                                <button onClick={handleCheckReady}>Check Ready</button>
                                { module.status === "RECORDING" && (
                                    <button onClick={handleStopRecording}>Stop Recording</button>
                                )}
                                { module.status === "READY" && (
                                    <button onClick={handleStartRecording}>Start Recording</button>
                                )}
                                <button onClick={() => setShowOptionsModal(false)}>Cancel</button>
                            </div>
                        </div>
                    </div>
                )}
        </div>

    );
}

export default ModuleCard;