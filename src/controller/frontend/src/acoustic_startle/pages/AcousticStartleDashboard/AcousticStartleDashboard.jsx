// src/pages/Dashboard.js
import React, { useEffect, useState } from "react";

// Styling and components
import "./AcousticStartleDashboard.css";
import ModuleList from "/src/basic/components/ModuleList/ModuleList";
import ExperimentMetadata from "/src/basic/components/ExperimentMetadata/ExperimentMetadata";
import CommandsPanel from "/src/basic/components/CommandsPanel/CommandsPanel";

// Hooks
import useModules from "/src/hooks/useModules";
import useExperimentTitle from "/src/hooks/useExperimentTitle";
import socket from "/src/socket";


import PlaySound from "../../components/PlaySound/PlaySound";


function Dashboard() {
  const { modules, moduleList } = useModules();
  const { experimentName } = useExperimentTitle();

  useEffect(() => {
    socket.emit("get_module_configs"); // Ask backend for module configs
  }, []);

  return (
    <main className="dashboard">
      <div className="dashboard-left">
        <section>
          <ModuleList modules = {moduleList} />
        </section>
      </div>
      <div className="dashboard-right">
        <section>
          <ExperimentMetadata experimentName={experimentName} />
        </section>
        <section>
          <CommandsPanel modules={moduleList} experimentName={experimentName} />
        </section>
        <section>
          <PlaySound modules={moduleList} />
        </section>
      </div>
    </main>
  );
}

export default Dashboard;
