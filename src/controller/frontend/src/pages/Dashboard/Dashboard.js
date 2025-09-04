// src/pages/Dashboard.js
import React, { useEffect, useState } from "react";
import socket from "../../socket";

// Styling and components
import "./Dashboard.css";
import ModuleCard from "../../components/ModuleCard/ModuleCard";
import LivestreamCard from "../../components/LivestreamCard/LivestreamCard";

// Check websocket connection
socket.on("connect", () => {
  console.log("Connected to backend", socket.id);
});

socket.on("disconnect", () => {
  console.log("Disconnected from backend");
});

function Dashboard() {
  const [modules, setModules] = useState([]);

  useEffect(() => {
    console.log("Emitting get_modules");
    socket.emit("get_modules"); // Ask backend for modules

    socket.on("modules_update", (data) => {
      console.log("Received modules:", data);
      // Expecting { modules: [...] }
      setModules(data.modules || []);
    });

    return () => {
      socket.off("modules_update");
    };
  }, []);

  const cameraModules = modules.filter((m) => m.type === "camera");

  return (
    <main className="dashboard">
      <section>
        <h2>Modules (Network)</h2>
        <div className="module-grid">
          {modules.length > 0 ? (
            modules.map((module) => (
              <ModuleCard key={module.id} module={module} />
            ))
          ) : (
            <p>No modules connected</p>
          )}
        </div>
      </section>

      <section>
        <h2>Camera Streams</h2>
        <div className="livestream-grid">
          {cameraModules.length > 0 ? (
            cameraModules.map((cam) => (
              <LivestreamCard key={cam.id} module={cam} />
            ))
          ) : (
            <p>No camera modules connected</p>
          )}
        </div>
      </section>
    </main>
  );
}

export default Dashboard;
