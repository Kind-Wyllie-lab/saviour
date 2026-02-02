// src/pages/Debug.js
import React, { useEffect, useState } from "react";
import socket from "../../../socket";
import "./Debug.css";
import LoginModal from "/src/basic/components/LoginModal/LoginModal";

function Debug() {
    const [debugData, setDebugData] = useState(null);  
    const [isAuthenticated, setIsAuthenticated] = useState(false);

    useEffect(() => {
        console.log("Emitting get_debug_data");
        socket.emit("get_debug_data");

        // Receive modules
        socket.on("debug_data", (data) => {
            console.log("Received debug data:", data);
            setDebugData(data);
        });

        // Unbind sockets
        return() => {
            socket.off("debug_data"); // Unregister listener to prevent multiple listeners on component re-render
        };
    }, []);

    function requestDebugData(){
        console.log("Emitting get debug data")
        socket.emit("get_debug_data")
    }

    return (
        <main className="debug">
            {!isAuthenticated ? (
                <LoginModal onSuccess={() => setIsAuthenticated(true)} />
            ) : (
                <div className="debug-container">
                    <div className="module-information">{JSON.stringify(debugData.modules)}</div>
                    <button onClick={requestDebugData}>Refresh Data</button>
                </div>
            )}
        </main>
    );
}

export default Debug;