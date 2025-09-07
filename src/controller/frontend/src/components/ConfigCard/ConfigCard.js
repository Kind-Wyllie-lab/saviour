// React imports
import React, { useEffect, useState } from "react";
import socket from "../../socket";

// Style imports
import './ConfigCard.css';

function ConfigCard({ id, config }) {
    console.log("Rendering ConfigCard with config:", config);
    return (
        <div className={`config-card`}>
            <h3>{id}</h3>
            <pre>{JSON.stringify(config, null, 2)}</pre>
        </div>
    );
}

export default ConfigCard;