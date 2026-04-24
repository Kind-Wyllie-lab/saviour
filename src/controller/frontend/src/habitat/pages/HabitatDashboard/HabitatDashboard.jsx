import React from "react";
import "./HabitatDashboard.css";

import useModules from "/src/hooks/useModules";
import HealthSummaryWidget from "/src/basic/components/HealthSummaryWidget/HealthSummaryWidget";
import RecordingStatusWidget from "/src/basic/components/RecordingStatusWidget/RecordingStatusWidget";
import HabitatLivestreamGrid from "../../components/HabitatLivestreamGrid/HabitatLivestreamGrid";
import HabitatModuleStatusList from "../../components/HabitatModuleStatusList/HabitatModuleStatusList";

function HabitatDashboard() {
  const { modules } = useModules();

  return (
    <main className="habitat-dashboard">
      <RecordingStatusWidget />

      <div className="habitat-dashboard-body">
        <section className="habitat-dashboard-left">
          <HealthSummaryWidget />
          <HabitatModuleStatusList modules={modules} />
        </section>

        <section className="habitat-dashboard-right">
          <div className="livestream-square">
            <HabitatLivestreamGrid modules={modules} />
          </div>
        </section>
      </div>
    </main>
  );
}

export default HabitatDashboard;
