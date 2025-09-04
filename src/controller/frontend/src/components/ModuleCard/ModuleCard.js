// React imports
import React, { useEffect, useState } from "react";
import socket from "../../socket";

// Style imports
import './ModuleCard.css';

function ModuleCard({ module }) {
    return (
        <div className="module-card">
            <h3>{module.id}</h3>
            <p>Status: {module.status}</p>
        </div>
    );
}

export default ModuleCard;