import React from "react";
import "./HabitatDashboard.css";

import useModules from "/src/hooks/useModules";
import useSessions from "/src/hooks/useSessions";
import HealthSummaryWidget from "/src/basic/components/HealthSummaryWidget/HealthSummaryWidget";
import HabitatLivestreamGrid from "../../components/HabitatLivestreamGrid/HabitatLivestreamGrid";
import HabitatModuleStatusList from "../../components/HabitatModuleStatusList/HabitatModuleStatusList";
import HabitatMicrophoneStrip from "../../components/HabitatMicrophoneStrip/HabitatMicrophoneStrip";

function HabitatDashboard() {
  const { modules } = useModules();
  const { sessionList } = useSessions();

  return (
    <main className="habitat-dashboard">
      <div className="habitat-dashboard-body">
        <section className="habitat-dashboard-left">
          <HealthSummaryWidget />
          <HabitatModuleStatusList modules={modules} sessions={sessionList} />
        </section>

        <section className="habitat-dashboard-center">
          <div className="livestream-square">
            <HabitatLivestreamGrid modules={modules} />
          </div>
        </section>

        <section className="habitat-dashboard-mics">
          <HabitatMicrophoneStrip modules={modules} />
        </section>
      </div>
    </main>
  );
}

export default HabitatDashboard;
