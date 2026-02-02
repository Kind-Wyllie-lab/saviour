// src/pages/Dashboard.js
import React, { useEffect, useState } from "react";
import socket from "../../../socket";

// Styling and components
import "./HabitatDashboard.css";
import HabitatLivestreamGrid from "../../components/HabitatLivestreamGrid/HabitatLivestreamGrid";
import Dashboard from "../../components/Dashboard/Dashboard";

// Check websocket connection
socket.on("connect", () => {
  console.log("Connected to backend", socket.id);
});

socket.on("disconnect", () => {
  console.log("Disconnected from backend");
});

function HabitatDashboard() {
  const [modules, setModules] = useState({}); // Modules object returned from backend
  const [experimentName, setExperimentName] = useState("loading..."); // The experiment name 

  useEffect(() => {
    console.log("Emitting get_modules");
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

  return (
    <main className="dashboard">
      <section className="dashboard-left">
        <Dashboard modules={modules} />
      </section>

      <section className="dashboard-right">
        <div className="livestream-square">
          <HabitatLivestreamGrid modules={modules} />
        </div>
      </section>

    </main>
  );
}

export default HabitatDashboard;
