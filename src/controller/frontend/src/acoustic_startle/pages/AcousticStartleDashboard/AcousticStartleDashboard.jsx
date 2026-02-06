// src/pages/Dashboard.js
import React, { useEffect, useState } from "react";

// Styling and components
import "./AcousticStartleDashboard.css";
import ModuleList from "/src/basic/components/ModuleList/ModuleList";
import ExperimentMetadata from "/src/basic/components/ExperimentMetadata/ExperimentMetadata";
import CommandsPanel from "/src/basic/components/CommandsPanel/CommandsPanel";
import LivestreamSelector from "/src/basic/components/LivestreamSelector/LivestreamSelector";

// Hooks
import useModules from "/src/hooks/useModules";
import useExperimentTitle from "/src/hooks/useExperimentTitle";
import socket from "/src/socket";

import PlaySound from "/src/acoustic_startle/components/PlaySound/PlaySound";


function Dashboard() {
  const { modules, moduleList } = useModules();
  const { experimentName } = useExperimentTitle();

  useEffect(() => {
    socket.emit("get_module_configs"); // Ask backend for module configs
  }, []);

  return (
    <main className="dashboard">
      <div className="dashboard-left">

      </div>
      <div className="dashboard-middle">
        <LivestreamSelector modules = {moduleList} />
      </div>
      <div className="dashboard-right">
        <ModuleList modules = {moduleList} />
        {/* <ExperimentMetadata experimentName={experimentName} />
        <CommandsPanel modules={moduleList} experimentName={experimentName} /> */}
        <PlaySound modules={moduleList} />
      </div>
    </main>
  );
}

export default Dashboard;
