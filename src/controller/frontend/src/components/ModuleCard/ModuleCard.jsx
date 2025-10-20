// React imports
import React, { useEffect, useState } from "react";
import socket from "../../socket";
import ReactDOM from "react-dom";

// Style imports
import './ModuleCard.css';

function ModuleCard({ module }) {
    const [showOptionsModal, setShowOptionsModal] = useState(false); // A modal for optional commands to do with module


    // Determine border colour based on status
    let borderClass = "";
    if (!module.online) borderClass = "offline";
    if (module.status === "READY") borderClass = "ready";
    if (module.status === "RECORDING") borderClass = "recording";

    const removeModule = () => {
        console.log("Emitting remove-module");
        socket.emit("remove_module", module);
        setShowOptionsModal(false);
    };

    return (
        <div className="module-container">
            <div className={`module-card ${borderClass}`} onClick={() => {if (!showOptionsModal) setShowOptionsModal(true)}}>
                <h3>{module.id}</h3>
                <p>IP: {module.ip}</p>
                <p>Type: {module.type}</p>
                {/* <p>Status: {module.online ? "Online" : "Offline"}</p> */}
                <p>Status: {module.status}</p>

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
                            <h3>Options - {module.id}</h3>
                            <div className="modal-buttons">
                                <button onClick={removeModule}>Remove Module</button>
                                <button onClick={() => setShowOptionsModal(false)}>Cancel</button>
                            </div>
                        </div>
                    </div>
                )}
        </div>

    );
}

export default ModuleCard;