// src/pages/Dashboard.js
import React, { useEffect, useState } from "react";
import socket from "../../socket";

// Styling and components
import "./Settings.css"; // optional, if you want page-specific styles
import ModuleCard from "../../components/ModuleCard/ModuleCard";

function Settings() {
  const [modules, setModules] = useState({}); 

  useEffect(() => { // React hook that runs "side effects"; functionality outside of rendering.
    console.log("Emitting get_modules");
    socket.emit("get_modules"); // Ask backend for modules

    socket.on("modules_update", (data) => {
      setModules(data);
    }); // When modules received from backend

    // Cleanup listener on unmount
    return () => {
      socket.off("modules_update");
    };
  }, []); // [] Dependency array indicates function should run once after first render (componentDidMount)


  return (
    <main className="settings">
      <h2>Modules to update settings for</h2>
      <div className="module-grid">
        {Object.entries(modules).map(([id, module]) => (
          <ModuleCard key={id} module={module} />
        ))}
      </div>
    </main>
  );
}

export default Settings;