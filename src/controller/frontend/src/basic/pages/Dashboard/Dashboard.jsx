// src/pages/Dashboard.js
import React from "react";

// Styling and components
import "./Dashboard.css";
import ModuleList from "/src/basic/components/ModuleList/ModuleList";
import ExperimentMetadata from "/src/basic/components/ExperimentMetadata/ExperimentMetadata";
import CommandsPanel from "/src/basic/components/CommandsPanel/CommandsPanel";
import HealthSummaryWidget from "/src/basic/components/HealthSummaryWidget/HealthSummaryWidget";
import LivestreamCard from "/src/basic/components/LivestreamCard/LivestreamCard";
import socket from "/src/socket";

// Hooks
import useModules from "/src/hooks/useModules";
import useExperimentTitle from "/src/hooks/useExperimentTitle";


function Dashboard() {
  const { moduleList } = useModules();
  const { experimentName } = useExperimentTitle();

  const cameraModules = (moduleList || []).filter(
    (m) => m.type === "camera"
  );

  return (
    <main className="dashboard">
      <div className="dashboard-left">
        <section>
          <HealthSummaryWidget />
          <ModuleList modules = { moduleList} />
        </section>
      </div>
      <div className="dashboard-right">
        <section>
          {cameraModules.map((m) => (
            <LivestreamCard module={ m } />
          ))}
        </section>
      </div>
    </main>
  );
}

export default Dashboard;
