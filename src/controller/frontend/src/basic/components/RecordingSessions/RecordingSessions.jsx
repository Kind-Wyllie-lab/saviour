import React, { useState, useEffect } from "react";
import "./RecordingSessions.css";

import socket from "/src/socket";

function RecordingSessions() {
    const [recordingSessions, setRecordingSessions] = useState({}); 

    useEffect(() => {
        socket.emit("get_recording_sessions")

        const handleUpdate = (data) => {
            console.log(data);
            setRecordingSessions(data);
        }

        socket.on("recording_sessions", handleUpdate);

        return () => {
            socket.off("recording_sessions", handleUpdate);
        };
    }, []);

    return (
        <div className="card">
            <p>Recording Sessions</p>
        </div>
    )
}

export default RecordingSessions;