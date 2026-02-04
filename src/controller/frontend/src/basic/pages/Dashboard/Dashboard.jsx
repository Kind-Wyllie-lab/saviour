// src/pages/Dashboard.js
import React, { useEffect, useState } from "react";

// Styling and components
import "./Dashboard.css";
import ModuleList from "/src/basic/components/ModuleList/ModuleList";
import ExperimentMetadata from "/src/basic/components/ExperimentMetadata/ExperimentMetadata";
import CommandsPanel from "/src/basic/components/CommandsPanel/CommandsPanel";


// Hooks
import useModules from "/src/hooks/useModules";
import useExperimentTitle from "/src/hooks/useExperimentTitle";

function Dashboard() {
  const { modules, moduleList } = useModules();
  const { experimentName } = useExperimentTitle();

  return (
    <main className="dashboard">
      <div className="dashboard-left">
        <section>
          <ModuleList modules = { moduleList} />
        </section>
      </div>
      <div className="dashboard-right">
        <section>
          <ExperimentMetadata experimentName={experimentName} />
        </section>
        <section>
          <CommandsPanel modules={moduleList} experimentName={experimentName} />
        </section>
      </div>
    </main>
  );
}

export default Dashboard;
