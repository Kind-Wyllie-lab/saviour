// src/pages/Debug.js
import React, { useEffect, useState } from "react";
import socket from "../../../socket";
import "./Debug.css";
import LoginModal from "../../components/LoginModal/LoginModal";

function Debug() {
    const [debugData, setDebugData] = useState(null);  
    const [isAuthenticated, setIsAuthenticated] = useState(false);

    useEffect(() => {
        socket.emit("get_debug_data");

        socket.on("debug_data", (data) => {
            setDebugData(data);
        });

        return () => {
            socket.off("debug_data");
        };
    }, []);

    function requestDebugData() {
        socket.emit("get_debug_data");
    }

    return (
        <main className="debug">
            {!isAuthenticated ? (
                <LoginModal onSuccess={() => setIsAuthenticated(true)} />
            ) : (
                <div className="debug-container">
                    {debugData
                        ? <pre className="module-information">{JSON.stringify(debugData, null, 2)}</pre>
                        : <p>Loading debug data...</p>
                    }
                    <button onClick={requestDebugData}>Refresh Data</button>
                </div>
            )}
        </main>
    );
}

export default Debug;