// src/pages/APADashboard.js
import React, { useEffect, useState } from "react";
import socket from "../../socket";

// Styling and components
import "./APADashboard.css";
import ModuleCard from "../../components/ModuleCard/ModuleCard";
import ModuleGrid from "../../components/ModuleGrid/ModuleGrid"
import APALivestreamCard from "../../components/APALivestreamCard/APALivestreamCard";
import ExperimentMetadata from "../../components/ExperimentMetadata/ExperimentMetadata";
import CommandsPanel from "../../components/CommandsPanel/CommandsPanel";
import APACommands from "../../components/APACommands/APACommands";

// Check websocket connection
socket.on("connect", () => {
  console.log("Connected to backend", socket.id);
});

socket.on("disconnect", () => {
  console.log("Disconnected from backend");
});

function APADashboard() {
  const [modules, setModules] = useState({}); // Modules object returned from backend
  const [experimentName, setExperimentName] = useState(""); // The experiment name 

  useEffect(() => {
    console.log("Emitting get_modules");
    socket.emit("get_modules"); // Ask backend for modules
    socket.emit("get_module_configs"); // Ask backend for module configs
    socket.emit("get_modules"); // Ask backend for modules
    socket.emit("get_experiment_metadata");

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

    socket.on("experiment_metadata_response", (data) => {
      setExperimentName(data.experiment_name);
    });

    return () => {
      socket.off("modules_update"); // Unregister listener to prevent multiple listeners on component re-render or remount
      socket.off("experiment_metadata_response");
      // socket.off("update_module_readiness"); // As above
    };
  }, []);

  useEffect(() => {
    socket.on("experiment_metadata_updated", (data) => {
      if (data.experiment_name) {
        setExperimentName(data.experiment_name);
      }
    });

    // On mount, request latest metadata
    socket.emit("get_experiment_metadata");

    return () => socket.off("experiment_metadata_updated");
  }, []);

  // Convert modujles object to array for easy rendering
  const moduleList = Object.values(modules);
  const cameraModules = moduleList.filter((m) => m.type === "camera");
  const apaCameraModules = moduleList.filter((m) => m.type === "apa_camera");
  if (apaCameraModules.length > 1) {
    console.error("Multiple modules of type apa_camera detected");
  }


  return (
    <main className="dashboard">

      <div className="dashboard-wrapper">
        {/* left side */}
        <div className="sidebar-container">
          <section>
            <ExperimentMetadata experimentName={experimentName} />
          </section>
          <section>
            <CommandsPanel modules={moduleList} experimentName={experimentName} />
          </section>
          <section>
            <ModuleGrid modules = {moduleList} />
          </section>
        </div>    
        {/* right side */}
        <div className="dashboard-container">
          <section>
            {apaCameraModules.length > 0 ? (
              <APALivestreamCard key={apaCameraModules[0].id} module={apaCameraModules[0]} />
            ) : (
              <div className="apa-camera-template">
                <p>APA camera not connected</p>
              </div>
            )}
          </section>
          <section>
            <APACommands modules={moduleList} />
          </section>
        </div>
      </div>
    </main>
  );
}

export default APADashboard;