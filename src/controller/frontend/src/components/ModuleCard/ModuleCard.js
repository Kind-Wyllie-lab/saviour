// React imports
import React, { useEffect, useState } from "react";
import socket from "../../socket";

// Style imports
import './ModuleCard.css';

function ModuleCard({ module }) {
    // Determine border colour based on status
    let borderClass = "";
    if (!module.online) borderClass = "offline";
    if (module.status === "READY") borderClass = "ready";
    if (module.status === "RECORDING") borderClass = "recording";


    return (
        <div className={`module-card ${borderClass}`}>
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
    );
}

export default ModuleCard;