// src/pages/Dashboard.js
import React, { useEffect, useState } from "react";
import socket from "../../socket";
import "./Settings.css";
import ConfigCard from "../../components/ConfigCard/ConfigCard";

function Settings() {
  const [moduleConfigs, setModuleConfigs] = useState(null); // null = loading state

  useEffect(() => {
    // Define handler
    const handleUpdate = (data) => {
      console.log("Received module configs:", data);
      setModuleConfigs(data.module_configs || {});
    };

    // Listen for updates
    socket.on("module_configs_update", handleUpdate);

    // Always request current configs on mount
    console.log("Emitting get_module_configs");
    socket.emit("get_module_configs");

    // Cleanup
    return () => {
      socket.off("module_configs_update", handleUpdate);
    };
  }, []);

  if (!moduleConfigs || Object.keys(moduleConfigs).length === 0) {
    return <main className="settings">
            <h2>Module Settings</h2>
            <p>Loading module configs, try refreshing</p>
          </main>;
  }

  return (
    <main className="settings">
      <h2>Module Settings</h2>
      <div className="module-grid">
        {Object.entries(moduleConfigs).map(([id, moduleConfig]) => (
          <ConfigCard key={id} id={id} config={moduleConfig} />
        ))}
      </div>
    </main>
  );
}

export default Settings;
