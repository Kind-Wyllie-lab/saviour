import React, { useEffect, useState } from "react";
import "./Dashboard.css";

import socket from "/src/socket";


function Dashboard({ modules }) {
    const [systemState, setSystemState] = useState({});

    useEffect(() => {
        const id = setInterval(() => {
            socket.emit("get_system_state");

            socket.on("system_state", (data) => {
                console.log("Received system state: ", data);
                setSystemState(data);
            })
        }, 10000);
        return () => clearInterval(id);
    }, []);

    // Filter the received modules
    const moduleList = Object.values(modules);
    const moduleCounts = moduleList.reduce((acc, m) => {
        const type = m.type || "unknown"; // fallback if type is missing
        acc[type] = (acc[type] || 0) + 1;
        return acc;
      }, {});

    return (
        <div className="dashboard-overview">
            <section className="connected-modules">
                <h3>Connected Modules</h3>
                {Object.entries(moduleCounts).map(([type, count]) => (
                    <p key={type}>
                    {count} {type}{count > 1 ? "s" : ""}
                    </p>
                ))}
                <p>{moduleList.length} total</p>
            </section>
            <section className="habitat-system">
                <h3>System</h3>
                <p>{systemState.recording? ("Recording") : ("Not Recording")}</p> {/*e.g. Recording for 127 minutes or Not recording*/}
                <p>PTP sync: {systemState.ptp_sync}ms</p>
                <p>Uptime: {Math.floor(systemState.uptime / 60)}m</p>
            </section>
        </div>
    )
}

export default Dashboard;
