// src/pages/Dashboard.js
import React, { useEffect, useState } from "react";
import socket from "../../socket";
import "./Settings.css";
import ConfigCard from "../../components/ConfigCard/ConfigCard";

function Settings() {
  const [modules, setModules] = useState({});

  useEffect(() => {
    console.log("Emitting get_modules");
    socket.emit("get_module_configs"); // Ask backend for module configs
    socket.emit("get_modules"); // Ask backend for modules


    // Expecting data.modules like
    // { "camera_d610": { ip: "192.168.1.136", type: "camera", online: true } }
    socket.on("modules_update", (data) => {
      console.log("Received modules:", data);

      // Catch bad payload
      if (!data || typeof data !== "object") {
        console.warn("Invalid modules_update payload:", data);
        return;
      }

      // Add default ready/checks/error
      const withDefaults = Object.fromEntries(
        Object.entries(data).map(([id, m]) => [
          id,
          { ...m, 
            id, 
            config: m.config || {}, 
            ready: false, 
            checks: {}, 
            error: null },
        ])
      );
      setModules(withDefaults);
    });

    return () => {
      socket.off("modules_update"); // Unregister listener to prevent multiple listeners on component re-render or remount
      // socket.off("update_module_readiness"); // As above
    };
  }, []);

  return (
    <main className="settings">
      <h2>Module Settings</h2>
      {Object.keys(modules).length === 0? ( // If no modules
        <p>Loading module configs, try refreshing</p>
      ) : (
        <div className="module-grid">
        {Object.entries(modules).map(([id, module]) => (
          <ConfigCard key={id} id={id} module={module} />
        ))}
      </div>
      )}
    </main>
  );
}

export default Settings;
