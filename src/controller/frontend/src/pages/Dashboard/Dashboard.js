// src/pages/Dashboard.js
import React, { useEffect, useState } from "react";
import socket from "../../socket";

// Styling and components
import "./Dashboard.css";
import ModuleCard from "../../components/ModuleCard/ModuleCard";
import LivestreamCard from "../../components/LivestreamCard/LivestreamCard";
import ExperimentMetadata from "../../components/ExperimentMetadata/ExperimentMetadata";
import CommandsPanel from "../../components/CommandsPanel/CommandsPanel";

// Check websocket connection
socket.on("connect", () => {
  console.log("Connected to backend", socket.id);
});

socket.on("disconnect", () => {
  console.log("Disconnected from backend");
});

function Dashboard() {
  const [modules, setModules] = useState({}); // Modules object returned from backend
  const [experimentName, setExperimentName] = useState(""); // The experiment name 

  useEffect(() => {
    console.log("Emitting get_modules");
    socket.emit("get_modules"); // Ask backend for modules

    // Expecting data.modules like
    // { "camera_d610": { ip: "192.168.1.136", type: "camera", online: true } }
    socket.on("modules_update", (data) => {
      console.log("Received modules:", data);
      // Add default ready/checks/error
      const withDefaults = Object.fromEntries(
        Object.entries(data).map(([id, m]) => [
          id,
          { ...m, id, ready: false, checks: {}, error: null },
        ])
      );
      setModules(withDefaults);
    });

    // socket.on("update_module_readiness", (data) => {
    //   console.log("Received update_module_readiness, data:", data);

    //   setModules((prevModules) => ({
    //     ...prevModules,
    //     [data.module_id]: {
    //       ...prevModules[data.module_id],
    //       ready: data.ready,
    //       checks: data.checks,
    //       error: data.error,
    //     },
    //   }));
    // });

    return () => {
      socket.off("modules_update"); // Unregister listener to prevent multiple listeners on component re-render or remount
      // socket.off("update_module_readiness"); // As above
    };
  }, []);

  // Convert modujles object to array for easy rendering
  const moduleList = Object.values(modules);
  const cameraModules = moduleList.filter((m) => m.type === "camera");

  return (
    <main className="dashboard">
      <div className="dashboard-wrapper">
        <div className="dashboard-container">
          {/* left side */}
          <section>
            <h2>Connected Modules</h2>
            <div className="module-grid">
              {moduleList.length > 0 ? (
                moduleList.map((module) => (
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
        </div>

        <div className="sidebar-container">
          <section>
            <ExperimentMetadata setExperimentName={setExperimentName} />
            <CommandsPanel modules={moduleList} experimentName={experimentName} />
          </section>
        </div>    
      </div>
    </main>
  );
}

export default Dashboard;
