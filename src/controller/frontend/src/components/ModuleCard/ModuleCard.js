// React imports
import React, { useEffect, useState } from "react";
import socket from "../../socket";

// Style imports
import './ModuleCard.css';

function ModuleCard({ module }) {
    return (
        <div className="module-card">
            <h3>{module.id}</h3>
            <p>IP: {module.ip}</p>
            <p>Type: {module.type}</p>
            <p>Status: {module.online ? "Online" : "Offline"}</p>
            <p>State: {module.status}</p>
        </div>
    );
}

export default ModuleCard;