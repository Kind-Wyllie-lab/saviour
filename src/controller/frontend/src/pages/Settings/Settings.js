// src/pages/Dashboard.js
import React, { useEffect, useState } from "react";
import socket from "../../socket";

// Styling and components
import "./Settings.css"; // optional, if you want page-specific styles
import ConfigCard from "../../components/ConfigCard/ConfigCard";

function Settings() {
  // const [modules, setModules] = useState({}); 
  const [moduleConfigs, setModuleConfigs] = useState({}); // State to hold module configurations

  useEffect(() => { // React hook that runs "side effects"; functionality outside of rendering.
    console.log("Emitting get_module_configs");
    socket.emit("get_module_configs"); // Ask backend for module configurations

    socket.on("module_configs_update", (data) => {
      setModuleConfigs(data);
      console.log("Received module configs:", data);
      console.log(Object.entries(data.module_configs || {}));
    }); // When module configs received from backend

    // Cleanup listener on unmount
    return () => {
      socket.off("module_configs_update");
    };
  }, []); // [] Dependency array indicates function should run once after first render (componentDidMount)


  return (
    <main className="settings">
      <h2>Modules to update settings for</h2>
      <div className="module-grid">
        {Object.entries(moduleConfigs.module_configs || {}).map(([id, moduleConfig]) => (
          <ConfigCard key={id} id={id} config={moduleConfig} />
        ))}
      </div>
    </main>
  );
}

export default Settings;